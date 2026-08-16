"""合成源历史季报快照：多期、真实 ann_date、严格 PIT、确定性。"""
from __future__ import annotations

import pandas as pd

from aselect.datasource.synthetic_source import SyntheticSource


def test_fundamentals_multi_period_per_symbol():
    fund = SyntheticSource().fundamentals(["600519"])
    assert (fund["symbol"] == "600519").all()
    assert len(fund) > 1                      # 逐季多期，不再是今日单快照


def test_fundamentals_ann_date_is_pit():
    today = pd.Timestamp.today().normalize()
    fund = SyntheticSource().fundamentals()
    d = pd.to_datetime(fund["date"])
    a = pd.to_datetime(fund["ann_date"])
    assert (a >= d).all()                     # 披露晚于报告期
    assert (a <= today).all()                 # 不披露未来（无前视）


def test_fundamentals_industry_constant_per_symbol():
    fund = SyntheticSource().fundamentals(["600519", "000001"])
    for sym, g in fund.groupby("symbol"):
        assert g["industry"].nunique() == 1   # 行业为结构性元数据，跨期恒定


def test_fundamentals_deterministic():
    a = SyntheticSource().fundamentals(["600519"]).reset_index(drop=True)
    b = SyntheticSource().fundamentals(["600519"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_fundamentals_columns_unchanged():
    fund = SyntheticSource().fundamentals(["600519"])
    for col in ("symbol", "date", "ann_date", "industry", "pe", "pb", "ps",
                "roe", "roa", "revenue_yoy", "profit_yoy", "gross_margin",
                "debt_ratio", "total_mv"):
        assert col in fund.columns
