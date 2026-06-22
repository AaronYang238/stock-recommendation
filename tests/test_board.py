"""板块分类（荐股标注，增补需求）+ 术语词典覆盖。"""
from __future__ import annotations

import pytest

from aselect.data.symbols import classify_board, status_label
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


def test_glossary_has_board_status_and_core_terms():
    for t in ("主板", "创业板", "科创板", "北交所", "状态", "ST", "退市",
              "PE", "ROE", "夏普比率", "最大回撤", "后复权", "T+1"):
        assert describe(t), f"术语词典缺少: {t}"
    assert describe("不存在的词") is None
