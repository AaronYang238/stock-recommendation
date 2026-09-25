"""研究纪律：ICIR 收缩定权、样本外使用登记、Deflated Sharpe。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.engine.factor_research import FactorICReport
from aselect.engine.stats import deflated_sharpe, expected_max_sharpe
from aselect.runner import _register_oos, ic_category_weights
from aselect.storage.sqlite_store import SQLiteStorage


def _r(name, ic, icir):
    return FactorICReport(name, ic, 0.1, icir, 0.5, 30, 0.0)


def test_weights_shrink_and_cap():
    reps = {"vol_60": _r("vol_60", 0.08, 2.0), "roe": _r("roe", 0.01, 0.1),
            "mom_60": _r("mom_60", 0.01, 0.1), "pe": _r("pe", -0.02, -0.3)}
    w = ic_category_weights(reps)
    assert abs(sum(w.values()) - 1) < 1e-9
    assert w["value"] == 0.0
    assert w["lowvol"] <= 0.4 + 1e-9                 # 不再押单一因子 100%
    assert w["quality"] > 0.2 and w["momentum"] > 0.2


def test_weights_all_nonpositive_equal():
    w = ic_category_weights({"pe": _r("pe", -0.01, -0.1), "roe": _r("roe", -0.02, -0.2)})
    assert w == {"value": 0.5, "quality": 0.5}


def test_oos_ledger_counts_overlapping_uses(tmp_path):
    s = SQLiteStorage(str(tmp_path / "l.sqlite"))
    assert _register_oos(s, "swing", "2025-02-28", None) == 1
    assert _register_oos(s, "swing", "2025-03-31", None) == 2      # 窗口重叠
    assert _register_oos(s, "strategy", "2025-02-28", None) == 1   # 不同回测线分开计
    assert _register_oos(s, "swing", "2026-10-01", None) == 3      # 旧窗口 end=None 覆盖至今


def test_deflated_sharpe_penalizes_many_trials():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.001, 0.01, 500))
    one, many = deflated_sharpe(r, 1), deflated_sharpe(r, 50)
    assert one is not None and many is not None and many < one
    assert expected_max_sharpe(1, 0.01) == 0.0
