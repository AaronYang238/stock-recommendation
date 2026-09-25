"""P0 修复：历史估值/财报/行业按 PIT 回填与读取（旧实现只有"今天"的快照）。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data import build_cross_section, save_fundamentals_snapshot
from aselect.data.backfill import run_backfill
from aselect.datasource.base import DataSource, first_disclosure
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={}, backtest={},
                  ai=AIConfig())


class FakeSource(DataSource):
    name = "fake"

    def list_symbols(self):
        return pd.DataFrame()

    def daily(self, symbol, adjust, start=None, end=None):
        return pd.DataFrame()

    def fundamentals(self, symbols=None):
        return pd.DataFrame()

    def trade_dates(self, start, end):
        return ["2024-01-02", "2024-01-03", "2024-06-03"]

    def valuation_by_date(self, d):
        pe = {"2024-01-02": 10.0, "2024-01-03": 11.0, "2024-06-03": 20.0}[d]
        return pd.DataFrame([{"symbol": "X", "date": d, "pe": pe, "pb": 1.0,
                              "ps": 2.0, "total_mv": 1e10}])

    def fundamentals_history(self, symbol, start):
        return first_disclosure(pd.DataFrame([
            {"symbol": symbol, "date": "2023-12-31", "ann_date": "2024-03-30", "roe": 20.0},
            {"symbol": symbol, "date": "2023-12-31", "ann_date": "2024-05-10", "roe": 99.0},
            {"symbol": symbol, "date": "2024-03-31", "ann_date": "2024-04-28", "roe": 25.0},
        ]))

    def industry_history(self):
        return pd.DataFrame([
            {"symbol": "X", "industry": "银行", "in_date": "2020-01-01", "out_date": "2024-03-01"},
            {"symbol": "X", "industry": "非银", "in_date": "2024-03-01", "out_date": None},
        ])

    def name_history(self, symbols=None):
        return pd.DataFrame([
            {"symbol": "X", "name": "测试X", "start_date": "2010-01-01", "end_date": "2024-04-30"},
            {"symbol": "X", "name": "*ST测试", "start_date": "2024-04-30", "end_date": None},
        ])


def _store(tmp_path):
    s = SQLiteStorage(str(tmp_path / "bf.sqlite"))
    s.upsert_symbols(pd.DataFrame([{"symbol": "X", "name": "*ST测试", "exchange": "SH",
                                    "status": "ST", "industry": "非银"}]))
    return s


def test_first_disclosure_keeps_earliest_version():
    df = FakeSource().fundamentals_history("X", "2020-01-01")
    fy = df[df["date"] == "2023-12-31"]
    assert len(fy) == 1 and float(fy["roe"].iloc[0]) == 20.0   # 修订版(5-10)不回灌历史


def test_backfill_then_cross_section_is_point_in_time(tmp_path):
    s = _store(tmp_path)
    run_backfill(FakeSource(), s, "2020-01-01", "2024-12-31", progress=lambda *_: None)
    c = build_cross_section(s, _cfg(), symbols=["X"], as_of="2024-01-10")
    row = c.iloc[0]
    assert row["pe"] == 11.0                     # 取 ≤as_of 最近估值日，而非今天
    assert pd.isna(row.get("roe"))                   # 2023 年报 3-30 才披露
    assert row["industry"] == "银行"             # 当时的行业，而非现在的"非银"
    c2 = build_cross_section(s, _cfg(), symbols=["X"], as_of="2024-05-01")
    assert c2.iloc[0]["roe"] == 25.0 and c2.iloc[0]["industry"] == "非银"
    # 估值过期(>15 天无新估值)不沿用
    c3 = build_cross_section(s, _cfg(), symbols=["X"], as_of="2024-05-01")
    assert pd.isna(c3.iloc[0]["pe"])


def test_backfill_is_idempotent(tmp_path):
    s = _store(tmp_path)
    run_backfill(FakeSource(), s, "2020-01-01", "2024-12-31", progress=lambda *_: None)
    out = run_backfill(FakeSource(), s, "2020-01-01", "2024-12-31",
                       what=("valuation",), progress=lambda *_: None)
    assert out["valuation"] == 0


def test_snapshot_split_keeps_valuation_off_report_rows(tmp_path):
    s = _store(tmp_path)
    save_fundamentals_snapshot(s, pd.DataFrame([{
        "symbol": "X", "date": "2024-03-31", "ann_date": "2024-04-28", "roe": 25.0,
        "pe": 30.0, "total_mv": 2e10, "val_date": "2024-09-20"}]))
    f = s.get_fundamentals(["X"])
    assert pd.isna(f["pe"].iloc[0])              # 财报行不再携带"今天"的估值
    # 站在 2024-05-01 看：9-20 的估值不可见
    c = build_cross_section(s, _cfg(), symbols=["X"], as_of="2024-05-01")
    assert pd.isna(c.iloc[0]["pe"]) and c.iloc[0]["roe"] == 25.0
    c_live = build_cross_section(s, _cfg(), symbols=["X"])
    assert c_live.iloc[0]["pe"] == 30.0
