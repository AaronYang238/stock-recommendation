"""引擎层 = 确定性核心（铁律一：本包内严禁任何 LLM 调用）。

技术指标、因子打分、条件筛选、回测，全部由确定性代码完成；
AI 产出只能作为已落地的特征列从 storage 读入，运行时不回调 AI。
"""
from .indicators import add_indicators
from .factors import score_factors
from .screener import FilterSpec, Condition, screen

__all__ = ["add_indicators", "score_factors", "FilterSpec", "Condition", "screen"]
