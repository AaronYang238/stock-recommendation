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
    ai_enabled = bool(cfg.ai.enabled) and cfg.ai.provider not in (None, "none", "")
    return {
        "n_symbols": n_symbols,
        "indicator_backend": indicator_backend(),
        "ai": {"enabled": ai_enabled, "provider": cfg.ai.provider, "model": cfg.ai.model},
        "disclaimer": cfg.disclaimer,
        "glossary": GLOSSARY,
        "field_schema": FIELD_SCHEMA,
        "status_labels": list(STATUS_LABELS.values()),
        "columns": CANDIDATE_COLS,
    }


# ── /api/candidates ───────────────────────────────────────
def candidates(pe_max: float, roe_min: float, top: int,
               boards: list[str] | None = None,
               statuses: list[str] | None = None) -> dict:
    with _store() as (cfg, store):
        cross = build_cross_section(store, cfg)
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
    }


# ── /api/stocks/<symbol>/daily ────────────────────────────
def daily_series(symbol: str) -> dict:
    with _store() as (cfg, store):
        adjust = cfg.datasource.get("adjust", "hfq")
        daily = store.get_daily(symbol, adjust)
    if daily.empty:
        return {"symbol": symbol, "points": []}
    ind = add_indicators(daily)
    ind["date"] = pd.to_datetime(ind["date"]).dt.strftime("%Y-%m-%d")
    cols = ["date", "close", "ma20", "ma60"]
    return {"symbol": symbol, "adjust": adjust, "points": _records(ind, cols)}


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
