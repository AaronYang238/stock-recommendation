"""方案B：验证「低波+高ROE+低PE」复合因子的样本外 IC。

复用系统因子管线（winsorize->zscore->中性化->再zscore），把三个字段合成为一个
复合因子 score，然后按 walk-forward 每月调仓算 Rank IC，分训练段/样本外报告。
确定性核心，无 LLM，走铁律2(PIT)/3(walk-forward)/4(IC)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pandas as pd
import numpy as np

from aselect.engine.factors import process_factor
from aselect.engine.factor_backtest import _rank_ic, _ret
from aselect.data.pipeline import build_universe, build_cross_section
from aselect.storage.sqlite_store import SQLiteStorage
from aselect.config import load_config
from aselect.runner import _rebalance_dates

START = "2021-08-19"
END = "2026-08-18"
FREQ = "M"
OOS = 0.7

def main():
    cfg = load_config()
    store = SQLiteStorage(cfg.storage["path"])

    universe = build_universe(store, include_delisted=True)
    adjust = cfg.datasource.get("adjust", "hfq")

    # 构造月级调仓日序列
    import sqlite3
    c = sqlite3.connect(cfg.storage["path"])
    dates = pd.DatetimeIndex(sorted({r[0] for r in c.execute(
        "SELECT DISTINCT date FROM daily WHERE date>=? AND date<=?", (START, END))}))
    schedule = _rebalance_dates(dates, FREQ)
    schedule = [pd.Timestamp(d) for d in schedule]
    if len(schedule) < 4:
        print("样本太短"); return

    k = max(1, int(len(schedule) * OOS))
    split = pd.Timestamp(schedule[k])
    print(f"调仓日 {len(schedule)} 个 | 切分日 {split.date()}")

    # panel：后复权收盘价（全市场）
    panel_rows = {}
    for sym in universe:
        daily = store.get_daily(sym, adjust, end=END)
        if daily.empty: continue
        panel_rows[sym] = daily.set_index("date")["close"]
    panel = pd.DataFrame(panel_rows).sort_index()
    panel = panel.loc[(panel.index >= pd.Timestamp(START)) & (panel.index <= pd.Timestamp(END))]
    print(f"panel: {panel.shape}")

    def composite_score(cross):
        """合成「低波+高ROE+低PE」复合因子，返回 symbol->score 的 Series。"""
        ind = cross["industry"] if "industry" in cross.columns else None
        size = cross["total_mv"] if "total_mv" in cross.columns else None
        cols = []
        if "vol_60" in cross.columns:
            cols.append(process_factor(cross["vol_60"], True, ind, size))   # 低波=值小好->取负
        if "roe" in cross.columns:
            cols.append(process_factor(cross["roe"], False, ind, size))
        if "pe" in cross.columns:
            cols.append(process_factor(cross["pe"], True, ind, size))       # 低PE=值小好->取负
        if not cols:
            return pd.Series(dtype=float)
        comp = pd.concat(cols, axis=1).mean(axis=1)   # 等权合成
        return pd.Series(comp.values, index=cross["symbol"].values).dropna()

    # 逐调仓日构建截面 + 打分
    scores = {}
    for t in schedule:
        as_of = t.strftime("%Y-%m-%d")
        cross = build_cross_section(store, cfg, symbols=universe, as_of=as_of)
        if cross.empty: continue
        s = composite_score(cross)
        if len(s): scores[t] = s
    print(f"有打分的调仓日: {len(scores)}/{len(schedule)}")

    # 算 IC：训练段 & 样本外
    def ic_series(ts_list):
        ics = []
        for i in range(len(ts_list)-1):
            t0, t1 = ts_list[i], ts_list[i+1]
            s = scores.get(t0)
            if s is None: continue
            ic = _rank_ic(s, panel, t0, t1)
            if ic is not None: ics.append(ic)
        return np.array(ics)

    train_dates = [t for t in schedule if t < split]
    oos_dates   = [t for t in schedule if t >= split]
    tr_ic = ic_series(train_dates)
    oo_ic = ic_series(oos_dates)

    print("\n===== 复合因子「低波+高ROE+低PE」样本外 IC 验证 =====")
    def fmt(a):
        if len(a)==0: return "无样本"
        return (f"IC均值 {a.mean():.4f} | ICIR {a.mean()/a.std():.3f} "
                f"| IC>0率 {(a>0).mean():.0%} | 期数 {len(a)}")
    print(f"[训练段] {fmt(tr_ic)}")
    print(f"[样本外] {fmt(oo_ic)}")
    print(f"\n判定：样本外 IC 均值 ≥0.03 视为有 alpha；<0 为无/负")
    store.close()

if __name__ == "__main__":
    main()
