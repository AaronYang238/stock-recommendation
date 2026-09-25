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
    """从 start_i 起找首个可成交日：买入避开涨停锁死，卖出避开跌停锁死。

    以开盘价相对昨收判定：开盘即在涨(跌)停价，则"开盘成交"不可能实现 → 顺延（保守；
    盘中开板后的成交因无分时价格不做假设）。frame 带 limit_pct 列时按其逐日阈值。"""
    has_col = "limit_pct" in frame.columns          # 分板块/ST 的逐日阈值优先
    for i in range(start_i, len(frame)):
        prev_close = float(frame["close"].iloc[i - 1]) if i > 0 else float(frame["open"].iloc[i])
        lp = (float(frame["limit_pct"].iloc[i]) if has_col
              else (limit_pct if limit_pct is not None else 0.095))
        lim = _is_limit(prev_close, float(frame["open"].iloc[i]), lp)
        if side == "buy" and lim == 1:
            continue
        if side == "sell" and lim == -1:
            continue
        return i
    return None


def _terminal_exit(frame: pd.DataFrame) -> tuple[int, float, str]:
    """数据尽头平仓：末日收盘；若标的在段内退市（attrs['delist_haircut']），
    计入退市折价（退市整理/转板后的损失），而不是"停在最后价"。"""
    n = len(frame)
    px = float(frame["close"].iloc[-1])
    h = float(frame.attrs.get("delist_haircut", 0.0) or 0.0)
    if h > 0:
        return n - 1, px * (1 - h), "delisted"
    return n - 1, px, "eod_close"


def _sell_after(frame: pd.DataFrame, i: int, limit_pct: float) -> tuple[int, float, str | None]:
    """信号日 i 收盘决定卖出 → 次一可成交日开盘成交；跌停锁死顺延；到数据尽头仍卖不出
    → _terminal_exit。返回 (成交行号, 成交价, 强制原因或 None)。"""
    si = _fillable_open(frame, i + 1, "sell", limit_pct)
    if si is None:
        ti, px, why = _terminal_exit(frame)
        return ti, px, why
    return si, float(frame["open"].iloc[si]), None


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
            si, exit_price, forced = _sell_after(frame, i, limit_pct)
            rem = 1.0 - scaled_frac
            gross = scaled_gross + rem * (exit_price / entry_price - 1)
            ret = gross - buy_cost - sell_cost
            return Trade(_sym(frame), frame.index[bi], frame.index[si],
                         entry_price, exit_price, round(ret, 6), forced or dec.reason,
                         scaled=scaled_frac > 0)

    # 未触发离场 → 数据尽头平仓（退市则计入退市折价）
    ti, exit_price, why = _terminal_exit(frame)
    rem = 1.0 - scaled_frac
    gross = scaled_gross + rem * (exit_price / entry_price - 1)
    ret = gross - buy_cost - sell_cost
    return Trade(_sym(frame), frame.index[bi], frame.index[ti],
                 entry_price, exit_price, round(ret, 6), why,
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
        lp = (float(frame["limit_pct"].iloc[i]) if "limit_pct" in frame.columns
              else (limit_pct if limit_pct is not None else 0.095))
        if prev > 0 and close / prev - 1 >= lp:                   # 涨停（分板块阈值）
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "sell_on_limit", buy_cost, sell_cost)
        if k >= exit_params.max_hold:
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "max_hold", buy_cost, sell_cost)
    ti, px, why = _terminal_exit(frame)
    return _mk(frame, bi, ti, entry_price, px, why, buy_cost, sell_cost)


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
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "fixed_take", buy_cost, sell_cost)
        if k >= exit_params.max_hold:
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "max_hold", buy_cost, sell_cost)
    ti, px, why = _terminal_exit(frame)
    return _mk(frame, bi, ti, entry_price, px, why, buy_cost, sell_cost)


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
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "fixed_stop", buy_cost, sell_cost)
        if close >= take_line:
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "fixed_take", buy_cost, sell_cost)
        if k >= exit_params.max_hold:
            si, px, forced = _sell_after(frame, i, limit_pct)
            return _mk(frame, bi, si, entry_price, px,
                       forced or "max_hold", buy_cost, sell_cost)
    ti, px, why = _terminal_exit(frame)
    return _mk(frame, bi, ti, entry_price, px, why, buy_cost, sell_cost)


