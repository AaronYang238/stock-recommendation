"""第 6 节可复现：相同输入 + 相同配置 → 确定性核心输出完全一致。"""
from __future__ import annotations

import pandas as pd

from aselect.datasource.synthetic_source import SyntheticSource
from aselect.engine import add_indicators, score_factors


def test_synthetic_source_reproducible():
    a = SyntheticSource().daily("600519", "hfq")
    b = SyntheticSource().daily("600519", "hfq")
    pd.testing.assert_frame_equal(a, b)


def test_indicators_deterministic():
    df = SyntheticSource().daily("000001", "hfq")
    i1 = add_indicators(df)
    i2 = add_indicators(df)
    pd.testing.assert_frame_equal(i1, i2)


def test_factor_scoring_deterministic():
    fund = SyntheticSource().fundamentals()
    s1 = score_factors(fund)
    s2 = score_factors(fund)
    pd.testing.assert_frame_equal(s1, s2)
