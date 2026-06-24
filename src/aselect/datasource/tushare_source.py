"""tushare 备用数据源（需积分，Token 走环境变量 TUSHARE_TOKEN）。

骨架实现：保证适配器接口齐全、可被工厂回退选中；具体接口字段按 tushare 文档补全。
"""
from __future__ import annotations

import os

import pandas as pd

from .base import DataSource


class TushareSource(DataSource):
    name = "tushare"

    def __init__(self, retry: int = 3, retry_backoff_s: float = 2.0,
                 min_interval_s: float = 0.31):
        import time as _t
        import tushare as ts  # 延迟导入
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError("缺少环境变量 TUSHARE_TOKEN")
        ts.set_token(token)
        self.pro = ts.pro_api()
        self.retry = retry
        self.backoff = retry_backoff_s
        self._min_interval = min_interval_s   # 限频：约 ≤200 次/分
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
                              fields="ts_code,name,exchange,list_date,delist_date")
            dead = dead.rename(columns={"ts_code": "symbol"})
            dead["symbol"] = dead["symbol"].str.split(".").str[0]
            dead["status"] = "D"
        except Exception:  # noqa: BLE001
            dead = None
        cols = ["symbol", "name", "exchange", "list_date", "delist_date", "status"]
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
        rows = []
        for sym in symbols:
            row = {"symbol": sym, "industry": imap.get(sym)}
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
                out = {}
                for r in df.itertuples():
                    tmv = _f(r.total_mv)
                    out[r.symbol] = {"pe": _f(r.pe), "pb": _f(r.pb), "ps": _f(r.ps),
                                     "total_mv": tmv * 1e4 if tmv is not None else None}
                return out
        return {}

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
