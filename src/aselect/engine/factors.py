"""因子模型：价值 / 成长 / 动量 / 质量 / 低波动。

每个因子按横截面排名打分（rank → [0,1]），再加权合成总分。
因子可配置、可扩展。打分确定性：相同输入恒得相同输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class FactorDef:
    name: str
    field: str          # 取自基本面/特征表的列名
    ascending: bool     # True=值越小越好（如 PE），打分时反转
    weight: float = 1.0


# 默认因子库：方向遵循常识（低估值/高成长/高质量/低波动优先）
DEFAULT_FACTORS: dict[str, list[FactorDef]] = {
    "value": [
        FactorDef("pe", "pe", ascending=True),
        FactorDef("pb", "pb", ascending=True),
        FactorDef("ps", "ps", ascending=True),
    ],
    "growth": [
        FactorDef("revenue_yoy", "revenue_yoy", ascending=False),
        FactorDef("profit_yoy", "profit_yoy", ascending=False),
    ],
    "quality": [
        FactorDef("roe", "roe", ascending=False),
        FactorDef("roa", "roa", ascending=False),
        FactorDef("gross_margin", "gross_margin", ascending=False),
    ],
    "momentum": [
        FactorDef("mom_60", "mom_60", ascending=False),
    ],
    "lowvol": [
        FactorDef("vol_60", "vol_60", ascending=True),
    ],
}


def _rank_score(s: pd.Series, ascending: bool) -> pd.Series:
    """横截面百分位排名 → [0,1]，越大越好。NaN 记 0.5（中性）。"""
    valid = s.dropna()
    if valid.empty:
        return pd.Series(0.5, index=s.index)
    pct = valid.rank(ascending=ascending, pct=True)  # ascending: 小值得高分
    return pct.reindex(s.index).fillna(0.5)


def score_factors(
    df: pd.DataFrame,
    weights: dict[str, float] | None = None,
    factors: dict[str, list[FactorDef]] | None = None,
) -> pd.DataFrame:
    """输入：每行一只股票、含因子原始列的 DataFrame（需含 symbol）。
    输出：追加各类别得分列 score_<cat> 与综合 total_score，并按总分降序。
    weights：各类别权重（默认等权，仅对数据中存在的因子生效）。
    """
    factors = factors or DEFAULT_FACTORS
    out = df.copy()
    cat_scores: dict[str, pd.Series] = {}

    for cat, defs in factors.items():
        present = [d for d in defs if d.field in out.columns]
        if not present:
            continue
        sub = pd.DataFrame(index=out.index)
        for d in present:
            sub[d.name] = _rank_score(out[d.field], d.ascending) * d.weight
        denom = sum(d.weight for d in present)
        cat_scores[cat] = sub.sum(axis=1) / denom
        out[f"score_{cat}"] = cat_scores[cat].round(4)

    if not cat_scores:
        out["total_score"] = 0.5
        return out

    w = weights or {c: 1.0 for c in cat_scores}
    total = pd.Series(0.0, index=out.index)
    wsum = 0.0
    for cat, score in cat_scores.items():
        wt = float(w.get(cat, 0.0))
        total += score * wt
        wsum += wt
    out["total_score"] = (total / wsum).round(4) if wsum else 0.5
    return out.sort_values("total_score", ascending=False)


def add_price_factors(daily: pd.DataFrame, lookback: int = 60) -> dict[str, float]:
    """从单只日线计算动量/波动因子（供合成到截面因子表）。"""
    close = daily["close"]
    if len(close) <= lookback:
        return {"mom_60": float("nan"), "vol_60": float("nan")}
    mom = close.iloc[-1] / close.iloc[-lookback - 1] - 1
    vol = close.pct_change().iloc[-lookback:].std()
    return {"mom_60": round(float(mom), 4), "vol_60": round(float(vol), 4)}
