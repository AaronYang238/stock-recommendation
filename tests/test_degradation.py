"""铁律二 / 第 4.4 节：AI 禁用或 Key 缺失时优雅降级，核心完整可用。"""
from __future__ import annotations

import pandas as pd

from aselect.ai import NullAnalyzer, get_analyzer
from aselect.config import AIConfig, Config
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.engine import score_factors, screen
from aselect.engine.screener import Condition, FilterSpec


def _cfg(**ai_kw) -> Config:
    ai = AIConfig(**ai_kw)
    return Config(app={}, datasource={"adjust": "hfq"}, storage={}, backtest={}, ai=ai)


def test_disabled_returns_null():
    assert isinstance(get_analyzer(_cfg(enabled=False)), NullAnalyzer)


def test_provider_none_returns_null():
    assert isinstance(get_analyzer(_cfg(enabled=True, provider="none")), NullAnalyzer)


def test_missing_key_degrades(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    a = get_analyzer(_cfg(enabled=True, provider="anthropic"))
    assert isinstance(a, NullAnalyzer)


def test_null_methods_neutral():
    a = NullAnalyzer()
    s = a.analyze_sentiment(["利好消息", "利空消息"])
    assert all(r.sentiment == 0.0 for r in s)
    assert a.extract_events(["x"]) == []
    assert a.nl_to_filter("低估值", {"pe": ""}).conditions == []
    assert "AI 未启用" in a.generate_report({}).text


def test_core_runs_without_ai():
    """关闭 AI 时，截面构建 + 因子打分 + 筛选全流程不报错。"""
    ds = SyntheticSource()
    fund = ds.fundamentals()
    scored = score_factors(fund)
    assert "total_score" in scored.columns
    spec = FilterSpec(conditions=[Condition("pe", "<", 100)])
    out = screen(scored, spec)
    assert len(out) >= 0  # 不抛错即达标
