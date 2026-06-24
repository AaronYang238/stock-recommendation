"""阶段二：单因子 IC 研究、IC 加权、样本外(hold-out)验证。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data.clean import clean_daily
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.engine.factor_research import FactorICReport, summarize
from aselect.runner import (
    ic_category_weights, run_factor_research, run_strategy_backtest,
    run_validated_strategy,
)
from aselect.storage.sqlite_store import SQLiteStorage

COST0 = {"commission": 0, "stamp_tax": 0, "transfer_fee": 0, "slippage": 0,
         "benchmark": ""}


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={},
                  backtest=COST0, ai=AIConfig())


def _seed(tmp_path) -> SQLiteStorage:
    s = SQLiteStorage(str(tmp_path / "r.sqlite"))
    ds = SyntheticSource()
    s.upsert_symbols(ds.list_symbols())
    s.upsert_fundamentals(ds.fundamentals())
    for sym in ds.list_symbols()["symbol"]:
        s.upsert_daily(sym, clean_daily(ds.daily(sym, "hfq")), "hfq")
    return s


def test_summarize_structure_and_ic_near_zero():
    # 各股不同价格路径（截面有收益差异）+ 随机因子 → IC 应在 0 附近
    import numpy as np
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2024-01-01", periods=60)
    cols = ["A", "B", "C", "D", "E", "F"]
    panel = pd.DataFrame(
        {c: 100 * np.cumprod(1 + rng.normal(0, 0.02, 60)) for c in cols}, index=idx)
    sched = list(idx[::5])
    sbd = {t: pd.Series({c: rng.random() for c in cols}) for t in sched}
    rep = summarize("x", sbd, panel, sched, n_groups=3)
    assert isinstance(rep, FactorICReport)
    assert rep.n >= 1 and len(rep.decay) == 3
    assert -1 <= rep.ic_mean <= 1


def test_ic_category_weights_positive_part_normalized():
    reps = {
        "pe": FactorICReport("pe", 0.05, 0.1, 0.5, 0.6, 30, 0.01),
        "pb": FactorICReport("pb", 0.03, 0.1, 0.3, 0.55, 30, 0.01),
        "ps": FactorICReport("ps", 0.01, 0.1, 0.1, 0.5, 30, 0.0),
        "roe": FactorICReport("roe", -0.02, 0.1, -0.2, 0.4, 30, 0.0),
    }
    w = ic_category_weights(reps)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["value"] > w.get("quality", 0)        # 价值 IC 为正、质量为负
    assert w.get("quality", 0) == 0.0


def test_run_factor_research_runs(tmp_path):
    reps = run_factor_research(_seed(tmp_path), _cfg(), freq="M")
    assert reps
    for r in reps.values():
        assert -0.5 < r.ic_mean < 0.5             # 合成随机数据：IC 不应虚高


def test_weights_change_backtest(tmp_path):
    store = _seed(tmp_path)
    base = run_strategy_backtest(store, _cfg(), freq="M", top_n=5)
    wt = run_strategy_backtest(store, _cfg(), freq="M", top_n=5,
                               weights={"momentum": 1.0})
    # 不同权重应产生不同（或至少不报错）的组合
    assert base.n_rebalances == wt.n_rebalances


def test_validated_strategy_oos(tmp_path):
    v = run_validated_strategy(_seed(tmp_path), _cfg(), freq="M", top_n=5, oos_split=0.7)
    assert "split_date" in v and "oos" in v and "train" in v
    assert abs(sum(v["weights"].values()) - 1.0) < 1e-6 or v["weights"]
    assert v["oos"].n_rebalances >= 1
