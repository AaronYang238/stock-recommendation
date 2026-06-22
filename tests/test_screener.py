"""筛选器正确性 + 防注入（第 4.2 / 6 节）。"""
from __future__ import annotations

import pandas as pd
import pytest

from aselect.engine.screener import Condition, FilterSpec, screen


def _df():
    return pd.DataFrame({
        "symbol": ["A", "B", "C"],
        "pe": [10, 25, 80],
        "roe": [20, 8, 15],
    })


def test_and_logic():
    spec = FilterSpec(conditions=[Condition("pe", "<", 30), Condition("roe", ">", 10)])
    out = screen(_df(), spec)
    assert out["symbol"].tolist() == ["A"]


def test_or_logic():
    spec = FilterSpec(logic="or",
                      conditions=[Condition("pe", "<", 15), Condition("roe", ">", 18)])
    out = screen(_df(), spec)
    assert set(out["symbol"]) == {"A"}


def test_illegal_operator_rejected():
    with pytest.raises(ValueError):
        Condition("pe", "__import__", 0)  # 非白名单算子 → 拒绝


def test_unknown_field_rejected_with_schema():
    schema = {"pe": "", "roe": ""}
    with pytest.raises(ValueError):
        FilterSpec.from_dict(
            {"conditions": [{"field": "os.system", "op": "<", "value": 1}]},
            field_schema=schema)


def test_missing_column_no_crash():
    spec = FilterSpec(conditions=[Condition("nonexistent", ">", 1)])
    out = screen(_df(), spec)   # 缺列不抛错
    assert len(out) == 0
