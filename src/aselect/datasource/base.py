"""数据源抽象（适配器模式）。主源失效时工厂可回退备用源。

统一输出 schema：
  symbols      : symbol, name, exchange, list_date, delist_date, status
  daily        : date, open, high, low, close, volume, amount
  fundamentals : symbol, date, pe, pb, ps, roe, roa, revenue_yoy,
                 profit_yoy, gross_margin, debt_ratio, total_mv
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    def list_symbols(self) -> pd.DataFrame:
        """全市场代码与状态（须含历史退市/ST 以避免幸存者偏差）。"""

    @abstractmethod
    def daily(self, symbol: str, adjust: str,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """单只日线。adjust ∈ {none, qfq, hfq}。"""

    @abstractmethod
    def fundamentals(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """基本面关键指标快照。"""

    def index_daily(self, index_code: str,
                    start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """指数日线（基准对比用）。默认未实现。"""
        raise NotImplementedError

    def industry_map(self) -> dict[str, str]:
        """symbol → 行业 映射（供因子行业中性化）。默认空，子类可实现。"""
        return {}
