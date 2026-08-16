"""M4：AI 舆情正交因子（AI 只在输入端；engine 只读数值列，无 LLM）。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data.pipeline import update_daily, update_symbols
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={},
                  backtest={}, ai=AIConfig())


def _seed(tmp_path):
    store = SQLiteStorage(str(tmp_path / "s.sqlite"))
    ds = SyntheticSource(days=220)
    update_symbols(ds, store)
    syms = ds._all_symbols()
    update_daily(ds, store, syms, "hfq")
    store.upsert_fundamentals(ds.fundamentals(syms))
    return store, _cfg(), syms


# ── Task 1: 注册 sentiment 正交因子 ─────────────────────────
def test_sentiment_registered_and_scored():
    from aselect.engine.factors import DEFAULT_FACTORS, score_factors
    assert "sentiment" in DEFAULT_FACTORS
    df = pd.DataFrame({
        "symbol": list("abcd"),
        "industry": ["科技", "科技", "医药", "医药"],
        "total_mv": [1e9, 1e9, 1e9, 1e9],
        "sentiment": [0.9, -0.8, 0.5, -0.4],
    })
    out = score_factors(df)
    assert "score_sentiment" in out.columns
    s = out.set_index("symbol")["score_sentiment"]
    assert s["a"] > s["b"]        # 情绪高 → 分高
