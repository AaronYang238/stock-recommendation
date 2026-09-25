"""P0 修复：ST 按当日简称判定，不能用"现在的名字"回看历史。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data import build_cross_section, exclude_st_rows, filter_tradable_universe
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={},
                  backtest={"exclude_st": True}, ai=AIConfig())


def _store(tmp_path, with_history=True):
    s = SQLiteStorage(str(tmp_path / "st.sqlite"))
    s.upsert_symbols(pd.DataFrame([
        {"symbol": "600001", "name": "*ST甲", "status": "ST"},     # 现在 ST，2022 年还正常
        {"symbol": "600002", "name": "乙股份", "status": "L"},     # 2022 年戴过帽，后摘帽
        {"symbol": "600003", "name": "退市丙", "status": "D", "delist_date": "2024-06-01"},
        {"symbol": "688001", "name": "科创丁", "status": "L"},
    ]))
    if with_history:
        s.upsert_name_history(pd.DataFrame([
            {"symbol": "600001", "name": "甲股份", "start_date": "2010-01-01"},
            {"symbol": "600001", "name": "*ST甲", "start_date": "2024-05-01"},
            {"symbol": "600002", "name": "ST乙", "start_date": "2021-05-01"},
            {"symbol": "600002", "name": "乙股份", "start_date": "2023-05-01"},
            {"symbol": "600003", "name": "丙股份", "start_date": "2010-01-01"},
            {"symbol": "600003", "name": "*ST丙", "start_date": "2023-05-01"},
        ]))
    return s


def test_universe_filter_keeps_st_and_delisted(tmp_path):
    s = _store(tmp_path)
    u = filter_tradable_universe(s, _cfg(), ["600001", "600002", "600003", "688001"])
    assert u == ["600001", "600002", "600003"]           # 只按板块过滤


def test_st_is_point_in_time(tmp_path):
    s = _store(tmp_path)
    syms = ["600001", "600002", "600003"]
    c = exclude_st_rows(build_cross_section(s, _cfg(), symbols=syms, as_of="2022-06-30"),
                        _cfg())
    assert set(c["symbol"]) == {"600001", "600003"}      # 甲/丙当时正常，乙当时 ST
    live = exclude_st_rows(build_cross_section(s, _cfg(), symbols=syms), _cfg())
    assert set(live["symbol"]) == {"600002"}


def test_no_history_does_not_use_current_name(tmp_path):
    s = _store(tmp_path, with_history=False)
    c = build_cross_section(s, _cfg(), symbols=["600001"], as_of="2022-06-30")
    assert not bool(c["is_st"].iloc[0])
