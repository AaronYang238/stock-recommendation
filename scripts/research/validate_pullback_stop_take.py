"""验证「回踩MA20企稳买入 + 固定8%止损 + 20%止盈」样本外表现。

用 _run_swing 编排（注入 gate_pullback_ma20 入场 + simulate_position_fixed_stop_take 离场），
分训练/样本外两段各跑一次，报告期望/盈亏比/夏普/回撤。与移动止损基线对照。
确定性核心，无 LLM，走铁律2(PIT)/3(样本外只测一次)/4(IC-无IC故用期望/盈亏比)。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import pandas as pd

from aselect.config import load_config
from aselect.storage.sqlite_store import SQLiteStorage
from aselect.runner import _run_swing, _rebalance_dates, run_factor_research, \
    ic_category_weights, _price_panel, build_universe
from aselect.engine.strategy_rules import ExitParams, gate_pullback_ma20
from aselect.engine.swing_backtest import simulate_position_fixed_stop_take, \
    simulate_position

START = "2021-08-19"
END = "2026-08-18"
FREQ = "W"
TOP_N = 10
OOS = 0.7
STOP_PCT = 0.08
TAKE_PCT = 0.20


def main():
    cfg = load_config()
    store = SQLiteStorage(cfg.storage["path"])
    universe = build_universe(store, include_delisted=True)
    adjust = cfg.datasource.get("adjust", "hfq")
    panel = _price_panel(store, universe, adjust, START, END)
    schedule = list(_rebalance_dates(panel.index, FREQ))
    schedule = [pd.Timestamp(t) for t in schedule]
    if len(schedule) < 4:
        print("样本太短"); return
    k = max(1, int(len(schedule) * OOS))
    split = pd.Timestamp(schedule[k])
    print(f"调仓日 {len(schedule)} | 切分日 {split.date()}")

    # 训练段定权重
    tr_reports = run_factor_research(store, cfg, freq=FREQ, start=START,
                                     end=split.strftime("%Y-%m-%d"))
    weights = ic_category_weights(tr_reports)
    print("IC权重:", {k: round(v,2) for k,v in weights.items()})

    ep = ExitParams(max_hold=20)

    # 策略A：回踩MA20企稳 + 固定8%止损/20%止盈
    from functools import partial
    from aselect.engine.swing_backtest import simulate_position_fixed_stop_take
    stratA = partial(simulate_position_fixed_stop_take, stop_pct=STOP_PCT, take_pct=TAKE_PCT)
    print("\n===== 策略A：回踩MA20企稳买入 + 8%止损 + 20%止盈 =====")
    for tag, s, e in [("训练段", START, split.strftime("%Y-%m-%d")),
                      ("样本外", split.strftime("%Y-%m-%d"), END)]:
        rep = _run_swing(store, cfg, s, e, FREQ, TOP_N, 2, weights,
                         True, ep, 0.095, gate_pullback_ma20, stratA)
        print(f"  [{tag}] 交易{rep.n_trades}笔 | 总收益{rep.total_return:+.2%} "
              f"| 夏普{rep.sharpe} | 期望{rep.expectancy:.4f}/笔 "
              f"| 盈亏比{rep.profit_loss_ratio} | 回撤{rep.max_drawdown:.2%}")

    # 对照：移动止损（吊灯）
    print("\n===== 对照：移动止损（吊灯） =====")
    for tag, s, e in [("样本外", split.strftime("%Y-%m-%d"), END)]:
        rep = _run_swing(store, cfg, s, e, FREQ, TOP_N, 2, weights,
                         True, ep, 0.095, gate_pullback_ma20, simulate_position)
        print(f"  [{tag}] 交易{rep.n_trades}笔 | 总收益{rep.total_return:+.2%} "
              f"| 夏普{rep.sharpe} | 期望{rep.expectancy:.4f}/笔 "
              f"| 盈亏比{rep.profit_loss_ratio} | 回撤{rep.max_drawdown:.2%}")

    store.close()

if __name__ == "__main__":
    main()
