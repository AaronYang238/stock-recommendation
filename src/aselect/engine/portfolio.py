"""真实组合会计：滚动账本·仓位上限 top_n·逐日盯市（Model A）。

修复旧「每周篮子收益连乘」会计的不自洽处（隐含每周清仓/全仓再部署，不表达
跨周持仓、现金空窗与逐日盯市）。本模块为纯确定性组合级模拟器（AI 禁区）。

约定：
- 最多并发 top_n 个仓位（slot），每仓按「开仓日组合净值/top_n」分配资金；
- 现金单独记账，仓位离场后资金回到现金，待下个调仓日回填（现金拖累真实计入）；
- 逐日盯市：净值 = 现金 + Σ(各仓 slot_value × remaining × 现价/开仓价)；
- 信号日(调仓日)选股 → 次一可成交日开盘买入；离场按 evaluate_exit 状态机，
  卖出顺延至可成交日开盘（避开跌停锁死）；
- 摩擦成本与旧模拟一致（佣金/印花税/过户/滑点）。

选股（picks_by_t）由 runner 层预计算（需要截面/闸门/基本面），本模块只做账务。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy_rules import PositionState, evaluate_exit
from .swing_backtest import _fillable_open


@dataclass
class OpenPos:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    slot_value: float          # 开仓投入的现金（毛额，不含买入成本）
    state: PositionState
    last_close: float = 0.0
    pending_frac: float = 0.0  # 已决定卖出、尚未成交的仓位比例（占整仓）
    exiting: bool = False      # 已触发全部离场，等待可成交日
    reason: str = ""
    realized: float = 0.0      # 已回笼现金（扣卖出成本）


@dataclass
class PendingEntry:
    fill_date: pd.Timestamp
    symbol: str
    entry_price: float
    slot_value: float
    atr0: float


@dataclass
class PortfolioReport:
    total_return: float
    sharpe: float
    max_drawdown: float
    n_trades: int
    avg_positions: float
    equity: pd.Series = field(default_factory=pd.Series)
    trades: list = field(default_factory=list)


def simulate_portfolio(frames: dict, panel: pd.DataFrame, schedule: list,
                       picks_by_t: dict, top_n: int, cost: dict,
                       exit_params=None, limit_pct: float = 0.095,
                       cash0: float = 1_000_000.0) -> PortfolioReport:
    """逐日推进滚动账本，返回每日净值曲线 + 成交记录。确定性纯函数。"""
    from .strategy_rules import ExitParams
    exit_params = exit_params or ExitParams()

    comm = float(cost.get("commission", 0.00025))
    stamp = float(cost.get("stamp_tax", 0.001))
    transfer = float(cost.get("transfer_fee", 0.00001))
    slip = float(cost.get("slippage", 0.001))
    buy_cost = comm + transfer + slip
    sell_cost = comm + transfer + slip + stamp

    schedule_set = set(schedule)
    cash = float(cash0)
    book: list[OpenPos] = []
    pending: list[PendingEntry] = []
    trades = []
    nav: dict = {}
    pos_acc = 0.0
    ndays = 0

    def _close_trade(pos: OpenPos, d, reason: str) -> None:
        trades.append({
            "symbol": pos.symbol, "entry_date": pos.entry_date, "exit_date": d,
            "ret": pos.realized / (pos.slot_value * (1 + buy_cost)) - 1,
            "reason": reason,
        })
        book.remove(pos)

    for d in panel.index:
        # 0) 挂起卖单：信号日之后的首个可成交开盘成交，**成交当日**才回笼现金
        #    （旧实现在信号日就按次日/数据末日价格记账 → 净值偷看未来）
        for pos in list(book):
            fr = frames.get(pos.symbol)
            if fr is None:
                continue
            if d > fr.index[-1]:
                h = float(fr.attrs.get("delist_haircut", 0.0) or 0.0)
                if h > 0:                      # 段内退市：按最后收盘 × (1-折价) 清算
                    frac = pos.state.remaining + pos.pending_frac
                    px = pos.last_close * (1 - h)
                    pos.realized += pos.slot_value * frac * (px / pos.entry_price) * (1 - sell_cost)
                    cash += pos.slot_value * frac * (px / pos.entry_price) * (1 - sell_cost)
                    pos.state.remaining, pos.pending_frac = 0.0, 0.0
                    _close_trade(pos, d, "delisted")
                continue                       # 未退市（段末停牌）→ 按最后价持有
            if not (pos.pending_frac > 0 or pos.exiting) or d not in fr.index:
                continue                       # 无挂单 / 停牌顺延
            i = fr.index.get_indexer([d])[0]
            if _fillable_open(fr, i, "sell", limit_pct) != i:
                continue                       # 跌停锁死 → 顺延
            px = float(fr["open"].iloc[i])
            frac = pos.pending_frac + (pos.state.remaining if pos.exiting else 0.0)
            proceeds = pos.slot_value * frac * (px / pos.entry_price) * (1 - sell_cost)
            cash += proceeds
            pos.realized += proceeds
            pos.pending_frac = 0.0
            if pos.exiting:
                pos.state.remaining = 0.0
                _close_trade(pos, d, pos.reason)

        # 1) 处理今日到期的挂起买入
        for pend in [p for p in pending if p.fill_date <= d]:
            slot = pend.slot_value                      # 资金已于信号日冻结
            if slot > 1e-9:
                cash -= slot * buy_cost
                st = PositionState(entry_price=pend.entry_price,
                                   atr_at_entry=pend.atr0,
                                   highest_close=pend.entry_price)
                book.append(OpenPos(pend.symbol, d, pend.entry_price, slot, st,
                                    last_close=pend.entry_price))
            pending.remove(pend)

        # 2) 离场状态机（收盘判定，次日起挂单卖出）+ 逐日盯市
        for pos in book:
            fr = frames.get(pos.symbol)
            if fr is None or d not in fr.index:
                continue                       # 停牌：按最后价持有
            i = fr.index.get_indexer([d])[0]
            close = float(fr["close"].iloc[i])
            pos.last_close = close
            if pos.exiting:
                continue
            atr = float(fr["atr14"].iloc[i]) if np.isfinite(fr["atr14"].iloc[i]) \
                else pos.state.atr_at_entry
            bar = {"close": close, "ma10": fr["ma10"].iloc[i], "atr": atr}
            dec = evaluate_exit(pos.state, bar, exit_params)
            if dec.action == "scale_out":
                pos.pending_frac += dec.fraction      # remaining 已由状态机扣减
            elif dec.action == "exit":
                pos.exiting, pos.reason = True, dec.reason

        # 3) 当日净值（挂单中的份额仍按现价计入）
        bookval = sum(pos.slot_value * (pos.state.remaining + pos.pending_frac)
                      * (pos.last_close / pos.entry_price) for pos in book)
        bookval += sum(p.slot_value for p in pending)   # 已冻结的待成交买入资金
        nav[d] = cash + bookval
        pos_acc += len(book)
        ndays += 1

        # 4) 调仓日：回填空仓（在途挂单也占槽位，已持有不重复买）
        if d in schedule_set:
            held = {p.symbol for p in book} | {p.symbol for p in pending}
            free = top_n - len(book) - len(pending)
            if free > 0:
                for sym in [x for x in (picks_by_t.get(d) or []) if x not in held][:free]:
                    fr = frames.get(sym)
                    if fr is None or d not in fr.index:
                        continue
                    i0 = fr.index.get_indexer([d])[0]
                    bi = _fillable_open(fr, i0 + 1, "buy", limit_pct)
                    if bi is None or bi >= len(fr):
                        continue
                    slot_value = min(nav[d] / top_n, cash)
                    if slot_value <= 1e-9:
                        break
                    cash -= slot_value                  # 冻结，成交日再扣买入成本
                    o = float(fr["open"].iloc[bi])
                    atr0 = float(fr["atr14"].iloc[bi])
                    atr0 = atr0 if np.isfinite(atr0) and atr0 > 0 else max(o * 0.02, 1e-6)
                    pending.append(PendingEntry(fr.index[bi], sym, o, slot_value, atr0))

    eq = pd.Series(nav).sort_index()
    rets = eq.pct_change().dropna()
    if len(eq) < 2 or rets.std() == 0 or eq.iloc[0] <= 0:
        return PortfolioReport(0.0, 0.0, 0.0, len(trades), 0.0, eq, trades)
    total = float(eq.iloc[-1] / eq.iloc[0] - 1)
    sharpe = float(np.sqrt(252) * rets.mean() / rets.std())
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min())
    avg_pos = pos_acc / ndays if ndays else 0.0
    return PortfolioReport(total, round(sharpe, 3), round(mdd, 4), len(trades),
                           round(avg_pos, 2), eq, trades)
