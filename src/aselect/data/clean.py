"""清洗标准化（强制，否则上层全是假信号，第 3.1 节）。"""
from __future__ import annotations

import pandas as pd

_OHLC = ["open", "high", "low", "close"]


def clean_daily(df: pd.DataFrame) -> pd.DataFrame:
    """去重、排序、剔除明显异常与停牌空行。"""
    if df.empty:
        return df
    out = df.drop_duplicates(subset=["date"]).sort_values("date")
    # 停牌：成交量为 0 或价格缺失 → 丢弃该日（不参与指标，避免假信号）
    if "volume" in out.columns:
        out = out[out["volume"].fillna(0) > 0]
    out = out.dropna(subset=[c for c in _OHLC if c in out.columns])
    # 非正价格视为异常
    for c in _OHLC:
        if c in out.columns:
            out = out[out[c] > 0]
    return out.reset_index(drop=True)


def is_limit_move(df: pd.DataFrame, pct: float = 0.099) -> pd.Series:
    """标注疑似涨跌停日（回测撮合时这些日通常无法成交）。
    主板约 ±10%、创业板/科创板 ±20%，此处用阈值近似。"""
    chg = df["close"].pct_change()
    return chg.abs() >= pct
