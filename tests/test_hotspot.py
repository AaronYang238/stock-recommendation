"""热点因子 M1：数据源增强字段 + 板块聚合热点因子（确定性核心，AI 禁区）。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data.clean import clean_daily
from aselect.data.pipeline import (
    build_cross_section, update_daily, update_symbols,
)
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={},
                  backtest={}, ai=AIConfig())


def _seed_store(tmp_path) -> tuple[SQLiteStorage, Config]:
    store = SQLiteStorage(str(tmp_path / "cs.sqlite"))
    ds = SyntheticSource()
    update_symbols(ds, store)
    syms = ds._all_symbols()
    update_daily(ds, store, syms, "hfq")
    store.upsert_fundamentals(ds.fundamentals(syms))
    return store, _cfg()


# ── Task 2: 数据源增强字段 ──────────────────────────────────
def test_synthetic_daily_has_turnover_and_net_inflow():
    df = SyntheticSource().daily("600519", "hfq")
    assert {"turnover", "net_inflow"}.issubset(df.columns)
    assert df["turnover"].notna().all()


def test_synthetic_daily_is_deterministic_for_new_cols():
    a = SyntheticSource().daily("000001", "hfq")[["turnover", "net_inflow"]]
    b = SyntheticSource().daily("000001", "hfq")[["turnover", "net_inflow"]]
    pd.testing.assert_frame_equal(a, b)


def test_storage_roundtrips_new_daily_cols(tmp_path):
    store = SQLiteStorage(str(tmp_path / "t.sqlite"))
    raw = SyntheticSource().daily("600519", "hfq")
    store.upsert_daily("600519", clean_daily(raw), "hfq")
    back = store.get_daily("600519", "hfq")
    assert {"turnover", "net_inflow"}.issubset(back.columns)
    assert back["net_inflow"].notna().any()


# ── Task 3: 截面表带入 net_inflow/turnover/pct_chg ──────────
def test_cross_section_has_flow_and_pctchg(tmp_path):
    store, cfg = _seed_store(tmp_path)
    cross = build_cross_section(store, cfg)
    for col in ("net_inflow", "turnover", "pct_chg"):
        assert col in cross.columns
    assert cross["net_inflow"].notna().any()
