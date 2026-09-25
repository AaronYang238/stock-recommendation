#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 tushare 第三方代理关键接口是否真实可用。"""
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
            head = str(df.iloc[0].to_dict())[:120]
        print(f"[{name}] 行数={n} 样例={head}")
    except Exception as e:
        print(f"[{name}] ✗ 失败: {str(e)[:150]}")

print("=== 验证关键接口 ===")
check("fina_indicator", ts_code="601991.SH", period="20251231")   # 财务指标
check("moneyflow", ts_code="601991.SH", start_date="20260810", end_date="20260814")  # 资金流向
check("top_list", trade_date="20260813")   # 龙虎榜（大唐 8/13 涨停，应上榜）
check("index_member", index_code="000001.SH")  # 沪深300成分
