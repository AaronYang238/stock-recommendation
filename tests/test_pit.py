"""铁律2：point-in-time（披露日）对齐，杜绝前视/数据泄漏。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data import build_cross_section
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={}, backtest={},
                  ai=AIConfig())


def _store(tmp_path) -> SQLiteStorage:
    s = SQLiteStorage(str(tmp_path / "pit.sqlite"))
    s.upsert_symbols(pd.DataFrame([
        {"symbol": "X", "name": "测试X", "exchange": "SH",
         "list_date": None, "delist_date": None, "status": "L"},
    ]))
    # 两期财报：FY2023(2024-03-30 披露) 与 2024Q1(2024-04-28 披露)
    s.upsert_fundamentals(pd.DataFrame([
        {"symbol": "X", "date": "2023-12-31", "ann_date": "2024-03-30",
         "pe": 10.0, "roe": 20.0, "total_mv": 1e10},
        {"symbol": "X", "date": "2024-03-31", "ann_date": "2024-04-28",
         "pe": 12.0, "roe": 25.0, "total_mv": 1.1e10},
    ]))
    return s


def test_get_fundamentals_as_of_filters_undisclosed(tmp_path):
    s = _store(tmp_path)
    # 2024-03-01：两期都还没披露
    assert s.get_fundamentals(["X"], as_of="2024-03-01").empty
    # 2024-04-01：只有 FY2023 已披露
    d = s.get_fundamentals(["X"], as_of="2024-04-01")
    assert d["ann_date"].tolist() == ["2024-03-30"]
    # 2024-05-01：两期都已披露
    assert len(s.get_fundamentals(["X"], as_of="2024-05-01")) == 2
    # 无 as_of：全取
    assert len(s.get_fundamentals(["X"])) == 2


def test_cross_section_uses_latest_disclosed(tmp_path):
    s = _store(tmp_path)
    cfg = _cfg()
    # as_of 在 Q1 披露前 → 应取 FY2023 的 pe=10
    c1 = build_cross_section(s, cfg, symbols=["X"], as_of="2024-04-10")
    assert float(c1.loc[c1.symbol == "X", "pe"].iloc[0]) == 10.0
    # as_of 在 Q1 披露后 → 取更新的 pe=12
    c2 = build_cross_section(s, cfg, symbols=["X"], as_of="2024-05-10")
    assert float(c2.loc[c2.symbol == "X", "pe"].iloc[0]) == 12.0


def test_no_lookahead_future_report_excluded(tmp_path):
    s = _store(tmp_path)
    cfg = _cfg()
    # 站在 2024-04-10 看，2024Q1(4-28才披露)绝不能出现 → pe 必须是旧的 10，不是 12
    c = build_cross_section(s, cfg, symbols=["X"], as_of="2024-04-10")
    assert float(c.loc[c.symbol == "X", "pe"].iloc[0]) != 12.0


def test_features_as_of_filter(tmp_path):
    s = _store(tmp_path)
    s.upsert_features(pd.DataFrame([
        {"symbol": "X", "date": "2024-04-02", "sentiment": 0.8,
         "confidence": 0.9, "event_type": "业绩预增", "as_of": "2024-04-02"},
    ]))
    # 情绪特征 as_of=2024-04-02：之前看不到
    assert s.get_features(["X"], as_of="2024-04-01").empty
    assert len(s.get_features(["X"], as_of="2024-04-03")) == 1
    cfg = _cfg()
    c_before = build_cross_section(s, cfg, symbols=["X"], as_of="2024-04-01")
    # 早于披露：要么无 sentiment 列，要么为空（不得泄漏未来情绪）
    if "sentiment" in c_before.columns:
        assert pd.isna(c_before["sentiment"].iloc[0])
