"""因子开发规范（CLAUDE.md）：去极值 + 标准化 + 行业中性 + 市值中性。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.datasource.synthetic_source import SyntheticSource
from aselect.engine.factors import (
    FactorDef, orthogonalize, process_factor, score_factors, winsorize, zscore,
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


def test_factordef_has_industry_neutral_flag_default_true():
    """FactorDef 默认做行业中性；热点类因子可显式关闭。"""
    assert FactorDef("x", "x", ascending=False).industry_neutral is True
    assert FactorDef("h", "h", ascending=False,
                     industry_neutral=False).industry_neutral is False


def test_score_factors_skips_industry_neutral_when_flag_false():
    """industry_neutral=False 时不做行业中性 → 纯行业信号得以保留。"""
    df = pd.DataFrame({
        "symbol": list("abcdef"),
        "industry": ["A", "A", "A", "B", "B", "B"],
        "total_mv": [1e9] * 6,
        "hot": [1.0, 1.1, 0.9, 5.0, 5.1, 4.9],   # B 行业整体更高
    })
    factors = {"hotspot": [FactorDef("hot", "hot", ascending=False,
                                     industry_neutral=False)]}
    out = score_factors(df, factors=factors)
    a = out.set_index("symbol").loc[["a", "b", "c"], "score_hotspot"].mean()
    b = out.set_index("symbol").loc[["d", "e", "f"], "score_hotspot"].mean()
    assert b - a > 1.0


def test_orthogonalize_removes_correlation_with_regressors():
    rng = np.random.default_rng(0)
    n = 200
    x1 = pd.Series(rng.normal(size=n))
    x2 = pd.Series(rng.normal(size=n))
    y = 2 * x1 - x2 + pd.Series(rng.normal(0, 0.1, n))   # 主要由 x1/x2 解释
    X = pd.DataFrame({"x1": x1, "x2": x2})
    r = orthogonalize(y, X)
    assert abs(np.corrcoef(r.values, x1.values)[0, 1]) < 0.05
    assert abs(np.corrcoef(r.values, x2.values)[0, 1]) < 0.05


def test_orthogonalize_empty_regressors_returns_input():
    y = pd.Series([1.0, 2.0, 3.0])
    pd.testing.assert_series_equal(orthogonalize(y, pd.DataFrame(index=y.index)), y)


def test_score_factors_orthogonalizes_sentiment_against_base():
    """sentiment=2*roe+独立部分：正交后 score_sentiment 与 score_quality 近乎不相关。"""
    rng = np.random.default_rng(1)
    n = 150
    roe = rng.normal(size=n)
    indep = rng.normal(size=n)
    df = pd.DataFrame({
        "symbol": [str(i) for i in range(n)],
        "industry": rng.choice(["A", "B", "C"], n),
        "total_mv": rng.uniform(50e8, 5000e8, n),
        "roe": roe,
        "sentiment": 2 * roe + indep,
    })
    out = score_factors(df).set_index("symbol")
    c = np.corrcoef(out["score_sentiment"], out["score_quality"])[0, 1]
    assert abs(c) < 0.15                    # 与基础因子(quality/roe)重叠部分被剔除
    assert out["total_score"].notna().all()
    # 仍保留独立信息：与 indep 正相关（经中性化管线后被稀释，但方向为正、未被抹平）
    assert np.corrcoef(out["score_sentiment"], indep)[0, 1] > 0.05
