"""热点因子特征聚合（纯函数，确定性核心，AI 禁区）。

板块(=行业)层面聚合出相对强度/资金流/涨停广度，映射回个股为原始热点值。
热点本质是板块归属，故【不做行业中性】——由 engine.FactorDef(industry_neutral=False)
承接，只做市值中性，避免板块信息被自我抵消（见设计 spec §5.1）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if not std or std < 1e-10:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def add_hotspot_factor(cross: pd.DataFrame, limit_pct: float = 0.099) -> pd.DataFrame:
    """按行业聚合板块相对强度 / 资金净流入 / 涨停广度，映射回个股为 hotspot 原始值。

    需要列：industry、mom_60、net_inflow、pct_chg；缺任一则 hotspot=NaN（引擎按缺失中性处理）。
    返回原表追加一列 hotspot（越大=板块越热）。纯函数、确定性，不依赖 ai/store。
    """
    out = cross.copy()
    need = {"industry", "mom_60", "net_inflow", "pct_chg"}
    if not need.issubset(out.columns):
        out["hotspot"] = np.nan
        return out

    overall_mom = out["mom_60"].mean()
    grp = out.groupby("industry")
    rel = grp["mom_60"].mean() - overall_mom               # 板块相对强度
    inflow = grp["net_inflow"].sum()                       # 板块资金净流入
    breadth = grp["pct_chg"].apply(lambda x: float((x >= limit_pct).mean()))  # 涨停广度

    sector_score = _zscore(rel) + _zscore(inflow) + _zscore(breadth)
    out["hotspot"] = out["industry"].map(sector_score)
    return out
