"""upsert_daily 尺度 guard 单测（2026-09-03，防 000004 型"新日期追加换基"损坏）。

场景：
1. 正常追加（同基准，新日期连续）→ 放行
2. 新日期追加但基准突变（close 跳 +1140%）→ 拒写 ScaleMismatchError
3. 重叠日期尺度不符（>1%）→ 拒写
4. 真实退市整理期暴跌（-25%）→ 放行（真实事件，非换基）
5. 非 hfq adjust → 不启用 guard
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from aselect.storage.sqlite_store import SQLiteStorage, ScaleMismatchError


def _mk_store(tmp: Path) -> SQLiteStorage:
    return SQLiteStorage(str(tmp / "t.sqlite"))


def _daily_frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "open": closes, "high": closes,
                         "low": closes, "close": closes,
                         "volume": [1e6] * len(dates), "amount": [1e7] * len(dates)})


def test_normal_append_passes(tmp_path):
    st = _mk_store(tmp_path)
    st.upsert_daily("000001", _daily_frame(["2026-01-05", "2026-01-06"], [10.0, 10.1]), "hfq")
    # 正常连续追加（无重叠，新首日 +1%）
    st.upsert_daily("000001", _daily_frame(["2026-01-07", "2026-01-08"], [10.2, 10.3]), "hfq")
    df = st.get_daily("000001", "hfq")
    assert len(df) == 4


def test_basis_break_append_rejected(tmp_path):
    st = _mk_store(tmp_path)
    st.upsert_daily("000001", _daily_frame(["2026-01-05", "2026-01-06"], [3.3, 3.3]), "hfq")
    # 000004 模式：新日期首日 41.04 = +1142% 假跳
    with pytest.raises(ScaleMismatchError):
        st.upsert_daily("000001", _daily_frame(["2026-01-07", "2026-01-08"], [41.0, 41.1]), "hfq")
    df = st.get_daily("000001", "hfq")
    assert len(df) == 2  # 未写入


def test_overlap_scale_mismatch_rejected(tmp_path):
    st = _mk_store(tmp_path)
    st.upsert_daily("000001", _daily_frame(["2026-01-05", "2026-01-06"], [100.0, 101.0]), "hfq")
    # 重叠日期但尺度差 5%
    with pytest.raises(ScaleMismatchError):
        st.upsert_daily("000001", _daily_frame(["2026-01-06", "2026-01-07"], [105.0, 106.0]), "hfq")
    assert len(st.get_daily("000001", "hfq")) == 2


def test_real_crash_passes(tmp_path):
    st = _mk_store(tmp_path)
    st.upsert_daily("000001", _daily_frame(["2026-01-05", "2026-01-06"], [10.0, 10.0]), "hfq")
    # 退市整理期真实暴跌 -25%
    st.upsert_daily("000001", _daily_frame(["2026-01-07", "2026-01-08"], [7.5, 7.4]), "hfq")
    assert len(st.get_daily("000001", "hfq")) == 4


def test_non_hfq_skips_guard(tmp_path):
    st = _mk_store(tmp_path)
    st.upsert_daily("000001", _daily_frame(["2026-01-05"], [10.0]), "qfq")
    # 非 hfq：任意尺度都放行
    st.upsert_daily("000001", _daily_frame(["2026-01-06"], [100.0]), "qfq")
    assert len(st.get_daily("000001", "qfq")) == 2


def test_gap_append_no_overlap_boundary_case(tmp_path):
    """存量末日与新首日之间隔 30 天（长假/停牌复牌）：连续性检查退化到宽松。

    停牌数月复牌可合法 ±40%+（A股复盘首日无涨跌幅限制），此时只拒极端假跳（>100%涨）。
    """
    st = _mk_store(tmp_path)
    st.upsert_daily("000001", _daily_frame(["2026-01-05"], [10.0]), "hfq")
    st.upsert_daily("000001", _daily_frame(["2026-03-05"], [13.0]), "hfq")  # +30% 长隔复牌，放行
    assert len(st.get_daily("000001", "hfq")) == 2
