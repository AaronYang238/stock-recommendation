#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MA5>MA10>MA20 多头排列 + 价格>MA60 趋势过滤 的策略回测（top100 池，向量化）。

规则：
- 持有条件 = MA5>MA10>MA20（多头排列）且 close>MA60（中期趋势多头）
- 等权持有满足条件的股票；月频调仓
- 计入佣金(0.03%)+印花税(卖出0.05%)+滑点(0.1%)；近似 T+1
"""
import sqlite3, math
from datetime import datetime
import pandas as pd

DB = "data_store/aselect.sqlite"
POOL = "/root/workspace/daily_stock_analysis/data/global_scan_pool.txt"
COST_BUY, COST_SELL = 0.0003, 0.0005 + 0.001   # 佣金 / 印花税+滑点

# 读取股票池
pool = [c.strip() for c in open(POOL).read().strip().split(",") if c.strip()]
conn = sqlite3.connect(DB)

# 读日线（后复权）
rows = []
for sym in pool:
    for (d, c) in conn.execute(
            "SELECT date, close FROM daily WHERE symbol=? AND adjust='hfq' ORDER BY date",
            (sym,)):
        rows.append((sym, d, float(c)))
df = pd.DataFrame(rows, columns=["symbol", "date", "close"])
df["date"] = pd.to_datetime(df["date"])

# 计算 MA + 信号（groupby transform 保留 symbol 列）
df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
g = df.groupby("symbol")["close"]
df["ma5"] = g.transform(lambda x: x.rolling(5).mean())
df["ma10"] = g.transform(lambda x: x.rolling(10).mean())
df["ma20"] = g.transform(lambda x: x.rolling(20).mean())
df["ma60"] = g.transform(lambda x: x.rolling(60).mean())
df["hold"] = ((df.ma5 > df.ma10) & (df.ma10 > df.ma20) & (df.close > df.ma60)).astype(int)

# 组合回测（日频，等权持有）
pivot = df.pivot(index="date", columns="symbol", values="close")
hold = df.pivot(index="date", columns="symbol", values="hold")
# 月频调仓：只在每月首日更新持仓
monthly = hold.resample("MS").first().reindex(hold.index).ffill()

ret = pivot.pct_change().fillna(0)
combo = (ret * monthly.shift(1)).sum(axis=1)  # 当日收益 = 持仓 * 个股日收益（等权已隐含在 hold 均值）
n_held = monthly.shift(1).sum(axis=1).replace(0, 1)
combo = combo / n_held

# 换手成本：每月调仓时，持仓变化比例 * 摩擦
chg = (monthly.shift(1).fillna(0) - monthly.shift(1).shift(1).fillna(0)).abs().sum(axis=1)
turn_cost = (chg * (COST_BUY + COST_SELL)) / n_held
combo = combo - turn_cost.fillna(0)

# 指标
nav = (1 + combo).cumprod()
total = nav.iloc[-1] - 1
years = (nav.index[-1] - nav.index[0]).days / 365.25
annual = (1 + total) ** (1 / years) - 1
dd = (nav / nav.cummax() - 1).min()
rf = 0.02
excess = combo - rf / 252
sharpe = excess.mean() / excess.std() * math.sqrt(252) if excess.std() > 0 else 0
wins = (combo > 0).mean()

print(f"[MA5>MA10>MA20 + close>MA60] top{len(pool)}池 · 月频")
print(f"  区间 {nav.index[0].date()} ~ {nav.index[-1].date()} | {years:.1f}年")
print(f"  总收益 {total:.2%} | 年化 {annual:.2%} | 夏普 {sharpe:.2f} | 最大回撤 {dd:.2%}")
print(f"  日胜率 {wins:.0%} | 平均持仓数 {monthly.mean(axis=1).mean():.1f} 只")
