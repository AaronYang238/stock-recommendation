"""股票池级·多因子·walk-forward 回测（纯函数，确定性核心，AI 禁区）。

只做"给定每个调仓日的选股与全市场打分 → 模拟组合净值 + 算指标"这一段纯计算；
点位/披露日对齐、选股、取价等编排在上层 runner 完成（防止 engine 反向依赖 data）。

按北极星与铁律评估：报告 **IC / 夏普 / 期望值 / 盈亏比 / 最大回撤 / 超额(对基准)**，
计入 A 股摩擦（佣金/印花税/过户费/滑点），调仓为月级天然满足 T+1；
"涨跌停无法成交"由上层在选股时剔除涨跌停锁死的标的（这里只做组合模拟）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FactorBacktestReport:
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    benchmark_return: float
    excess_return: float          # 总收益 − 基准收益
    ic_mean: float                # 平均 Rank IC（每个调仓日 score vs 前向收益）
    ic_ir: float                  # IC 均值/标准差（信息比）
    ic_win_rate: float            # IC>0 的比例
    win_rate: float               # 调仓期为正的比例
    profit_loss_ratio: float      # 平均盈利期 / 平均亏损期
    expectancy: float             # 每期平均收益（期望值）
    n_rebalances: int
    avg_turnover: float
    avg_positions: float
    equity_curve: pd.Series = field(repr=False, default_factory=pd.Series)
    benchmark_curve: pd.Series = field(repr=False, default_factory=pd.Series)
    period_returns: pd.Series = field(repr=False, default_factory=pd.Series)


def simulate(
    panel: pd.DataFrame,                         # index=日期(已排序), columns=symbol, 值=后复权收盘
    schedule: list,                              # 调仓日列表（panel.index 的子集）
    selections: dict,                            # {调仓日: {symbol: 权重}}
    scores: dict,                                # {调仓日: pd.Series(symbol->打分)}，用于算 IC
    benchmark: pd.Series,                        # index=日期, 值=基准价格
    cost: dict,                                  # commission/stamp_tax/transfer_fee/slippage
    delisted: set | None = None,                 # 段内退市的 symbol 集合
    delist_haircut: float = 0.0,                 # 退市折价（退市整理/转板后的损失）
) -> FactorBacktestReport:
    dates = list(schedule)
    if len(dates) < 2:
        return _empty_report()

    comm = float(cost.get("commission", 0.00025))
    stamp = float(cost.get("stamp_tax", 0.001))
    transfer = float(cost.get("transfer_fee", 0.00001))
    slip = float(cost.get("slippage", 0.001))

    equity, bench = [1.0], [1.0]
    eq_dates = [pd.Timestamp(dates[0])]
    period_rets, turnovers, n_pos, ics = [], [], [], []
    prev_w: dict = {}

    for i in range(len(dates) - 1):
        t0, t1 = dates[i], dates[i + 1]
        w = selections.get(t0, {})
        n_pos.append(len(w))

        # 组合毛收益：持仓权重 × 个股 t0→t1 持有收益
        # （停牌按最后价盯市；段内退市按最后价 ×(1-折价) 清算，不再"缺价记 0"）
        gross = 0.0
        liquidated = set()
        for sym, wt in w.items():
            r, gone = _hold_ret(panel, sym, t0, t1, delisted or set(), delist_haircut)
            if r is not None:
                gross += wt * r
            if gone:
                liquidated.add(sym)

        # 换手成本：买卖双边佣金+过户+滑点，卖出额外印花税
        syms = set(w) | set(prev_w)
        turnover = sum(abs(w.get(s, 0.0) - prev_w.get(s, 0.0)) for s in syms)
        sold = sum(max(prev_w.get(s, 0.0) - w.get(s, 0.0), 0.0) for s in syms)
        cost_rate = turnover * (comm + transfer + slip) + sold * stamp

        net = gross - cost_rate
        equity.append(equity[-1] * (1 + net))
        eq_dates.append(pd.Timestamp(t1))
        period_rets.append(net)
        turnovers.append(turnover)
        # 已退市清算的仓位不再参与下期换手（没有"再卖一次"）
        prev_w = {k: v for k, v in w.items() if k not in liquidated}

        # 基准
        br = _ret(benchmark, None, t0, t1)
        bench.append(bench[-1] * (1 + br) if br is not None else bench[-1])

        # Rank IC：t0 的打分 vs t0→t1 的前向收益
        ic = _rank_ic(scores.get(t0), panel, t0, t1)
        if ic is not None:
            ics.append(ic)

    return _metrics(pd.Series(equity, index=eq_dates),
                    pd.Series(bench, index=eq_dates),
                    pd.Series(period_rets), turnovers, n_pos, ics)


# ── 内部工具 ──────────────────────────────────────────────
def _ret(data, sym, t0, t1):
    """data 为 DataFrame(取列 sym) 或 Series(基准)。返回 t0→t1 简单收益，缺失返回 None。"""
    try:
        if isinstance(data, pd.DataFrame):
            if sym not in data.columns:
                return None
            p0, p1 = data.at[t0, sym], data.at[t1, sym]
        else:
            p0, p1 = data.get(t0), data.get(t1)
    except KeyError:
        return None
    if p0 is None or p1 is None or pd.isna(p0) or pd.isna(p1) or p0 <= 0:
        return None
    return float(p1 / p0 - 1)


def _hold_ret(panel: pd.DataFrame, sym, t0, t1, delisted: set, haircut: float):
    """持有 t0→t1 的收益与是否已退市清算。返回 (ret|None, liquidated)。"""
    if sym not in panel.columns:
        return None, False
    col = panel[sym]
    p0s = col.loc[:t0].dropna()
    if p0s.empty or p0s.iloc[-1] <= 0:
        return None, False
    p0 = float(p0s.iloc[-1])
    p1 = col.get(t1)
    if p1 is not None and pd.notna(p1):
        return float(p1 / p0 - 1), False
    seg = col.loc[:t1].dropna()
    last = float(seg.iloc[-1])
    if sym in delisted and col.loc[t1:].dropna().empty:
        return float(last * (1 - haircut) / p0 - 1), True
    return float(last / p0 - 1), False      # 停牌：按最后价盯市


def _rank_ic(score: pd.Series | None, panel: pd.DataFrame, t0, t1):
    if score is None or len(score) < 4:
        return None
    fwd = {s: _ret(panel, s, t0, t1) for s in score.index}
    fwd = pd.Series(fwd)
    pair = pd.DataFrame({"s": score, "f": fwd}).dropna()
    if len(pair) < 4 or pair["s"].std() == 0 or pair["f"].std() == 0:
        return None
    # Rank IC = 排名后的 Pearson 相关（等价 Spearman，但不依赖 scipy）
    return float(pair["s"].rank().corr(pair["f"].rank()))


def _metrics(eq, bc, pr, turnovers, n_pos, ics) -> FactorBacktestReport:
    total = float(eq.iloc[-1] - 1)
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    years = days / 365.25
    annual = float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else 0.0
    ppy = len(pr) / years if years > 0 else float(len(pr))   # 每年期数
    sharpe = float(np.sqrt(ppy) * pr.mean() / pr.std()) if pr.std() > 0 else 0.0
    peak = eq.cummax()
    mdd = float(((eq - peak) / peak).min())
    bench_total = float(bc.iloc[-1] - 1)

    wins, losses = pr[pr > 0], pr[pr < 0]
    win_rate = float(len(wins) / len(pr)) if len(pr) else 0.0
    pl_ratio = float(wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() != 0 else 0.0
    ic_arr = pd.Series(ics, dtype=float)
    ic_mean = float(ic_arr.mean()) if len(ic_arr) else 0.0
    ic_ir = float(ic_arr.mean() / ic_arr.std()) if len(ic_arr) > 1 and ic_arr.std() > 0 else 0.0
    ic_win = float((ic_arr > 0).mean()) if len(ic_arr) else 0.0

    return FactorBacktestReport(
        total_return=round(total, 4), annual_return=round(annual, 4),
        sharpe=round(sharpe, 3), max_drawdown=round(mdd, 4),
        benchmark_return=round(bench_total, 4),
        excess_return=round(total - bench_total, 4),
        ic_mean=round(ic_mean, 4), ic_ir=round(ic_ir, 3), ic_win_rate=round(ic_win, 3),
        win_rate=round(win_rate, 3), profit_loss_ratio=round(pl_ratio, 3),
        expectancy=round(float(pr.mean()), 5),
        n_rebalances=int(len(pr)),
        avg_turnover=round(float(np.mean(turnovers)), 3) if turnovers else 0.0,
        avg_positions=round(float(np.mean(n_pos)), 1) if n_pos else 0.0,
        equity_curve=eq, benchmark_curve=bc, period_returns=pr,
    )


def _empty_report() -> FactorBacktestReport:
    z = pd.Series(dtype=float)
    return FactorBacktestReport(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                equity_curve=z, benchmark_curve=z, period_returns=z)
