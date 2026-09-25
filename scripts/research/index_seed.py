#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 tushare 拉上证指数日线 close 到 index_daily 表。"""
import os, sys, sqlite3
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)
sys.path.insert(0, "src")
from aselect.datasource.tushare_source import TushareSource

ds = TushareSource()
df = ds._call("index_daily", ts_code="000001.SH", start_date="20170101", end_date="20260817")
print("index_daily 行数:", 0 if df is None else len(df))
if df is not None and len(df):
    print("列:", list(df.columns)[:6])
    print("首行:", df.iloc[0].to_dict())
    print("末行:", df.iloc[-1].to_dict())
    conn = sqlite3.connect("data_store/aselect.sqlite")
    cur = conn.cursor()
    rows = [(f"000001.SH", str(r["trade_date"]), float(r["close"])) for _, r in df.iterrows()]
    cur.executemany("INSERT OR REPLACE INTO index_daily(code,date,close) VALUES(?,?,?)", rows)
    conn.commit()
    print("已写入", len(rows), "行")
