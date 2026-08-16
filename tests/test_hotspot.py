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


# ── Task 5: 注册热点因子并接入截面表 ───────────────────────
def test_hotspot_registered_and_scored_end_to_end(tmp_path):
    from aselect.engine.factors import DEFAULT_FACTORS, score_factors
    store, cfg = _seed_store(tmp_path)
    cross = build_cross_section(store, cfg)
    assert "hotspot" in cross.columns
    assert "hotspot" in DEFAULT_FACTORS
    scored = score_factors(cross)
    assert "score_hotspot" in scored.columns


# ── Task 6: 热点因子单独 walk-forward IC 验证 ───────────────
def test_hotspot_included_in_factor_ic_orchestration(tmp_path):
    """热点因子已随 DEFAULT_FACTORS 纳入 factor-ic 编排，管线无错跑通。

    注：合成源的 fundamentals 仅一份「今日快照」(ann_date=today)，PIT 历史模式下
    行业/财务列缺失，故热点(依赖 industry)在合成回测里 n 可能为 0——这是合成测试
    夹具的局限，非热点因子逻辑缺陷；真实数据/历史财报快照下可产出历史 IC。
    """
    import math

    from aselect.runner import run_factor_research
    store, cfg = _seed_store(tmp_path)
    reports = run_factor_research(store, cfg, freq="M")
    assert "hotspot" in reports
    assert reports["hotspot"].name == "hotspot"
    assert math.isfinite(reports["hotspot"].ic_mean)


def test_historical_fundamentals_unlock_ic(tmp_path):
    """合成源历史季报快照就位后：基本面因子(roe)与依赖行业的热点因子获得历史 IC。

    此前合成源仅今日单快照，PIT 历史模式下基本面/行业缺失 → 这些因子 n==0。
    多期季报后 run_factor_research 能算出非空 IC，industry_neutral 标志随之可观测。
    """
    from aselect.runner import run_factor_research
    store, cfg = _seed_store(tmp_path)
    reports = run_factor_research(store, cfg, freq="M")
    assert reports["roe"].n > 0                 # 基本面因子历史 IC 解锁
    assert reports["hotspot"].n > 0             # 依赖 industry 的热点因子历史 IC 解锁


def test_hotspot_chain_produces_valid_ic_when_data_present():
    """端到端链路(add_hotspot_factor → process_factor(跳过行业中性) → summarize)在
    数据齐备时应产出有效、非空、方向正确的 IC 序列——不经合成源 PIT 财报瓶颈的真实验证。
    """
    import numpy as np

    from aselect.data.hotspot import add_hotspot_factor
    from aselect.engine.factor_research import summarize
    from aselect.engine.factors import process_factor

    syms = ["a", "b", "c", "d", "e", "f"]                # 医药(热) vs 银行(冷)
    sectors = ["医药", "医药", "医药", "银行", "银行", "银行"]
    idx = pd.bdate_range("2024-01-01", periods=30)
    # 医药板块每期跑赢银行 → 热点分高的组前向收益更高 → 正 IC
    grow = {"医药": 0.015, "银行": 0.002}
    panel = pd.DataFrame(
        {s: 100 * np.cumprod(1 + np.full(30, grow[sec]))
         for s, sec in zip(syms, sectors)}, index=idx)
    sched = list(idx[::5])

    sbd = {}
    for i, t in enumerate(sched):
        cross = pd.DataFrame({
            "symbol": syms, "industry": sectors,
            "mom_60": [0.2, 0.21, 0.19, -0.01, 0.0, -0.02],
            "net_inflow": [5e7, 6e7, 4e7, -1e7, 0.0, -2e7],
            "pct_chg": [0.05, 0.03, 0.02, 0.0, 0.01, -0.01],
        })
        hot = add_hotspot_factor(cross)
        proc = process_factor(hot["hotspot"], ascending=False, size=None)  # 跳过行业中性
        sbd[t] = pd.Series(proc.values, index=cross["symbol"].values)

    rep = summarize("hotspot", sbd, panel, sched, n_groups=2)
    assert rep.n > 0
    assert rep.ic_mean > 0            # 热板块前向收益更高 → 正 IC，方向正确
