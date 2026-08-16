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


# ── Task 4: 热点板块聚合器 ──────────────────────────────────
def test_hotspot_ranks_hot_sector_above_cold():
    cross = pd.DataFrame({
        "symbol": list("abcdef"),
        "industry": ["医药", "医药", "医药", "银行", "银行", "银行"],
        "mom_60": [0.20, 0.22, 0.18, -0.02, 0.00, -0.01],       # 医药强
        "net_inflow": [5e7, 6e7, 4e7, -1e7, 0.0, -2e7],          # 医药资金流入
        "pct_chg": [0.10, 0.05, 0.02, 0.00, 0.01, -0.01],        # 医药有涨停
    })
    from aselect.data.hotspot import add_hotspot_factor
    out = add_hotspot_factor(cross)
    assert "hotspot" in out.columns
    hot = out.set_index("symbol").loc[["a", "b", "c"], "hotspot"].mean()
    cold = out.set_index("symbol").loc[["d", "e", "f"], "hotspot"].mean()
    assert hot > cold


def test_hotspot_is_deterministic():
    from aselect.data.hotspot import add_hotspot_factor
    cross = pd.DataFrame({
        "symbol": ["a", "b"], "industry": ["医药", "银行"],
        "mom_60": [0.2, -0.1], "net_inflow": [1e7, -1e7], "pct_chg": [0.05, -0.02],
    })
    pd.testing.assert_frame_equal(add_hotspot_factor(cross), add_hotspot_factor(cross))


def test_hotspot_missing_inputs_returns_nan_column():
    from aselect.data.hotspot import add_hotspot_factor
    cross = pd.DataFrame({"symbol": ["a", "b"], "industry": ["医药", "银行"]})
    out = add_hotspot_factor(cross)
    assert "hotspot" in out.columns
    assert out["hotspot"].isna().all()
