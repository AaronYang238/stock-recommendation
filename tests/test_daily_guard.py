"""update_daily 的 hfq 常驻不变量 guard（append 边界连续性，2026-09）。

正常：重叠窗口拉取值与存库逐位一致 → 放行并写新行。
违反：重叠窗口 close 相对差 >1% → 判为复权基准改变，拒写（quarantine）。
非 hfq adjust → 不启用 guard。
"""
from __future__ import annotations

import pandas as pd
import pytest

from aselect.data.pipeline import update_daily
from aselect.storage.sqlite_store import SQLiteStorage


class FakeDS:
    """生成 close=100 的日线（重叠+新窗口），可注入 multiplier 模拟基准偏移。"""

    def __init__(self, close: float = 100.0):
        self.close = close

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        if end is None:
            end = "2026-09-01"
        if start is None:
            start = "2026-01-01"
        rng = pd.bdate_range(start=start, end=end)
        n = len(rng)
        df = pd.DataFrame({
            "date": rng.strftime("%Y-%m-%d"),
            "open": self.close, "high": self.close, "low": self.close,
            "close": self.close, "volume": 1_000_000, "amount": 1e8,
        })
        return df


def _seed(store, symbol, dates, close):
    df = pd.DataFrame({
        "date": pd.to_datetime(dates).strftime("%Y-%m-%d"),
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1_000_000, "amount": 1e8,
    })
    store.upsert_daily(symbol, df, "hfq")


def test_guard_passes_when_overlap_matches(tmp_path):
    store = SQLiteStorage(str(tmp_path / "t.sqlite"))
    ds = FakeDS(close=100.0)
    # 存 3 个旧行 close=100（与 FakeDS 一致）
    _seed(store, "600000", ["2026-08-24", "2026-08-25", "2026-08-26"], 100.0)
    last = store.last_daily_date("600000", "hfq")
    assert last == "2026-08-26"
    n = update_daily(ds, store, ["600000"], "hfq")
    assert n > 0  # 新行已写入
    rows = store.get_daily("600000", "hfq")
    assert rows["date"].max() > pd.Timestamp(last)
    store.close()


def test_guard_quarantines_on_basis_change(tmp_path):
    store = SQLiteStorage(str(tmp_path / "t.sqlite"))
    ds = FakeDS(close=100.0)          # 拉取值 close=100
    # 存库旧行 close=1000 → 重叠窗口相对差 900% → 基准改变
    _seed(store, "600000", ["2026-08-24", "2026-08-25", "2026-08-26"], 1000.0)
    n = update_daily(ds, store, ["600000"], "hfq")
    assert n == 0                     # 拒写
    rows = store.get_daily("600000", "hfq")
    assert len(rows) == 3             # 旧行未被改动
    assert (rows["close"] == 1000.0).all()
    store.close()


def test_guard_disabled_for_non_hfq(tmp_path):
    store = SQLiteStorage(str(tmp_path / "t.sqlite"))
    ds = FakeDS(close=100.0)
    _seed(store, "600000", ["2026-08-24", "2026-08-25", "2026-08-26"], 1000.0)
    # adjust='none'（raw）不启用 guard → 直接写
    n = update_daily(ds, store, ["600000"], "none")
    assert n > 0
    store.close()
