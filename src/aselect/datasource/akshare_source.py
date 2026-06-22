"""akshare 数据源适配器（主源，免费无 Key）。

健壮性：分批 + 重试 + 列名标准化。akshare 接口随上游改版易失效，
故所有调用都包重试，并把原始中文列映射成统一 schema。
"""
from __future__ import annotations

import time

import pandas as pd

from .base import DataSource

_ADJUST_MAP = {"none": "", "qfq": "qfq", "hfq": "hfq"}

# akshare 历史行情中文列 → 统一英文列
_HIST_COLS = {
    "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
    "收盘": "close", "成交量": "volume", "成交额": "amount",
}


def _retry(fn, retry: int, backoff: float):
    last = None
    for i in range(max(1, retry)):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — 上游异常类型不稳定
            last = e
            time.sleep(backoff * (i + 1))
    raise last  # type: ignore[misc]


class AkshareSource(DataSource):
    name = "akshare"

    def __init__(self, retry: int = 3, retry_backoff_s: float = 2.0):
        import akshare as ak  # 延迟导入：未装也不影响其它源
        self.ak = ak
        self.retry = retry
        self.backoff = retry_backoff_s

    def list_symbols(self) -> pd.DataFrame:
        df = _retry(self.ak.stock_info_a_code_name, self.retry, self.backoff)
        df = df.rename(columns={"code": "symbol", "name": "name"})
        df["exchange"] = df["symbol"].map(self._exchange_of)
        df["status"] = "L"  # 在市；历史退市需另接 stock_info_sh/sz_delist 接口补充
        for c in ("list_date", "delist_date"):
            df[c] = None
        return df[["symbol", "name", "exchange", "list_date", "delist_date", "status"]]

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        adj = _ADJUST_MAP.get(adjust, "")
        kwargs = dict(symbol=symbol, period="daily", adjust=adj)
        if start:
            kwargs["start_date"] = start.replace("-", "")
        if end:
            kwargs["end_date"] = end.replace("-", "")
        df = _retry(lambda: self.ak.stock_zh_a_hist(**kwargs), self.retry, self.backoff)
        if df is None or df.empty:
            return pd.DataFrame(columns=list(_HIST_COLS.values()))
        df = df.rename(columns=_HIST_COLS)
        keep = [c for c in _HIST_COLS.values() if c in df.columns]
        df = df[keep]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df

    def fundamentals(self, symbols=None) -> pd.DataFrame:
        # 全市场快照指标（PE/PB/总市值等）。不同 akshare 版本字段略有差异，做容错映射。
        df = _retry(self.ak.stock_zh_a_spot_em, self.retry, self.backoff)
        colmap = {
            "代码": "symbol", "市盈率-动态": "pe", "市净率": "pb", "总市值": "total_mv",
        }
        df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
        if symbols:
            df = df[df["symbol"].isin(symbols)]
        df["date"] = pd.Timestamp.today().strftime("%Y-%m-%d")
        cols = ["symbol", "date", "pe", "pb", "total_mv"]
        return df[[c for c in cols if c in df.columns]]

    def index_daily(self, index_code, start=None, end=None) -> pd.DataFrame:
        df = _retry(lambda: self.ak.stock_zh_index_daily(symbol=self._index_sym(index_code)),
                    self.retry, self.backoff)
        df = df.rename(columns={"date": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        return df

    @staticmethod
    def _exchange_of(symbol: str) -> str:
        if symbol.startswith(("60", "68", "9")):
            return "SH"
        if symbol.startswith(("00", "30", "20")):
            return "SZ"
        if symbol.startswith(("43", "83", "87", "88", "92")):
            return "BJ"
        return "?"

    @staticmethod
    def _index_sym(code: str) -> str:
        # 沪深300=000300 在上交所；按前缀判断
        return ("sh" if code.startswith(("000", "999")) else "sz") + code
