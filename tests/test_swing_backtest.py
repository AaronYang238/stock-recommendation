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
    ds = SyntheticSource()
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
