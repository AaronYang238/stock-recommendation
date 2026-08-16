"""akshare 列名规范化纯函数（离线可测，不 import akshare、不触网）。"""
from __future__ import annotations

import pandas as pd

from aselect.datasource.akshare_source import (
    normalize_fund_flow, normalize_hist, normalize_news,
)


# ── Task 1: 行情 + 资金流 ────────────────────────────────────
def test_normalize_hist_maps_turnover_and_date():
    raw = pd.DataFrame({
        "日期": ["2026-08-14", "2026-08-15"],
        "开盘": [10.0, 10.5], "收盘": [10.5, 10.8],
        "最高": [10.9, 11.0], "最低": [9.9, 10.4],
        "成交量": [1e6, 1.2e6], "成交额": [1e7, 1.3e7],
        "换手率": [3.2, 4.1], "涨跌幅": [1.0, 2.0],
    })
    out = normalize_hist(raw)
    assert "turnover" in out.columns
    assert list(out["turnover"]) == [3.2, 4.1]
    assert set(["date", "open", "high", "low", "close", "volume", "amount"]).issubset(out.columns)
    assert out["date"].iloc[0] == "2026-08-14"


def test_normalize_hist_without_turnover_ok():
    raw = pd.DataFrame({"日期": ["2026-08-15"], "开盘": [1.0], "收盘": [1.1],
                        "最高": [1.2], "最低": [0.9], "成交量": [1e6], "成交额": [1e6]})
    out = normalize_hist(raw)
    assert "turnover" not in out.columns          # 上游没有则不硬造
    assert out["close"].iloc[0] == 1.1


def test_normalize_fund_flow_maps_net_inflow():
    raw = pd.DataFrame({
        "日期": ["2026-08-14", "2026-08-15"],
        "主力净流入-净额": [1.2e7, -3.4e6],
        "主力净流入-净占比": [5.1, -1.2],
    })
    out = normalize_fund_flow(raw)
    assert list(out.columns) == ["date", "net_inflow"]
    assert out["net_inflow"].iloc[0] == 1.2e7
    assert out["date"].iloc[1] == "2026-08-15"
