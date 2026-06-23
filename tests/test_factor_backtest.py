"""股票池级 walk-forward 回测：纯引擎正确性 + 编排端到端（A1）。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data.clean import clean_daily
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.engine.factor_backtest import simulate
from aselect.runner import _tradable, run_strategy_backtest
from aselect.storage.sqlite_store import SQLiteStorage

COST0 = {"commission": 0, "stamp_tax": 0, "transfer_fee": 0, "slippage": 0}


def _cfg(backtest=None) -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={},
                  backtest=backtest or COST0, ai=AIConfig())


# ── 纯引擎 ──
def test_simulate_compounds_returns():
    idx = pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29"])
    panel = pd.DataFrame({"A": [100.0, 110, 121]}, index=idx)
    sel = {d: {"A": 1.0} for d in idx}
    bench = pd.Series([100.0, 110, 121], index=idx)
    rep = simulate(panel, list(idx), sel, {}, bench, COST0)
    assert abs(rep.total_return - 0.21) < 1e-9        # 1.1*1.1-1
    assert abs(rep.benchmark_return - 0.21) < 1e-9
    assert abs(rep.excess_return) < 1e-9
    assert rep.n_rebalances == 2


def test_costs_reduce_return():
    idx = pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29"])
    panel = pd.DataFrame({"A": [100.0, 110, 121]}, index=idx)
    sel = {d: {"A": 1.0} for d in idx}
    bench = pd.Series([100.0, 110, 121], index=idx)
    free = simulate(panel, list(idx), sel, {}, bench, COST0)
    costly = simulate(panel, list(idx), sel, {}, bench,
                      {"commission": 0.001, "stamp_tax": 0.001,
                       "transfer_fee": 0.0001, "slippage": 0.002})
    assert costly.total_return < free.total_return


def test_tradable_excludes_limit_locked():
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    panel = pd.DataFrame({"A": [100.0, 100.5], "B": [100.0, 120.0]}, index=idx)
    tr = _tradable(panel, idx[1], limit_pct=0.095)
    assert "A" in tr and "B" not in tr          # B 当日 +20% 涨停锁死


# ── 编排端到端（合成数据）──
def _seed(tmp_path) -> SQLiteStorage:
    s = SQLiteStorage(str(tmp_path / "bt.sqlite"))
    ds = SyntheticSource()
    s.upsert_symbols(ds.list_symbols())
    s.upsert_fundamentals(ds.fundamentals())
    for sym in ds.list_symbols()["symbol"]:
        s.upsert_daily(sym, clean_daily(ds.daily(sym, "hfq")), "hfq")
    return s


def test_run_strategy_backtest_walk_forward(tmp_path):
    store = _seed(tmp_path)
    rep = run_strategy_backtest(store, _cfg(), freq="M", top_n=5)
    assert rep.n_rebalances >= 10                       # 750 交易日 ≈ 36 个月
    assert len(rep.equity_curve) == rep.n_rebalances + 1
    assert 0 < rep.avg_positions <= 5
    assert pd.notna(rep.benchmark_return)
    # 随机行情无真实 alpha：IC 应在 0 附近（不得出现虚高）
    assert abs(rep.ic_mean) < 0.35


def test_higher_cost_lowers_return_end_to_end(tmp_path):
    store = _seed(tmp_path)
    free = run_strategy_backtest(store, _cfg(COST0), freq="M", top_n=5)
    costly = run_strategy_backtest(
        store, _cfg({"commission": 0.0005, "stamp_tax": 0.001,
                     "transfer_fee": 0.00002, "slippage": 0.002}),
        freq="M", top_n=5)
    assert costly.total_return <= free.total_return
