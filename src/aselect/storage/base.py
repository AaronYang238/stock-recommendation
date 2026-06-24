"""存储抽象。存储层需可替换（SQLite/Parquet → DuckDB/时序库）。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Storage(ABC):
    """统一存储接口。所有读写以 pandas.DataFrame 为载体。"""

    # ── 股票列表与状态（含历史退市/ST，用于避免幸存者偏差）──
    @abstractmethod
    def upsert_symbols(self, df: pd.DataFrame) -> None: ...

    @abstractmethod
    def get_symbols(self, include_delisted: bool = True) -> pd.DataFrame: ...

    # ── 日线行情 ──
    @abstractmethod
    def upsert_daily(self, symbol: str, df: pd.DataFrame, adjust: str) -> None: ...

    @abstractmethod
    def get_daily(
        self,
        symbol: str,
        adjust: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame: ...

    @abstractmethod
    def last_daily_date(self, symbol: str, adjust: str) -> str | None:
        """返回某标的已存最后一个交易日，用于增量更新。"""

    # ── 基本面指标 ──
    @abstractmethod
    def upsert_fundamentals(self, df: pd.DataFrame) -> None: ...

    @abstractmethod
    def get_fundamentals(self, symbols: list[str] | None = None,
                         as_of: str | None = None) -> pd.DataFrame:
        """as_of：point-in-time 截止日，只取截至该日已披露的财务数据（防前视）。"""

    # ── AI 产出特征（情绪分/事件标签等，作为普通因子列存储）──
    @abstractmethod
    def upsert_features(self, df: pd.DataFrame) -> None: ...

    @abstractmethod
    def get_features(self, symbols: list[str] | None = None,
                     as_of: str | None = None) -> pd.DataFrame: ...

    # ── 指数日线 / 数据状态（可选实现） ──
    def upsert_index(self, code: str, df: pd.DataFrame) -> None:
        raise NotImplementedError

    def get_index(self, code: str, start: str | None = None,
                  end: str | None = None) -> pd.DataFrame:
        return pd.DataFrame(columns=["date", "close"])

    def data_status(self) -> dict:
        return {}

    def close(self) -> None:  # 可选实现
        pass
