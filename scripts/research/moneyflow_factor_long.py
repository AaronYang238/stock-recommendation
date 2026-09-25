#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长样本(约1年)资金流因子验证。
因子=个股当日主力净流入(net)；结果=未来5日累计收益
输出：整体/前后段 top-bot 价差、价差胜率、Rank IC
"""
import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect("data_store/aselect.sqlite")
df = pd.read_sql("SELECT date,ts_code,net,pct FROM moneyflow_hist", conn)
conn.close()
df["date"] = pd.to_datetime(df["date"])
print(f"样本: {len(df)} 行, {df['date'].nunique()} 个交易日, {df['ts_code'].nunique()} 只股票")

# 因子与收益 pivot
net = df.pivot_table(index="date", columns="ts_code", values="net")
ret = df.pivot_table(index="date", columns="ts_code", values="pct")
dates = sorted(net.index)
net = net.loc[dates]; ret = ret.loc[dates]

# 未来5日累计收益（次日~后5日）
fwd5 = ret.rolling(5, min_periods=1).sum().shift(-1)

rows = []
for i in range(len(dates) - 6):
    d = dates[i]
    fac = net.loc[d]; fut = fwd5.loc[d]
    m = fac.notna() & fut.notna()
    fac, fut = fac[m], fut[m]
    if len(fac) < 30:
        continue
    thr = fac.quantile(0.7); thrb = fac.quantile(0.3)
    top = fut[fac >= thr].mean()
    bot = fut[fac <= thrb].mean()
    # rank IC
    ic = fac.rank().corr(fut.rank())
    rows.append({"date": d, "top": top, "bot": bot, "ic": ic})

res = pd.DataFrame(rows)
n = len(res)
print(f"有效交易日: {n}")
print(f"资金流入top 未来5日: {res['top'].mean():.3f}%")
print(f"资金流出bot 未来5日: {res['bot'].mean():.3f}%")
print(f"价差(top-bot): {res['top'].mean()-res['bot'].mean():.3f}%")
print(f"价差为正天数占比: {(res['top']>res['bot']).mean()*100:.1f}%")
print(f"Rank IC 均值: {res['ic'].mean():.4f} | IC>0占比: {(res['ic']>0).mean()*100:.1f}%")
# 前后段
half = n // 2
print(f"[前半段] 价差 {res['top'][:half].mean()-res['bot'][:half].mean():.3f}% | IC {res['ic'][:half].mean():.4f}")
print(f"[后半段] 价差 {res['top'][half:].mean()-res['bot'][half:].mean():.3f}% | IC {res['ic'][half:].mean():.4f}")
