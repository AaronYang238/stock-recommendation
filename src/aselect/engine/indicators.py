"""技术指标计算。

优先使用 pandas-ta（禁止自实现易错版本，第 3.2 节）；当运行环境不可用
（如 numpy 2.x 下旧版 pandas-ta 导入失败）时，回退到一组**经测试**的
标准向量化实现（见 tests/test_indicators.py 对拍）。两条路径输出列名一致。
"""
from __future__ import annotations

import pandas as pd

try:  # 优先 pandas-ta
    import pandas_ta as _pta  # type: ignore
    _HAS_PTA = True
except Exception:  # noqa: BLE001
    _HAS_PTA = False


def add_indicators(df: pd.DataFrame,
                   ma_windows: tuple[int, ...] = (5, 10, 20, 60)) -> pd.DataFrame:
    """在日线 DataFrame 上追加常用指标列。要求含 close/high/low 列。"""
    out = df.copy()
    close, high, low = out["close"], out["high"], out["low"]

    for w in ma_windows:
        out[f"ma{w}"] = _sma(close, w)
    out["ema12"] = _ema(close, 12)
    out["ema26"] = _ema(close, 26)

    macd, signal, hist = _macd(close)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd, signal, hist

    out["rsi14"] = _rsi(close, 14)

    k, d, j = _kdj(high, low, close)
    out["kdj_k"], out["kdj_d"], out["kdj_j"] = k, d, j

    mid, upper, lower = _boll(close, 20, 2)
    out["boll_mid"], out["boll_up"], out["boll_low"] = mid, upper, lower
    return out


# ── pandas-ta 优先，否则向量化实现 ──────────────────────────
def _sma(s: pd.Series, n: int) -> pd.Series:
    if _HAS_PTA:
        return _pta.sma(s, length=n)
    return s.rolling(n).mean()


def _ema(s: pd.Series, n: int) -> pd.Series:
    if _HAS_PTA:
        return _pta.ema(s, length=n)
    return s.ewm(span=n, adjust=False).mean()


def _macd(s: pd.Series, fast=12, slow=26, signal=9):
    if _HAS_PTA:
        m = _pta.macd(s, fast=fast, slow=slow, signal=signal)
        return m.iloc[:, 0], m.iloc[:, 2], m.iloc[:, 1]
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    if _HAS_PTA:
        return _pta.rsi(s, length=n)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder 平滑
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
    if _HAS_PTA:
        st = _pta.stoch(high, low, close, k=n, d=3)
        k, d = st.iloc[:, 0], st.iloc[:, 1]
        return k, d, 3 * k - 2 * d
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    return k, d, 3 * k - 2 * d


def _boll(s: pd.Series, n: int = 20, k: float = 2.0):
    if _HAS_PTA:
        b = _pta.bbands(s, length=n, std=k)
        return b.iloc[:, 1], b.iloc[:, 2], b.iloc[:, 0]
    mid = s.rolling(n).mean()
    std = s.rolling(n).std(ddof=0)
    return mid, mid + k * std, mid - k * std


def backend() -> str:
    """当前指标后端（用于在 UI/日志显示，便于复现追溯）。"""
    return "pandas-ta" if _HAS_PTA else "vectorized-fallback"
