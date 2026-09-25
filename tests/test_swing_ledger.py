"""摆动回测记账回归：禁止把重叠持有的逐笔收益当周收益连乘（旧口径约 4 倍杠杆）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.engine.indicators import add_indicators
from aselect.engine.strategy_rules import ExitParams
from aselect.engine.swing_backtest import (SlotBook, Trade, _swing_metrics,
                                           ledger_equity, simulate_position)

COST = {"commission": 0.00025, "stamp_tax": 0.001,
        "transfer_fee": 0.00001, "slippage": 0.001}


def _bull(n=260):
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(0)
    close = 10 * np.cumprod(1 + 0.003 + rng.normal(0, 0.004, n))
    df = pd.DataFrame({"date": idx, "open": close / 1.001, "high": close * 1.005,
                       "low": close * 0.995, "close": close, "volume": 1e6})
    fr = add_indicators(df)
    fr.index = idx
    fr["symbol"] = "X"
    return fr


def test_single_stock_cannot_beat_buy_and_hold():
    fr = _bull()
    sched = list(pd.Series(fr.index, index=fr.index)
                 .groupby(fr.index.to_period("W")).last().values)[5:-5]
    book = SlotBook(1)
    for t in sched:
        if book.free(t) == 0 or "X" in book.held(t):
            continue
        tr = simulate_position(fr, fr.index.get_loc(pd.Timestamp(t)), COST,
                               ExitParams(max_hold=20))
        if tr:
            book.add(t, tr)
    eq = ledger_equity(book.accepted, {"X": fr}, fr.index, 1)
    rep = _swing_metrics(book.trades, COST, eq)
    bh = fr["close"].iloc[-1] / fr["close"].iloc[0] - 1
    assert rep.total_return <= bh + 1e-9          # 单仓位不可能跑赢买入持有
    # 任一时刻至多 1 个在途仓位（无重叠）
    spans = sorted((tr.entry_date, tr.exit_date) for tr in book.trades)
    for (_, e0), (s1, _) in zip(spans, spans[1:]):
        assert s1 >= e0


def test_slotbook_blocks_duplicates_and_caps_slots():
    d = pd.bdate_range("2024-01-01", periods=30)
    tr = Trade("A", d[1], d[20], 10.0, 11.0, 0.1, "x")
    book = SlotBook(2)
    book.add(d[0], tr)
    assert book.held(d[5]) == {"A"} and book.free(d[5]) == 1
    assert book.held(d[20]) == set() and book.free(d[20]) == 2   # 离场日开盘已卖出


def test_ledger_realizes_trade_return():
    d = pd.bdate_range("2024-01-01", periods=10)
    fr = pd.DataFrame({"close": [10.0] * 10}, index=d)
    tr = Trade("A", d[1], d[5], 10.0, 11.0, 0.10, "x")
    eq = ledger_equity([(d[0], tr, 1.0)], {"A": fr}, d, top_n=2, cash0=1.0)
    # 半仓参与一笔 +10% → 组合 +5%
    assert abs(eq.iloc[-1] - 1.05) < 1e-12
