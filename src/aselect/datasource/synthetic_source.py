"""合成数据源：用确定性随机游走生成行情，供离线演示 / 测试。

不联网，固定 seed → 可复现（对应第 6 节「可复现」与验收：核心在无网络时也能跑通）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import DataSource

_DEMO = [
    ("600000", "浦发银行", "SH"), ("600519", "贵州茅台", "SH"),
    ("000001", "平安银行", "SZ"), ("000002", "万科A", "SZ"),
    ("300750", "宁德时代", "SZ"), ("688981", "中芯国际", "SH"),
    ("601318", "中国平安", "SH"), ("000858", "五粮液", "SZ"),
    ("002594", "比亚迪", "SZ"), ("600036", "招商银行", "SH"),
]


class SyntheticSource(DataSource):
    name = "synthetic"

    def __init__(self, days: int = 750, seed: int = 42):
        self.days = days
        self.seed = seed

    def list_symbols(self) -> pd.DataFrame:
        rows = [{"symbol": s, "name": n, "exchange": e,
                 "list_date": "2015-01-01", "delist_date": None, "status": "L"}
                for s, n, e in _DEMO]
        return pd.DataFrame(rows)

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        # 每个 symbol 用独立但确定的种子
        rng = np.random.default_rng(self.seed + (hash(symbol) % 10_000))
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=self.days)
        ret = rng.normal(0.0004, 0.018, len(dates))
        base = 10 + (hash(symbol) % 90)
        close = base * np.exp(np.cumsum(ret))
        high = close * (1 + np.abs(rng.normal(0, 0.01, len(dates))))
        low = close * (1 - np.abs(rng.normal(0, 0.01, len(dates))))
        open_ = (high + low) / 2
        volume = rng.integers(5e5, 5e7, len(dates)).astype(float)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": open_.round(2), "high": high.round(2),
            "low": low.round(2), "close": close.round(2),
            "volume": volume, "amount": (volume * close).round(0),
        })
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        return df.reset_index(drop=True)

    def fundamentals(self, symbols=None) -> pd.DataFrame:
        syms = symbols or [s for s, _, _ in _DEMO]
        rng = np.random.default_rng(self.seed)
        rows = []
        for s in syms:
            r = np.random.default_rng(self.seed + (hash(s) % 10_000))
            rows.append({
                "symbol": s, "date": pd.Timestamp.today().strftime("%Y-%m-%d"),
                "pe": round(float(r.uniform(5, 60)), 2),
                "pb": round(float(r.uniform(0.5, 12)), 2),
                "ps": round(float(r.uniform(0.5, 20)), 2),
                "roe": round(float(r.uniform(-5, 35)), 2),
                "roa": round(float(r.uniform(-2, 18)), 2),
                "revenue_yoy": round(float(r.uniform(-20, 60)), 2),
                "profit_yoy": round(float(r.uniform(-40, 80)), 2),
                "gross_margin": round(float(r.uniform(5, 70)), 2),
                "debt_ratio": round(float(r.uniform(10, 80)), 2),
                "total_mv": round(float(r.uniform(50, 5000)) * 1e8, 0),
            })
        return pd.DataFrame(rows)

    def index_daily(self, index_code, start=None, end=None) -> pd.DataFrame:
        return self.daily(f"IDX{index_code}", "none", start, end)
