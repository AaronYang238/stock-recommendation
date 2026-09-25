"""基金筛选与动量信号合成（纯确定性，禁 LLM）。

初筛（screen_universe）：规模/成立时长/近1年收益 → 候选池。
信号（fund_scores）：截至 T 日只用 T 日及之前净值，月频截面打分：
  mom3/mom6   近 63/126 交易日日收益年化夏普（风险调整动量）
  dd_pen      近 126 交易日最大回撤（越深越扣）
  size_fee    规模/费率惩罚（缺失按截面中性处理）
合成权重：
  mode=icir   训练窗口内各因子对下月收益的秩 IC 的 ICIR，取正部 + 收缩
              （复用 factor_weights 思路：单因子上限 0.4，向正 IC 等权收缩）
  mode=fixed  固定权重（config）
全部函数只消费 pandas 截面/时间序列，无网络、无模型调用 → 可单测确定性。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# 因子窗口（交易日）：预注册，不随回测调参
MOM3_WIN, MOM6_WIN, DD_WIN = 63, 126, 126
MIN_HIST = 60          # 少于 60 个净值点不打分
DEFAULT_FIXED = {"dd_pen": 0.55, "mom3": 0.15, "mom6": 0.10, "size_fee": 0.20}


# ── 初筛 ──
def screen_universe(info: pd.DataFrame, min_scale_yi: float = 2.0,
                    min_age_days: int = 365, min_ret_1y: float = 0.0,
                    kinds: tuple[str, ...] = ("open",),
                    today: str | None = None) -> pd.DataFrame:
    """初筛：规模、成立时长、近1年收益（近1年夏普>0 的可得代理）。

    info：fund_info 表内容（含 setup_date/scale_yi/ret_1y/kind）。
    规模缺失的基金按不通过处理（无法确认流动性）。
    """
    df = info.copy()
    if df.empty:
        return df
    if kinds:
        df = df[df["kind"].isin(kinds)]
    # 类型粗过滤：股混为主（基金类型含"股"或"混"；ETF 全保留）
    if "fund_type" in df.columns:
        is_etf = df["kind"] == "etf"
        is_equity = df["fund_type"].fillna("").str.contains("股|混", regex=True)
        df = df[is_etf | is_equity]
    ok = pd.Series(True, index=df.index)
    if "scale_yi" in df.columns:
        ok &= df["scale_yi"].notna() & (df["scale_yi"] >= min_scale_yi)
    if "setup_date" in df.columns:
        ref = pd.Timestamp(today) if today else pd.Timestamp.today()
        age = (ref - pd.to_datetime(df["setup_date"], errors="coerce")).dt.days
        ok &= age >= min_age_days
    if "ret_1y" in df.columns:
        ok &= df["ret_1y"].notna() & (df["ret_1y"] >= min_ret_1y)
    out = df[ok].copy()
    log.info("初筛：%d → %d 只", len(df), len(out))
    return out.reset_index(drop=True)


# ── 单基金因子 ──
def fund_factor_row(nav: pd.Series, as_of, scale_yi: float | None,
                    fee_rate: float | None, scale_median: float,
                    fee_median: float) -> dict | None:
    """单基金在 as_of 时点的因子值（nav 为截至 as_of 的单位净值序列）。

    返回 dict(mom3, mom6, dd_pen, size_fee) 或 None（历史不足）。
    """
    s = nav.dropna().astype(float)
    s = s[s.index <= pd.Timestamp(as_of)] if as_of is not None else s
    s = s[s > 0]
    if len(s) < MIN_HIST:
        return None
    ret = s.pct_change().dropna()

    def _sharpe(win: int) -> float | None:
        r = ret.iloc[-win:]
        if len(r) < win // 2:
            return None
        sd = r.std()
        return float(r.mean() / sd * np.sqrt(252)) if sd > 0 else None

    mom3, mom6 = _sharpe(MOM3_WIN), _sharpe(MOM6_WIN)
    if mom3 is None or mom6 is None:
        return None
    w = s.iloc[-DD_WIN:]
    dd = float((w / w.cummax() - 1.0).min()) if len(w) >= MIN_HIST else None
    if dd is None:
        return None
    # 规模/费率惩罚：截面内标准化缺口（缺失 → 0 即中性）
    pen = 0.0
    if scale_yi is not None and scale_yi > 0 and scale_median > 0:
        pen += max(-1.0, min(1.0, np.log(scale_yi / scale_median)))   # 小于中位为负
    if fee_rate is not None and fee_rate > 0 and fee_median > 0:
        pen += max(-1.0, min(1.0, np.log(fee_median / fee_rate)))    # 费高于中位为负
    return {"mom3": mom3, "mom6": mom6, "dd_pen": -dd, "size_fee": pen}


def cross_section_factors(wide: pd.DataFrame, as_of,
                          info: pd.DataFrame) -> pd.DataFrame:
    """全部候选基金在 as_of 时点的因子截面。

    wide：index=nav_date, columns=fund_code, values=nav（get_fund_nav_wide）。
    info：fund_info（含 scale_yi/fee_rate）。返回 index=fund_code 的因子表。
    内部对每只基金做除息假跳修正（见 adjust_for_dividends）。
    """
    as_of = pd.Timestamp(as_of)
    info = info.set_index("fund_code") if "fund_code" in info.columns else info
    scale_med = float(pd.to_numeric(info.get("scale_yi"), errors="coerce")
                      .dropna().median()) if len(info) else 0.0
    fee_med = float(pd.to_numeric(info.get("fee_rate"), errors="coerce")
                    .dropna().median()) if len(info) else 0.0
    rows = {}
    for code in wide.columns:
        nav = wide[code].dropna()
        if nav.empty or nav.index.max() < as_of:
            # as_of 之前无净值 = 该时点该基金尚不存在/未入库 → 不打分（防幸存者）
            continue
        nav = nav[nav.index <= as_of]
        nav = adjust_for_dividends(nav)
        row = fund_factor_row(nav, None, _num(info, code, "scale_yi"),
                              _num(info, code, "fee_rate"), scale_med, fee_med)
        if row is not None:
            rows[code] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def _num(info: pd.DataFrame, code: str, col: str) -> float | None:
    try:
        v = info.loc[code, col]
    except KeyError:
        return None
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)


# ── 除息假跳修正 ──
MAX_DAILY_DROP = -0.12   # 正常股混基金单日净值跌幅极限（A股跌停~10%+误差）


def adjust_for_dividends(nav: pd.Series) -> pd.Series:
    """除息假跳修正（分红再投资复权，向后调整因子法）。

    akshare 的"单位净值走势"在分红除息日不做除息调整 → 净值出现单日 -15%~-25%
    的假跳（实测 569/2226 只基金受影响，如 001678 于 2021-11-11 单日 -23.5%）。
    修正：单日跌幅 < -12% 视为除息，复权因子 = 前日净值/当日净值 × 当日合理收益
    （合理收益取 0，保守），把缺口以上调历史的方式抹平。低回撤/动量因子因此
    不再被分红假跳污染（否则除息基金永远排名垫底 → 系统性选到不分红基金）。
    """
    s = nav.dropna().astype(float).copy()
    if len(s) < 2:
        return s
    ret = s.pct_change()
    factor = pd.Series(1.0, index=s.index)
    cum = 1.0
    rets = ret.values
    idx = s.index
    vals = s.values
    out = vals.copy()
    for i in range(1, len(out)):
        r = rets[i]
        if r is not None and not pd.isna(r) and r < MAX_DAILY_DROP:
            cum *= vals[i - 1] / vals[i]      # 缺口全记为分红
            factor.iloc[i] = cum
        else:
            factor.iloc[i] = cum
    # 复权：历史(除息日前)上调 cum，使复权序列连续
    # 因子法（后复权语义）：adj_t = raw_t * cum_t_before？—— 标准做法是把
    # 除息日及以后的 nav 除以 cum 反而会压低。此处用"前段上调"实现连续：
    out = vals * factor.values * (vals.max() / (vals * factor.values).max())
    return pd.Series(out, index=s.index)


# ── 合成 ──
def zscore(x: pd.Series) -> pd.Series:
    """截面 Z-score；全同值/单点 → 0（中性，不产生假信号）。"""
    sd = x.std()
    if pd.isna(sd) or sd == 0 or len(x) < 2:
        return x * 0.0
    return (x - x.mean()) / sd


def icir_shrink_weights(ic_table: pd.DataFrame, shrink: float = 0.5,
                        max_weight: float = 0.4) -> dict[str, float]:
    """ICIR 收缩定权（复用 factor_weights 思路，确定性实现）。

    ic_table：index=调仓期, columns=因子名, values=该期因子对下月收益的秩 IC。
    ICIR=IC均值/IC标准差，取正部；向正 ICIR 因子等权收缩；单因子上限（类别过少放宽）。
    全部非正 → 等权。
    """
    if ic_table.empty:
        return {}
    raw = {}
    for c in ic_table.columns:
        s = ic_table[c].dropna()
        if len(s) < 2:
            raw[c] = 0.0
            continue
        sd = s.std()
        raw[c] = max(s.mean() / sd, 0.0) if sd > 0 else 0.0
    total = sum(raw.values())
    if total <= 0:
        n = len(raw)
        return {c: 1.0 / n for c in raw} if n else {}
    pos = [c for c, v in raw.items() if v > 0]
    w = {c: (shrink * raw[c] / total + (1 - shrink) / len(pos)) if c in pos else 0.0
         for c in raw}
    caps = {c: max(max_weight, 1.0 / len(pos)) for c in w}
    return _cap(w, caps)


def _cap(w: dict, caps: dict) -> dict:
    out = dict(w)
    for _ in range(len(out) + 1):
        over = {c: v - caps[c] for c, v in out.items() if v > caps[c] + 1e-12}
        if not over:
            break
        excess = sum(over.values())
        for c in over:
            out[c] = caps[c]
        free = {c: v for c, v in out.items() if v > 0 and c not in over and v < caps[c]}
        ft = sum(free.values())
        if ft <= 0:
            break
        for c, v in free.items():
            out[c] = v + excess * v / ft
    return out


def composite_score(factors: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """因子 Z-score 加权合成 → 截面总分（降序即推荐序）。确定性。"""
    if factors.empty or not weights:
        return pd.Series(dtype=float)
    total = pd.Series(0.0, index=factors.index)
    used = 0.0
    for name, w in weights.items():
        if name in factors.columns and w > 0:
            total = total + w * zscore(factors[name].astype(float))
            used += w
    if used > 0:
        total = total / used
    return total.sort_values(ascending=False)


def rank_ic(factors: pd.DataFrame, fwd_ret: pd.Series) -> dict[str, float]:
    """单期因子秩 IC（Spearman）：因子截面 vs 下月收益。缺收益 → NaN。"""
    from scipy.stats import spearmanr  # scipy 已是 pandas 依赖链内可选，兜底手算
    out = {}
    common = factors.index.intersection(fwd_ret.dropna().index)
    if len(common) < 10:
        return {c: float("nan") for c in factors.columns}
    fr = fwd_ret.loc[common].rank()
    for c in factors.columns:
        fc = factors.loc[common, c]
        if fc.notna().sum() < 10:
            out[c] = float("nan")
            continue
        try:
            rho = float(spearmanr(fc, fr).statistic)
        except Exception:  # noqa: BLE001
            rho = _rank_corr(fc, fr)
        out[c] = rho
    return out


def _rank_corr(a: pd.Series, b: pd.Series) -> float:
    """皮尔逊于秩 = 斯皮尔曼（无 scipy 时的确定性兜底）。"""
    x, y = a.rank(), b.rank()
    ok = x.notna() & y.notna()
    x, y = x[ok], y[ok]
    if len(x) < 2 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(((x - x.mean()) * (y - y.mean())).sum()
                 / np.sqrt(((x - x.std()) ** 2).sum() * ((y - y.std()) ** 2).sum()))
