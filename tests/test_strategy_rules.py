"""策略纪律规则：反追高入场闸门 + 反卖飞离场状态机（确定性核心，AI 禁区）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.engine.strategy_rules import (
    ExitParams, GateParams, OversoldParams, PositionState, entry_gate,
    evaluate_exit, gate_oversold_rsi,
)


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


# ── 左侧超卖闸门（规则 L）───────────────────────────────────
def test_oversold_gate_triggers_on_crash():
    # 持续下跌 → RSI 落到 <30 → 触发左侧买入
    closes = list(np.linspace(10, 20, 40)) + list(np.linspace(20, 8, 20))
    res = gate_oversold_rsi(_bars(closes))
    assert res.passed is True
    assert res.checks["rsi_oversold"] is True


def test_oversold_gate_rejects_uptrend():
    # 平稳上行 → RSI 高位 → 不触发
    closes = list(np.linspace(10, 20, 60))
    res = gate_oversold_rsi(_bars(closes))
    assert res.passed is False


def test_oversold_gate_rejects_short_history():
    # 历史不足以算 RSI(14) → 保守拒绝
    res = gate_oversold_rsi(_bars(list(np.linspace(10, 5, 5))))
    assert res.passed is False


def test_oversold_gate_custom_threshold():
    # 高阈值(200)：默认(30)拒绝上行市，但阈值调高到必然高于任意 RSI 后放行 → 参数生效
    closes = list(np.linspace(10, 20, 60))
    assert gate_oversold_rsi(_bars(closes)).passed is False
    res = gate_oversold_rsi(_bars(closes),
                            params=OversoldParams(rsi_oversold=200.0))
    assert res.passed is True


# ── Task 3: 离场状态机 ───────────────────────────────────────
def _state(entry=100.0, atr=2.0):
    return PositionState(entry_price=entry, atr_at_entry=atr, highest_close=entry)


def test_exit_hard_stop_triggers_first():
    st = _state()
    d = evaluate_exit(st, {"close": 95.0, "ma10": 99.0, "atr": 2.0})   # < 100-2*2=96
    assert d.action == "exit" and d.reason == "hard_stop"


def test_exit_trailing_chandelier():
    st = _state()
    evaluate_exit(st, {"close": 120.0, "ma10": 110.0, "atr": 2.0})     # 抬高 highest=120
    d = evaluate_exit(st, {"close": 113.0, "ma10": 112.0, "atr": 2.0})  # <120-3*2=114
    assert d.action == "exit" and d.reason == "trailing_stop"


def test_exit_trend_break_below_ma10():
    st = _state()
    d = evaluate_exit(st, {"close": 101.0, "ma10": 102.0, "atr": 2.0})  # 未触止损但跌破 MA10
    assert d.action == "exit" and d.reason == "trend_break"


def test_scale_out_at_2R_once():
    st = _state(entry=100.0, atr=2.0)     # R=hard_stop_atr*atr=2*2=4 → 2R=+8 → 108
    d1 = evaluate_exit(st, {"close": 109.0, "ma10": 105.0, "atr": 2.0})
    assert d1.action == "scale_out" and abs(d1.fraction - 0.5) < 1e-9
    assert st.scaled_out is True and abs(st.remaining - 0.5) < 1e-9
    d2 = evaluate_exit(st, {"close": 110.0, "ma10": 106.0, "atr": 2.0})
    assert d2.action != "scale_out"


def test_exit_max_hold():
    st = _state()
    st.days_held = 19
    d = evaluate_exit(st, {"close": 101.0, "ma10": 100.0, "atr": 2.0})  # 满 20 日
    assert d.action == "exit" and d.reason == "max_hold"
