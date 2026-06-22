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
                                  fields="ts_code,name,exchange,list_date")
        df = df.rename(columns={"ts_code": "symbol"})
        df["symbol"] = df["symbol"].str.split(".").str[0]
        df["delist_date"] = None
        df["status"] = "L"
        return df[["symbol", "name", "exchange", "list_date", "delist_date", "status"]]

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
        raise NotImplementedError("tushare 基本面接口待按需补全")

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        if symbol.startswith(("60", "68", "9")):
            return f"{symbol}.SH"
        if symbol.startswith(("43", "83", "87", "88", "92")):
            return f"{symbol}.BJ"
        return f"{symbol}.SZ"
