"""严谨的 walk-forward 滚动权重验证（回答：改权重能不能救选股 alpha）。

与现有 `run_validated_strategy`（一次性固定权重）不同：
- 每个调仓日 t_i，只用【截至 t_i 之前已实现】的历史调仓期拟合各因子权重
  （ic_category_weights），无前视；
- 用该滚动权重在 t_i 打分 → 选 top_n → 等权，交给 simulate 模拟净值；
- 报样本外的 IC / 夏普 / 期望 / 回撤 / 盈亏比。

若滚动权重下样本外 IC 仍 ≈0 或为负，则证明「改权重救不了 alpha」。
确定性核心，无 LLM，走铁律2(PIT)/3(walk-forward)/4(IC)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pandas as pd
import numpy as np

from aselect.engine.factors import DEFAULT_FACTORS, process_factor
from aselect.engine.factor_research import summarize
from aselect.engine.factor_backtest import simulate
from aselect.data.pipeline import build_universe, build_cross_section
from aselect.storage.sqlite_store import SQLiteStorage
from aselect.config import load_config
from aselect.runner import _price_panel, _rebalance_dates, _tradable, \
    ic_category_weights, _load_benchmark

START = os.environ.get("WF_START", "2021-08-19")
END = os.environ.get("WF_END", "2026-08-18")
FREQ = "M"
TOP_N = int(os.environ.get("WF_TOP", "20"))
OOS = float(os.environ.get("WF_OOS", "0.7"))


def main():
    cfg = load_config()
    store = SQLiteStorage(cfg.storage["path"])
    universe = build_universe(store, include_delisted=True)
    adjust = cfg.datasource.get("adjust", "hfq")

    panel = _price_panel(store, universe, adjust, START, END)
    if panel.shape[0] < 4:
        print("panel 太小"); return
    schedule = list(_rebalance_dates(panel.index, FREQ))
    schedule = [pd.Timestamp(t) for t in schedule]
    print(f"调仓日 {len(schedule)} | panel {panel.shape}")

    # 一次性构建所有调仓日的 PIT 截面
    cross_by_t = {}
    for t in schedule:
        cross = build_cross_section(store, cfg, symbols=universe,
                                    as_of=t.strftime("%Y-%m-%d"))
        if not cross.empty:
            cross_by_t[t] = cross
    print(f"有截面调仓日: {len(cross_by_t)}/{len(schedule)}")

    # 逐因子逐期算处理值（sbd[factor][t] = symbol->score）
    ind_col = "industry" if "industry" in next(iter(cross_by_t.values())).columns else None
    size_col = "total_mv" if "total_mv" in next(iter(cross_by_t.values())).columns else None
    sbd_all = {}   # factor name -> {t: Series}
    for cat, defs in DEFAULT_FACTORS.items():
        for d in defs:
            sbd = {}
            for t, cross in cross_by_t.items():
                if d.field not in cross.columns:
                    continue
                ind = cross[ind_col] if (d.industry_neutral and ind_col) else None
                size = cross[size_col] if size_col else None
                proc = process_factor(cross[d.field], d.ascending, ind, size)
                sbd[t] = pd.Series(proc.values, index=cross["symbol"].values)
            if sbd:
                sbd_all[d.name] = sbd
    print("因子:", list(sbd_all.keys()))

    # walk-forward：对每个调仓日 t_i，用截至 t_{i-1} 的历史拟合权重（无前视）
    selections, scores = {}, {}
    dates = schedule
    for i, t in enumerate(dates):
        # 拟合权重只用 schedule[0..i-1]（这些期收益已实现）
        hist_dates = dates[:i]
        if len(hist_dates) >= 3:
            reports = {}
            for name, sbd in sbd_all.items():
                hist_sbd = {d: sbd[d] for d in hist_dates if d in sbd}
                if hist_sbd:
                    reports[name] = summarize(name, hist_sbd, panel, hist_dates)
            w = ic_category_weights(reports)
        else:
            w = None   # 历史不足 → 等权

        if t not in cross_by_t:
            selections[t], scores[t] = {}, pd.Series(dtype=float)
            continue
        from aselect.engine import score_factors
        scored = score_factors(cross_by_t[t], weights=w)
        tradable = _tradable(panel, t, 0.095)
        cand = scored[scored["symbol"].isin(tradable)]
        sel = cand.head(TOP_N)
        selections[t] = {s: 1.0 / len(sel) for s in sel["symbol"]} if len(sel) else {}
        scores[t] = scored.set_index("symbol")["total_score"]

    bench = _load_benchmark(store, cfg, panel).reindex(panel.index).ffill()
    rep = simulate(panel, dates, selections, scores, bench, cfg.backtest)

    # 切分报告
    k = max(1, int(len(dates) * OOS))
    split = dates[k]
    tr_sel = {t: s for t, s in selections.items() if t < split}
    oo_sel = {t: s for t, s in selections.items() if t >= split}
    tr_sco = {t: s for t, s in scores.items() if t < split}
    oo_sco = {t: s for t, s in scores.items() if t >= split}
    tr_dates = [t for t in dates if t < split]
    oo_dates = [t for t in dates if t >= split]
    tr = simulate(panel, tr_dates, tr_sel, tr_sco, bench, cfg.backtest)
    oo = simulate(panel, oo_dates, oo_sel, oo_sco, bench, cfg.backtest)

    print(f"\n切分日 {split.date()}")
    def line(tag, r):
        print(f"[{tag}] 总收益 {r.total_return:+.2%} | 年化 {r.annual_return:+.2%} "
              f"| 夏普 {r.sharpe} | 超额 {r.excess_return:+.2%} "
              f"| IC {r.ic_mean} | 盈亏比 {r.profit_loss_ratio} | 期望 {r.expectancy:.4f}")
    line("训练段", tr)
    line("样本外", oo)
    print("\n判定：样本外 IC 均值 ≥0.03 才算有 alpha；若≈0/负 → 滚动权重也救不了。")
    store.close()


if __name__ == "__main__":
    main()
