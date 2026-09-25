#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 250 个交易日个股资金流(moneyflow_ths)存 sqlite。"""
import os, sys, sqlite3
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k in ("TUSHARE_TOKEN", "TUSHARE_HTTP_URL"):
            os.environ.setdefault(k, v)
sys.path.insert(0, "src")
from aselect.datasource.tushare_source import TushareSource

s = TushareSource()
conn = sqlite3.connect("data_store/aselect.sqlite")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS moneyflow_hist(
    date TEXT, ts_code TEXT, net REAL, pct REAL,
    PRIMARY KEY(date, ts_code))""")
dates = [r[0] for r in cur.execute(
    "SELECT date FROM index_daily ORDER BY date DESC LIMIT 250").fetchall()]
dates = list(reversed(dates))
print(f"待拉 {len(dates)} 个交易日")
done = 0
for d in dates:
    try:
        df = s._call("moneyflow_ths", trade_date=d.replace("-", ""))
        if df is not None and len(df):
            rows = [(d, r["ts_code"], float(r.get("net_amount", 0) or 0),
                     float(r.get("pct_change", 0) or 0)) for _, r in df.iterrows()]
            cur.executemany(
                "INSERT OR REPLACE INTO moneyflow_hist(date,ts_code,net,pct) VALUES(?,?,?,?)",
                rows)
            conn.commit()
            done += 1
    except Exception as e:
        print(f"  {d} 失败: {str(e)[:40]}")
print(f"成功 {done}/{len(dates)} 日")
conn.close()
