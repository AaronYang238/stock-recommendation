#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 top100 候选股票日线到 store（tushare 数据源），供 swing 回测。"""
import os, sys
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, "src")
from aselect.config import load_config
from aselect.storage import get_storage
from aselect.datasource.tushare_source import TushareSource
from aselect.data.pipeline import update_daily, update_index

cfg = load_config()
store = get_storage(cfg)
ds = TushareSource()

codes = open("/root/workspace/daily_stock_analysis/data/global_scan_pool.txt").read().strip().split(",")
codes = [c.strip() for c in codes if c.strip()]
print(f"top100 代码数: {len(codes)}")

n = update_daily(ds, store, codes, cfg.datasource.get("adjust", "hfq"))
print(f"更新 {len(codes)} 只, 写入日线 {n} 行")

for idx in ["000001.SH", "000300.SH"]:
    try:
        update_index(ds, store, idx)
        print(f"更新指数 {idx}")
    except Exception as e:
        print(f"指数 {idx} 失败: {e}")
print("done")
