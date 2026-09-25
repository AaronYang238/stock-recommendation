"""策略回测编排（应用层：组合 data + engine，位于 engine 之上）。

在每个调仓日 T：
  1. 以 **point-in-time(as_of=T)** 构建截面 → 因子打分（A3 防前视 + A2 中性化）；
  2. 剔除停牌 / 涨跌停锁死（无法成交）的标的；
  3. 取打分最高的 top_n，等权建仓；
  4. 把"每期选股 + 全市场打分 + 价格面板 + 基准"交给纯引擎 simulate 模拟净值与指标。

股票池含历史退市/ST（防幸存者偏差，铁律2）。
"""
from __future__ import annotations

import pandas as pd

from .config import Config
from .data import (build_cross_section, build_universe, exclude_st_rows,
                   filter_tradable_universe)
from .engine import score_factors
from .engine.factor_backtest import FactorBacktestReport, simulate
from .storage import Storage


def run_strategy_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "M", top_n: int = 20,
    benchmark_prices: pd.Series | None = None,
    limit_pct: float | None = None,
    weights: dict | None = None,
    hold_buffer: float = 1.0,
) -> FactorBacktestReport:
    """股票池级多因子回测。信号日 t 收盘出信号 → **t+1 开盘**成交（旧实现按 t 日收盘价
    成交，而信号本身就用了 t 日收盘价，等于在同一价格上"看完再买"）。

    - 买入：t+1 开盘有价且未开在涨停价；
    - 卖出：t+1 停牌或开在跌停价 → 卖不出，按原权重顺延持有（新仓分剩余权重）；
    - hold_buffer>1：已持仓只要仍排在前 top_n×hold_buffer 就不卖（缓冲带降换手）。
    """
    from .engine.limits import limit_threshold
    adjust = config.datasource.get("adjust", "hfq")
    # 含退市（防幸存者偏差）；板块按账户权限过滤，ST 逐日剔除
    universe = filter_tradable_universe(store, config,
                                        build_universe(store, include_delisted=True))
    panel, opens = _price_panels(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or panel.shape[1] == 0:
        return simulate(panel, [], {}, {}, pd.Series(dtype=float), config.backtest)
    exec_px = opens.shift(-1)          # 在 t 行放 t+1 开盘价：t 日信号的可成交价

    # 调仓日须存在次日开盘价（末个交易日无法在"次日"成交）
    schedule = [t for t in _rebalance_dates(panel.index, freq)
                if exec_px.loc[t].notna().any()]
    selections: dict = {}
    scores: dict = {}
    prev_w: dict = {}

    def _thr(sym, t, st):
        return limit_pct if limit_pct is not None else limit_threshold(sym, t, st)

    for t in schedule:
        as_of = pd.Timestamp(t).strftime("%Y-%m-%d")
        cross = exclude_st_rows(
            build_cross_section(store, config, symbols=universe, as_of=as_of), config)
        st_map = dict(zip(cross["symbol"], cross["is_st"])) if "is_st" in cross.columns else {}
        close_t, open_n = panel.loc[t], exec_px.loc[t]

        def _gap(sym):
            c, o = close_t.get(sym), open_n.get(sym)
            if pd.isna(c) or pd.isna(o) or c <= 0:
                return None
            return o / c - 1

        def _sell_blocked(sym):
            g = _gap(sym)
            if g is None:                                   # 次日无价：停牌 or 已退市
                return not panel[sym].loc[t:].iloc[1:].dropna().empty
            return g <= -_thr(sym, t, bool(st_map.get(sym, False)))

        if cross.empty:
            scores[t] = pd.Series(dtype=float)
            ranked = []
        else:
            scored = score_factors(cross, weights=weights)   # weights=None 即等权
            scores[t] = scored.set_index("symbol")["total_score"]
            ranked = list(scored["symbol"])

        keep = []
        if hold_buffer > 1.0 and prev_w:
            band = set(ranked[: int(round(top_n * hold_buffer))])
            keep = [s_ for s_ in prev_w if s_ in band and _gap(s_) is not None]
        buyable = []
        for sym in ranked:
            if len(keep) + len(buyable) >= top_n:
                break
            if sym in keep:
                continue
            g = _gap(sym)
            if g is None or g >= _thr(sym, t, bool(st_map.get(sym, False))):
                continue                                    # 停牌 / 开盘涨停买不进
            buyable.append(sym)
        picks = keep + buyable
        carried = {s_: w_ for s_, w_ in prev_w.items()
                   if s_ not in picks and _sell_blocked(s_)}
        room = max(1.0 - sum(carried.values()), 0.0)
        sel = dict(carried)
        if picks and room > 0:
            sel.update({s_: room / len(picks) for s_ in picks})
        selections[t] = sel
        prev_w = sel

    if benchmark_prices is None:
        benchmark_prices = _load_benchmark(store, config, panel)
    bench = benchmark_prices.reindex(panel.index).ffill()
    return simulate(exec_px, list(schedule), selections, scores, bench, config.backtest,
                    delisted=_delisted_in_panel(store, exec_px),
                    delist_haircut=_delist_haircut(config))


# ── 阶段二：单因子 IC 研究 + IC 加权 + 样本外验证 ──────────
def run_factor_research(store: Storage, config: Config, factors: dict | None = None,
                        freq: str = "M", start: str | None = None,
                        end: str | None = None,
                        progress: bool = False,
                        cross_by_t: dict | None = None) -> dict:
    """对每个因子做 walk-forward 单因子 IC 研究（逐期 PIT 截面 → 因子处理值 → IC）。
    返回 {因子名: FactorICReport}。progress=True 时打印构建进度。
    cross_by_t 可传入已构建的 {调仓日: 截面} 字典以复用（去重），None 则内部构建。"""
    from .engine.factors import DEFAULT_FACTORS, process_factor
    from .engine.factor_research import summarize

    factors = factors or DEFAULT_FACTORS
    adjust = config.datasource.get("adjust", "hfq")
    universe = filter_tradable_universe(store, config,
                                        build_universe(store, include_delisted=True))
    panel = _price_panel(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or panel.shape[1] == 0:
        return {}
    schedule = _rebalance_dates(panel.index, freq)

    # 逐调仓日构建一次 PIT 截面（各因子共用）；已传入则直接复用（去重）
    if cross_by_t is None:
        _progress(progress, f"[因子研究] 构建 {len(schedule)} 个调仓日的 PIT 截面（全市场）…")
        cross_by_t = {}
        _n = 0
        for t in schedule:
            _n += 1
            if progress and (_n % max(1, len(schedule) // 10) == 0 or _n == len(schedule)):
                _progress(progress, f"[因子研究] PIT 截面 {_n}/{len(schedule)} ({_n / len(schedule):.0%})")
            cross_by_t[t] = build_cross_section(store, config, symbols=universe,
                                                as_of=pd.Timestamp(t).strftime("%Y-%m-%d"))

    reports = {}
    for defs in factors.values():
        for d in defs:
            sbd = {}
            for t, cross in cross_by_t.items():
                if cross.empty or d.field not in cross.columns:
                    continue
                ind = (cross["industry"] if d.industry_neutral
                       and "industry" in cross.columns else None)
                size = cross["total_mv"] if "total_mv" in cross.columns else None
                proc = process_factor(cross[d.field], d.ascending, ind, size)
                # 打分时缺失记 0（行业中性后的"中性"值），但算 IC 时必须剔除：
                # 大量并列的 0 会稀释秩相关，让稀疏因子看起来比实际更弱/更随机
                proc = proc.where(cross[d.field].notna())
                sbd[t] = pd.Series(proc.values, index=cross["symbol"].values).dropna()
            if sbd:
                reports[d.name] = summarize(d.name, sbd, panel, schedule)
    return reports


def ic_category_weights(reports: dict, factors: dict | None = None,
                        shrink: float = 0.5, max_weight: float = 0.4) -> dict:
    """由单因子 IC 报告聚合出类别权重。

    旧实现按"类别平均 IC 的正部"直接归一，训练段样本少时会得出 lowvol:1.00 这种
    押单一因子的极端权重。现改为：
    1. 用 ICIR（IC 均值/标准差，稳定性）而非 IC 均值打分，取正部；
    2. 向"正分类别等权"收缩（shrink=0.5 → 一半信号、一半等权）；
    3. 单类别权重上限 max_weight（类别过少时自动放宽），资金流另有 0.25 上限。
    全部非正 → 全类别等权。
    """
    from .engine.factors import DEFAULT_FACTORS
    factors = factors or DEFAULT_FACTORS
    name2cat = {d.name: cat for cat, defs in factors.items() for d in defs}
    cat_ir: dict = {}
    for name, r in reports.items():
        cat = name2cat.get(name)
        if cat:
            cat_ir.setdefault(cat, []).append(r.icir)
    raw = {cat: max(sum(v) / len(v), 0.0) for cat, v in cat_ir.items()}
    total = sum(raw.values())
    if total <= 0:
        return {cat: 1.0 / len(cat_ir) for cat in cat_ir} if cat_ir else {}
    pos = [c for c, v in raw.items() if v > 0]
    weights = {c: (shrink * raw[c] / total + (1 - shrink) / len(pos)) if c in pos else 0.0
               for c in raw}
    caps = {c: max(max_weight, 1.0 / len(pos)) for c in weights}
    if "moneyflow" in caps:            # 资金流作增强因子：权重上限 0.25（grill-me Q5）
        caps["moneyflow"] = min(caps["moneyflow"], 0.25)
    return _cap_weights(weights, caps)


def _cap_weights(weights: dict, caps: dict) -> dict:
    """迭代截顶并把超额按比例分给未触顶的正权重类别，保持总和为 1。"""
    w = dict(weights)
    for _ in range(len(w) + 1):
        over = {c: v - caps[c] for c, v in w.items() if v > caps[c] + 1e-12}
        if not over:
            break
        excess = sum(over.values())
        for c in over:
            w[c] = caps[c]
        free = {c: v for c, v in w.items() if v > 0 and c not in over and v < caps[c]}
        ft = sum(free.values())
        if ft <= 0:
            break
        for c, v in free.items():
            w[c] = v + excess * v / ft
    return w


def _oos_returns(rep) -> pd.Series:
    if hasattr(rep, "period_returns") and len(rep.period_returns):
        return rep.period_returns                       # 调仓期收益
    eq = getattr(rep, "equity_curve", None)
    if eq is None or len(eq) < 2:
        return pd.Series(dtype=float)
    return eq.pct_change().dropna()                     # 逐日净值收益


def _with_oos_audit(store: Storage, kind: str, split_date: str, end, out: dict) -> dict:
    """登记样本外使用次数，并给出按试验次数折算的 Deflated Sharpe。"""
    from .engine.stats import deflated_sharpe
    n = _register_oos(store, kind, split_date, end)
    out["oos_uses"] = n
    oos = out["oos"]
    if isinstance(oos, dict):          # 多臂（如 base/fund）：看过几臂就算几次试验
        out["dsr"] = {k: deflated_sharpe(_oos_returns(r), n * len(oos)) for k, r in oos.items()}
    else:
        out["dsr"] = deflated_sharpe(_oos_returns(oos), n)
    return out


def _register_oos(store: Storage, kind: str, split_date: str, end: str | None) -> int:
    """样本外使用登记：同一类回测、样本外窗口重叠的每次运行都算一次"试验"。
    返回含本次在内的使用次数（>1 即该样本外已被看过，不再干净）。"""
    import json
    try:
        raw = store.get_state("oos_ledger")
    except (AttributeError, NotImplementedError):
        return 1
    ledger = json.loads(raw) if raw else []
    lo, hi = split_date, end or "9999-12-31"
    n = 1 + sum(1 for e in ledger if e["kind"] == kind
                and e["split"] <= hi and (e["end"] or "9999-12-31") >= lo)
    ledger.append({"kind": kind, "split": split_date, "end": end,
                   "at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")})
    store.set_state("oos_ledger", json.dumps(ledger, ensure_ascii=False))
    return n


def run_validated_strategy(store: Storage, config: Config, freq: str = "M",
                           top_n: int = 20, oos_split: float = 0.7,
                           start: str | None = None, end: str | None = None,
                           hold_buffer: float = 1.0) -> dict:
    """样本外纪律（铁律3）：训练段拟合 IC 权重，**只在样本外段测一次**。

    返回 {split_date, weights, train, oos}，oos 为对外头条指标（禁止在其上反复调参）。
    start/end 可限定回测窗口（如近 5 年）以降内存峰值。
    """
    adjust = config.datasource.get("adjust", "hfq")
    universe = filter_tradable_universe(store, config,
                                        build_universe(store, include_delisted=True))
    panel = _price_panel(store, universe, adjust, start, end)
    schedule = _rebalance_dates(panel.index, freq)
    if len(schedule) < 4:
        return {"error": "样本太短，无法切分训练/样本外。"}
    k = max(1, int(len(schedule) * oos_split))
    split_date = pd.Timestamp(schedule[k]).strftime("%Y-%m-%d")

    # 仅用训练段研究 IC → 定权重
    train_reports = run_factor_research(store, config, freq=freq,
                                        start=start, end=split_date)
    weights = ic_category_weights(train_reports)

    train = run_strategy_backtest(store, config, freq=freq, top_n=top_n,
                                  weights=weights, start=start, end=split_date,
                                  hold_buffer=hold_buffer)
    oos = run_strategy_backtest(store, config, freq=freq, top_n=top_n,
                                weights=weights, start=split_date, end=end,
                                hold_buffer=hold_buffer)
    return _with_oos_audit(store, "strategy", split_date, end,
                           {"split_date": split_date, "weights": weights,
                            "train": train, "oos": oos})


# ── 阶段三：事件驱动周级摆动回测 + 消融对照 ────────────────
def _regime_alloc(store: Storage, as_of: str, ma_n: int = 20,
                  idx: str = "000001.SH") -> float:
    """按上证指数 MA20+斜率判档，返回仓位系数（进攻1.0/平衡0.6/防守0.3）。

    只使用 ≤as_of 的指数数据（防前视）；指数数据不足时回退中性 0.6。
    """
    try:
        df = store.get_index(idx, end=as_of)
        if df is None or len(df) < ma_n + 5:
            return 0.6
        df = df.sort_values("date")
        closes = [float(x) for x in df["close"].tolist()]
        last = closes[-1]
        ma = sum(closes[-ma_n:]) / ma_n
        ma_prev = sum(closes[-ma_n - 5:-5]) / ma_n
        above = last > ma
        up = ma > ma_prev
        if above and up:
            return 1.0          # 进攻
        if (not above) and (not up):
            return 0.3          # 防守
        return 0.6              # 平衡
    except Exception:
        return 0.6


def _progress(progress: bool, msg: str) -> None:
    """进度打印（默认关闭，不影响确定性/输出）。"""
    if progress:
        print(msg, flush=True)


def _build_cross_by_t(store, config, universe, frames, schedule, progress, label):
    """逐调仓日构建 PIT 截面字典 {t: cross_df}（各子回测/因子研究共用，去重）。"""
    cross_by_t = {}
    _n, _tot = 0, len(schedule)
    for t in schedule:
        _n += 1
        if progress and (_n % max(1, _tot // 10) == 0 or _n == _tot):
            _progress(progress, f"[{label}] 截面 {_n}/{_tot} ({_n / _tot:.0%})")
        cross_by_t[t] = build_cross_section(store, config, symbols=universe,
                                            as_of=pd.Timestamp(t).strftime("%Y-%m-%d"),
                                            frames=frames)
    return cross_by_t


def run_swing_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "W", top_n: int = 10, max_per_industry: int = 2,
    weights: dict | None = None, gate: bool = True,
    exit_params=None, limit_pct: float | None = None, position_fn=None,
    regime: bool = False, entry_gate=None, symbols=None,
    frames=None, panel=None, progress: bool = False, label: str = "",
    cross_by_t: dict | None = None, random_seed: int | None = None,
):
    """组合级事件驱动回测：每周刷新候选(打分→入场闸门→单行业≤2→top_n)，
    每笔用 position_fn 逐日离场。返回 SwingReport。

    gate=False 时不做入场闸门(供闸门消融)；position_fn 可注入基线离场(供离场消融)。
    regime=True 时按上证指数 MA20 档位调整每期篮子仓位(进攻1.0/平衡0.6/防守0.3)。
    entry_gate 可注入替代入场闸门(默认反追高)；symbols 可覆盖股票池(默认全宇宙含退市)。
    frames/panel/cross_by_t 预构建后传入可省去重复加载(供 OOS 里 base/fund 共用)。
    progress=True 时打印阶段与逐调仓日进度；label 为进度前缀。
    random_seed 非空时改为种子化随机选股(供随机选股对照组，seed 固定可复现)。
    """
    from .engine.strategy_rules import ExitParams, entry_gate as _default_gate
    from .engine.swing_backtest import simulate_position
    return _run_swing(store, config, start, end, freq, top_n, max_per_industry,
                      weights, gate, exit_params or ExitParams(), limit_pct,
                      entry_gate or _default_gate, position_fn or simulate_position,
                      regime=regime, symbols=symbols, frames=frames, panel=panel,
                      progress=progress, label=label, cross_by_t=cross_by_t,
                      random_seed=random_seed)


def run_fundamental_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "W", top_n: int = 10, max_per_industry: int = 2,
    weights: dict | None = None, exit_params=None, limit_pct: float | None = None,
    position_fn=None, regime: bool = False, entry_gate=None,
    roe_min: float = 10.0, pe_max: float = 35.0,
    frames=None, panel=None, symbols=None,
    progress: bool = False, label: str = "",
    cross_by_t: dict | None = None,
):
    """右侧摆动回测 + 基本面安全门（ROE>roe_min 且 0<PE≤pe_max，缺失放行）。

    与 run_swing_backtest 完全同款：同调仓、同篮子、同离场，仅额外叠加基本面门，
    便于 A/B 对比基本面安全带来的增量（期望/回撤/交易数变化）。
    frames/panel/cross_by_t 预构建后传入可省去重复加载(供 OOS 里 base/fund 共用)。
    symbols 可覆盖股票池(默认全宇宙含退市)。
    progress=True 时打印阶段与逐调仓日进度；label 为进度前缀。
    """
    from .engine.strategy_rules import ExitParams, FundamentalParams, entry_gate as _default_gate
    from .engine.swing_backtest import simulate_position
    fund_params = FundamentalParams(roe_min=roe_min, pe_max=pe_max, require=True)
    return _run_swing(store, config, start, end, freq, top_n, max_per_industry,
                      weights, True, exit_params or ExitParams(), limit_pct,
                      entry_gate or _default_gate, position_fn or simulate_position,
                      regime=regime, fund_params=fund_params,
                      frames=frames, panel=panel, symbols=symbols,
                      progress=progress, label=label, cross_by_t=cross_by_t)


def _leftside_symbols(store, pool: str):
    """左侧回测股票池。

    pool='filtered' → 返回 None，沿用右侧因子打分/选股池，仅换入场闸门（对比干净）。
    pool='broad'    → 全部非科创板、非北交所/指数/B股的 A 股（含退市，防幸存者偏差）；
                      ST 由回测逐调仓日按当时简称剔除。
    """
    if pool == "filtered":
        return None
    df = store.get_symbols(include_delisted=True)
    out: list[str] = []
    for _, r in df.iterrows():
        sym = str(r["symbol"])
        ex = str(r.get("exchange") or "")
        if ex == "SSE":
            if not (sym.startswith("60") and not sym.startswith("688")
                    and not sym.startswith("689")):
                continue
        elif ex in ("SZSE", "SZ"):
            if not (sym.startswith("00") or sym.startswith("30")):
                continue
        else:
            continue                                  # BSE(北交所)/其他 剔除
        # ST 不在此按"现在的名字"剔除（前视+幸存者偏差），由回测逐日按当时简称剔除
        out.append(sym)
    return out


def run_leftside_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "W", pool: str = "broad",
    top_n: int | None = None, max_per_industry: int | None = None,
    weights: dict | None = None, exit_params=None, limit_pct: float | None = None,
    regime: bool = False, rsi_period: int = 14, rsi_oversold: float = 30.0,
):
    """左侧超卖均值回归回测：RSI<rsi_oversold 触发单笔买入，离场纪律与右侧完全一致。

    pool='broad'   → 全部非科创板非ST票 + 中性打分（闸门为唯一约束），测"左侧整体赚不赚钱"。
    pool='filtered'→ 沿用右侧因子池（低波+ROE+PE 打分 top_n），测"同一批好票里左 vs 右"。
    """
    from functools import partial

    from .engine.strategy_rules import ExitParams, OversoldParams, gate_oversold_rsi
    from .engine.swing_backtest import simulate_position

    if pool == "filtered":
        top_n = top_n if top_n is not None else 10
        max_per_industry = max_per_industry if max_per_industry is not None else 2
    else:
        # broad：每周「最超卖的前 top_n 只」篮子（中性打分，闸门为唯一约束）。
        # 不宜取全市场每只超卖股(top_n→几千)：会产出几十万笔交易，4GB 机器 OOM。
        top_n = top_n if top_n is not None else 30
        max_per_industry = max_per_industry if max_per_industry is not None else 100

    symbols = _leftside_symbols(store, pool)
    gate_fn = partial(gate_oversold_rsi, params=OversoldParams(
        rsi_period=rsi_period, rsi_oversold=rsi_oversold))
    return _run_swing(store, config, start, end, freq, top_n, max_per_industry,
                      weights, True, exit_params or ExitParams(), limit_pct,
                      gate_fn, simulate_position, regime=regime, symbols=symbols)


def run_validated_swing(store: Storage, config: Config, freq: str = "W",
                        top_n: int = 10, max_per_industry: int = 2,
                        oos_split: float = 0.7, regime: bool = False,
                        start: str | None = None, end: str | None = None) -> dict:
    """摆动回测的样本外纪律（铁律3）：训练段拟合 IC 权重，**样本外段只测一次**。

    返回 {split_date, weights, train, oos}；oos 为对外头条指标（禁止在其上反复调参）。
    start/end 可限定回测窗口（如近 5 年）以降内存峰值。
    """
    adjust = config.datasource.get("adjust", "hfq")
    universe = filter_tradable_universe(store, config,
                                        build_universe(store, include_delisted=True))
    panel = _price_panel(store, universe, adjust, start, end)
    schedule = _rebalance_dates(panel.index, freq)
    if len(schedule) < 4:
        return {"error": "样本太短，无法切分训练/样本外。"}
    k = max(1, int(len(schedule) * oos_split))
    split_date = pd.Timestamp(schedule[k]).strftime("%Y-%m-%d")

    train_reports = run_factor_research(store, config, freq=freq,
                                        start=start, end=split_date)
    weights = ic_category_weights(train_reports)

    train = run_swing_backtest(store, config, freq=freq, top_n=top_n,
                               max_per_industry=max_per_industry,
                               weights=weights, start=start, end=split_date,
                               regime=regime)
    oos = run_swing_backtest(store, config, freq=freq, top_n=top_n,
                             max_per_industry=max_per_industry,
                             weights=weights, start=split_date, end=end,
                             regime=regime)
    return _with_oos_audit(store, "swing", split_date, end,
                           {"split_date": split_date, "weights": weights,
                            "train": train, "oos": oos})


def run_validated_fund_backtest(
    store: Storage, config: Config, freq: str = "W",
    top_n: int = 10, max_per_industry: int = 2,
    oos_split: float = 0.7, regime: bool = False,
    start: str | None = None, end: str | None = None,
    roe_min: float = 10.0, pe_max: float = 35.0,
    progress: bool = False,
) -> dict:
    """右侧摆动回测 + 基本面安全门的样本外纪律（铁律3）。

    与 run_validated_swing 同款切分：训练段拟合 IC 权重，**样本外段只测一次**。
    在每一段内同时跑 无基本面(base) 与 加基本面(fund) 两线，A/B 以样本外段为准，
    避免全窗口回测里基本面门只是"在数据上过拟合出的加分"。

    返回 {split_date, weights, train:{base,fund}, oos:{base,fund}}；
    oos 为对外头条指标（禁止在其上反复调参）。
    """
    adjust = config.datasource.get("adjust", "hfq")
    universe = filter_tradable_universe(store, config,
                                        build_universe(store, include_delisted=True))
    panel = _price_panel(store, universe, adjust, start, end)
    schedule = _rebalance_dates(panel.index, freq)
    if len(schedule) < 4:
        return {"error": "样本太短，无法切分训练/样本外。"}
    k = max(1, int(len(schedule) * oos_split))
    split_date = pd.Timestamp(schedule[k]).strftime("%Y-%m-%d")
    del panel                                            # 仅用于切分，释放全窗口面板内存

    _progress(progress, "== 阶段1/4: 因子研究（IC 拟合）==")
    # 因子研究内部建 train 截面（frames=None），与回测截面(frames=...)语义不同，不可共享
    train_reports = run_factor_research(store, config, freq=freq,
                                        start=start, end=split_date,
                                        progress=progress)
    weights = ic_category_weights(train_reports)

    kw = dict(freq=freq, top_n=top_n, max_per_industry=max_per_industry,
              weights=weights, regime=regime)

    def _run(seg_start, seg_end, fund: bool, frames, panel, cross, label):
        _progress(progress, f"[{label}] 开始…")
        if fund:
            r = run_fundamental_backtest(store, config, start=seg_start,
                                         end=seg_end, roe_min=roe_min,
                                         pe_max=pe_max, frames=frames,
                                         panel=panel, progress=progress,
                                         label=label, cross_by_t=cross, **kw)
        else:
            r = run_swing_backtest(store, config, start=seg_start, end=seg_end,
                                   frames=frames, panel=panel,
                                   progress=progress, label=label,
                                   cross_by_t=cross, **kw)
        _progress(progress, f"[{label}] 完成：{r.n_trades} 笔")
        return r

    # 去重：每段帧+截面只建一次，base/fund 两线共用（串行、确定性不变）。
    results = {}
    _progress(progress, "== 阶段2/4: train 段建帧+截面 ==")
    train_frames = _ohlc_frames(store, universe, adjust, _warm_start(start),
                                split_date, config)
    train_panel = _price_panel(store, universe, adjust, start, split_date)
    train_schedule = _rebalance_dates(train_panel.index, freq)
    train_cross = _build_cross_by_t(store, config, universe, train_frames,
                                    train_schedule, progress, "train/截面")
    _progress(progress, "== 阶段3/4: train 段回测（base/fund 共用）==")
    results["train"] = {
        "base": _run(start, split_date, False, train_frames, train_panel,
                     train_cross, "train/base"),
        "fund": _run(start, split_date, True, train_frames, train_panel,
                     train_cross, "train/fund"),
    }
    del train_frames, train_panel, train_cross

    _progress(progress, "== 阶段4/4: oos 段建帧+截面+回测 ==")
    oos_frames = _ohlc_frames(store, universe, adjust, _warm_start(split_date),
                              end, config)
    oos_panel = _price_panel(store, universe, adjust, split_date, end)
    oos_schedule = _rebalance_dates(oos_panel.index, freq)
    oos_cross = _build_cross_by_t(store, config, universe, oos_frames,
                                  oos_schedule, progress, "oos/截面")
    results["oos"] = {
        "base": _run(split_date, end, False, oos_frames, oos_panel,
                     oos_cross, "oos/base"),
        "fund": _run(split_date, end, True, oos_frames, oos_panel,
                     oos_cross, "oos/fund"),
    }
    del oos_frames, oos_panel, oos_cross
    return _with_oos_audit(store, "swing-fund", split_date, end,
                           {"split_date": split_date, "weights": weights, **results})


def run_gate_ablation(store: Storage, config: Config, **kw) -> dict:
    """入场闸门消融：有/无闸门的期望对比。expectancy_delta>0 即闸门带来正期望增量。"""
    on = run_swing_backtest(store, config, gate=True, **kw)
    off = run_swing_backtest(store, config, gate=False, **kw)
    return {"gate_on": on, "gate_off": off,
            "expectancy_delta": round(on.expectancy - off.expectancy, 5)}


def run_exit_ablation(store: Storage, config: Config,
                      fixed_pct: float = 0.08, **kw) -> dict:
    """离场消融：吊灯移动止损 vs 涨停即清 / 固定止盈两基线的按笔盈亏比对比。"""
    from functools import partial

    from .engine.swing_backtest import (
        simulate_position, simulate_position_fixed_take,
        simulate_position_sell_on_limit,
    )
    trailing = run_swing_backtest(store, config, gate=True,
                                  position_fn=simulate_position, **kw)
    limit = run_swing_backtest(store, config, gate=True,
                               position_fn=simulate_position_sell_on_limit, **kw)
    fixed = run_swing_backtest(
        store, config, gate=True,
        position_fn=partial(simulate_position_fixed_take, fixed_pct=fixed_pct), **kw)
    return {
        "trailing": trailing, "sell_on_limit": limit, "fixed_pct": fixed,
        "pl_ratio_delta_vs_limit": round(
            trailing.profit_loss_ratio - limit.profit_loss_ratio, 3),
        "pl_ratio_delta_vs_fixed": round(
            trailing.profit_loss_ratio - fixed.profit_loss_ratio, 3),
    }


def _run_swing(store, config, start, end, freq, top_n, max_per_industry,
               weights, gate, exit_params, limit_pct, entry_gate, position_fn,
               regime: bool = False, symbols=None, fund_params=None,
               frames=None, panel=None, progress: bool = False,
               label: str = "", cross_by_t: dict | None = None,
               random_seed: int | None = None):
    from .engine.swing_backtest import SlotBook, _swing_metrics, ledger_equity

    adjust = config.datasource.get("adjust", "hfq")
    universe = (symbols if symbols is not None
                else filter_tradable_universe(store, config,
                                              build_universe(store, include_delisted=True)))
    if frames is None:
        frames = _ohlc_frames(store, universe, adjust, _warm_start(start), end, config)
    if panel is None:
        panel = _price_panel(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or not frames:
        return _swing_metrics([], config.backtest)
    schedule = _rebalance_dates(panel.index, freq)
    _progress(progress, f"[{label or '回测'}] 调仓日共 {len(schedule)} 个，逐日推进…")

    # 仓位槽账本：最多 top_n 并发、已持有不重复开仓，净值逐日盯市（替代旧"篮子连乘"）
    book = SlotBook(top_n)
    _tot = len(schedule)
    _n = 0
    for t in schedule:
        _n += 1
        if progress and (_n % max(1, _tot // 10) == 0 or _n == _tot):
            _progress(progress,
                      f"[{label or '回测'}] 调仓日 {_n}/{_tot} ({_n / _tot:.0%})")
        free = book.free(t)
        if free <= 0:
            continue
        as_of = pd.Timestamp(t).strftime("%Y-%m-%d")
        if cross_by_t is not None:
            cross = cross_by_t.get(t)
            if cross is None:
                continue
        else:
            cross = build_cross_section(store, config, symbols=universe, as_of=as_of,
                                        frames=frames)
        cross = exclude_st_rows(cross, config)          # 当日 ST（按当时简称）
        if cross.empty:
            continue
        scored = score_factors(cross, weights=weights)
        tradable = set(_tradable(panel, t, limit_pct))
        held = book.held(t)
        picks = _select_candidates(scored, tradable - held, frames, t, free,
                                   max_per_industry, gate, entry_gate,
                                   fund_params=fund_params,
                                   random_seed=random_seed, held=held)
        alloc = _regime_alloc(store, as_of) if regime else 1.0
        for sym in picks:
            fr = frames[sym]
            loc = fr.index.get_indexer([pd.Timestamp(t)])[0]
            if loc < 0:
                continue
            tr = position_fn(fr, entry_idx=loc, cost=config.backtest,
                             exit_params=exit_params, limit_pct=limit_pct)
            if tr is not None:
                book.add(t, tr, alloc)
    equity = ledger_equity(book.accepted, frames, panel.index, top_n)
    return _swing_metrics(book.trades, config.backtest, equity)


def _select_candidates(scored, tradable, frames, t, top_n, max_per_industry,
                       gate, entry_gate, fund_params=None, random_seed=None,
                       held=None) -> list:
    """按打分降序取候选：可成交 + 通过入场闸门 + 单行业≤上限，最多 top_n 只。
    fund_params 非空时，在价格门之上再叠加基本面安全门（ROE/PE）。
    random_seed 非空时，改为「种子化随机」从通过闸门的候选中等概率抽 top_n 只
    （供随机选股对照组；seed 固定 → 结果可复现，确定性纪律不变）。"""
    from .engine.strategy_rules import fundamental_safety
    import random
    has_ind = "industry" in scored.columns
    per_ind: dict = {}
    if held and has_ind:                 # 已持仓也占行业名额（单行业≤上限对整个组合生效）
        for ind in scored.loc[scored["symbol"].isin(held), "industry"].dropna():
            per_ind[ind] = per_ind.get(ind, 0) + 1
    passed: list = []                    # 通过全部闸门的候选（score 降序）
    ts = pd.Timestamp(t)
    for _, row in scored.iterrows():
        sym = row["symbol"]
        if sym not in tradable or sym not in frames:
            continue
        fr = frames[sym]
        loc = fr.index.get_indexer([ts])[0]
        if loc < 21:                       # 历史不足以算闸门指标
            continue
        if gate:
            if not entry_gate(fr.iloc[:loc + 1]).passed:
                continue
        if fund_params is not None:        # 基本面安全门（叠加）
            if not fundamental_safety(row.get("pe"), row.get("roe"),
                                      fund_params).passed:
                continue
        ind = row["industry"] if has_ind and pd.notna(row.get("industry")) else None
        if ind is not None and per_ind.get(ind, 0) >= max_per_industry:
            continue
        passed.append(sym)
        if ind is not None:
            per_ind[ind] = per_ind.get(ind, 0) + 1
        if not random_seed and len(passed) >= top_n:
            break
    if random_seed is not None:
        rng = random.Random(random_seed)
        pool = list(passed)
        rng.shuffle(pool)
        return pool[:top_n]
    return passed[:top_n]


def run_swing_portfolio(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "W", top_n: int = 10, max_per_industry: int = 2,
    weights: dict | None = None, gate: bool = True,
    exit_params=None, limit_pct: float | None = None,
    regime: bool = False, entry_gate=None, symbols=None,
    fund_params=None, frames=None, panel=None, progress: bool = False,
    label: str = "", cross_by_t: dict | None = None,
) -> "PortfolioReport":
    """组合级真实会计回测（Model A 滚动账本·仓位上限 top_n·逐日盯市）。

    与 run_swing_backtest 同款选股/闸门/离场，但净值按真实组合 P&L 计算：
    现金记账、逐日盯市、跨周持仓、现金空窗拖累、T+1 撮合。修复旧「每周篮子
    收益连乘」会计不自洽的问题。返回 PortfolioReport（每日净值曲线）。
    """
    from .engine.portfolio import simulate_portfolio
    from .engine.strategy_rules import ExitParams, entry_gate as _default_gate

    adjust = config.datasource.get("adjust", "hfq")
    universe = (symbols if symbols is not None
                else filter_tradable_universe(store, config,
                                              build_universe(store, include_delisted=True)))
    if frames is None:
        frames = _ohlc_frames(store, universe, adjust, _warm_start(start), end, config)
    if panel is None:
        panel = _price_panel(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or not frames:
        from .engine.portfolio import PortfolioReport
        return PortfolioReport(0.0, 0.0, 0.0, 0, 0.0)
    schedule = _rebalance_dates(panel.index, freq)

    if cross_by_t is None:
        _progress(progress, f"[{label or '组合'}] 构建 {len(schedule)} 个调仓日截面…")
        cross_by_t = {}
        for i, t in enumerate(schedule):
            cross_by_t[t] = build_cross_section(
                store, config, symbols=universe,
                as_of=pd.Timestamp(t).strftime("%Y-%m-%d"), frames=frames)
            if progress and (i + 1) % max(1, len(schedule) // 10) == 0:
                _progress(progress, f"[{label or '组合'}] 截面 {i+1}/{len(schedule)}")

    picks_by_t: dict = {}
    for t in schedule:
        cross = exclude_st_rows(cross_by_t.get(t), config)
        if cross is None or cross.empty:
            continue
        scored = score_factors(cross, weights=weights)
        tradable = set(_tradable(panel, t, limit_pct))
        picks_by_t[t] = _select_candidates(
            scored, tradable, frames, t, top_n, max_per_industry, gate,
            entry_gate or _default_gate, fund_params=fund_params)

    cash0 = float(config.backtest.get("cash", 1_000_000))
    return simulate_portfolio(frames, panel, schedule, picks_by_t, top_n,
                              config.backtest, exit_params or ExitParams(),
                              limit_pct, cash0)


def latest_candidates(store: Storage, config: Config, top_n: int = 10) -> list:
    """最新截面：打分排序取 top_n，并标注每只是否通过反追高入场闸门（供通知层）。

    返回 [{symbol, name, total_score, gate_passed, industry}]。纯读，无副作用。
    """
    from .engine.strategy_rules import entry_gate

    adjust = config.datasource.get("adjust", "hfq")
    universe = filter_tradable_universe(store, config,
                                        build_universe(store, include_delisted=True))
    cross = exclude_st_rows(build_cross_section(store, config, symbols=universe),
                            config)                            # as_of=None → 最新
    if cross.empty:
        return []
    if "status" in cross.columns:                     # 实盘候选剔除已退市（回测池才含退市）
        cross = cross[cross["status"] != "D"]
    scored = score_factors(cross).head(top_n)
    frames = _ohlc_frames(store, list(scored["symbol"]), adjust, None, None)
    rows = []
    for _, r in scored.iterrows():
        sym = r["symbol"]
        fr = frames.get(sym)
        passed = bool(entry_gate(fr).passed) if fr is not None and len(fr) >= 21 else False
        rows.append({
            "symbol": sym, "name": r.get("name", ""),
            "total_score": float(r.get("total_score", 0.0)),
            "gate_passed": passed,
            "industry": r.get("industry", "-") if pd.notna(r.get("industry", None)) else "-",
        })
    return rows


# ── 数据准备 ──────────────────────────────────────────────
def _price_panels(store: Storage, symbols, adjust, start, end):
    """(收盘宽表, 开盘宽表)，index=日期, columns=symbol，后复权。"""
    closes, opens = {}, {}
    for sym in symbols:
        d = store.get_daily(sym, adjust, start=start, end=end)
        if d.empty:
            continue
        d = d.set_index(pd.to_datetime(d["date"]))
        closes[sym], opens[sym] = d["close"], d["open"]
    if not closes:
        return pd.DataFrame(), pd.DataFrame()
    return pd.DataFrame(closes).sort_index(), pd.DataFrame(opens).sort_index()


def _price_panel(store: Storage, symbols, adjust, start, end) -> pd.DataFrame:
    """宽表：index=日期, columns=symbol, 值=后复权收盘。"""
    series = {}
    for sym in symbols:
        d = store.get_daily(sym, adjust, start=start, end=end)
        if d.empty:
            continue
        s = d.set_index(pd.to_datetime(d["date"]))["close"]
        series[sym] = s
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def _ohlc_frames(store: Storage, symbols, adjust, start, end, config=None) -> dict:
    """每 symbol 一张按日期索引、含技术指标（ma/rsi/atr…）的 OHLC 表。

    供事件驱动周级回测的入场闸门与逐仓离场逐日读取。空表跳过。
    已退市且行情在本段内提前终止的标的，在 frame.attrs["delist_haircut"] 标注退市折价
    （config.backtest.delist_haircut），离场引擎据此把"停在最后价"改为计入退市损失。
    """
    from .engine.indicators import add_indicators
    frames: dict = {}
    for sym in symbols:
        d = store.get_daily(sym, adjust, start=start, end=end)
        if d.empty:
            continue
        d = d.sort_values("date").reset_index(drop=True)
        ind = add_indicators(d)
        ind.index = pd.to_datetime(d["date"])
        frames[sym] = ind
    _attach_limits(store, frames)
    _mark_delisted(store, frames, config)
    return frames


def _attach_limits(store: Storage, frames: dict) -> None:
    """为每张 frame 加逐日涨跌停判定阈值列 limit_pct（分板块、按当日是否 ST）。"""
    import numpy as np

    from .data.symbols import is_st_name
    from .engine.limits import limit_series
    if not frames:
        return
    nh = store.get_name_history(list(frames))
    by_sym = {s: g.sort_values("start_date") for s, g in nh.groupby("symbol")} if len(nh) else {}
    for sym, fr in frames.items():
        st = None
        g = by_sym.get(sym)
        if g is not None:
            starts = pd.to_datetime(g["start_date"]).values
            flags = np.array([is_st_name(n) for n in g["name"]])
            k = np.searchsorted(starts, fr.index.values, side="right") - 1
            st = np.where(k >= 0, flags[np.clip(k, 0, None)], False)
        fr["limit_pct"] = limit_series(sym, fr.index, st)


def _warm_start(start: str | None, days: int = 200) -> str | None:
    """指标预热起点：段起点前推 ~200 自然日（≈130 交易日，覆盖 MA60/动量60/ATR）。
    只读取过去的数据，不产生前视；旧实现从段起点才开始算指标，样本外前 60 日
    mom_60/MA20 为空、入场闸门全拒。"""
    if not start:
        return start
    return (pd.Timestamp(start) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def _delist_haircut(config) -> float:
    if config is None:
        return 0.0
    return float((config.backtest or {}).get("delist_haircut", 0.5))


def _delisted_symbols(store: Storage) -> dict:
    """symbol → delist_date（status='D'）。"""
    meta = store.get_symbols(include_delisted=True)
    if meta.empty or "status" not in meta.columns:
        return {}
    d = meta[meta["status"] == "D"]
    return dict(zip(d["symbol"], d["delist_date"]))


def _delisted_in_panel(store: Storage, panel: pd.DataFrame) -> set:
    """面板内行情提前终止的已退市标的。"""
    if panel.empty:
        return set()
    last = panel.index[-1]
    out = set()
    for sym in _delisted_symbols(store):
        if sym in panel.columns:
            lv = panel[sym].last_valid_index()
            if lv is not None and lv < last:
                out.add(sym)
    return out


def _mark_delisted(store: Storage, frames: dict, config) -> None:
    h = _delist_haircut(config)
    if not frames or h <= 0:
        return
    seg_last = max(fr.index[-1] for fr in frames.values())
    for sym, dd in _delisted_symbols(store).items():
        fr = frames.get(sym)
        if fr is None or fr.index[-1] >= seg_last:
            continue                       # 本段内未终止交易 → 与退市无关
        fr.attrs["delist_haircut"] = h


def _rebalance_dates(index: pd.DatetimeIndex, freq: str) -> list:
    """调仓日：'M'/'W'=每月/每周最后一个交易日；整数 N=每 N 个交易日。"""
    idx = pd.DatetimeIndex(index)
    if isinstance(freq, int) or (isinstance(freq, str) and freq.isdigit()):
        n = int(freq)
        return list(idx[::n])
    period = {"M": "M", "W": "W", "Q": "Q"}.get(str(freq).upper(), "M")
    s = pd.Series(idx, index=idx)
    last = s.groupby(idx.to_period(period)).last()
    return list(last.values)


def _tradable(panel: pd.DataFrame, t, limit_pct: float | None = None) -> set:
    """t 日可成交：有价、非停牌(前一交易日也有价)、且当日未涨跌停锁死。

    limit_pct=None → 按板块/日期取阈值（主板 10%、创业板注册制后 20%、北交所 30%）；
    给定浮点数则全市场统一（兼容旧调用）。
    """
    from .engine.limits import limit_threshold
    if t not in panel.index:
        return set()
    pos = panel.index.get_loc(t)
    today = panel.iloc[pos]
    if pos == 0:
        return set(today.dropna().index)
    prev = panel.iloc[pos - 1]
    out = set()
    for sym in panel.columns:
        p, q = today.get(sym), prev.get(sym)
        if pd.isna(p) or pd.isna(q) or q <= 0:
            continue                                  # 停牌/缺价
        thr = limit_pct if limit_pct is not None else limit_threshold(sym, t)
        if abs(p / q - 1) >= thr:                     # 涨跌停锁死 → 无法成交
            continue
        out.add(sym)
    return out


def _load_benchmark(store: Storage, config: Config, panel: pd.DataFrame) -> pd.Series:
    """优先用库里存的真实基准指数（如沪深300）；缺失则退化为等权全市场代理。"""
    code = str(config.backtest.get("benchmark", "")).strip()
    if code:
        idx = store.get_index(code)
        if idx is not None and not idx.empty:
            s = idx.set_index(pd.to_datetime(idx["date"]))["close"].sort_index()
            if len(s) >= 2:
                return s
    return _equal_weight_benchmark(panel)


def _equal_weight_benchmark(panel: pd.DataFrame) -> pd.Series:
    """等权全市场基准（缺真实指数时的代理）：每日横截面平均收益累乘。"""
    rets = panel.pct_change().mean(axis=1).fillna(0.0)
    return (1 + rets).cumprod()
