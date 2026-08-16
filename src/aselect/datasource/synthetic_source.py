"""合成数据源：用确定性随机游走生成行情，供离线演示 / 测试。

不联网，固定 seed → 可复现（对应第 6 节「可复现」与验收：核心在无网络时也能跑通）。
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .base import DataSource


def _stable_seed(symbol: str) -> int:
    """跨进程稳定的 symbol 派生种子（内置 hash() 带盐、跨进程不一致 → 不可复现）。"""
    return zlib.crc32(str(symbol).encode("utf-8")) % 100_000

# 在市普通股：(symbol, name, exchange)
_DEMO = [
    ("600000", "浦发银行", "SH"), ("600519", "贵州茅台", "SH"),
    ("000001", "平安银行", "SZ"), ("000002", "万科A", "SZ"),
    ("300750", "宁德时代", "SZ"), ("688981", "中芯国际", "SH"),
    ("601318", "中国平安", "SH"), ("000858", "五粮液", "SZ"),
    ("002594", "比亚迪", "SZ"), ("600036", "招商银行", "SH"),
]

# 行业（供因子行业中性化用；真实数据需另接行业分类接口）
_INDUSTRY = {
    "600000": "银行", "000001": "银行", "600036": "银行",
    "600519": "白酒", "000858": "白酒",
    "000002": "房地产", "300750": "电池", "688981": "半导体",
    "601318": "保险", "002594": "汽车",
    "000004": "科技", "600145": "石化", "002680": "医药", "600256": "能源",
}


# ST / 退市样本：(symbol, name, exchange, status, delist_date) —
# 用于离线验证「股票池含历史 ST/退市标的、避免幸存者偏差」。
_DEMO_SPECIAL = [
    ("000004", "*ST国华", "SZ", "ST", None),
    ("600145", "ST石化", "SH", "ST", None),
    ("002680", "退市长生", "SZ", "D", "2019-11-27"),
    ("600256", "退市广汇", "SH", "D", "2024-08-22"),
]


class SyntheticSource(DataSource):
    name = "synthetic"

    def __init__(self, days: int = 750, seed: int = 42, fund_quarters: int = 12):
        self.days = days
        self.seed = seed
        self.fund_quarters = fund_quarters

    def list_symbols(self) -> pd.DataFrame:
        rows = [{"symbol": s, "name": n, "exchange": e,
                 "list_date": "2015-01-01", "delist_date": None, "status": "L"}
                for s, n, e in _DEMO]
        rows += [{"symbol": s, "name": n, "exchange": e,
                  "list_date": "2010-01-01", "delist_date": dd, "status": st}
                 for s, n, e, st, dd in _DEMO_SPECIAL]
        return pd.DataFrame(rows)

    def _all_symbols(self) -> list[str]:
        return ([s for s, *_ in _DEMO] + [s for s, *_ in _DEMO_SPECIAL])

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        # 每个 symbol 用独立但确定的种子
        rng = np.random.default_rng(self.seed + (_stable_seed(symbol) % 10_000))
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=self.days)
        ret = rng.normal(0.0004, 0.018, len(dates))
        base = 10 + (_stable_seed(symbol) % 90)
        close = base * np.exp(np.cumsum(ret))
        high = close * (1 + np.abs(rng.normal(0, 0.01, len(dates))))
        low = close * (1 - np.abs(rng.normal(0, 0.01, len(dates))))
        open_ = (high + low) / 2
        volume = rng.integers(5e5, 5e7, len(dates)).astype(float)
        turnover = np.abs(rng.normal(3.0, 1.5, len(dates))).round(2)      # 换手率 %
        net_inflow = (rng.normal(0, 1, len(dates)) * volume * close * 0.01).round(0)  # 资金净流入(元)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": open_.round(2), "high": high.round(2),
            "low": low.round(2), "close": close.round(2),
            "volume": volume, "amount": (volume * close).round(0),
            "turnover": turnover, "net_inflow": net_inflow,
        })
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        return df.reset_index(drop=True)

    def fundamentals(self, symbols=None) -> pd.DataFrame:
        """逐季度历史快照（真实披露日 ann_date，严格 PIT，防前视）。

        每期 date=报告期(季度末)、ann_date=披露日(报告期+~1月且≤今日)；行业跨期恒定；
        财务指标按 (symbol, 期序) 确定性派生。多期入库后 PIT 历史回测才拿得到基本面/行业。
        """
        syms = symbols or self._all_symbols()
        today = pd.Timestamp.today().normalize()
        q_ends = pd.date_range(end=today, periods=self.fund_quarters + 4, freq="QE")
        rows = []
        for s in syms:
            base = _stable_seed(s) % 10_000
            for qi, rep in enumerate(q_ends):
                ann = rep + pd.Timedelta(days=35)      # 披露 lag ~1 月
                if ann > today:                        # 不披露未来（无前视）
                    continue
                r = np.random.default_rng(self.seed + base + qi)
                rows.append({
                    "symbol": s,
                    "date": rep.strftime("%Y-%m-%d"),          # 报告期
                    "ann_date": ann.strftime("%Y-%m-%d"),      # 披露日
                    "industry": _INDUSTRY.get(s, "其他"),       # 结构性元数据，跨期恒定
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

    def news(self, symbol: str) -> list[dict]:
        return []       # 离线合成源无新闻；情绪因子退化为中性（核心照跑）
