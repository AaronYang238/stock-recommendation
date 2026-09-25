#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, "src")
from aselect.datasource.tushare_source import TushareSource
s = TushareSource()

def check(name, **kw):
    try:
        df = s._call(name, **kw)
        n = 0 if df is None else len(df)
        head = ""
        if df is not None and n:
            row = df.iloc[0]
            head = str(row.to_dict() if hasattr(row, "to_dict") else row)[:100]
        print(f"[{name}] 行数={n} 样例={head}")
    except Exception as e:
        print(f"[{name}] ✗ {str(e)[:120]}")

check("ths_index", exchange="A", type="N")       # 概念板块指数列表
check("concept_detail", id="TS2")                # 概念成分
check("stock_basic", list_status="L")            # 全市场列表