class SlotBook:
    """仓位槽账本（选股阶段用）：最多 top_n 个并发仓位，已持有的标的不重复开仓。

    旧口径把每个调仓日的一篮子逐笔收益当作"周收益"连乘，而单笔持有最长 20 个
    交易日 → 各周篮子在时间上重叠，等价于约 4 倍杠杆、同一只股票被每周重复买入。
    本账本只在有空槽时接新仓，并记录每笔成交供 ledger_equity 逐日盯市。
    """

    def __init__(self, top_n: int):
        self.top_n = int(top_n)
        self.accepted: list = []          # [(signal_date, Trade, alloc)]

    def _live(self, t) -> list:
        t = pd.Timestamp(t)
        # exit_date == t：已于 t 日开盘卖出，t 收盘时槽位已空出
        return [tr for _, tr, _ in self.accepted if pd.Timestamp(tr.exit_date) > t]

    def held(self, t) -> set:
        return {tr.symbol for tr in self._live(t)}

    def free(self, t) -> int:
        return max(self.top_n - len(self._live(t)), 0)

    def add(self, t, trade: Trade, alloc: float = 1.0) -> None:
        self.accepted.append((pd.Timestamp(t), trade, float(alloc)))

    @property
    def trades(self) -> list:
        return [tr for _, tr, _ in self.accepted]


def ledger_equity(accepted: list, frames: dict, calendar, top_n: int,
                  cash0: float = 1.0) -> pd.Series:
    """由已接受的逐笔交易构建逐日净值（现金 + 挂单 + 逐日盯市持仓）。

    约定：信号日 t 收盘按 NAV_t × alloc / top_n 从现金划出一个槽；入场日起按
    收盘/入场价盯市（停牌沿用最后收盘）；离场日按该笔扣费后净收益 (1+ret) 回笼现金。
    现金不足时按剩余现金缩小槽位。确定性纯函数。
    """
    cal = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(calendar))))
    by_signal: dict = {}
    for t, tr, alloc in accepted:
        by_signal.setdefault(pd.Timestamp(t), []).append((tr, alloc))
    cash = float(cash0)
    live: list = []                       # [slot, trade, last_px]
    nav = {}
    for d in cal:
        # 1) 离场回笼
        keep = []
        for slot, tr, last_px in live:
            if pd.Timestamp(tr.exit_date) <= d:
                cash += slot * (1.0 + tr.ret)
            else:
                keep.append([slot, tr, last_px])
        live = keep
        # 2) 盯市
        val = 0.0
        for pos in live:
            slot, tr, last_px = pos
            if pd.Timestamp(tr.entry_date) > d:
                val += slot                               # 挂单：资金已冻结
                continue
            fr = frames.get(tr.symbol)
            if fr is not None and d in fr.index:
                pos[2] = last_px = float(fr.at[d, "close"])
            val += slot * (last_px / tr.entry_price)
        nav[d] = cash + val
        # 3) 调仓日开新槽
        for tr, alloc in by_signal.get(d, []):
            want = nav[d] * alloc / top_n
            slot = min(want, cash)
            if slot <= 1e-12:
                continue
            cash -= slot
            live.append([slot, tr, tr.entry_price])
    return pd.Series(nav, dtype=float)


def _swing_metrics(trades: list, cost: dict,
                   equity: pd.Series | None = None) -> SwingReport:
    """逐笔统计（期望/按笔盈亏比/胜率仅参考）+ 由逐日净值算总收益/夏普/回撤。"""
    n = len(trades)
    if n == 0:
        return SwingReport(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0,
                           trades=[], equity_curve=pd.Series(dtype=float))
    rets = pd.Series([t.ret for t in trades], dtype=float)
    wins, losses = rets[rets > 0], rets[rets < 0]
    expectancy = float(rets.mean())
    pl_ratio = float(wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() != 0 else 0.0
    win_rate = float(len(wins) / n)

    if equity is not None and len(equity) >= 2 and equity.iloc[0] > 0:
        eq = equity.sort_index()
        dr = eq.pct_change().dropna()
        total = float(eq.iloc[-1] / eq.iloc[0] - 1)
        sharpe = float(np.sqrt(252) * dr.mean() / dr.std()) if dr.std() > 0 else 0.0
        peak = eq.cummax()
        mdd = float(((eq - peak) / peak).min())
    else:
        eq, total, sharpe, mdd = pd.Series(dtype=float), 0.0, 0.0, 0.0

    return SwingReport(
        total_return=round(total, 4), sharpe=round(sharpe, 3),
        max_drawdown=round(mdd, 4), expectancy=round(expectancy, 5),
        profit_loss_ratio=round(pl_ratio, 3), win_rate=round(win_rate, 3),
        n_trades=n, trades=trades, equity_curve=eq)
