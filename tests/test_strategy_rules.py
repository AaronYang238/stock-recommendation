"""策略纪律规则：反追高入场闸门 + 反卖飞离场状态机（确定性核心，AI 禁区）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.engine.strategy_rules import GateParams, entry_gate


def _bars(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or [c * 1.005 for c in closes]
    lows = lows or [c * 0.995 for c in closes]
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [1e6] * n,
    })


# ── Task 2: 入场闸门 ─────────────────────────────────────────
def test_gate_passes_clean_right_side_entry():
    # 上行 → 回撤冷却 RSI → 温和反弹重回 MA5：四闸门皆过（非单调，否则 RSI≈100）
    closes = (list(np.linspace(10, 13, 28))
              + list(np.linspace(13, 12.2, 8))
              + [12.25, 12.35])
    res = entry_gate(_bars(closes))
    assert res.passed is True
    assert all(res.checks.values())


def test_gate_rejects_intraday_surge():
    closes = list(np.linspace(10, 12, 40)) + [12 * 1.06]
    res = entry_gate(_bars(closes))
    assert res.passed is False
    assert res.checks["intraday"] is False


def test_gate_rejects_overextended_from_ma20():
    closes = list(np.linspace(10, 11, 40)) + [15.0]
    res = entry_gate(_bars(closes))
    assert res.passed is False
    assert res.checks["not_extended"] is False


def test_gate_rejects_below_ma5_falling_knife():
    closes = list(np.linspace(20, 10, 40)) + [9.0]
    res = entry_gate(_bars(closes))
    assert res.passed is False
    assert res.checks["right_side"] is False
