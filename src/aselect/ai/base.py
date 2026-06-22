"""AI 统一接口契约（Provider-Agnostic）。

输入输出强制结构化（JSON / dataclass）。实现类**不得包含任何选股/回测的
数值计算**——它们只做「文本↔结构化」的边界转换（第 4.2 节）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SentimentResult:
    sentiment: float    # [-1, 1]
    confidence: float   # [0, 1]


@dataclass(frozen=True)
class EventResult:
    ticker: str
    event_type: str     # 如 业绩预增 / 减持 / 重组 / 监管处罚 ...
    confidence: float


@dataclass(frozen=True)
class FilterSpecDTO:
    """nl_to_filter 的受控输出（参数化，不含可执行代码）。
    与 engine.screener.FilterSpec 解耦，避免引擎反向依赖 AI 包。"""
    name: str
    conditions: list[dict]   # [{field, op, value}]
    logic: str = "and"
    sort_by: str | None = None
    ascending: bool = False
    limit: int | None = None


@dataclass(frozen=True)
class ReportResult:
    text: str
    grounded: bool       # 是否严格基于传入数据（RAG）生成


class AIAnalyzer(ABC):
    """所有 AI 功能的统一入口。"""

    @abstractmethod
    def analyze_sentiment(self, texts: list[str]) -> list[SentimentResult]:
        """接入点①：新闻/公告 → 情绪分。"""

    @abstractmethod
    def extract_events(self, texts: list[str]) -> list[EventResult]:
        """接入点①：文本 → 事件标签。"""

    @abstractmethod
    def nl_to_filter(self, instruction: str, field_schema: dict) -> FilterSpecDTO:
        """接入点②：自然语言 → 受控结构化筛选条件（禁止生成可执行代码）。"""

    @abstractmethod
    def generate_report(self, candidate_data: dict) -> ReportResult:
        """接入点③：基于传入真实数据(RAG)生成分析；禁止补充任何数字。"""
