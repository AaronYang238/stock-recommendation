"""退市：摆动逐仓 / 组合账本在行情提前终止时计入退市折价。"""
from __future__ import annotations

import pandas as pd

from aselect.engine.indicators import add_indicators
from aselect.engine.portfolio import simulate_portfolio
from aselect.engine.strategy_rules import ExitParams
from aselect.engine.swing_backtest import simulate_position

COST0 = {"commission": 0, "stamp_tax": 0, "transfer_fee": 0, "slippage": 0}


def _frame(n, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=n)
    closes = [10.0] * n
    df = pd.DataFrame({"date": idx, "open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": 1e6})
    fr = add_indicators(df)
    fr.index = idx
    return fr


def test_simulate_position_applies_delist_haircut():
    fr = _frame(30)
    fr.attrs["delist_haircut"] = 0.5
    tr = simulate_position(fr, 25, COST0, ExitParams(max_hold=50))
    assert tr.reason == "delisted" and abs(tr.ret - (-0.5)) < 1e-9


def test_portfolio_liquidates_delisted_position():
    live = _frame(60)
    dead = _frame(30)
    dead.attrs["delist_haircut"] = 0.5
    panel = pd.DataFrame({"L": live["close"]})
    t = dead.index[25]
    rep = simulate_portfolio({"D": dead, "L": live}, panel, [t], {t: ["D"]}, 1, COST0,
                             ExitParams(max_hold=100), cash0=1.0)
    assert any(tr["reason"] == "delisted" for tr in rep.trades)
    assert abs(rep.equity.iloc[-1] - 0.5) < 1e-9
