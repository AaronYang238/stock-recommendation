#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""近1.5年(2025-01起) swing 对比：有/无资金流因子。"""
import os, sys
for line in open(".env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        if k in ("TUSHARE_TOKEN", "TUSHARE_HTTP_URL"):
            os.environ.setdefault(k, v)
sys.path.insert(0, "src")
from aselect.config import load_config
from aselect.storage import get_storage
from aselect.runner import run_factor_research, ic_category_weights, run_swing_backtest

cfg = load_config()
store = get_storage(cfg)
START = "2025-01-01"

reports = run_factor_research(store, cfg, freq="M", start=START)
print("因子IC:", {k: round(v.ic_mean, 3) for k, v in reports.items()})
weights_with = ic_category_weights(reports)
weights_without = {c: w for c, w in weights_with.items() if c != "moneyflow"}
print("权重(含资金流):", {k: round(v, 3) for k, v in weights_with.items()})
print("权重(无资金流):", {k: round(v, 3) for k, v in weights_without.items()})

for tag, w in [("有资金流", weights_with), ("无资金流", weights_without)]:
    try:
        rep = run_swing_backtest(store, cfg, freq="M", top_n=10, weights=w, start=START)
        print(f"[{tag}] 交易{rep.trades} | 总收益{rep.total_return:.2%} | "
              f"夏普{rep.sharpe:.3f} | 回撤{rep.max_drawdown:.2%} | "
              f"期望{rep.expectancy:.4f} | 盈亏比{rep.profit_loss_ratio:.3f}")
    except Exception as e:
        print(f"[{tag}] 失败: {str(e)[:80]}")
