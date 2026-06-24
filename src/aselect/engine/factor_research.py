"""单因子 walk-forward IC 研究（纯函数，确定性核心，AI 禁区）。

对每个因子，逐调仓日算 Rank IC（因子值 vs 前向收益），汇总 IC 均值/标准差/ICIR/IC胜率/
IC 衰减；并按分位分层算"顶组−底组"前向收益差（多空 spread）。
复用 factor_backtest 的 _rank_ic / _ret；编排（取截面、算各因子处理值）在 runner，
因此本模块不依赖 store/data（不反向依赖上层）。

IC 解读（CLAUDE 北极星）：|IC| 稳定在 0.03~0.05 即好因子，ICIR>0.5 佳；不要追虚高数字。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .factor_backtest import _rank_ic, _ret


@dataclass
class FactorICReport:
    name: str
    ic_mean: float
    ic_std: float
    icir: float            # IC 均值 / IC 标准差（信息比）
    ic_win_rate: float     # IC>0 的比例
    n: int
    quantile_spread: float # 顶组−底组 平均前向收益差（每期）
    decay: list = field(default_factory=list)   # 1/2/3 期前向 IC（衰减）


def ic_series(scores_by_date: dict, panel: pd.DataFrame,
              schedule: list, horizon: int = 1) -> pd.Series:
    """各调仓日的 Rank IC 序列（因子值 vs 未来 horizon 期收益）。"""
    dates = list(schedule)
    ics = []
    for i in range(len(dates) - horizon):
        ic = _rank_ic(scores_by_date.get(dates[i]), panel, dates[i], dates[i + horizon])
        if ic is not None:
            ics.append(ic)
    return pd.Series(ics, dtype=float)


def quantile_spread(scores_by_date: dict, panel: pd.DataFrame,
                    schedule: list, n_groups: int = 5) -> pd.Series:
    """按因子值分 n 组，顶组−底组的下一期平均收益差（分层有效性）。"""
    dates = list(schedule)
    spreads = []
    for i in range(len(dates) - 1):
        t0, t1 = dates[i], dates[i + 1]
        sc = scores_by_date.get(t0)
        if sc is None:
            continue
        fwd = pd.Series({s: _ret(panel, s, t0, t1) for s in sc.index})
        pair = pd.DataFrame({"s": sc, "f": fwd}).dropna()
        if len(pair) < n_groups * 2:
            continue
        g = pd.qcut(pair["s"].rank(method="first"), n_groups, labels=False)
        top = pair.loc[g == n_groups - 1, "f"].mean()
        bot = pair.loc[g == 0, "f"].mean()
        spreads.append(top - bot)
    return pd.Series(spreads, dtype=float)


def summarize(name: str, scores_by_date: dict, panel: pd.DataFrame,
              schedule: list, n_groups: int = 5) -> FactorICReport:
    base = ic_series(scores_by_date, panel, schedule, 1)
    mean = float(base.mean()) if len(base) else 0.0
    std = float(base.std()) if len(base) > 1 else 0.0
    decay = []
    for h in (1, 2, 3):
        s = ic_series(scores_by_date, panel, schedule, h)
        decay.append(round(float(s.mean()), 4) if len(s) else 0.0)
    sp = quantile_spread(scores_by_date, panel, schedule, n_groups)
    return FactorICReport(
        name=name, ic_mean=round(mean, 4), ic_std=round(std, 4),
        icir=round(mean / std, 3) if std > 0 else 0.0,
        ic_win_rate=round(float((base > 0).mean()), 3) if len(base) else 0.0,
        n=int(len(base)),
        quantile_spread=round(float(sp.mean()), 4) if len(sp) else 0.0,
        decay=decay,
    )
