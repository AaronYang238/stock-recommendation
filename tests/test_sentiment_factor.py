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


# ── Task 2: 数据源 news 接口 ────────────────────────────────
def test_synthetic_news_empty():
    assert SyntheticSource().news("600519") == []


def test_datasource_base_news_default_empty():
    from aselect.datasource.base import DataSource

    class _Min(DataSource):
        def list_symbols(self): ...
        def daily(self, *a, **k): ...
        def fundamentals(self, *a, **k): ...

    assert _Min().news("x") == []


# ── Task 3: build_sentiment_features（AI 输入端）─────────────
class _FakeAnalyzer:
    """按文本关键词给情绪分（模拟 AI 输入端；不做任何选股/涨跌判断）。"""
    def analyze_sentiment(self, texts):
        from aselect.ai.base import SentimentResult
        out = []
        for t in texts:
            s = 0.8 if "利好" in t else (-0.6 if "利空" in t else 0.0)
            out.append(SentimentResult(sentiment=s, confidence=0.9))
        return out


def _news_fn(sym):
    data = {
        "600519": [{"text": "公司业绩利好", "date": "2026-08-15"}],
        "000001": [{"text": "遭遇利空传闻", "date": "2026-08-15"}],
    }
    return data.get(sym, [])


def test_build_sentiment_features_writes_signed_scores(tmp_path):
    from aselect.data.sentiment import build_sentiment_features
    store, cfg, syms = _seed(tmp_path)
    n = build_sentiment_features(store, cfg, syms,
                                 analyzer=_FakeAnalyzer(), news_fn=_news_fn)
    assert n == 2
    feats = store.get_features(["600519", "000001"]).set_index("symbol")
    assert feats.loc["600519", "sentiment"] > 0     # 利好 → 正
    assert feats.loc["000001", "sentiment"] < 0     # 利空 → 负
    assert feats.loc["600519", "as_of"] == "2026-08-15"   # PIT：as_of=新闻日


def test_build_sentiment_features_null_analyzer_neutral(tmp_path):
    from aselect.ai.null_analyzer import NullAnalyzer
    from aselect.data.sentiment import build_sentiment_features
    store, cfg, syms = _seed(tmp_path)
    build_sentiment_features(store, cfg, syms,
                             analyzer=NullAnalyzer(), news_fn=_news_fn)
    feats = store.get_features(["600519"])
    if not feats.empty:                              # 写了也应是中性 0
        assert float(feats.set_index("symbol").loc["600519", "sentiment"]) == 0.0
