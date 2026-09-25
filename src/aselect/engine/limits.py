"""A 股涨跌停幅度（纯函数，确定性核心，AI 禁区）。

旧实现全市场固定 9.5%：创业板(2020-08-24 注册制后 ±20%)涨 9.5%~20% 被误判为"锁死"
而被排除（选股偏差），ST(±5%) 跌停又被当成可成交（过度乐观）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CHINEXT_REFORM = pd.Timestamp("2020-08-24")    # 创业板注册制：涨跌幅 10% → 20%
TOLERANCE = 0.005                              # 价格取整容差：判定阈值 = 名义幅度 − 0.5%


def nominal_limit(symbol: str, date, is_st: bool = False) -> float:
    """名义涨跌停幅度。"""
    s = str(symbol).zfill(6)
    if s.startswith(("688", "689")):
        return 0.20
    if s.startswith(("300", "301")):
        return 0.20 if pd.Timestamp(date) >= CHINEXT_REFORM else (0.05 if is_st else 0.10)
    if s.startswith(("4", "8", "920")):
        return 0.30
    return 0.05 if is_st else 0.10


def limit_threshold(symbol: str, date, is_st: bool = False) -> float:
    """"触及涨跌停"的判定阈值（名义幅度 − 容差）。"""
    return nominal_limit(symbol, date, is_st) - TOLERANCE


def limit_series(symbol: str, dates: pd.DatetimeIndex,
                 st_mask: np.ndarray | None = None) -> np.ndarray:
    """逐日判定阈值（向量化）。st_mask：与 dates 对齐的当日是否 ST。"""
    dates = pd.DatetimeIndex(dates)
    st = np.zeros(len(dates), dtype=bool) if st_mask is None else np.asarray(st_mask, bool)
    s = str(symbol).zfill(6)
    if s.startswith(("688", "689")):
        nom = np.full(len(dates), 0.20)
    elif s.startswith(("300", "301")):
        post = dates >= CHINEXT_REFORM
        nom = np.where(post, 0.20, np.where(st, 0.05, 0.10))
    elif s.startswith(("4", "8", "920")):
        nom = np.full(len(dates), 0.30)
    else:
        nom = np.where(st, 0.05, 0.10)
    return nom - TOLERANCE
