#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 top100 候选池内验证资金流因子（未来5日 IC/价差/前后段）。"""
import sqlite3
import pandas as pd

# 读 top100 代码 -> ts_code
def to_ts(code):
    code = code.strip().zfill(6)
    return code + (".SH" if code[0] == "6" else ".SZ")

pool = [to_ts(c) for c in
        open("/root/workspace/daily_stock_analysis/data/global_scan_pool.txt")
        .read().strip().split(",") if c.strip()]
print(f"top100 池: {len(pool)} 只")

conn = sqlite3.connect("data_store/aselect.sqlite")
ph = ",".join(["?"] * len(pool))
df = pd.read_sql(
    f"SELECT date,ts_code,net,pct FROM moneyflow_hist WHERE ts_code IN ({ph})",
    conn, params=pool)
conn.close()
df["date"] = pd.to_datetime(df["date"])
print(f"池内样本: {len(df)} 行, {df['date'].nunique()} 交易日, {df['ts_code'].nunique()} 只有资金流")

net = df.pivot_table(index="date", columns="ts_code", values="net")
ret = df.pivot_table(index="date", columns="ts_code", values="pct")
dates = sorted(net.index)
net = net.loc[dates]; ret = ret.loc[dates]
fwd5 = ret.rolling(5, min_periods=1).sum().shift(-1)

rows = []
for i in range(len(dates) - 6):
    d = dates[i]
    fac = net.loc[d]; fut = fwd5.loc[d]
    m = fac.notna() & fut.notna()
    fac, fut = fac[m], fut[m]
    if len(fac) < 10:
        continue
    thr = fac.quantile(0.7); thrb = fac.quantile(0.3)
    top = fut[fac >= thr].mean(); bot = fut[fac <= thrb].mean()
    ic = fac.rank().corr(fut.rank())
    rows.append({"date": d, "top": top, "bot": bot, "ic": ic})
res = pd.DataFrame(rows)
n = len(res)
if n == 0:
    print("无有效数据"); raise SystemExit
print(f"有效交易日: {n}")
print(f"资金流入top 未来5日: {res['top'].mean():.3f}%")
print(f"资金流出bot 未来5日: {res['bot'].mean():.3f}%")
print(f"价差(top-bot): {res['top'].mean()-res['bot'].mean():.3f}%")
print(f"价差为正占比: {(res['top']>res['bot']).mean()*100:.1f}%")
print(f"Rank IC 均值: {res['ic'].mean():.4f} | IC>0占比: {(res['ic']>0).mean()*100:.1f}%")
half = n // 2
print(f"[前半段] 价差 {res['top'][:half].mean()-res['bot'][:half].mean():.3f}% | IC {res['ic'][:half].mean():.4f}")
print(f"[后半段] 价差 {res['top'][half:].mean()-res['bot'][half:].mean():.3f}% | IC {res['ic'][half:].mean():.4f}")
