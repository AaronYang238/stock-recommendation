"""NullAnalyzer：优雅降级实现（强制行为，第 4.4 节）。

当 ai.enabled=false / provider=none / Key 缺失时由工厂返回。
所有方法返回中性/空结果，绝不抛错，保证确定性核心完整可用。
"""
from __future__ import annotations

from .base import (
    AIAnalyzer, SentimentResult, EventResult, FilterSpecDTO, ReportResult,
)

_PLACEHOLDER = "（AI 未启用：本报告为占位，未生成 AI 分析。）"


class NullAnalyzer(AIAnalyzer):
    def analyze_sentiment(self, texts):
        return [SentimentResult(sentiment=0.0, confidence=0.0) for _ in texts]

    def extract_events(self, texts):
        return []  # 无事件

    def nl_to_filter(self, instruction, field_schema):
        # 返回空条件：引擎照常运行，等价于「不额外过滤」
        return FilterSpecDTO(name="ai-disabled", conditions=[])

    def generate_report(self, candidate_data):
        return ReportResult(text=_PLACEHOLDER, grounded=True)
