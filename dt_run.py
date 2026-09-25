#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑大唐发电(601991)回测验证（baostock 主源）。"""
import sys
from pathlib import Path
import pandas as pd
import baostock as bs

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from aselect.config import load_config
from aselect.engine.backtest import run_ma_backtest


def fetch(sym, start="2022-01-01"):
    lg = bs.login()
    rs = bs.query_history_k_data_plus(
        sym, "date,open,high,low,close,volume",
        start_date=start, end_date="2026-08-17", frequency="d", adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c])
    return df


cfg = load_config()
print(f"数据源 primary={cfg.datasource['primary']}, adjust={cfg.datasource['adjust']}")
df = fetch("sh.601991")
print(f"大唐日线: {len(df)} 行 ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")
res = run_ma_backtest(df, cfg.backtest)
print("--- 回测结果 (MA5/20 交叉) ---")
print(f"总收益 {res.total_return:.2%} | 年化 {res.annual_return:.2%}")
print(f"夏普 {res.sharpe:.3f} | 最大回撤 {res.max_drawdown:.2%} | 交易 {res.trades} 次")
print(f"引擎 {res.engine}")
