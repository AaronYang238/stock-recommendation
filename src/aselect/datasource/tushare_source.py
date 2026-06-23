"""tushare 备用数据源（需积分，Token 走环境变量 TUSHARE_TOKEN）。

骨架实现：保证适配器接口齐全、可被工厂回退选中；具体接口字段按 tushare 文档补全。
"""
from __future__ import annotations

import os

import pandas as pd

from .base import DataSource


class TushareSource(DataSource):
    name = "tushare"

    def __init__(self, retry: int = 3, retry_backoff_s: float = 2.0):
        import tushare as ts  # 延迟导入
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError("缺少环境变量 TUSHARE_TOKEN")
        ts.set_token(token)
        self.pro = ts.pro_api()
        self.retry = retry
        self.backoff = retry_backoff_s

    def list_symbols(self) -> pd.DataFrame:
        df = self.pro.stock_basic(exchange="", list_status="L",
                                  fields="ts_code,name,exchange,list_date,industry")
        df = df.rename(columns={"ts_code": "symbol"})
        df["symbol"] = df["symbol"].str.split(".").str[0]
        df["delist_date"] = None
        df["status"] = "L"
        self._industry_cache = dict(zip(df["symbol"], df.get("industry", "")))
        return df[["symbol", "name", "exchange", "list_date", "delist_date", "status"]]

    def industry_map(self) -> dict[str, str]:
        """tushare stock_basic 自带申万行业字段，直接取用。"""
        cache = getattr(self, "_industry_cache", None)
        if cache:
            return {k: v for k, v in cache.items() if v}
        df = self.pro.stock_basic(exchange="", list_status="L",
                                  fields="ts_code,industry")
        df["symbol"] = df["ts_code"].str.split(".").str[0]
        return {r.symbol: r.industry for r in df.itertuples() if r.industry}

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        ts_code = self._to_ts_code(symbol)
        df = self.pro.daily(ts_code=ts_code,
                            start_date=(start or "").replace("-", ""),
                            end_date=(end or "").replace("-", ""))
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low",
                                         "close", "volume", "amount"])
        df = df.rename(columns={"trade_date": "date", "vol": "volume", "amount": "amount"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["date", "open", "high", "low", "close", "volume", "amount"]].iloc[::-1]

    def fundamentals(self, symbols=None) -> pd.DataFrame:
        """估值(daily_basic 最新) + 财务指标(fina_indicator 最新一期，含披露日 ann_date)。

        逐只拉取，适配 `update --limit N` 的批量；单只失败跳过。
        - 估值 pe/pb/ps/total_mv 为价格快照（按交易日变化）。
        - roe/毛利率/负债率/营收同比/净利同比 来自财报，date=报告期、ann_date=披露日，
          供 point-in-time 对齐（铁律2）。
        """
        if not symbols:
            return pd.DataFrame()
        imap = self.industry_map()
        rows = []
        for sym in symbols:
            ts_code = self._to_ts_code(sym)
            row = {"symbol": sym, "industry": imap.get(sym)}
            try:
                db = self.pro.daily_basic(
                    ts_code=ts_code,
                    fields="trade_date,pe,pb,ps,total_mv")
                if db is not None and not db.empty:
                    last = db.sort_values("trade_date").iloc[-1]
                    row.update(pe=_f(last.get("pe")), pb=_f(last.get("pb")),
                               ps=_f(last.get("ps")),
                               total_mv=_f(last.get("total_mv")) * 1e4
                               if last.get("total_mv") is not None else None)
            except Exception:  # noqa: BLE001
                pass
            try:
                fi = self.pro.fina_indicator(
                    ts_code=ts_code,
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
