"""统计检验（纯函数，确定性核心，AI 禁区）。

Deflated Sharpe Ratio（Bailey & López de Prado, 2014）：在"试了 N 个变体、挑最好的"
的情形下，扣除多重试验带来的运气成分，给出夏普"真的 > 0"的概率。
N 取同一样本外窗口被使用的次数（见 runner 的 OOS 使用登记）。
"""
from __future__ import annotations

import math
from statistics import NormalDist

import pandas as pd

_N = NormalDist()
_EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """N 个零真值试验中最大夏普的期望（每期口径）。"""
    if n_trials <= 1 or sr_var <= 0:
        return 0.0
    a = _N.inv_cdf(1 - 1.0 / n_trials)
    b = _N.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_var) * ((1 - _EULER) * a + _EULER * b)


def deflated_sharpe(returns: pd.Series, n_trials: int = 1) -> float | None:
    """返回 P(真实夏普 > 多重试验下的期望最大夏普)。样本不足返回 None。

    returns：每期收益（日/周/月均可，口径一致即可）。sr 方差用零假设近似 1/(T-1)。
    """
    r = pd.Series(returns, dtype=float).dropna()
    t = len(r)
    if t < 10 or r.std() == 0:
        return None
    sr = r.mean() / r.std()
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0                 # pandas 为超额峰度
    sr0 = expected_max_sharpe(max(int(n_trials), 1), 1.0 / (t - 1))
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    z = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)
    return round(_N.cdf(z), 4)
