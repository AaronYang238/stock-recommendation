"""推荐生成 + 回测编排（walk-forward，无前视）。

build_monthly_scores：对每个月末 T，用 ≤T 的净值算因子截面 → 合成分。
  ICIR 权重模式：用 T 之前 24 个月的滚动窗口（因子 vs 下月收益的秩 IC）
  估计 ICIR 权重——只用历史，无未来函数；样本不足 12 期时回退固定权重。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .strategy import (DEFAULT_FIXED, composite_score, cross_section_factors,
                       icir_shrink_weights, rank_ic)

log = logging.getLogger(__name__)


def month_ends(wide_index: pd.DatetimeIndex, start=None, end=None) -> list[pd.Timestamp]:
    idx = pd.DatetimeIndex(wide_index)
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx <= pd.Timestamp(end)]
    s = pd.Series(idx, index=idx)
    return list(s.groupby([idx.year, idx.month]).max())


def build_monthly_scores(wide: pd.DataFrame, info: pd.DataFrame,
                         mode: str = "icir", shrink: float = 0.5,
                         max_weight: float = 0.4,
                         fixed: dict | None = None,
                         start=None, end=None,
                         min_icir_months: int = 12) -> dict[pd.Timestamp, pd.Series]:
    """逐月末打分。返回 {月末T: 分数截面}，每个截面只用 ≤T 数据。"""
    fixed = fixed or DEFAULT_FIXED
    wide = wide.sort_index()
    mes = month_ends(wide.index, start, end)
    if not mes:
        return {}
    # 预计算每个月末的因子截面与下月收益（供 ICIR 权重）
    factor_by_t: dict[pd.Timestamp, pd.DataFrame] = {}
    fwd_by_t: dict[pd.Timestamp, pd.Series] = {}
    for i, t in enumerate(mes):
        fac = cross_section_factors(wide, t, info)
        if fac.empty:
            continue
        factor_by_t[t] = fac
        nxt = mes[i + 1] if i + 1 < len(mes) else None
        if nxt is not None:
            f0 = wide[wide.index <= t].ffill().iloc[-1]
            f1 = wide[wide.index <= nxt].ffill().iloc[-1]
            common = fac.index.intersection(f0.dropna().index)
            fwd = (f1.reindex(common) / f0.reindex(common) - 1.0)
            fwd_by_t[t] = fwd
    scores: dict[pd.Timestamp, pd.Series] = {}
    ic_history: list[pd.Timestamp] = []
    ic_rows: list[dict] = []
    for t in mes:
        fac = factor_by_t.get(t)
        if fac is None:
            continue
        if mode == "icir" and len(ic_rows) >= min_icir_months:
            ic_tab = pd.DataFrame(ic_rows)
            w = icir_shrink_weights(ic_tab, shrink=shrink, max_weight=max_weight)
        else:
            w = fixed if mode == "fixed" else dict(fixed)
        scores[t] = composite_score(fac, w)
        # 登记 IC（供后续月末的权重估计）
        if t in fwd_by_t and not fwd_by_t[t].dropna().empty:
            ic_rows.append(rank_ic(fac, fwd_by_t[t]))
            ic_history.append(t)
    return scores
