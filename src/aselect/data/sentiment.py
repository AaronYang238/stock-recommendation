"""AI 舆情特征编排（AI 只在文本输入端；产出结构化情绪入库，供 engine 当数值因子读）。

铁律1：AI 不接触任何选股/回测数值计算，只做「新闻文本 → 情绪分」的边界转换。
铁律2：情绪特征按新闻披露日 as_of 落地，回测按 as_of 过滤防前视。
热插拔：analyzer 缺省由工厂给出，缺 Key/未启用 → NullAnalyzer（情绪 0，核心照跑）。
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def build_sentiment_features(store, config, symbols, *,
                             analyzer=None, news_fn=None, source: str = "ai") -> int:
    """对每只：news_fn(symbol)→[{text,date}] → analyzer.analyze_sentiment →
    置信度加权聚合出 (sentiment, confidence) → upsert_features（as_of=最新新闻日）。

    返回写入的 symbol 数。无新闻的跳过；NullAnalyzer 时写中性 0（核心照跑）。
    """
    if analyzer is None:
        from ..ai.factory import get_analyzer
        analyzer = get_analyzer(config)
    if news_fn is None:
        from ..datasource.factory import get_datasource
        news_fn = get_datasource(config).news

    rows = []
    for sym in symbols:
        items = news_fn(sym) or []
        if not items:
            continue
        texts = [it["text"] for it in items]
        results = analyzer.analyze_sentiment(texts)
        den = sum(r.confidence for r in results)
        sent = (sum(r.sentiment * r.confidence for r in results) / den) if den > 0 else 0.0
        conf = (den / len(results)) if results else 0.0
        latest = max(it["date"] for it in items)
        rows.append({"symbol": sym, "date": latest, "sentiment": round(sent, 4),
                     "confidence": round(conf, 4), "as_of": latest, "source": source})
    if rows:
        store.upsert_features(pd.DataFrame(rows))
    log.info("舆情特征写入 %d 只（analyzer=%s）", len(rows), type(analyzer).__name__)
    return len(rows)
