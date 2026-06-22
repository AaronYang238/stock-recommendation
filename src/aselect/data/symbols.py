"""股票状态分类与合并（避免幸存者偏差，第 3.1 节）。

把「在市/ST/退市」三类标的统一到同一张 symbols 表：
  - 在市普通股 status='L'
  - 风险警示股 status='ST'（含 *ST、ST、退市整理期保留为 ST）
  - 已退市     status='D'，并带 delist_date

纯函数实现，与具体数据源解耦，便于离线测试。
"""
from __future__ import annotations

import pandas as pd

SYMBOL_COLS = ["symbol", "name", "exchange", "list_date", "delist_date", "status"]

# 状态优先级：退市 > ST > 在市（合并冲突时取更"危险"的状态）
_STATUS_RANK = {"D": 3, "ST": 2, "L": 1}

# 状态代码 → 候选股展示标签
STATUS_LABELS = {"L": "正常", "ST": "ST", "D": "退市"}


def status_label(code: str | None) -> str:
    """状态代码转中文标签，供候选股列表展示。"""
    return STATUS_LABELS.get(code, code or "未知")


def classify_board(symbol: str | None) -> str:
    """按证券代码前缀判定所属板块（荐股时标注，增补需求）。

    主板：沪 600/601/603/605、深 000/001/002/003
    创业板：深 300/301        科创板：沪 688/689
    北交所：4/8 开头及 920    其余 → 其他
    """
    if not symbol:
        return "其他"
    s = str(symbol).zfill(6)
    if s.startswith(("688", "689")):
        return "科创板"
    if s.startswith(("300", "301")):
        return "创业板"
    if s.startswith(("43", "83", "87", "88", "920")) or s[0] in ("4", "8"):
        return "北交所"
    if s.startswith(("60", "000", "001", "002", "003")):
        return "主板"
    return "其他"


def classify_status(name: str | None, *, default: str = "L") -> str:
    """按证券简称判定状态。名称含 ST / *ST → 风险警示股。"""
    if not name:
        return default
    upper = str(name).upper().replace(" ", "")
    if "ST" in upper:          # 覆盖 "ST"、"*ST"、"退市整理期 *ST"
        return "ST"
    return default


def normalize_symbol_frame(df: pd.DataFrame) -> pd.DataFrame:
    """补齐缺失列并按统一列顺序返回，保证可被存储 upsert。"""
    out = df.copy()
    for c in SYMBOL_COLS:
        if c not in out.columns:
            out[c] = None
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    return out[SYMBOL_COLS]


def merge_symbols(*frames: pd.DataFrame) -> pd.DataFrame:
    """合并多个来源（在市 + 退市等），按 symbol 去重。

    冲突时保留状态优先级更高者（D > ST > L），并尽量保留非空的
    list_date / delist_date。
    """
    valid = [normalize_symbol_frame(f) for f in frames if f is not None and not f.empty]
    if not valid:
        return pd.DataFrame(columns=SYMBOL_COLS)

    allrows = pd.concat(valid, ignore_index=True)
    allrows["_rank"] = allrows["status"].map(_STATUS_RANK).fillna(0)
    # 同一 symbol：先按状态优先级降序，使首行为最危险状态
    allrows = allrows.sort_values("_rank", ascending=False)

    merged = []
    for sym, grp in allrows.groupby("symbol", sort=False):
        top = grp.iloc[0].copy()
        # 跨行回填非空字段（如退市行有 delist_date，在市行有 list_date）
        for col in ("name", "exchange", "list_date", "delist_date"):
            non_null = grp[col].dropna()
            if pd.isna(top[col]) and not non_null.empty:
                top[col] = non_null.iloc[0]
        merged.append(top)

    res = pd.DataFrame(merged).drop(columns=["_rank"])
    return res[SYMBOL_COLS].reset_index(drop=True)
