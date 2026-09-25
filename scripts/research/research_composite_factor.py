"""影子研究：低波+ROE+PE 复合因子 IC 验证（方案B）。

复用现有 PIT 截面 / process_factor / summarize 工具链，纯新增研究脚本，
不改任何现有打分/权重/上线逻辑（影子模式）。

对每个候选因子（复合 + 3 个单组件）：
  - 全市场含退市/ST（防幸存者偏差）
  - 逐月 PIT 截面（as_of 对齐，防前视）
  - process_factor 标准流水线：winsorize → zscore → 行业+市值中性 → 再zscore → 方向统一
  - 0.7 OOS 切分：训练段看方向，样本外只测一次
  - 报告 IC均值 / ICIR / IC胜率 / 分位spread / IC衰减
"""
import sys
import json
import pandas as pd

from aselect.config import load_config
from aselect.storage import get_storage
from aselect.data import build_universe, build_cross_section
from aselect.runner import _price_panel, _rebalance_dates
from aselect.engine.factors import process_factor
from aselect.engine.factor_research import summarize

# 复合因子组件：(标签, 字段, ascending[True=值越小越好])
COMPONENTS = [
    ("lowvol", "vol_60", True),   # 低波：波动越小越好
    ("quality", "roe",   False),  # 质量：ROE 越高越好
    ("value",   "pe",    True),   # 价值：PE 越低越好
]


def build_scores(cross_by_t, field, ascending):
    """对每个调仓日跑 process_factor，返回 {date: Series(processed)}。"""
    sbd = {}
    for t, cross in cross_by_t.items():
        if cross.empty or field not in cross.columns:
            continue
        ind = cross["industry"] if "industry" in cross.columns else None
        size = cross["total_mv"] if "total_mv" in cross.columns else None
        proc = process_factor(cross[field], ascending, ind, size)
        sbd[t] = pd.Series(proc.values, index=cross["symbol"].values)
    return sbd


def composite_scores(cross_by_t):
    """复合分 = (低波Z + ROE_Z + PE_Z) / 3，逐日期等权平均三组件处理值。"""
    comp_sbd = {}
    for t, cross in cross_by_t.items():
        if cross.empty:
            continue
        parts = []
        for _tag, field, ascending in COMPONENTS:
            if field not in cross.columns:
                continue
            ind = cross["industry"] if "industry" in cross.columns else None
            size = cross["total_mv"] if "total_mv" in cross.columns else None
            parts.append(process_factor(cross[field], ascending, ind, size))
        if not parts:
            continue
        comp = pd.concat(parts, axis=1).mean(axis=1)  # 等权 /3
        comp_sbd[t] = pd.Series(comp.values, index=cross["symbol"].values)
    return comp_sbd


def summarize_split(name, sbd, panel, schedule, oos_split):
    """按 0.7 切分训练/样本外，分别 summarize IC。"""
    dates = list(schedule)
    k = max(1, int(len(dates) * oos_split))
    split_date = pd.Timestamp(dates[k])
    train_sched = [d for d in dates if d < split_date]
    oos_sched = [d for d in dates if d >= split_date]
    out = {"name": name, "split_date": str(split_date.date())}
    for seg, sched in (("train", train_sched), ("oos", oos_sched)):
        if len(sched) >= 3:
            r = summarize(name, sbd, panel, sched)
            out[seg] = {
                "ic_mean": r.ic_mean, "icir": r.icir,
                "ic_win_rate": r.ic_win_rate, "n": r.n,
                "quantile_spread": r.quantile_spread, "decay": r.decay,
            }
        else:
            out[seg] = None
    return out


def main():
    oos_split = float(sys.argv[1]) if len(sys.argv) > 1 else 0.7
    cfg = load_config()
    store = get_storage(cfg)
    adjust = cfg.datasource.get("adjust", "hfq")
    universe = build_universe(store, include_delisted=True)
    panel = _price_panel(store, universe, adjust, None, None)
    if panel.shape[0] < 4:
        print(json.dumps({"error": "panel too short"}, ensure_ascii=False))
        return
    schedule = _rebalance_dates(panel.index, "M")
    print(f"universe={len(universe)}  panel_days={panel.shape[0]}  "
          f"monthly_rebalances={len(schedule)}  oos_split={oos_split}",
          flush=True)

    # 逐月 PIT 截面（各因子共用，一次构建）
    cross_by_t = {
        t: build_cross_section(store, cfg, symbols=universe,
                               as_of=pd.Timestamp(t).strftime("%Y-%m-%d"))
        for t in schedule
    }
    print(f"cross_sections built: {sum(1 for c in cross_by_t.values() if not c.empty)}",
          flush=True)

    results = []
    # 复合因子
    comp = composite_scores(cross_by_t)
    if comp:
        results.append(summarize_split("composite_lowvol+roe+pe", comp, panel,
                                       schedule, oos_split))
    # 消融对照组：三个单组件
    for tag, field, ascending in COMPONENTS:
        sbd = build_scores(cross_by_t, field, ascending)
        if sbd:
            results.append(summarize_split(f"single_{tag}({field})", sbd,
                                           panel, schedule, oos_split))

    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
