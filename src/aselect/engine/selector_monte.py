"""P0-2 随机对照组的蒙特卡洛归因实验（只读，不调参）。

四臂归因阶梯，只允许「选谁」不同，其余逐字共用同一份代码路径：
  A0 = 全池随机10，无离场(持有到下次调仓)          → 池子 beta
  A  = 全池随机10，完整离场状态机                  → A−A0 = 离场纪律价值
  B  = 过三门后随机10，完整离场状态机              → B−A  = 入场门价值
  C  = 完整八因子 + 三门，完整离场状态机           → C−B  = 因子模型价值

关键不变量：四臂共用 simulate_portfolio 的 Model A 逐日会计；随机从 **PIT
可交易快照** 抽样（非现存股票，防幸存者红利）；B 臂的门在**全市场**重算
（非 top-N，防因子分数污染池子）；抽不满不重抽（缺口现金持有）；配对 seed。

本模块只产出「是/否」归因结论，不在结果上调参（否则烧掉本实验）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .portfolio import simulate_portfolio
from .swing_backtest import SwingReport, _swing_metrics
from .strategy_rules import fundamental_safety


# ── 快速闸门评估：直接用 _ohlc_frames 已预计算的指标列，避免重复 add_indicators ──
def fast_entry_gate(fr: pd.DataFrame, loc: int, params=None) -> bool:
    """等价于 entry_gate(fr.iloc[:loc+1]).passed，但读预计算列。确定性纯函数。"""
    if loc < 20:
        return False
    close = float(fr["close"].iloc[loc])
    prev = float(fr["close"].iloc[loc - 1])
    ma20 = float(fr["ma20"].iloc[loc])
    ma5 = float(fr["ma5"].iloc[loc])
    rsi = fr["rsi14"].iloc[loc]
    if not (np.isfinite(ma20) and ma20 > 0 and np.isfinite(ma5)):
        return False
    intraday = close / prev - 1
    ext = close / ma20 - 1
    ok = (intraday <= 0.03 and intraday < 0.095
          and ext <= 0.15
          and close > ma5
          and (np.isnan(rsi) or rsi <= 70.0))
    return ok


def fast_pullback_gate(fr: pd.DataFrame, loc: int,
                       near_ma20: float = 0.02, min_above_days: int = 3) -> bool:
    """等价于 gate_pullback_ma20(fr.iloc[:loc+1]).passed，读预计算列。
    回踩 MA20 企稳：站上MA20且贴近(偏离≤2%)、当日收阳、此前连续≥3天在MA20上方。"""
    if loc < 20:
        return False
    close = float(fr["close"].iloc[loc])
    op = float(fr["open"].iloc[loc])
    ma20 = float(fr["ma20"].iloc[loc])
    if not (np.isfinite(ma20) and ma20 > 0):
        return False
    dev = close / ma20 - 1
    if not (0 < dev <= near_ma20):          # 站上MA20 且 贴近
        return False
    if not (close > op):                    # 当日收阳企稳
        return False
    above = 0
    for i in range(loc - 1, 0, -1):         # 此前连续在MA20上方
        if float(fr["close"].iloc[i]) > float(fr["ma20"].iloc[i]):
            above += 1
        else:
            break
        if above >= min_above_days:
            break
    return above >= min_above_days


def fund_pass(row, fund_params=None) -> bool:
    """等价于 fundamental_safety(row.pe, row.roe, fund_params).passed。"""
    if fund_params is None:
        return True
    return fundamental_safety(row.get("pe"), row.get("roe"), fund_params).passed


# ── 一次性构建四臂的每期池子 ─────────────────────────────────
@dataclass
class Pools:
    schedule: list
    full: dict        # date -> tradable PIT symbols（全池）
    gated: dict       # date -> 过三门 symbols（B 臂池）
    industry: dict    # date -> {symbol: industry}
    picks_c: dict     # date -> 因子 top10（C 臂，确定性）
    rebalance_dates: list


def build_pools(frames, panel, cross_by_t, schedule, top_n, max_per_industry,
                gate=True, entry_gate_fn=fast_entry_gate, fund_params=None,
                limit_pct=0.095, c_gate=None):
    """对每个调仓日：tradable 全池（PIT）、过门池、因子 top10 picks。
    entry_gate_fn=(fr,loc)->bool 用于 B 臂门池；c_gate=(bars_slice)->GateResult
    用于 C 臂（默认真 entry_gate，可换回踩门等）。"""
    full, gated, industry, picks_c = {}, {}, {}, {}
    ts_sched = set(pd.Timestamp(x) for x in schedule)
    for t in schedule:
        cross = cross_by_t.get(t)
        if cross is None or cross.empty:
            continue
        ts = pd.Timestamp(t)
        tradable = set()
        # 可成交判断与 runner._tradable 完全一致（含 pos==0 边界）
        if ts in panel.index:
            pos = panel.index.get_loc(ts)
            today = panel.iloc[pos]
            if pos == 0:
                tradable = set(today.dropna().index)
            else:
                prev = panel.iloc[pos - 1]
                for sym in panel.columns:
                    p = today.get(sym)
                    q = prev.get(sym)
                    if pd.isna(p) or pd.isna(q) or q <= 0:
                        continue
                    if abs(p / q - 1) >= limit_pct:
                        continue
                    tradable.add(sym)
        ind_map = {}
        if "industry" in cross.columns:
            ind_map = dict(zip(cross["symbol"], cross["industry"]))
        full_list, gated_list = [], []
        for _, row in cross.iterrows():
            sym = row["symbol"]
            if sym not in tradable:
                continue
            fr = frames.get(sym)
            if fr is None:
                continue
            if ts not in fr.index:
                continue
            loc = fr.index.get_indexer([ts])[0]
            if loc < 20:
                continue
            full_list.append(sym)
            if gate:
                if entry_gate_fn(fr, loc) and fund_pass(row, fund_params):
                    gated_list.append(sym)
            else:
                gated_list.append(sym)
        full[ts] = full_list
        gated[ts] = gated_list
        industry[ts] = ind_map
        # C：因子打分取 top_n（确定性，用与现有回测一致的真 gate，或 c_gate 指定）
        from ..runner import score_factors, _select_candidates
        from .strategy_rules import entry_gate as _real_gate
        scored = score_factors(cross)
        c_picks = _select_candidates(scored, tradable, frames, ts, top_n,
                                     max_per_industry, True,
                                     c_gate or _real_gate,
                                     fund_params=fund_params)
        picks_c[ts] = c_picks
    keys = sorted(full.keys())
    return Pools(keys, full, gated, industry, picks_c, keys)


# ── 随机选股（配对 seed；含行业上限；抽不满不重抽）──────────────
def random_picks(pool, ind_map, top_n, max_per_industry, rng):
    order = list(pool)
    rng.shuffle(order)
    picks, per_ind = [], {}
    for sym in order:
        ind = ind_map.get(sym)
        if ind is not None and pd.notna(ind) and per_ind.get(ind, 0) >= max_per_industry:
            continue
        picks.append(sym)
        if ind is not None and pd.notna(ind):
            per_ind[ind] = per_ind.get(ind, 0) + 1
        if len(picks) >= top_n:
            break
    return picks  # 可能 < top_n（缺口现金持有）


# ── A0 臂：无离场、持有到下次调仓、周度全再平衡（池子 beta）────
def run_a0(frames, pools: Pools, top_n, max_per_industry, seed):
    rng = random.Random(seed)
    eq = {}
    dates = pools.schedule
    for i, d in enumerate(dates):
        pool = pools.full.get(d)
        if not pool:
            continue
        picks = random_picks(pool, pools.industry.get(d, {}), top_n, max_per_industry, rng)
        if not picks:
            continue
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        if nxt is None:
            continue
        rets = []
        for sym in picks:
            fr = frames.get(sym)
            if fr is None or d not in fr.index or nxt not in fr.index:
                continue
            p0 = float(fr["close"].iloc[fr.index.get_indexer([d])[0]])
            p1 = float(fr["close"].iloc[fr.index.get_indexer([nxt])[0]])
            if p0 > 0:
                rets.append(p1 / p0 - 1)
        if rets:
            eq[nxt] = float(pd.Series(rets).mean())
    if not eq:
        return SwingReport(0, 0, 0, 0, 0, 0, 0)
    s = pd.Series(eq).sort_index()
    cur = (1 + s).cumprod()
    total = float(cur.iloc[-1] - 1)
    sharpe = float(np.sqrt(52) * s.mean() / s.std()) if s.std() > 0 else 0.0
    peak = cur.cummax()
    mdd = float(((cur - peak) / peak).min())
    return SwingReport(total, sharpe, mdd, 0, 0.0, 0.0, 0, equity_curve=cur)


def run_monte_carlo(frames, panel, pools: Pools, top_n, max_per_industry,
                    cost, exit_params, limit_pct, cash0, seed, arm="A",
                    n_iter=1000):
    """arm='A'（全池随机）或 arm='B'（过门随机）。返回指标矩阵 + 净值曲线。"""
    pool_of = pools.full if arm == "A" else pools.gated
    rows = []
    curves = {}
    for it in range(n_iter):
        seed_i = seed + it
        picks_by_t = {}
        for d in pools.schedule:
            pool = pool_of.get(d)
            if not pool:
                continue
            rng = random.Random(seed_i * 100000 + (d.value % 100000))
            picks_by_t[d] = random_picks(pool, pools.industry.get(d, {}),
                                         top_n, max_per_industry, rng)
        rep = simulate_portfolio(frames, panel, pools.schedule, picks_by_t,
                                 top_n, cost, exit_params, limit_pct, cash0)
        rows.append((rep.total_return, rep.sharpe, rep.max_drawdown, rep.n_trades,
                     rep.avg_positions, _calmar(rep)))
        curves[it] = rep.equity
    df = pd.DataFrame(rows, columns=["total_return", "sharpe", "max_dd",
                                     "n_trades", "avg_pos", "calmar"])
    return df, curves


def _calmar(rep):
    return rep.total_return / abs(rep.max_drawdown) if rep.max_drawdown < 0 else np.nan


def run_c_once(frames, panel, pools: Pools, top_n, cost, exit_params,
               limit_pct, cash0):
    rep = simulate_portfolio(frames, panel, pools.schedule, pools.picks_c,
                             top_n, cost, exit_params, limit_pct, cash0)
    return rep
