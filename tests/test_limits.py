"""分板块涨跌停：主板 10% / 创业板注册制后 20% / 科创 20% / 北交所 30% / ST 5%。"""
from __future__ import annotations

import pandas as pd

from aselect.engine.limits import limit_series, nominal_limit
from aselect.runner import _tradable


def test_nominal_limits():
    assert nominal_limit("600000", "2024-01-02") == 0.10
    assert nominal_limit("600000", "2024-01-02", is_st=True) == 0.05
    assert nominal_limit("300750", "2020-08-21") == 0.10
    assert nominal_limit("300750", "2020-08-24") == 0.20
    assert nominal_limit("300750", "2024-01-02", is_st=True) == 0.20
    assert nominal_limit("688981", "2024-01-02") == 0.20
    assert nominal_limit("830799", "2024-01-02") == 0.30


def test_limit_series_switches_on_reform_date():
    d = pd.to_datetime(["2020-08-21", "2020-08-24"])
    s = limit_series("300001", d)
    assert s[0] < 0.1 < s[1]


def test_chinext_12pct_move_is_tradable():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    panel = pd.DataFrame({"300001": [10.0, 11.2], "600001": [10.0, 11.0]}, index=idx)
    tr = _tradable(panel, idx[1])
    assert "300001" in tr            # +12% 未触及创业板 20% 涨停
    assert "600001" not in tr        # 主板 +10% 涨停锁死
