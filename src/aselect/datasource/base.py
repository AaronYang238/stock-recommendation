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

    # ── 历史 PIT 数据（backfill 用；不支持的源保持 NotImplementedError）──
    def trade_dates(self, start: str, end: str) -> list[str]:
        """[start, end] 内的交易日 YYYY-MM-DD 列表。"""
        raise NotImplementedError

    def valuation_by_date(self, trade_date: str) -> pd.DataFrame:
        """某交易日全市场估值：symbol, date, pe, pb, ps, total_mv, circ_mv, turnover_rate。"""
        raise NotImplementedError

    def valuation_history(self, symbol: str, start: str, end: str | None = None) -> pd.DataFrame:
        """单只逐日估值（按标的拉取的源用；列同 valuation_by_date）。"""
        raise NotImplementedError

    def fundamentals_history(self, symbol: str, start: str) -> pd.DataFrame:
        """单只全部历史报告期财务（不含估值）：symbol, date(报告期), ann_date(披露日),
        roe, roa, gross_margin, debt_ratio, revenue_yoy, profit_yoy。
        同一报告期多次披露(修订)只保留**最早**披露的一条（PIT：当时能看到的版本）。"""
        raise NotImplementedError

    def industry_history(self) -> pd.DataFrame:
        """行业归属历史：symbol, industry, in_date, out_date。"""
        raise NotImplementedError

    def name_history(self, symbols: list[str] | None = None) -> pd.DataFrame:
        """证券简称变更历史：symbol, name, start_date, end_date（用于按日判定 ST）。"""
        raise NotImplementedError

    def news(self, symbol: str) -> list[dict]:
        """个股新闻/公告文本（供 AI 舆情情绪因子的输入端）。

        每条 {"text": str, "date": "YYYY-MM-DD"}。默认空，子类可接真实新闻源。
        """
        return []


def first_disclosure(df: pd.DataFrame) -> pd.DataFrame:
    """同一 (symbol, 报告期) 多次披露(更正/修订)时只保留最早披露的一条（PIT）。"""
    if df.empty:
        return df
    df = df.dropna(subset=["date", "ann_date"])
    return (df.sort_values(["symbol", "date", "ann_date"])
              .drop_duplicates(["symbol", "date"], keep="first")
              .reset_index(drop=True))
