"""阶段一：基准指数入库、数据新鲜度、回测用真实基准、调度构造。"""
from __future__ import annotations

import pandas as pd

from aselect.config import AIConfig, Config
from aselect.data import update_index
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.runner import _load_benchmark
from aselect.scheduler import build_scheduler
from aselect.storage.sqlite_store import SQLiteStorage


def _cfg(benchmark="000300") -> Config:
    return Config(app={}, datasource={"adjust": "hfq"}, storage={},
                  backtest={"benchmark": benchmark}, ai=AIConfig())


def test_index_storage_roundtrip(tmp_path):
    s = SQLiteStorage(str(tmp_path / "i.sqlite"))
    df = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [3000.0, 3050.0]})
    s.upsert_index("000300", df)
    got = s.get_index("000300")
    assert got["close"].tolist() == [3000.0, 3050.0]
    assert s.get_index("999999").empty


def test_update_index_from_source(tmp_path):
    s = SQLiteStorage(str(tmp_path / "i.sqlite"))
    n = update_index(SyntheticSource(), s, "000300")
    assert n > 0
    assert not s.get_index("000300").empty


def test_data_status(tmp_path):
    s = SQLiteStorage(str(tmp_path / "i.sqlite"))
    ds = SyntheticSource()
    from aselect.data.clean import clean_daily
    s.upsert_symbols(ds.list_symbols())
    s.upsert_fundamentals(ds.fundamentals())
    s.upsert_daily("600519", clean_daily(ds.daily("600519", "hfq")), "hfq")
    st = s.data_status()
    assert st["last_daily_date"] is not None
    assert st["n_with_fundamentals"] >= 1


def test_backtest_prefers_stored_benchmark(tmp_path):
    s = SQLiteStorage(str(tmp_path / "i.sqlite"))
    s.upsert_index("000300", pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
        "close": [3000.0, 3030.0, 3060.0]}))
    panel = pd.DataFrame(
        {"A": [10.0, 11, 12]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    bench = _load_benchmark(s, _cfg("000300"), panel)
    assert bench.iloc[0] == 3000.0 and bench.iloc[-1] == 3060.0   # 用了真实指数
    # 无基准配置 → 退化等权代理（非指数值）
    proxy = _load_benchmark(s, _cfg(""), panel)
    assert proxy.iloc[0] != 3000.0


def test_scheduler_builds_one_job():
    sched = build_scheduler(hour=16, minute=30, limit=0)
    jobs = sched.get_jobs()
    assert len(jobs) == 1 and jobs[0].id == "daily_sync"
