"""指标正确性对拍：用已知序列验证回退实现与标准定义一致。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.engine.indicators import add_indicators


def _df(closes):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1e6] * n,
    })


def test_sma_matches_rolling_mean():
    closes = list(np.linspace(10, 20, 80))
    out = add_indicators(_df(closes), ma_windows=(5,))
    expected = pd.Series(closes).rolling(5).mean()
    np.testing.assert_allclose(out["ma5"].dropna().values,
                               expected.dropna().values, rtol=1e-6)


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    closes = list(100 + np.cumsum(rng.normal(0, 1, 200)))
    out = add_indicators(_df(closes))
    rsi = out["rsi14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_macd_hist_relation():
    closes = list(np.linspace(10, 30, 120))
    out = add_indicators(_df(closes))
    diff = (out["macd"] - out["macd_signal"]).dropna()
    hist = out["macd_hist"].dropna()
    np.testing.assert_allclose(diff.values, hist.values, rtol=1e-6)
