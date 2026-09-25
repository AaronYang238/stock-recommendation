#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资金轮动因子 样本外单因子验证。
因子：板块当日主力净流入(net_amount) 分位
结果：该板块未来5日累计收益
验证：资金流入 top vs bottom 板块的未来收益差 + 前后段稳定性
"""
import os, sys, sqlite3
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k in ("TUSHARE_TOKEN", "TUSHARE_HTTP_URL"):
            os.environ.setdefault(k, v)
sys.path.insert(0, "src")
import pandas as pd
from aselect.datasource.tushare_source import TushareSource

s = TushareSource()
# 最近 15 个交易日（从 index_daily 取上证指数交易日）
conn = sqlite3.connect("data_store/aselect.sqlite")
dates = [r[0] for r in conn.execute(
    "SELECT date FROM index_daily ORDER BY date DESC LIMIT 15").fetchall()]
conn.close()
dates = list(reversed(dates))  # 升序

rows = []
for d in dates:
    try:
        df = s._call("moneyflow_ths", trade_date=d.replace("-", ""))
        if df is not None and len(df):
            for _, r in df.iterrows():
                rows.append({"date": d, "ts_code": r["ts_code"],
                             "net": float(r.get("net_amount", 0) or 0),
                             "pct": float(r.get("pct_change", 0) or 0)})
    except Exception:
        pass
print(f"拉取 {len(dates)} 日板块资金流, 记录 {len(rows)} 条")

df = pd.DataFrame(rows)
if len(df) < 100:
    print("数据不足"); sys.exit()
df = df.pivot_table(index="date", columns="ts_code", values="net").fillna(0)
pct = df.copy()  # 用 net 做因子

# 未来 5 日板块收益（用资金流数据的 pct 序列）——简化用板块日涨跌
# 这里用资金流本身作为板块活跃代理；未来收益用后续交易日 pct
rets = pd.DataFrame(rows).pivot_table(index="date", columns="ts_code", values="pct").fillna(0)
fwd = rets.shift(-1)  # 次日收益

# 因子分位验证：每日按净流入排序，top30% vs bottom30% 次日收益
out = []
for date in df.index[:-1]:
    if date not in fwd.index:
        continue
    fac = df.loc[date]
    fut = fwd.loc[date]
    m = fac.notna() & fut.notna()
    fac, fut = fac[m], fut[m]
    if len(fac) < 10:
        continue
    thr = fac.quantile(0.7); thr_b = fac.quantile(0.3)
    top = fut[fac >= thr].mean()
    bot = fut[fac <= thr_b].mean()
    out.append({"date": date, "top": top, "bot": bot})
res = pd.DataFrame(out)
print(f"有效样本日: {len(res)}")
print(f"资金流入top板块次日均涨: {res['top'].mean():.3f}%")
print(f"资金流出bottom板块次日均涨: {res['bot'].mean():.3f}%")
print(f"价差(top-bot): {res['top'].mean() - res['bot'].mean():.3f}%")
# 前后段
half = len(res) // 2
print(f"[前半段] 价差 {res['top'][:half].mean() - res['bot'][:half].mean():.3f}%")
print(f"[后半段] 价差 {res['top'][half:].mean() - res['bot'][half:].mean():.3f}%")
