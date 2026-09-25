#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 tushare 板块资金流接口。"""
import os, sys
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k in ("TUSHARE_TOKEN", "TUSHARE_HTTP_URL"):
            os.environ.setdefault(k, v)
sys.path.insert(0, "src")
from aselect.datasource.tushare_source import TushareSource
s = TushareSource()
for api in ["moneyflow_ths", "moneyflow_ind", "moneyflow", "ths_index"]:
    try:
        df = s._call(api)
        if df is None:
            print(f"{api}: None/空")
            continue
        import pandas as pd
        if isinstance(df, pd.DataFrame) and len(df) > 0:
            print(f"{api}: OK {len(df)} 行 | 列: {list(df.columns)[:6]}")
        else:
            print(f"{api}: 空")
    except Exception as e:
        print(f"{api}: 失败 ({str(e)[:60]})")
