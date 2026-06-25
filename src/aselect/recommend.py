"""每日推荐落库 + 事后战绩跟踪（应用层）。

用真实表现证明"高回报"：每日把 top-N 推荐连同快照理由落库；待行情到位后，
回填每条推荐的 5/20 个交易日前向收益，并汇总命中率/平均收益。
"""
from __future__ import annotations

import pandas as pd

from .config import Config
from .data import build_cross_section
from .data.symbols import status_label
from .engine import score_factors
from .storage import Storage

_RECO_COLS = ["date", "symbol", "name", "rank", "score", "board", "status", "pe", "roe"]


def generate_recommendations(store: Storage, config: Config, top_n: int = 20,
                             as_of: str | None = None,
                             weights: dict | None = None) -> int:
    """生成并落库当日（或 as_of 日）top-N 推荐。默认剔除退市标的。"""
    date = as_of or pd.Timestamp.today().strftime("%Y-%m-%d")
    cross = build_cross_section(store, config, as_of=as_of)
    if cross.empty:
        return 0
    scored = score_factors(cross, weights=weights)
    if "status_label" in scored.columns:
        scored = scored[scored["status_label"] != "退市"]
    top = scored.head(top_n).reset_index(drop=True)
    if top.empty:
        return 0
    rows = []
    for i, r in top.iterrows():
        rows.append({
            "date": date, "symbol": r["symbol"], "name": r.get("name"),
            "rank": int(i) + 1, "score": _num(r.get("total_score")),
            "board": r.get("board"), "status": r.get("status_label"),
            "pe": _num(r.get("pe")), "roe": _num(r.get("roe")),
        })
    store.upsert_recommendations(pd.DataFrame(rows)[_RECO_COLS])
    return len(rows)


_SNAP_COLS = ["symbol", "name", "board", "status_label", "pe", "pb", "roe",
              "revenue_yoy", "mom_60", "score_value", "score_quality", "total_score"]


def refresh_snapshot(store: Storage, config: Config, weights: dict | None = None) -> int:
    """预计算当日打分截面 → factor_snapshot，供 /api/candidates 直接读（免每请求重扫）。"""
    cross = build_cross_section(store, config)        # 实时（as_of=None）
    if cross.empty:
        return 0
    scored = score_factors(cross, weights=weights)
    keep = [c for c in _SNAP_COLS if c in scored.columns]
    snap_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    store.replace_factor_snapshot(snap_date, scored[keep])
    return len(scored)


def track_recommendation_returns(store: Storage, config: Config) -> int:
    """为缺失前向收益的推荐回填 5/20 个交易日收益（后复权）。返回更新条数。"""
    df = store.get_recommendations()
    if df.empty:
        return 0
    adjust = config.datasource.get("adjust", "hfq")
    pending = df[df["fwd_5d"].isna() | df["fwd_20d"].isna()]
    updated = 0
    for sym, grp in pending.groupby("symbol"):
        daily = store.get_daily(sym, adjust)
        if daily.empty:
            continue
        d = daily.sort_values("date").reset_index(drop=True)
        dates = d["date"].dt.strftime("%Y-%m-%d").tolist()
        closes = d["close"].tolist()
        for _, row in grp.iterrows():
            pos = _pos_on_or_after(dates, row["date"])
            if pos is None:
                continue
            p0 = closes[pos]
            f5 = _fwd(closes, pos, 5, p0)
            f20 = _fwd(closes, pos, 20, p0)
            if f5 is None and f20 is None:
                continue
            store.set_recommendation_returns(row["date"], sym, f5, f20)
            updated += 1
    return updated


def recommendation_performance(store: Storage) -> dict:
    """汇总已回填推荐的战绩：条数、平均前向收益、命中率。"""
    df = store.get_recommendations()
    out = {"n_total": int(len(df)), "n_dates": int(df["date"].nunique()) if len(df) else 0}
    for col, key in (("fwd_5d", "5d"), ("fwd_20d", "20d")):
        s = df[col].dropna() if col in df.columns else pd.Series(dtype=float)
        out[f"avg_{key}"] = round(float(s.mean()), 4) if len(s) else None
        out[f"win_{key}"] = round(float((s > 0).mean()), 3) if len(s) else None
        out[f"n_{key}"] = int(len(s))
    return out


# ── 工具 ──
def _pos_on_or_after(dates: list[str], target: str):
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return None


def _fwd(closes: list, pos: int, n: int, p0: float):
    j = pos + n
    if j < len(closes) and p0 and closes[j] is not None and not pd.isna(closes[j]):
        return round(closes[j] / p0 - 1, 4)
    return None


def _num(v):
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None
