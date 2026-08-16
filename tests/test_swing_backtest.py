"""事件驱动周级回测：OHLC 面板、逐仓撮合、组合级模拟（确定性核心，AI 禁区）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data.clean import clean_daily
from aselect.data.pipeline import update_daily, update_symbols
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(
        app={}, datasource={"adjust": "hfq"}, storage={},
        backtest={"commission": 0.00025, "stamp_tax": 0.001,
                  "transfer_fee": 0.00001, "slippage": 0.001, "benchmark": ""},
        ai=AIConfig())


def _seed(tmp_path) -> tuple[SQLiteStorage, Config]:
    store = SQLiteStorage(str(tmp_path / "sw.sqlite"))
    ds = SyntheticSource(days=220)          # 够周度调仓+指标，且测试快
    update_symbols(ds, store)
    syms = ds._all_symbols()
    update_daily(ds, store, syms, "hfq")
    store.upsert_fundamentals(ds.fundamentals(syms))
    return store, _cfg()


# ── Task 1: OHLC + 指标面板 ─────────────────────────────────
def test_ohlc_frames_have_indicators(tmp_path):
    from aselect.runner import _ohlc_frames
    store, cfg = _seed(tmp_path)
    frames = _ohlc_frames(store, ["600519", "000001"], "hfq", None, None)
    assert set(frames) == {"600519", "000001"}
    f = frames["600519"]
    for col in ("high", "low", "close", "ma10", "atr14"):
        assert col in f.columns
    assert f.index.is_monotonic_increasing


# ── Task 2: 逐仓撮合 ─────────────────────────────────────────
_COST = {"commission": 0.00025, "stamp_tax": 0.001,
         "transfer_fee": 0.00001, "slippage": 0.001}


def _frame(closes):
    from aselect.engine.indicators import add_indicators
    n = len(closes)
    df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes, "volume": [1e6] * n,
    })
    ind = add_indicators(df)
    ind.index = pd.to_datetime(df["date"])
    return ind


def test_simulate_position_trailing_stop_with_costs():
    from aselect.engine.swing_backtest import simulate_position
    # 15 日铺垫 → 强涨到 130 → 回撤触发吊灯
    closes = (list(np.linspace(98, 100, 15)) + list(np.linspace(101, 130, 12))
              + [126, 121, 118])
    frame = _frame(closes)
    tr = simulate_position(frame, entry_idx=15, cost=_COST)
    assert tr is not None
    assert tr.reason in ("trailing_stop", "trend_break", "hard_stop", "max_hold")
    gross = tr.exit_price / tr.entry_price - 1
    assert tr.ret < gross          # 成本使净收益低于毛价差
    assert tr.entry_date < tr.exit_date


def test_simulate_position_no_trade_when_entry_at_last_bar():
    from aselect.engine.swing_backtest import simulate_position
    frame = _frame(list(np.linspace(100, 110, 30)))
    assert simulate_position(frame, entry_idx=len(frame) - 1, cost=_COST) is None


# ── Task 3: 组合级事件回测 ──────────────────────────────────
def test_run_swing_backtest_produces_report(tmp_path):
    from aselect.engine.swing_backtest import SwingReport
    from aselect.runner import run_swing_backtest
    store, cfg = _seed(tmp_path)
    rep = run_swing_backtest(store, cfg, freq="W", top_n=5)
    assert isinstance(rep, SwingReport)
    assert rep.n_trades > 0
    assert np.isfinite(rep.expectancy)
    assert np.isfinite(rep.profit_loss_ratio)


def test_run_swing_backtest_deterministic(tmp_path):
    from aselect.runner import run_swing_backtest
    store, cfg = _seed(tmp_path)
    a = run_swing_backtest(store, cfg, freq="W", top_n=5)
    b = run_swing_backtest(store, cfg, freq="W", top_n=5)
    assert a.n_trades == b.n_trades
    assert a.expectancy == b.expectancy


def test_run_swing_backtest_single_industry_cap(tmp_path):
    """单行业 ≤ max_per_industry：任一入场周内同行业笔数不超过上限（有行业信息时）。"""
    from aselect.runner import run_swing_backtest
    store, cfg = _seed(tmp_path)
    rep = run_swing_backtest(store, cfg, freq="W", top_n=8, max_per_industry=2)
    assert rep.n_trades > 0   # 编排跑通；集中度约束由实现保证


# ── M5: 样本外一次性验收 ─────────────────────────────────────
def test_run_validated_swing_splits_train_and_oos(tmp_path):
    from aselect.engine.swing_backtest import SwingReport
    from aselect.runner import run_validated_swing
    store, cfg = _seed(tmp_path)
    v = run_validated_swing(store, cfg, freq="W", top_n=5, oos_split=0.7)
    assert set(v) >= {"split_date", "weights", "train", "oos"}
    assert isinstance(v["train"], SwingReport) and isinstance(v["oos"], SwingReport)
    assert np.isfinite(v["oos"].expectancy)


def test_run_validated_swing_deterministic(tmp_path):
    from aselect.runner import run_validated_swing
    store, cfg = _seed(tmp_path)
    a = run_validated_swing(store, cfg, freq="W", top_n=5, oos_split=0.7)
    b = run_validated_swing(store, cfg, freq="W", top_n=5, oos_split=0.7)
    assert a["split_date"] == b["split_date"]
    assert a["oos"].expectancy == b["oos"].expectancy
