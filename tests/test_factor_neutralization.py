"""因子开发规范（CLAUDE.md）：去极值 + 标准化 + 行业中性 + 市值中性。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.datasource.synthetic_source import SyntheticSource
from aselect.engine.factors import (
    process_factor, score_factors, winsorize, zscore,
)


def test_winsorize_clips_outlier():
    s = pd.Series([1.0, 2, 3, 4, 5, 1000])
    w = winsorize(s)
    assert w.max() < 1000          # 极端值被压回
    assert w.min() == s.min() or w.min() >= s.min()


def test_zscore_mean0_std1():
    z = zscore(pd.Series([1.0, 2, 3, 4, 5]))
    assert abs(z.mean()) < 1e-9
    assert abs(z.std(ddof=0) - 1) < 1e-9


def test_direction_unified():
    raw = pd.Series([1.0, 2, 3, 4, 5], index=list("abcde"))
    hi = process_factor(raw, ascending=False)   # 值大=好
    lo = process_factor(raw, ascending=True)    # 值小=好
    assert hi.idxmax() == "e"
    assert lo.idxmax() == "a"


def test_size_neutral_orthogonal_to_logmv():
    """因子主要由市值驱动时，处理后应与 log 市值近乎正交（市值中性）。"""
    rng = np.random.default_rng(0)
    n = 40
    size = pd.Series(rng.uniform(50e8, 5000e8, n))
    raw = pd.Series(np.log(size.values) + rng.normal(0, 0.1, n))
    p = process_factor(raw, ascending=False, size=size)
    corr = np.corrcoef(p.values, np.log(size.values))[0, 1]
    assert abs(corr) < 0.1          # 市值系统性影响已剔除


def test_industry_neutral_removes_group_effect():
    """因子在行业内恒定（纯行业效应）时，行业中性后各行业均值≈0。"""
    ind = pd.Series(["A"] * 10 + ["B"] * 10 + ["C"] * 10)
    raw = pd.Series([10.0] * 10 + [20.0] * 10 + [30.0] * 10)
    p = process_factor(raw, ascending=False, industry=ind)
    group_means = p.groupby(ind).mean().abs()
    assert (group_means < 1e-6).all()


def test_score_factors_runs_with_industry_and_size():
    df = SyntheticSource().fundamentals()        # 含 industry / total_mv / 各因子
    assert "industry" in df.columns
    scored = score_factors(df)
    assert "total_score" in scored.columns
    assert "score_value" in scored.columns and "score_quality" in scored.columns
    assert scored["total_score"].notna().all()
    # 确定性
    pd.testing.assert_frame_equal(scored, score_factors(df))


def test_score_factors_without_industry_or_size():
    """缺行业/市值列也不报错（真实数据当前无行业 → 退化为仅标准化+可得的中性化）。"""
    df = pd.DataFrame({
        "symbol": ["A", "B", "C", "D"],
        "pe": [10, 20, 30, 40], "roe": [25, 15, 5, -5],
    })
    scored = score_factors(df)
    assert "total_score" in scored.columns
    assert len(scored) == 4
