"""第 3.1 / 6 节：股票池须含历史 ST/退市标的，避免幸存者偏差。"""
from __future__ import annotations

import pandas as pd

from aselect.data.symbols import classify_status, merge_symbols, normalize_symbol_frame
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.storage.sqlite_store import SQLiteStorage


def test_classify_status():
    assert classify_status("贵州茅台") == "L"
    assert classify_status("*ST国华") == "ST"
    assert classify_status("ST石化") == "ST"
    assert classify_status("退市整理 *ST康得") == "ST"
    assert classify_status(None) == "L"


def test_normalize_pads_symbol():
    df = normalize_symbol_frame(pd.DataFrame({"symbol": [1, 600519], "name": ["a", "b"]}))
    assert df["symbol"].tolist() == ["000001", "600519"]
    # 缺列补齐
    assert set(["exchange", "list_date", "delist_date", "status"]).issubset(df.columns)


def test_merge_prefers_delisted_and_backfills():
    active = pd.DataFrame({
        "symbol": ["000002"], "name": ["万科A"], "exchange": ["SZ"],
        "list_date": ["1991-01-29"], "delist_date": [None], "status": ["L"],
    })
    delisted = pd.DataFrame({
        "symbol": ["002680"], "name": ["退市长生"], "exchange": ["SZ"],
        "list_date": [None], "delist_date": ["2019-11-27"], "status": ["D"],
    })
    merged = merge_symbols(active, delisted)
    assert set(merged["symbol"]) == {"000002", "002680"}
    d = merged.set_index("symbol")
    assert d.loc["002680", "status"] == "D"
    assert d.loc["002680", "delist_date"] == "2019-11-27"


def test_conflict_keeps_more_dangerous_status():
    # 同一代码在两份数据中状态不同 → 取更危险者(D > ST > L)，且回填退市日
    a = pd.DataFrame({"symbol": ["600256"], "name": ["广汇能源"], "status": ["L"]})
    b = pd.DataFrame({"symbol": ["600256"], "name": ["退市广汇"],
                      "delist_date": ["2024-08-22"], "status": ["D"]})
    merged = merge_symbols(a, b).set_index("symbol")
    assert merged.loc["600256", "status"] == "D"
    assert merged.loc["600256", "delist_date"] == "2024-08-22"


def test_universe_includes_delisted_but_filter_excludes(tmp_path):
    store = SQLiteStorage(str(tmp_path / "t.sqlite"))
    store.upsert_symbols(SyntheticSource().list_symbols())

    all_syms = store.get_symbols(include_delisted=True)
    active_syms = store.get_symbols(include_delisted=False)

    statuses = set(all_syms["status"])
    assert {"L", "ST", "D"}.issubset(statuses)        # 三态齐全
    # 回测/全量池含退市标的（避免幸存者偏差）
    assert "002680" in set(all_syms["symbol"])
    # 仅当前在市视图剔除退市，但保留 ST
    assert "002680" not in set(active_syms["symbol"])
    assert "000004" in set(active_syms["symbol"])     # ST 仍在
    store.close()
