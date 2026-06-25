"""服务层：把 aselect 确定性核心 + 热插拔 AI 封装成后端可调用的函数。

铁律不变：这里只是 REST 外壳，所有数值仍由 aselect.engine(确定性) 产出；
AI 经 aselect.ai.get_analyzer 获取，禁用/缺 Key 时自动降级，不影响其余接口。

存储按请求开关：SQLite 连接非跨线程安全，故每次调用新开、用完即关，
避免 Django 多线程开发服下的连接复用问题。
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Any

import pandas as pd

from aselect.ai import get_analyzer
from aselect.config import load_config
from aselect.data import build_cross_section
from aselect.data.symbols import STATUS_LABELS
from aselect.engine import add_indicators, score_factors, screen
from aselect.engine.backtest import run_ma_backtest
from aselect.engine.indicators import backend as indicator_backend
from aselect.engine.screener import Condition, FilterSpec
from aselect.field_schema import FIELD_SCHEMA
from aselect.glossary import GLOSSARY
from aselect.storage import get_storage

# 候选股展示列（与前端表格列对应）
CANDIDATE_COLS = [
    "symbol", "name", "board", "status_label", "pe", "pb", "roe",
    "revenue_yoy", "mom_60", "score_value", "score_quality", "total_score",
]


def get_config():
    return load_config()


@contextmanager
def _store():
    cfg = load_config()
    store = get_storage(cfg)
    try:
        yield cfg, store
    finally:
        store.close()


def _clean(v: Any) -> Any:
    """把 NaN/NumPy 标量转成 JSON 友好的原生类型。"""
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):          # numpy 标量
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    use = [c for c in cols if c in df.columns]
    out = []
    for _, row in df[use].iterrows():
        out.append({c: _clean(row[c]) for c in use})
    return out


# ── /api/meta ─────────────────────────────────────────────
def meta() -> dict:
    cfg = load_config()
    with _store() as (_, store):
        n_symbols = len(store.get_symbols())
        status = store.data_status()
    ai_enabled = bool(cfg.ai.enabled) and cfg.ai.provider not in (None, "none", "")
    return {
        "n_symbols": n_symbols,
        "indicator_backend": indicator_backend(),
        "ai": {"enabled": ai_enabled, "provider": cfg.ai.provider, "model": cfg.ai.model},
        "data": {"last_daily_date": status.get("last_daily_date"),
                 "n_with_fundamentals": status.get("n_with_fundamentals", 0)},
        "disclaimer": cfg.disclaimer,
        "glossary": GLOSSARY,
        "field_schema": FIELD_SCHEMA,
        "status_labels": list(STATUS_LABELS.values()),
        "columns": CANDIDATE_COLS,
    }


RECO_COLS = ["date", "symbol", "name", "rank", "score", "board", "status",
             "pe", "roe", "fwd_5d", "fwd_20d"]


# ── /api/health （数据新鲜度 + 调度状态 + AI）──────────────
def health() -> dict:
    cfg = load_config()
    with _store() as (_, store):
        status = store.data_status()
        last_sync = store.get_state("last_sync")
        n_reco = len(store.recommendation_dates())
    ai_enabled = bool(cfg.ai.enabled) and cfg.ai.provider not in (None, "none", "")
    return {
        "status": "ok",
        "indicator_backend": indicator_backend(),
        "ai": {"enabled": ai_enabled, "provider": cfg.ai.provider},
        "data": status,
        "last_sync": last_sync,
        "recommendation_days": n_reco,
    }


# ── /api/recommendations + /performance ───────────────────
def recommendations(date: str | None = None, limit: int = 50) -> dict:
    with _store() as (_, store):
        dates = store.recommendation_dates()
        latest = date or (dates[-1] if dates else None)
        df = store.get_recommendations(date=latest) if latest else \
            store.get_recommendations()
    return {
        "rows": _records(df.head(limit), RECO_COLS),
        "dates": dates[-60:],
        "latest_date": latest,
    }


def recommendation_performance() -> dict:
    from aselect.recommend import recommendation_performance as _perf
    with _store() as (_, store):
        return _perf(store)


# ── /api/candidates ───────────────────────────────────────
def candidates(pe_max: float, roe_min: float, top: int,
               boards: list[str] | None = None,
               statuses: list[str] | None = None,
               as_of: str | None = None) -> dict:
    # as_of：point-in-time 截面（防前视）。缺省=实时（取最新已披露）。
    # 实时模式优先读 sync 预计算的因子快照（毫秒级，免每请求重扫全市场）。
    with _store() as (cfg, store):
        snap = store.get_factor_snapshot() if not as_of else None
        if snap is not None and not snap.empty:
            scored = snap
        else:
            cross = build_cross_section(store, cfg, as_of=as_of)
            scored = score_factors(cross)
    spec = FilterSpec(
        name="api-screen",
        conditions=[Condition("pe", "<", pe_max), Condition("roe", ">", roe_min)],
        sort_by="total_score", ascending=False, limit=int(top),
    )
    result = screen(scored, spec)
    if boards and "board" in result.columns:
        result = result[result["board"].isin(boards)]
    if statuses and "status_label" in result.columns:
        result = result[result["status_label"].isin(statuses)]

    board_opts = sorted(scored["board"].dropna().unique().tolist()) \
        if "board" in scored.columns else []
    status_opts = sorted(scored["status_label"].dropna().unique().tolist()) \
        if "status_label" in scored.columns else []
    return {
        "rows": _records(result, CANDIDATE_COLS),
        "count": int(len(result)),
        "board_options": board_opts,
        "status_options": status_opts,
        "as_of": as_of,
    }


# ── /api/stocks/<symbol>/daily ────────────────────────────
def daily_series(symbol: str, start: str | None = None,
                 end: str | None = None) -> dict:
    """指定区间 [start, end] 的日 K + 均线。

    指标在全量序列上计算后再按区间切片，保证窗口起点的 MA20/MA60 也正确
    （否则区间头部会因缺少前置数据而为 NaN）。
    """
    with _store() as (cfg, store):
        adjust = cfg.datasource.get("adjust", "hfq")
        name = _symbol_name(store, symbol)
        daily = store.get_daily(symbol, adjust)
    if daily.empty:
        return {"symbol": symbol, "name": name, "points": [], "available": None}
    ind = add_indicators(daily)
    ind["date"] = pd.to_datetime(ind["date"]).dt.strftime("%Y-%m-%d")
    available = {"start": ind["date"].iloc[0], "end": ind["date"].iloc[-1]}
    if start:
        ind = ind[ind["date"] >= start]
    if end:
        ind = ind[ind["date"] <= end]
    cols = ["date", "open", "high", "low", "close", "ma20", "ma60"]
    return {
        "symbol": symbol, "name": name, "adjust": adjust,
        "start": start, "end": end, "available": available,
        "points": _records(ind, cols),
    }


def _symbol_name(store, symbol: str) -> str | None:
    df = store.get_symbols()
    hit = df[df["symbol"] == symbol]
    return None if hit.empty else hit["name"].iloc[0]


# ── /api/stocks/<symbol>/backtest ─────────────────────────
def backtest(symbol: str, fast: int = 5, slow: int = 20) -> dict:
    with _store() as (cfg, store):
        adjust = cfg.datasource.get("adjust", "hfq")
        daily = store.get_daily(symbol, adjust)
        if daily.empty:
            return {"symbol": symbol, "error": "无数据，请先 seed/update。"}
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        res = run_ma_backtest(d, cfg.backtest, fast=fast, slow=slow)
    return {
        "symbol": symbol, "engine": res.engine,
        "total_return": res.total_return, "annual_return": res.annual_return,
        "sharpe": res.sharpe, "max_drawdown": res.max_drawdown, "trades": res.trades,
    }


# ── /api/research/report （阶段二：因子 IC + 样本外验证）──
def research_report(freq: str = "M", top: int = 20) -> dict:
    from aselect.runner import run_factor_research, run_validated_strategy
    with _store() as (cfg, store):
        reps = run_factor_research(store, cfg, freq=freq)
        val = run_validated_strategy(store, cfg, freq=freq, top_n=top)
    factors = [{"name": n, "ic_mean": r.ic_mean, "icir": r.icir,
                "ic_win_rate": r.ic_win_rate, "quantile_spread": r.quantile_spread,
                "n": r.n, "decay": r.decay}
               for n, r in sorted(reps.items(), key=lambda kv: -abs(kv[1].ic_mean))]

    def _m(rep) -> dict:
        return {"total_return": rep.total_return, "annual_return": rep.annual_return,
                "sharpe": rep.sharpe, "max_drawdown": rep.max_drawdown,
                "excess_return": rep.excess_return, "ic_mean": rep.ic_mean,
                "profit_loss_ratio": rep.profit_loss_ratio,
                "n_rebalances": rep.n_rebalances}
    validated = None if "error" in val else {
        "split_date": val["split_date"], "weights": val["weights"],
        "train": _m(val["train"]), "oos": _m(val["oos"]),
    }
    return {"freq": freq, "factors": factors, "validated": validated,
            "note": val.get("error")}


# ── /api/strategy/backtest ────────────────────────────────
def strategy_backtest(top: int = 20, freq: str = "M",
                      start: str | None = None, end: str | None = None) -> dict:
    from aselect.runner import run_strategy_backtest
    with _store() as (cfg, store):
        rep = run_strategy_backtest(store, cfg, start=start, end=end,
                                    freq=freq, top_n=top)
    eq, bc = rep.equity_curve, rep.benchmark_curve
    bc = bc.reindex(eq.index)
    curve = [{"date": pd.Timestamp(d).strftime("%Y-%m-%d"),
              "equity": round(float(e), 4),
              "benchmark": round(float(b), 4) if pd.notna(b) else None}
             for d, e, b in zip(eq.index, eq.values, bc.values)]
    return {
        "params": {"top": top, "freq": freq, "start": start, "end": end},
        "metrics": {
            "total_return": rep.total_return, "annual_return": rep.annual_return,
            "sharpe": rep.sharpe, "max_drawdown": rep.max_drawdown,
            "benchmark_return": rep.benchmark_return, "excess_return": rep.excess_return,
            "ic_mean": rep.ic_mean, "ic_ir": rep.ic_ir, "ic_win_rate": rep.ic_win_rate,
            "win_rate": rep.win_rate, "profit_loss_ratio": rep.profit_loss_ratio,
            "expectancy": rep.expectancy, "n_rebalances": rep.n_rebalances,
            "avg_turnover": rep.avg_turnover, "avg_positions": rep.avg_positions,
        },
        "curve": curve,
    }


# ── /api/stocks/<symbol>/report （AI · RAG）───────────────
def ai_report(symbol: str) -> dict:
    with _store() as (cfg, store):
        cross = build_cross_section(store, cfg)
    scored = score_factors(cross)
    row = scored[scored["symbol"] == symbol]
    payload = _records(row, CANDIDATE_COLS)
    candidate = payload[0] if payload else {"symbol": symbol}
    analyzer = get_analyzer(cfg)            # 禁用时返回 NullAnalyzer
    report = analyzer.generate_report({"candidate": candidate, "fields": FIELD_SCHEMA})
    return {"symbol": symbol, "text": report.text, "grounded": report.grounded,
            "ai_enabled": cfg.ai.enabled}
