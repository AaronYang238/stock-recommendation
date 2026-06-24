"""baostock 适配器：纯工具函数（代码转换/比率换算）可离线测试；
网络部分需 baostock + 联网，故仅验证不依赖网络的逻辑。"""
from __future__ import annotations

import pytest

from aselect.datasource.baostock_source import (
    BaostockSource, _pct, _recent_quarters,
)


@pytest.mark.parametrize("symbol,expected", [
    ("600000", "sh.600000"), ("688981", "sh.688981"), ("900001", "sh.900001"),
    ("000001", "sz.000001"), ("300750", "sz.300750"), ("002594", "sz.002594"),
    ("1", "sz.000001"),          # 补零
    ("830799", None),            # 北交所不覆盖
])
def test_to_bs_code(symbol, expected):
    assert BaostockSource._to_bs(symbol) == expected


def test_from_bs_code():
    assert BaostockSource._from_bs("sh.600000") == "600000"
    assert BaostockSource._from_bs("sz.000001") == "000001"


def test_pct_converts_ratio_to_percent():
    assert _pct("0.1523") == 15.23      # 小数 → 百分数
    assert _pct(None) is None
    assert _pct("") is None


def test_recent_quarters_descending():
    qs = _recent_quarters(6)
    assert len(qs) == 6
    # 由近及远、严格递减
    keys = [y * 4 + q for y, q in qs]
    assert keys == sorted(keys, reverse=True)


def test_factory_falls_back_when_baostock_missing():
    """primary=baostock 但未装 baostock → 工厂优雅回退（不崩）。"""
    from aselect.config import load_config
    from aselect.datasource import get_datasource
    cfg = load_config()
    object.__setattr__(cfg, "datasource",
                       {"primary": "baostock", "fallback": "synthetic"})
    ds = get_datasource(cfg)            # baostock 未装 → 回退 synthetic
    assert ds.name in ("baostock", "synthetic")
