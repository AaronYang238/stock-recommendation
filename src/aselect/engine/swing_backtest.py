"""事件驱动·逐仓·周级摆动回测（纯引擎，确定性核心，AI 禁区）。

现有 factor_backtest 是收盘价面板·按期固定权重，无法表达日级移动止损。
本模块按 OHLC+指标逐日撮合单笔交易：T 日收盘出信号 → T+1 开盘成交，逐日 evaluate_exit，
涨跌停锁死顺延，计入 A 股摩擦（佣金/印花税/过户/滑点）。只做纯计算，编排在 runner。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy_rules import ExitParams, PositionState, evaluate_exit


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    ret: float                 # 扣全部成本后的净收益（对整仓）
    reason: str
    scaled: bool = False


@dataclass
class SwingReport:
    total_return: float
    sharpe: float
    max_drawdown: float
    expectancy: float          # 每笔平均净收益（期望值）
    profit_loss_ratio: float   # 平均盈利笔 / 平均亏损笔（按笔）
    win_rate: float            # 仅参考，不作验收
    n_trades: int
    trades: list = field(default_factory=list, repr=False)
    equity_curve: pd.Series = field(default_factory=pd.Series, repr=False)


def _is_limit(prev_close: float, price: float, limit_pct: float) -> int:
    """返回 +1=涨停锁死 / -1=跌停锁死 / 0=可成交（用开盘相对昨收近似）。"""
    if prev_close <= 0:
        return 0
    chg = price / prev_close - 1
    if chg >= limit_pct:
        return 1
    if chg <= -limit_pct:
        return -1
    return 0


def _fillable_open(frame: pd.DataFrame, start_i: int, side: str,
                   limit_pct: float) -> int | None:
    """从 start_i 起找首个可成交日：买入避开涨停锁死，卖出避开跌停锁死。"""
    for i in range(start_i, len(frame)):
        prev_close = float(frame["close"].iloc[i - 1]) if i > 0 else float(frame["open"].iloc[i])
        lim = _is_limit(prev_close, float(frame["open"].iloc[i]), limit_pct)
        if side == "buy" and lim == 1:
            continue
        if side == "sell" and lim == -1:
            continue
        return i
    return None


def simulate_position(frame: pd.DataFrame, entry_idx: int, cost: dict,
                      exit_params: ExitParams = ExitParams(),
                      limit_pct: float = 0.095) -> Trade | None:
    """模拟一笔交易：entry_idx 为信号日 T，T+1 开盘入场，逐日离场。

    frame：单只按日期索引、含 close/high/low/ma10/atr14 的表。无法入场返回 None。
    """
    comm = float(cost.get("commission", 0.00025))
    stamp = float(cost.get("stamp_tax", 0.001))
    transfer = float(cost.get("transfer_fee", 0.00001))
    slip = float(cost.get("slippage", 0.001))
    buy_cost = comm + transfer + slip
    sell_cost = comm + transfer + slip + stamp

    n = len(frame)
    bi = _fillable_open(frame, entry_idx + 1, "buy", limit_pct)
    if bi is None or bi >= n:
        return None
    entry_price = float(frame["open"].iloc[bi])
    atr0 = float(frame["atr14"].iloc[bi])
    if not np.isfinite(atr0) or atr0 <= 0:
        atr0 = max(entry_price * 0.02, 1e-6)     # ATR 缺失兜底
    state = PositionState(entry_price=entry_price, atr_at_entry=atr0,
                          highest_close=entry_price)

    scaled_gross = 0.0        # 已分批了结部分对整仓的毛贡献
    scaled_frac = 0.0
    for i in range(bi + 1, n):
        bar = {"close": float(frame["close"].iloc[i]),
               "ma10": frame["ma10"].iloc[i],
               "atr": float(frame["atr14"].iloc[i]) if np.isfinite(frame["atr14"].iloc[i]) else atr0}
        dec = evaluate_exit(state, bar, exit_params)
        if dec.action == "scale_out":
            si = _fillable_open(frame, i + 1, "sell", limit_pct)
            if si is not None:
                px = float(frame["open"].iloc[si])
                scaled_gross += dec.fraction * (px / entry_price - 1)
                scaled_frac += dec.fraction
            continue
        if dec.action == "exit":
            si = _fillable_open(frame, i + 1, "sell", limit_pct)
            si = si if si is not None else n - 1
            exit_price = float(frame["open"].iloc[si])
            exit_date = frame.index[si]
            rem = 1.0 - scaled_frac
            gross = scaled_gross + rem * (exit_price / entry_price - 1)
            ret = gross - buy_cost - sell_cost
            return Trade(_sym(frame), frame.index[bi], exit_date,
                         entry_price, exit_price, round(ret, 6), dec.reason,
                         scaled=scaled_frac > 0)

    # 未触发离场 → 末日收盘强平
    exit_price = float(frame["close"].iloc[-1])
    rem = 1.0 - scaled_frac
    gross = scaled_gross + rem * (exit_price / entry_price - 1)
    ret = gross - buy_cost - sell_cost
    return Trade(_sym(frame), frame.index[bi], frame.index[-1],
                 entry_price, exit_price, round(ret, 6), "eod_close",
                 scaled=scaled_frac > 0)


def _sym(frame: pd.DataFrame) -> str:
    return str(frame["symbol"].iloc[0]) if "symbol" in frame.columns else "?"


def _costs(cost: dict) -> tuple[float, float]:
    comm = float(cost.get("commission", 0.00025))
    stamp = float(cost.get("stamp_tax", 0.001))
    transfer = float(cost.get("transfer_fee", 0.00001))
    slip = float(cost.get("slippage", 0.001))
    return comm + transfer + slip, comm + transfer + slip + stamp


def _mk(frame, bi, si, entry_price, exit_price, reason,
        buy_cost, sell_cost) -> Trade:
    ret = (exit_price / entry_price - 1) - buy_cost - sell_cost
    return Trade(_sym(frame), frame.index[bi], frame.index[si],
                 entry_price, float(exit_price), round(ret, 6), reason)


# ── 消融基线离场（用户 2026-08-16 决定：两条基线都对照）──────
def simulate_position_sell_on_limit(frame, entry_idx, cost,
                                    exit_params: ExitParams = ExitParams(),
                                    limit_pct: float = 0.095) -> Trade | None:
    """基线①「涨停即清」：首次涨停收盘的次日开盘全清；否则最大持仓/末日清。"""
    buy_cost, sell_cost = _costs(cost)
    n = len(frame)
    bi = _fillable_open(frame, entry_idx + 1, "buy", limit_pct)
    if bi is None or bi >= n:
        return None
    entry_price = float(frame["open"].iloc[bi])
    for k, i in enumerate(range(bi + 1, n), start=1):
        prev = float(frame["close"].iloc[i - 1])
        close = float(frame["close"].iloc[i])
        if prev > 0 and close / prev - 1 >= limit_pct:            # 涨停
            si = _fillable_open(frame, i + 1, "sell", limit_pct)
            si = si if si is not None else n - 1
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "sell_on_limit", buy_cost, sell_cost)
        if k >= exit_params.max_hold:
            si = _fillable_open(frame, i + 1, "sell", limit_pct) or i
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "max_hold", buy_cost, sell_cost)
    return _mk(frame, bi, n - 1, entry_price, float(frame["close"].iloc[-1]),
               "eod_close", buy_cost, sell_cost)


def simulate_position_fixed_take(frame, entry_idx, cost,
                                 exit_params: ExitParams = ExitParams(),
                                 limit_pct: float = 0.095,
                                 fixed_pct: float = 0.08) -> Trade | None:
    """基线②「固定止盈」：盈利达 fixed_pct 次日开盘全清；否则最大持仓/末日清。"""
    buy_cost, sell_cost = _costs(cost)
    n = len(frame)
    bi = _fillable_open(frame, entry_idx + 1, "buy", limit_pct)
    if bi is None or bi >= n:
        return None
    entry_price = float(frame["open"].iloc[bi])
    target = entry_price * (1 + fixed_pct)
    for k, i in enumerate(range(bi + 1, n), start=1):
        if float(frame["close"].iloc[i]) >= target:
            si = _fillable_open(frame, i + 1, "sell", limit_pct)
            si = si if si is not None else n - 1
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "fixed_take", buy_cost, sell_cost)
        if k >= exit_params.max_hold:
            si = _fillable_open(frame, i + 1, "sell", limit_pct) or i
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "max_hold", buy_cost, sell_cost)
    return _mk(frame, bi, n - 1, entry_price, float(frame["close"].iloc[-1]),
               "eod_close", buy_cost, sell_cost)


def simulate_position_fixed_stop_take(frame, entry_idx, cost,
                                      exit_params: ExitParams = ExitParams(),
                                      limit_pct: float = 0.095,
                                      stop_pct: float = 0.08,
                                      take_pct: float = 0.20) -> Trade | None:
    """固定百分比「止损 + 止盈」：跌 stop_pct 止损、涨 take_pct 止盈，次日开盘执行；
    最大持仓/末日兜底。用于验证「回踩买 + 8%止损 + 20%止盈」这套参数。"""
    buy_cost, sell_cost = _costs(cost)
    n = len(frame)
    bi = _fillable_open(frame, entry_idx + 1, "buy", limit_pct)
    if bi is None or bi >= n:
        return None
    entry_price = float(frame["open"].iloc[bi])
    stop_line = entry_price * (1 - stop_pct)
    take_line = entry_price * (1 + take_pct)
    for k, i in enumerate(range(bi + 1, n), start=1):
        close = float(frame["close"].iloc[i])
        if close <= stop_line:
            si = _fillable_open(frame, i + 1, "sell", limit_pct)
            si = si if si is not None else n - 1
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "fixed_stop", buy_cost, sell_cost)
        if close >= take_line:
            si = _fillable_open(frame, i + 1, "sell", limit_pct)
            si = si if si is not None else n - 1
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "fixed_take", buy_cost, sell_cost)
        if k >= exit_params.max_hold:
            si = _fillable_open(frame, i + 1, "sell", limit_pct) or i
            return _mk(frame, bi, si, entry_price, float(frame["open"].iloc[si]),
                       "max_hold", buy_cost, sell_cost)
    return _mk(frame, bi, n - 1, entry_price, float(frame["close"].iloc[-1]),
               "eod_close", buy_cost, sell_cost)


def _swing_metrics(trades: list, cost: dict, baskets: dict | None = None) -> SwingReport:
    """由逐笔交易 + 每调仓日等权篮子收益，汇总组合指标（按笔盈亏比/期望，含胜率仅参考）。"""
    n = len(trades)
    if n == 0:
        return SwingReport(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0,
                           trades=[], equity_curve=pd.Series(dtype=float))
    rets = pd.Series([t.ret for t in trades], dtype=float)
    wins, losses = rets[rets > 0], rets[rets < 0]
    expectancy = float(rets.mean())
    pl_ratio = float(wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() != 0 else 0.0
    win_rate = float(len(wins) / n)

    baskets = baskets or {}
    if baskets:
        s = pd.Series(baskets).sort_index()
        eq = (1 + s).cumprod()
        total = float(eq.iloc[-1] - 1)
        sharpe = float(np.sqrt(52) * s.mean() / s.std()) if s.std() > 0 else 0.0
        peak = eq.cummax()
        mdd = float(((eq - peak) / peak).min())
    else:
        eq, total, sharpe, mdd = pd.Series(dtype=float), 0.0, 0.0, 0.0

    return SwingReport(
        total_return=round(total, 4), sharpe=round(sharpe, 3),
        max_drawdown=round(mdd, 4), expectancy=round(expectancy, 5),
        profit_loss_ratio=round(pl_ratio, 3), win_rate=round(win_rate, 3),
        n_trades=n, trades=trades, equity_curve=eq)
