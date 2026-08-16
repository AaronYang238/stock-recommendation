"""消融对照：入场闸门 on/off；离场吊灯 vs 涨停即清 / 固定止盈（把两大风险量化成钱）。"""
from __future__ import annotations

import numpy as np

from aselect.config import AIConfig, Config
from aselect.data.pipeline import update_daily, update_symbols
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(
        app={}, datasource={"adjust": "hfq"}, storage={},
        backtest={"commission": 0.00025, "stamp_tax": 0.001,
                  "transfer_fee": 0.00001, "slippage": 0.001, "benchmark": ""},
        ai=AIConfig())


def _seed(tmp_path):
    store = SQLiteStorage(str(tmp_path / "abl.sqlite"))
    ds = SyntheticSource(days=220)
    update_symbols(ds, store)
    syms = ds._all_symbols()
    update_daily(ds, store, syms, "hfq")
    store.upsert_fundamentals(ds.fundamentals(syms))
    return store, _cfg()


def test_gate_ablation_returns_both_arms_and_delta(tmp_path):
    from aselect.runner import run_gate_ablation
    store, cfg = _seed(tmp_path)
    res = run_gate_ablation(store, cfg, freq="W", top_n=5)
    assert set(res) >= {"gate_on", "gate_off", "expectancy_delta"}
    assert res["gate_on"].n_trades > 0 and res["gate_off"].n_trades > 0
    assert np.isfinite(res["expectancy_delta"])


def test_exit_ablation_three_arms(tmp_path):
    from aselect.runner import run_exit_ablation
    store, cfg = _seed(tmp_path)
    res = run_exit_ablation(store, cfg, freq="W", top_n=5)
    for k in ("trailing", "sell_on_limit", "fixed_pct"):
        assert k in res and res[k].n_trades > 0
    assert np.isfinite(res["pl_ratio_delta_vs_limit"])
    assert np.isfinite(res["pl_ratio_delta_vs_fixed"])
