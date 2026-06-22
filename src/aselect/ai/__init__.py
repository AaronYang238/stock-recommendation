"""AI 模块：独立 + 适配器模式，对核心完全可插拔（铁律二）。

业务侧只依赖 base.AIAnalyzer 抽象与 factory.get_analyzer；
换提供商/模型/Key、开关单个功能均只改配置，不改业务代码。
"""
from .base import (
    AIAnalyzer, SentimentResult, EventResult, ReportResult,
)
from .factory import get_analyzer
from .null_analyzer import NullAnalyzer

__all__ = [
    "AIAnalyzer", "SentimentResult", "EventResult", "ReportResult",
    "get_analyzer", "NullAnalyzer",
]
