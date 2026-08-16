"""策略纪律规则（纯函数，确定性核心，AI 禁区）。

反追高入场闸门 + 反卖飞离场纪律。阈值为常识固定初值（见设计 spec §6），
训练段仅粗检验、不精调。不读文件/网络/时钟；相同输入恒得相同输出。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import add_indicators


# ── 反追高入场闸门 ──────────────────────────────────────────
@dataclass(frozen=True)
class GateParams:
    max_intraday_gain: float = 0.03   # 当日涨幅上限（超过=追高）
    near_limit: float = 0.095         # 接近涨停阈值
    max_ext_ma20: float = 0.15        # 偏离 MA20 上限
    rsi_overheat: float = 70.0        # RSI 过热


@dataclass
class GateResult:
    passed: bool
    checks: dict


def entry_gate(bars: pd.DataFrame, params: GateParams = GateParams()) -> GateResult:
    """逐条闸门检查该 symbol 截至信号日 T 的日线，全过 → passed=True。

    bars：含 open/high/low/close，已按日期排序；取最后一行为 T。
    """
    if len(bars) < 21:                       # 不足以算 MA20 → 保守拒绝
        return GateResult(False, {"insufficient_history": False})
    ind = add_indicators(bars)
    last = ind.iloc[-1]
    prev_close = float(ind["close"].iloc[-2])
    close = float(last["close"])
    intraday = close / prev_close - 1
    ext = close / float(last["ma20"]) - 1 if pd.notna(last["ma20"]) else 0.0

    checks = {
        "intraday": (intraday <= params.max_intraday_gain) and (intraday < params.near_limit),
        "not_extended": ext <= params.max_ext_ma20,
        "right_side": pd.notna(last["ma5"]) and close > float(last["ma5"]),
        "rsi_ok": pd.isna(last["rsi14"]) or float(last["rsi14"]) <= params.rsi_overheat,
    }
    return GateResult(passed=all(checks.values()), checks=checks)
