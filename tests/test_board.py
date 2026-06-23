"""板块分类（荐股标注，增补需求）+ 术语词典覆盖。"""
from __future__ import annotations

import pytest

import pandas as pd

from aselect.data.symbols import attach_industry, classify_board, status_label
from aselect.glossary import describe


@pytest.mark.parametrize("symbol,expected", [
    ("600519", "主板"), ("601318", "主板"), ("603288", "主板"),
    ("000001", "主板"), ("002594", "主板"), ("001979", "主板"),
    ("300750", "创业板"), ("301236", "创业板"),
    ("688981", "科创板"), ("689009", "科创板"),
    ("830799", "北交所"), ("430139", "北交所"), ("920029", "北交所"),
    (None, "其他"),
])
def test_classify_board(symbol, expected):
    assert classify_board(symbol) == expected


def test_board_pads_short_code():
    assert classify_board("1") == "主板"      # 000001 → 主板


@pytest.mark.parametrize("code,expected", [
    ("L", "正常"), ("ST", "ST"), ("D", "退市"), (None, "未知"),
])
def test_status_label(code, expected):
    assert status_label(code) == expected


def test_attach_industry_fills_and_respects_existing():
    df = pd.DataFrame({"symbol": ["600519", "000001"], "industry": ["白酒", None]})
    out = attach_industry(df, {"000001": "银行"})   # 映射补 000001，600519 保留白酒
    assert out.set_index("symbol")["industry"].to_dict() == {"600519": "白酒", "000001": "银行"}


def test_attach_industry_zfills_symbol():
    df = pd.DataFrame({"symbol": ["1"]})
    out = attach_industry(df, {"000001": "银行"})
    assert out["industry"].iloc[0] == "银行"


def test_attach_industry_empty_mapping_noop():
    df = pd.DataFrame({"symbol": ["600519"], "industry": ["白酒"]})
    assert attach_industry(df, {}).equals(df)
    assert attach_industry(df, None).equals(df)


def test_glossary_has_board_status_and_core_terms():
    for t in ("主板", "创业板", "科创板", "北交所", "状态", "ST", "退市",
              "PE", "ROE", "夏普比率", "最大回撤", "后复权", "T+1"):
        assert describe(t), f"术语词典缺少: {t}"
    assert describe("不存在的词") is None
