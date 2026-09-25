"""30y A0 vs A 的 trade outcome cache（2026-09，最高价值实验的使能件）。

背景：30 年窗口的全市场日线帧在 4GB 机上不可行（~10GB+）。拆成两段：
  1) 逐只流式构建 trade cache：每只股票的每个「周度调仓日入场」预计算退出事件，
     复用 evaluate_exit / _fillable_open / add_indicators（与 simulate_portfolio
     逐位一致的代码路径），落盘 parquet（内存有界：一次一只）。
  2) A0 臂 = 周度随机 top10 + 持有 1 周（无状态机，向量化）；
     A 臂  = 同样的周度随机（配对 seed），用 cache 事件重放退出（查表，无逐日指标）。

纪律：本模块是 30y 实验的实现层；必须先做影子验证（小窗口 cache 重放 vs
现有 run_monte_carlo A 臂逐位一致）再上 30y 全量。
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

from .strategy_rules import ExitParams, PositionState, evaluate_exit
from .swing_backtest import _fillable_open
from .indicators import add_indicators


def build_symbol_trades(df: pd.DataFrame, weekly_dates: pd.DatetimeIndex,
                        exit_params: ExitParams = None,
                        limit_pct: float = 0.095, min_bars: int = 30) -> pd.DataFrame:
    """对单只股票的日线 df（含 open/high/low/close/volume）构建每入场日一笔的退出结果。

    与 simulate_portfolio 相同的语义：
      - 入场：调仓日 t → 下一个可成交 open（_fillable_open buy）
      - 退出：逐日 evaluate_exit；scale_out/exit 在信号日的下一个可成交 open 成交
      - highest_close 从 entry_price 起
    返回 DataFrame：entry_date, fill_date, entry_price, atr0, exit_fill_date,
      exit_price, exit_fraction, exit_reason, scale_fill_date, scale_price,
      scale_fraction, closes(bytes=float32 日收盘路径[fill..exit])。
    """
    ep = exit_params or ExitParams()
    if df is None or len(df) < min_bars:
        return pd.DataFrame()
    fr = add_indicators(df.reset_index(drop=True))
    idx = pd.DatetimeIndex(pd.to_datetime(fr["date"] if "date" in fr.columns else fr.index))
    fr.index = idx
    close = fr["close"].astype(float).values
    open_ = fr["open"].astype(float).values
    atr14 = fr["atr14"].astype(float).values
    ma10 = fr["ma10"].astype(float).values
    rows = []
    dates = list(idx)
    loc_of = {d: i for i, d in enumerate(dates)}

    for t in weekly_dates:
        if t not in loc_of:
            continue
        i0 = loc_of[t]
        bi = _fillable_open(fr, i0 + 1, "buy", limit_pct)
        if bi is None or bi >= len(fr):
            continue
        entry_price = float(open_[bi])
        a0 = float(atr14[bi]) if np.isfinite(atr14[bi]) and atr14[bi] > 0 else max(entry_price * 0.02, 1e-6)
        # 状态机逐日推进（从 fill 日起，max_hold 封顶）
        st = PositionState(entry_price=entry_price, atr_at_entry=a0, highest_close=entry_price)
        scale_fill_date = scale_price = scale_frac = np.nan
        exit_fill_date = exit_price = exit_frac = exit_reason = np.nan
        closes = [float(close[bi])]
        for i in range(bi, min(bi + ep.max_hold + 2, len(fr))):
            if i > bi:
                closes.append(float(close[i]))
            atr = float(atr14[i]) if np.isfinite(atr14[i]) else a0
            bar = {"close": float(close[i]), "ma10": float(ma10[i]), "atr": atr}
            dec = evaluate_exit(st, bar, ep)
            if dec.action == "scale_out":
                si = _fillable_open(fr, i + 1, "sell", limit_pct)
                if si is not None:
                    scale_fill_date = dates[si]
                    scale_price = float(open_[si])
                    scale_frac = dec.fraction
            elif dec.action == "exit":
                si = _fillable_open(fr, i + 1, "sell", limit_pct)
                si = si if si is not None else len(fr) - 1
                exit_fill_date = dates[si]
                exit_price = float(open_[si])
                exit_frac = st.remaining
                exit_reason = dec.reason
                break
        rows.append({
            "entry_date": t,
            "fill_date": dates[bi],
            "entry_price": entry_price,
            "atr0": a0,
            "exit_fill_date": exit_fill_date,
            "exit_price": exit_price,
            "exit_fraction": exit_frac,
            "exit_reason": exit_reason,
            "scale_fill_date": scale_fill_date,
            "scale_price": scale_price,
            "scale_fraction": scale_frac,
            "closes": np.asarray(closes, dtype=np.float32).tobytes(),
        })
    return pd.DataFrame(rows)


def run_a0_weekly(panel_w: pd.DataFrame, full_pool: dict, top_n: int, seed: int) -> pd.Series:
    """A0：每周随机 top_n、持有 1 周、周度全再平衡（池子 beta）。返回周收益序列。"""
    import random
    rng = random.Random(seed)
    rets = {}
    for t in sorted(full_pool):
        pool = full_pool[t]
        if not pool:
            continue
        picks = rng.sample(pool, min(top_n, len(pool)))
        # 下一周
        dates = sorted(panel_w.index)
        nxt = dates[dates.index(t) + 1] if t in dates and dates.index(t) + 1 < len(dates) else None
        if nxt is None:
            continue
        w0 = panel_w.loc[t]
        w1 = panel_w.loc[nxt]
        rs = []
        for s in picks:
            if s in w0.index and s in w1.index and pd.notna(w0[s]) and pd.notna(w1[s]) and w0[s] > 0:
                rs.append(w1[s] / w0[s] - 1)
        if rs:
            rets[nxt] = float(np.mean(rs))
    return pd.Series(rets).sort_index()


def run_a_cached(cache: dict, trading_days: pd.DatetimeIndex, schedule: list,
                 full_pool: dict, top_n: int, cost: dict,
                 exit_params: ExitParams = None, limit_pct: float = 0.095,
                 cash0: float = 1_000_000.0, seed: int = 0, n_iter: int = 1):
    """A 臂（随机入场 + 完整离场状态机）用 trade-cache 重放（查表，无逐日指标）。

    cache[(symbol, entry_date)] = 入场记录。对每个 MC 迭代：
    每周随机 top_n（配对 seed 与 A0），入场用 cache 的 fill_date/entry_price，
    逐日按 closes 盯市、按 exit/scale 事件卖出。返回指标行列表。
    """
    import random
    comm = float(cost.get("commission", 0.00025))
    stamp = float(cost.get("stamp_tax", 0.001))
    transfer = float(cost.get("transfer_fee", 0.00001))
    slip = float(cost.get("slippage", 0.001))
    buy_cost = comm + transfer + slip
    sell_cost = comm + transfer + slip + stamp

    schedule_set = set(pd.Timestamp(x) for x in schedule)
    day_locs = {pd.Timestamp(d): i for i, d in enumerate(trading_days)}
    out = []
    for it in range(n_iter):
        rng = random.Random(seed * 100000 + it)
        cash = float(cash0)
        book = []       # [dict: sym, entry_price, slot, remaining, fill_idx, closes, scale_date, scale_price, exit_date, exit_price, exit_frac]
        pending = []    # [dict: sym, fill_idx, entry_price, slot_value]
        nav = {}
        pos_acc = 0
        ndays = 0
        for d in trading_days:
            dl = day_locs[pd.Timestamp(d)]
            # 1) 到期挂起入场
            for p in [x for x in pending if x["fill_idx"] == dl]:
                slot = min(p["slot_value"], cash)
                if slot > 1e-9:
                    cash -= slot * (1 + buy_cost)
                    rec = cache.get((p["sym"], p["entry_date"]))
                    if rec is None:
                        continue
                    closes = np.frombuffer(rec["closes"], dtype=np.float32)
                    book.append({
                        "sym": p["sym"], "entry_price": p["entry_price"],
                        "slot": slot, "remaining": 1.0, "fill_idx": p["fill_idx"],
                        "closes": closes, "exit_date": rec["exit_fill_date"],
                        "exit_price": rec["exit_price"], "exit_frac": rec["exit_fraction"],
                        "scale_date": rec["scale_fill_date"], "scale_price": rec["scale_price"],
                        "scale_frac": rec["scale_fraction"] or 0.0,
                    })
                pending.remove(p)
            # 2) 盯市 + 事件卖出
            for b in list(book):
                off = dl - b["fill_idx"]
                b["last_close"] = float(b["closes"][off]) if 0 <= off < len(b["closes"]) else b["entry_price"]
                if b["scale_date"] is not None and pd.Timestamp(b["scale_date"]) == pd.Timestamp(d) and b["scale_frac"]:
                    px = b["scale_price"]
                    proceeds = b["slot"] * b["scale_frac"] * (px / b["entry_price"])
                    cash += proceeds * (1 - sell_cost)
                    b["remaining"] = round(b["remaining"] - b["scale_frac"], 6)
                if b["exit_date"] is not None and pd.Timestamp(b["exit_date"]) == pd.Timestamp(d):
                    px = b["exit_price"]
                    proceeds = b["slot"] * b["remaining"] * (px / b["entry_price"])
                    cash += proceeds * (1 - sell_cost)
                    book.remove(b)
            # 3) 净值
            bookval = sum(b["slot"] * b["remaining"] * (b["last_close"] / b["entry_price"]) for b in book)
            nav[pd.Timestamp(d)] = cash + bookval
            pos_acc += len(book)
            ndays += 1
            # 4) 调仓日：排入新入场
            if pd.Timestamp(d) in schedule_set:
                free = top_n - len(book)
                if free > 0:
                    pool = full_pool.get(pd.Timestamp(d))
                    if pool:
                        picks = rng.sample(pool, min(free, len(pool)))
                        for sym in picks:
                            rec = cache.get((sym, pd.Timestamp(d)))
                            if rec is None:
                                continue
                            fi = day_locs.get(pd.Timestamp(rec["fill_date"]))
                            if fi is None or fi < dl:
                                continue
                            pending.append({
                                "sym": sym, "fill_idx": fi,
                                "entry_price": rec["entry_price"],
                                "slot_value": nav[pd.Timestamp(d)] / top_n,
                                "entry_date": pd.Timestamp(d),
                            })
        eq = pd.Series(nav).sort_index()
        rets = eq.pct_change().dropna()
        if len(eq) < 2 or rets.std() == 0 or eq.iloc[0] <= 0:
            out.append((0.0, 0.0, 0.0, 0.0))
            continue
        total = float(eq.iloc[-1] / eq.iloc[0] - 1)
        sharpe = float(np.sqrt(252) * rets.mean() / rets.std())
        mdd = float(((eq - eq.cummax()) / eq.cummax()).min())
        out.append((total, round(sharpe, 3), round(mdd, 4), round(pos_acc / ndays, 2)))
    return out

