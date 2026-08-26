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


@dataclass(frozen=True)
class PullbackParams:
    """规则 A「回踩 MA20 企稳」参数（常识初值，供回测粗检验）。"""
    near_ma20: float = 0.02      # 收盘贴近 MA20 上限：0 < close/ma20-1 <= 2%
    min_above_days: int = 3      # 此前至少 N 天收盘在 MA20 上方（确认是回踩非破位）


def gate_pullback_ma20(bars: pd.DataFrame,
                       params: PullbackParams = PullbackParams()) -> GateResult:
    """回踩 MA20 企稳入场：收盘站上 MA20、回踩贴近 MA20、当日收阳企稳、
    此前连续在 MA20 上方（趋势中的回踩）。确定性纯函数。"""
    if len(bars) < 21:
        return GateResult(False, {"insufficient_history": False})
    ind = add_indicators(bars)
    last = ind.iloc[-1]
    close = float(last["close"])
    ma20 = float(last["ma20"])
    if pd.isna(ma20) or ma20 <= 0:
        return GateResult(False, {"no_ma20": False})
    dev = close / ma20 - 1

    # 此前 min_above_days 天是否都在 MA20 上方（倒数第 2 天起往前数）
    above = 0
    for i in range(2, len(ind)):
        if float(ind["close"].iloc[-i]) > float(ind["ma20"].iloc[-i]):
            above += 1
        else:
            break
        if above >= params.min_above_days:
            break

    checks = {
        "above_ma20": dev > 0,                                    # 站上 MA20
        "near_ma20": 0 < dev <= params.near_ma20,                 # 回踩贴近 MA20
        "bullish_bar": float(last["close"]) > float(last["open"]),  # 当日收阳企稳
        "was_above": above >= params.min_above_days,              # 此前在 MA20 上方
    }
    return GateResult(passed=all(checks.values()), checks=checks)


@dataclass(frozen=True)
class OversoldParams:
    """规则 L「左侧超卖」参数（常识固定初值，训练段仅粗检验不精调）。"""
    rsi_period: int = 14      # RSI 周期
    rsi_oversold: float = 30.0  # 超卖阈值：RSI < 该值触发左侧买入


def gate_oversold_rsi(bars: pd.DataFrame,
                      params: OversoldParams = OversoldParams()) -> GateResult:
    """左侧超卖均值回归入场：RSI(14) < 30 触发，单笔买入。

    与 gate_pullback_ma20 同款纯函数模式。确定性、不读文件/网络/时钟。
    历史不足以算 RSI → 保守拒绝。
    """
    if len(bars) < params.rsi_period + 1:
        return GateResult(False, {"insufficient_history": False})
    ind = add_indicators(bars)
    last = ind.iloc[-1]
    rsi = float(last.get("rsi14")) if "rsi14" in last else float("nan")
    checks = {"rsi_oversold": pd.notna(rsi) and rsi < params.rsi_oversold}
    return GateResult(passed=all(checks.values()), checks=checks)


# ── 基本面安全门（叠加在价格入场门之上，纯函数）─────────────────
@dataclass(frozen=True)
class FundamentalParams:
    """基本面安全门槛（常识固定初值，对应买入检查单：ROE>10、PE<35）。
    缺失基本面数据（NaN/None）一律放行——"有数据才卡"，避免早期覆盖薄导致回测空窗。"""
    roe_min: float = 10.0     # ROE 下限(%)
    pe_max: float = 35.0      # PE 上限
    require: bool = True      # True=应用该门；False=完全放行（供消融/对照）


def fundamental_safety(pe, roe, params: FundamentalParams = FundamentalParams()) -> GateResult:
    """基本面安全门：ROE ≥ roe_min 且 0 < PE ≤ pe_max。

    缺失(NaN/None) → 放行（PIT 覆盖薄时不误杀）；PE≤0(亏损/负估值) → 拒绝；
    ROE<门槛 → 拒绝。纯确定性函数，供 _select_candidates 在价格门之上叠加。
    """
    def _nan(v):
        return v is None or (isinstance(v, float) and pd.isna(v))

    if not params.require:
        return GateResult(True, {"fundamental_off": True})

    pe_ok = True
    if not _nan(pe):
        pe_ok = 0 < float(pe) <= params.pe_max
    roe_ok = True
    if not _nan(roe):
        roe_ok = float(roe) >= params.roe_min

    checks = {"pe_ok": pe_ok, "roe_ok": roe_ok}
    return GateResult(passed=all(checks.values()), checks=checks)


# ── 反卖飞离场纪律（逐仓状态机）────────────────────────────
@dataclass(frozen=True)
class ExitParams:
    chandelier_k: float = 3.0      # 吊灯止损：最高收盘 − k×ATR
    hard_stop_atr: float = 2.0     # 硬止损：入场价 − n×ATR（初始风险 R=hard_stop_atr×ATR）
    trend_ma: int = 10             # 跌破 MA10 趋势离场
    scale_out_R: float = 2.0       # 盈利达 2R 分批
    scale_out_frac: float = 0.5    # 减仓比例
    max_hold: int = 20             # 最大持仓交易日


@dataclass
class PositionState:
    entry_price: float
    atr_at_entry: float
    highest_close: float
    days_held: int = 0
    scaled_out: bool = False
    remaining: float = 1.0


@dataclass
class ExitDecision:
    action: str        # "none" | "scale_out" | "exit"
    reason: str = ""
    fraction: float = 0.0


def evaluate_exit(state: PositionState, bar: dict,
                  params: ExitParams = ExitParams()) -> ExitDecision:
    """推进持仓一日（更新最高收盘/持仓天数），按优先级返回离场决策。

    优先级：hard_stop > trailing_stop > trend_break > scale_out > max_hold。
    bar：当日 {"close", "ma10", "atr"}。纯确定性，无副作用外泄（仅改传入 state）。
    """
    close = float(bar["close"])
    atr = float(bar.get("atr") or state.atr_at_entry)
    state.days_held += 1
    state.highest_close = max(state.highest_close, close)
    R = params.hard_stop_atr * state.atr_at_entry     # 初始风险

    if close <= state.entry_price - R:
        return ExitDecision("exit", "hard_stop")
    if close <= state.highest_close - params.chandelier_k * atr:
        return ExitDecision("exit", "trailing_stop")
    ma10 = bar.get("ma10")
    if ma10 is not None and pd.notna(ma10) and close < float(ma10):
        return ExitDecision("exit", "trend_break")
    if (not state.scaled_out
            and close >= state.entry_price + params.scale_out_R * R):
        state.scaled_out = True
        state.remaining = round(state.remaining - params.scale_out_frac, 6)
        return ExitDecision("scale_out", "target_2R", params.scale_out_frac)
    if state.days_held >= params.max_hold:
        return ExitDecision("exit", "max_hold")
    return ExitDecision("none")
