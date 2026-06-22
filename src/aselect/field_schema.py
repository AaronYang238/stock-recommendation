"""筛选可用字段 schema（供 screener 校验 & AI nl_to_filter 约束）。

集中定义，既是文档也是防注入白名单。
"""
from __future__ import annotations

FIELD_SCHEMA: dict[str, str] = {
    "pe": "市盈率(动态)",
    "pb": "市净率",
    "ps": "市销率",
    "roe": "净资产收益率(%)",
    "roa": "总资产收益率(%)",
    "revenue_yoy": "营收同比(%)",
    "profit_yoy": "净利润同比(%)",
    "gross_margin": "毛利率(%)",
    "debt_ratio": "资产负债率(%)",
    "total_mv": "总市值(元)",
    "close": "最新收盘价",
    "ma60": "60日均线",
    "mom_60": "60日动量",
    "vol_60": "60日波动率",
    "sentiment": "AI情绪分[-1,1]",
    "total_score": "多因子综合得分",
}
