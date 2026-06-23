"""因子模型：价值 / 成长 / 动量 / 质量 / 低波动。

每个因子入库前按「因子开发规范」处理（CLAUDE.md）：
  去极值(Winsorize) → 标准化(Z-score) → 行业中性 + 市值中性(OLS 残差)。
跳过中性化会把"选低估值"暗中变成"押小盘股 / 押某行业"，风格切换即崩。

处理后各因子是均值≈0 的 Z 值（越大越好，方向已统一），加权合成 total_score。
打分确定性：相同输入恒得相同输出。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FactorDef:
    name: str
    field: str          # 取自基本面/特征表的列名
    ascending: bool     # True=值越小越好（如 PE），处理后取负以统一"大=好"
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

# 中性化默认所用列
INDUSTRY_COL = "industry"
SIZE_COL = "total_mv"          # 市值；中性化时取 log


# ── 单因子处理步骤 ─────────────────────────────────────────
def winsorize(s: pd.Series, n_mad: float = 3.0) -> pd.Series:
    """中位数 ± n×MAD 去极值（对异常值比 std 稳健）。MAD=0 时退化到分位裁剪。"""
    med = s.median()
    mad = (s - med).abs().median()
    if mad and mad > 0:
        scale = 1.4826 * mad         # 使 MAD 与正态 std 同尺度
        lo, hi = med - n_mad * scale, med + n_mad * scale
    else:
        lo, hi = s.quantile(0.01), s.quantile(0.99)
    return s.clip(lo, hi)


def zscore(s: pd.Series) -> pd.Series:
    """横截面标准化：(x-mean)/std。

    std 近似为 0 时返回 0（无区分度）——这也避免「因子被中性化完全解释、残差≈0」时
    再标准化把浮点噪声放大成虚假因子。
    """
    std = s.std(ddof=0)
    if not std or std < 1e-10:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def neutralize(s: pd.Series, industry: pd.Series | None = None,
               size: pd.Series | None = None) -> pd.Series:
    """行业 + 市值中性：用 OLS 把因子对【行业哑变量 + log 市值】回归，取残差。

    残差即剔除了行业/市值系统性影响后的"纯"因子暴露。缺行业或市值则只中性化存在的那项；
    两者都缺则原样返回（仅依赖前面的 winsorize+zscore）。
    """
    cols = [pd.Series(1.0, index=s.index, name="const")]   # 截距
    if size is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            logmv = np.log(size.where(size > 0))
        cols.append(zscore(logmv.fillna(logmv.mean())).rename("logmv"))
    if industry is not None and industry.notna().any():
        dummies = pd.get_dummies(industry.astype("object"), dtype=float)
        # 丢一列避免与截距共线
        if dummies.shape[1] > 1:
            dummies = dummies.iloc[:, 1:]
        cols.append(dummies)
    X = pd.concat(cols, axis=1).astype(float)

    y = s.astype(float)
    mask = y.notna() & X.notna().all(axis=1)
    if mask.sum() < X.shape[1] + 1:        # 样本不足以回归，跳过中性化
        return s
    beta, *_ = np.linalg.lstsq(X[mask].values, y[mask].values, rcond=None)
    resid = pd.Series(np.nan, index=s.index)
    resid[mask] = y[mask].values - X[mask].values @ beta
    return resid


def process_factor(raw: pd.Series, ascending: bool,
                   industry: pd.Series | None = None,
                   size: pd.Series | None = None) -> pd.Series:
    """单因子完整处理：winsorize → zscore → 中性化 → 再 zscore → 方向统一。
    缺失值最终记 0（中性）。返回"大=好"的 Z 值。
    """
    valid = raw.dropna()
    if valid.empty:
        return pd.Series(0.0, index=raw.index)
    z = zscore(winsorize(valid))
    ind = industry.reindex(valid.index) if industry is not None else None
    sz = size.reindex(valid.index) if size is not None else None
    z = zscore(neutralize(z, ind, sz))
    if ascending:                 # 值越小越好 → 取负，统一为"大=好"
        z = -z
    return z.reindex(raw.index).fillna(0.0)


# ── 多因子合成 ─────────────────────────────────────────────
def score_factors(
    df: pd.DataFrame,
    weights: dict[str, float] | None = None,
    factors: dict[str, list[FactorDef]] | None = None,
    industry_col: str = INDUSTRY_COL,
    size_col: str = SIZE_COL,
) -> pd.DataFrame:
    """输入：每行一只股票、含因子原始列的 DataFrame（需含 symbol）。
    输出：追加各类别得分 score_<cat> 与综合 total_score（均为中性化后 Z 值，越大越好），
    并按 total_score 降序。weights：各类别权重（默认等权，仅对数据中存在的因子生效）。
    """
    factors = factors or DEFAULT_FACTORS
    out = df.copy()
    industry = out[industry_col] if industry_col in out.columns else None
    size = out[size_col] if size_col in out.columns else None

    cat_scores: dict[str, pd.Series] = {}
    for cat, defs in factors.items():
        present = [d for d in defs if d.field in out.columns]
        if not present:
            continue
        sub = pd.DataFrame(index=out.index)
        for d in present:
            sub[d.name] = process_factor(out[d.field], d.ascending, industry, size) * d.weight
        denom = sum(d.weight for d in present)
        cat_scores[cat] = sub.sum(axis=1) / denom
        out[f"score_{cat}"] = cat_scores[cat].round(4)

    if not cat_scores:
        out["total_score"] = 0.0
        return out

    w = weights or {c: 1.0 for c in cat_scores}
    total = pd.Series(0.0, index=out.index)
    wsum = 0.0
    for cat, score in cat_scores.items():
        wt = float(w.get(cat, 0.0))
        total += score * wt
        wsum += wt
    out["total_score"] = (total / wsum).round(4) if wsum else 0.0
    return out.sort_values("total_score", ascending=False)


def add_price_factors(daily: pd.DataFrame, lookback: int = 60) -> dict[str, float]:
    """从单只日线计算动量/波动因子（供合成到截面因子表）。"""
    close = daily["close"]
    if len(close) <= lookback:
        return {"mom_60": float("nan"), "vol_60": float("nan")}
    mom = close.iloc[-1] / close.iloc[-lookback - 1] - 1
    vol = close.pct_change().iloc[-lookback:].std()
    return {"mom_60": round(float(mom), 4), "vol_60": round(float(vol), 4)}
