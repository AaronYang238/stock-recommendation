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

    for d in panel.index:
        # 1) 处理今日到期的挂起买入
        for pend in [p for p in pending if p.fill_date <= d]:
            slot = min(pend.slot_value, cash)
            if slot > 1e-9:
                cash -= slot
                cash -= slot * buy_cost
                st = PositionState(entry_price=pend.entry_price,
                                   atr_at_entry=pend.atr0,
                                   highest_close=pend.entry_price)
                book.append(OpenPos(pend.symbol, d, pend.entry_price, slot, st,
                                    last_close=pend.entry_price))
            pending.remove(pend)

        # 2) 离场状态机 + 逐日盯市
        for pos in list(book):
            fr = frames.get(pos.symbol)
            if fr is None or d not in fr.index:
                continue                       # 停牌/退市：按最后价持有
            i = fr.index.get_indexer([d])[0]
            close = float(fr["close"].iloc[i])
            pos.last_close = close
            atr = float(fr["atr14"].iloc[i]) if np.isfinite(fr["atr14"].iloc[i]) \
                else pos.state.atr_at_entry
            bar = {"close": close, "ma10": fr["ma10"].iloc[i], "atr": atr}
            dec = evaluate_exit(pos.state, bar, exit_params)
            if dec.action == "scale_out":
                si = _fillable_open(fr, i + 1, "sell", limit_pct)
                if si is not None:
                    px = float(fr["open"].iloc[si])
                    proceeds = pos.slot_value * dec.fraction * (px / pos.entry_price)
                    cash += proceeds * (1 - sell_cost)
            elif dec.action == "exit":
                si = _fillable_open(fr, i + 1, "sell", limit_pct)
                si = si if si is not None else len(fr) - 1
                px = float(fr["open"].iloc[si])
                proceeds = pos.slot_value * pos.state.remaining * (px / pos.entry_price)
                cash += proceeds * (1 - sell_cost)
                trades.append({
                    "symbol": pos.symbol,
                    "entry_date": pos.entry_date,
                    "exit_date": fr.index[si],
                    "ret": proceeds / (pos.slot_value * (1 + buy_cost)) - 1 - sell_cost,
                })
                book.remove(pos)

        # 3) 当日净值
        bookval = sum(pos.slot_value * pos.state.remaining * (pos.last_close / pos.entry_price)
                      for pos in book)
        nav[d] = cash + bookval
        pos_acc += len(book)
        ndays += 1

        # 4) 调仓日：回填空仓
        if d in schedule_set:
            free = top_n - len(book)
            if free > 0:
                for sym in (picks_by_t.get(d) or [])[:free]:
                    fr = frames.get(sym)
                    if fr is None or d not in fr.index:
                        continue
                    i0 = fr.index.get_indexer([d])[0]
                    bi = _fillable_open(fr, i0 + 1, "buy", limit_pct)
                    if bi is None or bi >= len(fr):
                        continue
                    slot_value = nav[d] / top_n
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
