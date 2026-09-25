"""tushare 备用数据源（需积分，Token 走环境变量 TUSHARE_TOKEN）。

骨架实现：保证适配器接口齐全、可被工厂回退选中；具体接口字段按 tushare 文档补全。
"""
from __future__ import annotations

import os

import pandas as pd

from .base import DataSource, first_disclosure


class TushareSource(DataSource):
    name = "tushare"

    def __init__(self, retry: int = 3, retry_backoff_s: float = 2.0,
                 min_interval_s: float = 0.5, timeout_s: float = 30.0):
        import socket
        import time as _t
        import tushare as ts  # 延迟导入
        # 关键健壮性：HTTP 调用不设超时会永久挂起（本服务器代理路径曾出现）
        socket.setdefaulttimeout(timeout_s)
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError("缺少环境变量 TUSHARE_TOKEN")
        ts.set_token(token)
        self.pro = ts.pro_api()
        api_url = os.environ.get("TUSHARE_HTTP_URL")  # 第三方代理端点(可选)
        if api_url:
            self.pro._DataApi__http_url = api_url
        self.retry = retry
        self.backoff = retry_backoff_s
        self._min_interval = min_interval_s   # 限频：0.5s ≈ ≤120 次/分（< 第三方150限流，留余量）
        self._last_call = 0.0
        self._t = _t

    def _call(self, api: str, **kwargs):
        """限频 + 重试地调用 tushare pro 接口。"""
        for i in range(max(1, self.retry)):
            wait = self._min_interval - (self._t.time() - self._last_call)
            if wait > 0:
                self._t.sleep(wait)
            try:
                self._last_call = self._t.time()
                return getattr(self.pro, api)(**kwargs)
            except Exception:  # noqa: BLE001
                if i == self.retry - 1:
                    raise
                self._t.sleep(self.backoff * (i + 1))

    def list_symbols(self) -> pd.DataFrame:
        from ..data.symbols import merge_symbols

        live = self._call("stock_basic", exchange="", list_status="L",
                          fields="ts_code,name,exchange,list_date,industry")
        live = live.rename(columns={"ts_code": "symbol"})
        live["symbol"] = live["symbol"].str.split(".").str[0]
        self._industry_cache = dict(zip(live["symbol"], live.get("industry", "")))
        live["status"] = "L"
        live["delist_date"] = None

        # 退市标的（防幸存者偏差）
        try:
            dead = self._call("stock_basic", exchange="", list_status="D",
                              fields="ts_code,name,exchange,list_date,delist_date,industry")
            dead = dead.rename(columns={"ts_code": "symbol"})
            dead["symbol"] = dead["symbol"].str.split(".").str[0]
            dead["status"] = "D"
        except Exception:  # noqa: BLE001
            dead = None
        cols = ["symbol", "name", "exchange", "list_date", "delist_date", "status", "industry"]
        for df_ in (live, dead):
            if df_ is not None and "industry" not in df_.columns:
                df_["industry"] = None
        return merge_symbols(live[cols], dead[cols] if dead is not None else None)

    def industry_map(self) -> dict[str, str]:
        """tushare stock_basic 自带申万行业字段，直接取用。"""
        cache = getattr(self, "_industry_cache", None)
        if cache:
            return {k: v for k, v in cache.items() if v}
        df = self._call("stock_basic", exchange="", list_status="L",
                        fields="ts_code,industry")
        df["symbol"] = df["ts_code"].str.split(".").str[0]
        return {r.symbol: r.industry for r in df.itertuples() if r.industry}

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        ts_code = self._to_ts_code(symbol)
        df = self._call("daily", ts_code=ts_code,
                        start_date=(start or "").replace("-", ""),
                        end_date=(end or "").replace("-", ""))
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low",
                                         "close", "volume", "amount"])
        df = df.rename(columns={"trade_date": "date", "vol": "volume", "amount": "amount"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["date", "open", "high", "low", "close", "volume", "amount"]].iloc[::-1]

    def fundamentals(self, symbols=None) -> pd.DataFrame:
        """估值(daily_basic 全市场一日批量) + 财务(fina_indicator 逐只最新一期，含披露日)。

        - 估值 pe/pb/ps/total_mv：用最近交易日的 `daily_basic` **一次拉全市场**再按 symbol 取，
          避免逐只调用被限频（B1 痛点）。
        - roe/毛利率/负债率/营收·净利同比：`fina_indicator` 逐只最新一期，date=报告期、
          ann_date=披露日 → 供 point-in-time 对齐（铁律2）。逐只调用已限频+重试。
        """
        if not symbols:
            return pd.DataFrame()
        imap = self.industry_map()
        valuation = self._latest_valuation()           # symbol -> {pe,pb,ps,total_mv}
        val_date = getattr(self, "_val_date", None)
        rows = []
        for sym in symbols:
            row = {"symbol": sym, "industry": imap.get(sym), "val_date": val_date}
            row.update(valuation.get(sym, {}))
            try:
                fi = self._call("fina_indicator", ts_code=self._to_ts_code(sym),
                                fields="end_date,ann_date,roe,roa,grossprofit_margin,"
                                       "debt_to_assets,or_yoy,netprofit_yoy")
                if fi is not None and not fi.empty:
                    f = fi.sort_values("end_date").iloc[-1]
                    row.update(
                        date=_d(f.get("end_date")), ann_date=_d(f.get("ann_date")),
                        roe=_f(f.get("roe")), roa=_f(f.get("roa")),
                        gross_margin=_f(f.get("grossprofit_margin")),
                        debt_ratio=_f(f.get("debt_to_assets")),
                        revenue_yoy=_f(f.get("or_yoy")), profit_yoy=_f(f.get("netprofit_yoy")))
            except Exception:  # noqa: BLE001
                pass
            row.setdefault("date", pd.Timestamp.today().strftime("%Y-%m-%d"))
            row.setdefault("ann_date", row["date"])
            rows.append(row)
        return pd.DataFrame(rows)

    def _latest_valuation(self) -> dict[str, dict]:
        """取最近一个交易日的全市场估值（daily_basic 单次调用）。"""
        for back in range(0, 10):
            d = (pd.Timestamp.today() - pd.Timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = self._call("daily_basic", trade_date=d,
                                fields="ts_code,pe,pb,ps,total_mv")
            except Exception:  # noqa: BLE001
                continue
            if df is not None and not df.empty:
                df["symbol"] = df["ts_code"].str.split(".").str[0]
                self._val_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"   # 估值所属交易日
                out = {}
                for r in df.itertuples():
                    tmv = _f(r.total_mv)
                    out[r.symbol] = {"pe": _f(r.pe), "pb": _f(r.pb), "ps": _f(r.ps),
                                     "total_mv": tmv * 1e4 if tmv is not None else None}
                return out
        return {}

    # ── 历史 PIT 数据（backfill）──
    def trade_dates(self, start: str, end: str) -> list[str]:
        df = self._call("trade_cal", exchange="SSE", is_open="1",
                        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
                        fields="cal_date")
        if df is None or df.empty:
            return []
        return sorted(_d(x) for x in df["cal_date"])

    def valuation_by_date(self, trade_date: str) -> pd.DataFrame:
        df = self._call("daily_basic", trade_date=trade_date.replace("-", ""),
                        fields="ts_code,trade_date,pe_ttm,pe,pb,ps_ttm,ps,"
                               "total_mv,circ_mv,turnover_rate")
        if df is None or df.empty:
            return pd.DataFrame()
        out = pd.DataFrame({
            "symbol": df["ts_code"].str.split(".").str[0],
            "date": df["trade_date"].map(_d),
            # TTM 口径优先（与季报节奏无关、跨期可比）；缺失回退静态
            "pe": pd.to_numeric(df["pe_ttm"], errors="coerce").fillna(
                pd.to_numeric(df["pe"], errors="coerce")),
            "pb": pd.to_numeric(df["pb"], errors="coerce"),
            "ps": pd.to_numeric(df["ps_ttm"], errors="coerce").fillna(
                pd.to_numeric(df["ps"], errors="coerce")),
            "total_mv": pd.to_numeric(df["total_mv"], errors="coerce") * 1e4,   # 万元 → 元
            "circ_mv": pd.to_numeric(df["circ_mv"], errors="coerce") * 1e4,
            "turnover_rate": pd.to_numeric(df["turnover_rate"], errors="coerce"),
        })
        return out

    def fundamentals_history(self, symbol: str, start: str) -> pd.DataFrame:
        fi = self._call("fina_indicator", ts_code=self._to_ts_code(symbol),
                        start_date=start.replace("-", ""),
                        fields="end_date,ann_date,roe,roa,grossprofit_margin,"
                               "debt_to_assets,or_yoy,netprofit_yoy")
        if fi is None or fi.empty:
            return pd.DataFrame()
        out = pd.DataFrame({
            "symbol": symbol,
            "date": fi["end_date"].map(_d), "ann_date": fi["ann_date"].map(_d),
            "roe": pd.to_numeric(fi["roe"], errors="coerce"),
            "roa": pd.to_numeric(fi["roa"], errors="coerce"),
            "gross_margin": pd.to_numeric(fi["grossprofit_margin"], errors="coerce"),
            "debt_ratio": pd.to_numeric(fi["debt_to_assets"], errors="coerce"),
            "revenue_yoy": pd.to_numeric(fi["or_yoy"], errors="coerce"),
            "profit_yoy": pd.to_numeric(fi["netprofit_yoy"], errors="coerce"),
        })
        return first_disclosure(out)

    def industry_history(self) -> pd.DataFrame:
        """申万(2021)一级行业成分历史（index_member_all，含纳入/剔除日）。"""
        cls = self._call("index_classify", level="L1", src="SW2021")
        if cls is None or cls.empty:
            return pd.DataFrame()
        frames = []
        for code, name in zip(cls["index_code"], cls["industry_name"]):
            for is_new in ("Y", "N"):
                m = self._call("index_member_all", l1_code=code, is_new=is_new,
                               fields="ts_code,l1_name,in_date,out_date")
                if m is None or m.empty:
                    continue
                frames.append(pd.DataFrame({
                    "symbol": m["ts_code"].str.split(".").str[0],
                    "industry": m["l1_name"].fillna(name),
                    "in_date": m["in_date"].map(_d),
                    "out_date": m["out_date"].map(_d),
                }))
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["in_date"])

    def name_history(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """namechange 全市场分页拉取（每页 ≤ 5000 行）；给定 symbols 时逐只拉。"""
        fields = "ts_code,name,start_date,end_date"
        frames = []
        if symbols:
            for sym in symbols:
                df = self._call("namechange", ts_code=self._to_ts_code(sym), fields=fields)
                if df is not None and not df.empty:
                    frames.append(df)
        else:
            offset = 0
            while True:
                df = self._call("namechange", fields=fields, limit=5000, offset=offset)
                if df is None or df.empty:
                    break
                frames.append(df)
                if len(df) < 5000:
                    break
                offset += 5000
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        return pd.DataFrame({
            "symbol": df["ts_code"].str.split(".").str[0],
            "name": df["name"],
            "start_date": df["start_date"].map(_d),
            "end_date": df["end_date"].map(_d),
        }).dropna(subset=["start_date"]).drop_duplicates(["symbol", "start_date"])

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        if symbol.startswith(("60", "68", "9")):
            return f"{symbol}.SH"
        if symbol.startswith(("43", "83", "87", "88", "92")):
            return f"{symbol}.BJ"
        return f"{symbol}.SZ"


def _f(v):
    """转 float；空/非数返回 None。"""
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _d(v):
    """tushare 日期 YYYYMMDD → YYYY-MM-DD；空返回 None。"""
    if not v or pd.isna(v):
        return None
    s = str(int(v)) if isinstance(v, (int, float)) else str(v)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s
