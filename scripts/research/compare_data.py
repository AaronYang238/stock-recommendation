#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 tushare(第三方代理) vs baostock 的大唐发电日线收盘价。"""
import os, sys, statistics
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)
sys.path.insert(0, "src")
import baostock as bs
from aselect.datasource.tushare_source import TushareSource

bs.login()
rs = bs.query_history_k_data_plus("sh.601991", "date,close", start_date="2026-07-01", end_date="2026-08-17", frequency="d", adjustflag="3")
bs_df = {}
while rs.error_code == "0" and rs.next():
    r = rs.get_row_data()
    bs_df[r[0]] = float(r[1])
bs.logout()

s = TushareSource()
ts_df = {}
df = s.daily("601991", "", "2026-07-01", "2026-08-17")
for _, row in df.iterrows():
    ts_df[row["date"]] = row["close"]

dates = sorted(set(bs_df) & set(ts_df))
print("共同交易日:", len(dates))
diffs = []
for d in dates:
    dp = abs(bs_df[d] - ts_df[d]) / bs_df[d] * 100
    diffs.append((d, bs_df[d], ts_df[d], dp))
print("%-12s %10s %10s %8s" % ("日期", "baostock", "tushare", "差%"))
for d, b, t, dp in diffs[-10:]:
    print("%-12s %10.3f %10.3f %7.2f%%" % (d, b, t, dp))
dp_list = [x[3] for x in diffs]
print("最大差异%%: %.2f | 平均差异%%: %.2f | 完全一致天数: %d/%d" % (
    max(dp_list), statistics.mean(dp_list),
    sum(1 for x in diffs if x[3] < 0.01), len(diffs)))
