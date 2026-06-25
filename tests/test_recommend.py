"""阶段三：推荐落库 + 战绩跟踪 + 因子快照缓存 + 运行状态。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data.clean import clean_daily
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.recommend import (
    generate_recommendations, recommendation_performance, refresh_snapshot,
    track_recommendation_returns,
)
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg() -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={}, backtest={},
                  ai=AIConfig())


def _seed(tmp_path) -> SQLiteStorage:
    s = SQLiteStorage(str(tmp_path / "rec.sqlite"))
    ds = SyntheticSource()
    s.upsert_symbols(ds.list_symbols())
    s.upsert_fundamentals(ds.fundamentals())
    for sym in ds.list_symbols()["symbol"]:
        s.upsert_daily(sym, clean_daily(ds.daily(sym, "hfq")), "hfq")
    return s


def test_generate_recommendations(tmp_path):
    store = _seed(tmp_path)
    n = generate_recommendations(store, _cfg(), top_n=5)
    assert n > 0
    df = store.get_recommendations()
    assert len(df) == n and df["rank"].min() == 1
    assert "退市" not in set(df["status"].dropna())     # 默认剔除退市


def test_track_forward_returns(tmp_path):
    store = _seed(tmp_path)
    daily = store.get_daily("600519", "hfq")
    dates = daily["date"].dt.strftime("%Y-%m-%d").tolist()
    target = dates[-40]                                  # 距末尾 40 日 → 5/20 期未来存在
    store.upsert_recommendations(pd.DataFrame(
        [{"date": target, "symbol": "600519", "rank": 1}]))
    nt = track_recommendation_returns(store, _cfg())
    assert nt >= 1
    row = store.get_recommendations(date=target).iloc[0]
    assert pd.notna(row["fwd_5d"]) and pd.notna(row["fwd_20d"])


def test_performance_aggregate(tmp_path):
    store = _seed(tmp_path)
    store.upsert_recommendations(pd.DataFrame([
        {"date": "2024-01-01", "symbol": "A", "fwd_5d": 0.1, "fwd_20d": 0.2},
        {"date": "2024-01-01", "symbol": "B", "fwd_5d": -0.05, "fwd_20d": 0.1}]))
    p = recommendation_performance(store)
    assert p["n_5d"] == 2 and abs(p["win_5d"] - 0.5) < 1e-9
    assert p["avg_20d"] is not None


def test_snapshot_roundtrip(tmp_path):
    store = _seed(tmp_path)
    n = refresh_snapshot(store, _cfg())
    assert n > 0
    snap = store.get_factor_snapshot()
    assert not snap.empty and "total_score" in snap.columns


def test_app_state(tmp_path):
    store = _seed(tmp_path)
    store.set_state("last_sync", "2024-01-01 10:00:00")
    assert store.get_state("last_sync") == "2024-01-01 10:00:00"
    assert store.get_state("missing") is None


def test_generate_idempotent_same_day(tmp_path):
    store = _seed(tmp_path)
    generate_recommendations(store, _cfg(), top_n=5)
    generate_recommendations(store, _cfg(), top_n=5)     # 同日重跑 → 主键覆盖，不重复
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    df = store.get_recommendations(date=today)
    assert len(df) <= 5
