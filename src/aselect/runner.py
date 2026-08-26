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
from .data import build_cross_section, build_universe
from .engine import score_factors
from .engine.factor_backtest import FactorBacktestReport, simulate
from .storage import Storage


def run_strategy_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "M", top_n: int = 20,
    benchmark_prices: pd.Series | None = None,
    limit_pct: float = 0.095,
    weights: dict | None = None,
) -> FactorBacktestReport:
    adjust = config.datasource.get("adjust", "hfq")
    universe = build_universe(store, include_delisted=True)   # 含退市/ST
    panel = _price_panel(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or panel.shape[1] == 0:
        return simulate(panel, [], {}, {}, pd.Series(dtype=float), config.backtest)

    schedule = _rebalance_dates(panel.index, freq)
    selections: dict = {}
    scores: dict = {}
    for t in schedule:
        as_of = pd.Timestamp(t).strftime("%Y-%m-%d")
        cross = build_cross_section(store, config, symbols=universe, as_of=as_of)
        if cross.empty:
            selections[t], scores[t] = {}, pd.Series(dtype=float)
            continue
        scored = score_factors(cross, weights=weights)   # weights=None 即等权
        tradable = _tradable(panel, t, limit_pct)
        cand = scored[scored["symbol"].isin(tradable)]
        sel = cand.head(top_n)
        if len(sel):
            w = 1.0 / len(sel)
            selections[t] = {s: w for s in sel["symbol"]}
        else:
            selections[t] = {}
        scores[t] = scored.set_index("symbol")["total_score"]

    if benchmark_prices is None:
        benchmark_prices = _load_benchmark(store, config, panel)
    bench = benchmark_prices.reindex(panel.index).ffill()
    return simulate(panel, list(schedule), selections, scores, bench, config.backtest)


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
    universe = build_universe(store, include_delisted=True)
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
                sbd[t] = pd.Series(proc.values, index=cross["symbol"].values)
            if sbd:
                reports[d.name] = summarize(d.name, sbd, panel, schedule)
    return reports


def ic_category_weights(reports: dict, factors: dict | None = None) -> dict:
    """由单因子 IC 报告聚合出类别权重（按类别平均 IC 的正部归一；全非正则等权）。"""
    from .engine.factors import DEFAULT_FACTORS
    factors = factors or DEFAULT_FACTORS
    name2cat = {d.name: cat for cat, defs in factors.items() for d in defs}
    cat_ic: dict = {}
    for name, r in reports.items():
        cat = name2cat.get(name)
        if cat:
            cat_ic.setdefault(cat, []).append(r.ic_mean)
    raw = {cat: max(sum(v) / len(v), 0.0) for cat, v in cat_ic.items()}
    total = sum(raw.values())
    if total <= 0:
        return {cat: 1.0 for cat in cat_ic}          # 无正 IC → 等权
    weights = {cat: w / total for cat, w in raw.items()}
    # 资金流(net_inflow)作增强因子：权重上限 0.25（grill-me Q5），超额按比例分给其他类别
    if "moneyflow" in weights and weights["moneyflow"] > 0.25:
        excess = weights["moneyflow"] - 0.25
        weights["moneyflow"] = 0.25
        others = {c: w for c, w in weights.items() if c != "moneyflow"}
        ot = sum(others.values()) or 1.0
        for c in others:
            weights[c] = others[c] + excess * (others[c] / ot)
    return weights


def run_validated_strategy(store: Storage, config: Config, freq: str = "M",
                           top_n: int = 20, oos_split: float = 0.7,
                           start: str | None = None, end: str | None = None) -> dict:
    """样本外纪律（铁律3）：训练段拟合 IC 权重，**只在样本外段测一次**。

    返回 {split_date, weights, train, oos}，oos 为对外头条指标（禁止在其上反复调参）。
    start/end 可限定回测窗口（如近 5 年）以降内存峰值。
    """
    adjust = config.datasource.get("adjust", "hfq")
    universe = build_universe(store, include_delisted=True)
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
                                  weights=weights, start=start, end=split_date)
    oos = run_strategy_backtest(store, config, freq=freq, top_n=top_n,
                                weights=weights, start=split_date, end=end)
    return {"split_date": split_date, "weights": weights, "train": train, "oos": oos}


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
    exit_params=None, limit_pct: float = 0.095, position_fn=None,
    regime: bool = False, entry_gate=None, symbols=None,
    frames=None, panel=None, progress: bool = False, label: str = "",
    cross_by_t: dict | None = None,
):
    """组合级事件驱动回测：每周刷新候选(打分→入场闸门→单行业≤2→top_n)，
    每笔用 position_fn 逐日离场。返回 SwingReport。

    gate=False 时不做入场闸门(供闸门消融)；position_fn 可注入基线离场(供离场消融)。
    regime=True 时按上证指数 MA20 档位调整每期篮子仓位(进攻1.0/平衡0.6/防守0.3)。
    entry_gate 可注入替代入场闸门(默认反追高)；symbols 可覆盖股票池(默认全宇宙含退市)。
    frames/panel/cross_by_t 预构建后传入可省去重复加载(供 OOS 里 base/fund 共用)。
    progress=True 时打印阶段与逐调仓日进度；label 为进度前缀。
    """
    from .engine.strategy_rules import ExitParams, entry_gate as _default_gate
    from .engine.swing_backtest import simulate_position
    return _run_swing(store, config, start, end, freq, top_n, max_per_industry,
                      weights, gate, exit_params or ExitParams(), limit_pct,
                      entry_gate or _default_gate, position_fn or simulate_position,
                      regime=regime, symbols=symbols, frames=frames, panel=panel,
                      progress=progress, label=label, cross_by_t=cross_by_t)


def run_fundamental_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "W", top_n: int = 10, max_per_industry: int = 2,
    weights: dict | None = None, exit_params=None, limit_pct: float = 0.095,
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
    pool='broad'    → 全部非科创板、非 ST、非北交所/指数/B股的 A 股（含退市，防幸存者偏差）。
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
        name = str(r.get("name") or "").upper()
        status = str(r.get("status") or "")
        if status == "ST" or "ST" in name:            # 剔除 ST/*ST
            continue
        out.append(sym)
    return out


def run_leftside_backtest(
    store: Storage, config: Config,
    start: str | None = None, end: str | None = None,
    freq: str = "W", pool: str = "broad",
    top_n: int | None = None, max_per_industry: int | None = None,
    weights: dict | None = None, exit_params=None, limit_pct: float = 0.095,
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
    universe = build_universe(store, include_delisted=True)
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
    return {"split_date": split_date, "weights": weights, "train": train, "oos": oos}


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
    universe = build_universe(store, include_delisted=True)
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
    train_frames = _ohlc_frames(store, universe, adjust, start, split_date)
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
    oos_frames = _ohlc_frames(store, universe, adjust, split_date, end)
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
    return {"split_date": split_date, "weights": weights, **results}


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
               label: str = "", cross_by_t: dict | None = None):
    from .engine.swing_backtest import SwingReport, _swing_metrics

    adjust = config.datasource.get("adjust", "hfq")
    universe = symbols if symbols is not None else build_universe(store, include_delisted=True)
    if frames is None:
        frames = _ohlc_frames(store, universe, adjust, start, end)
    if panel is None:
        panel = _price_panel(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or not frames:
        return _swing_metrics([], config.backtest)
    schedule = _rebalance_dates(panel.index, freq)
    _progress(progress, f"[{label or '回测'}] 调仓日共 {len(schedule)} 个，逐日推进…")

    trades = []
    baskets: dict = {}                    # 每调仓日的一篮子净收益（等权）
    _tot = len(schedule)
    _n = 0
    for t in schedule:
        _n += 1
        if progress and (_n % max(1, _tot // 10) == 0 or _n == _tot):
            _progress(progress,
                      f"[{label or '回测'}] 调仓日 {_n}/{_tot} ({_n / _tot:.0%})")
        as_of = pd.Timestamp(t).strftime("%Y-%m-%d")
        if cross_by_t is not None:
            cross = cross_by_t.get(t)
            if cross is None:
                continue
        else:
            cross = build_cross_section(store, config, symbols=universe, as_of=as_of,
                                        frames=frames)
        if cross.empty:
            continue
        scored = score_factors(cross, weights=weights)
        tradable = set(_tradable(panel, t, limit_pct))
        picks = _select_candidates(scored, tradable, frames, t, top_n,
                                   max_per_industry, gate, entry_gate,
                                   fund_params=fund_params)
        basket = []
        for sym in picks:
            fr = frames[sym]
            loc = fr.index.get_indexer([pd.Timestamp(t)])[0]
            if loc < 0:
                continue
            tr = position_fn(fr, entry_idx=loc, cost=config.backtest,
                             exit_params=exit_params, limit_pct=limit_pct)
            if tr is not None:
                trades.append(tr)
                basket.append(tr.ret)
        if basket:
            alloc = _regime_alloc(store, as_of) if regime else 1.0
            baskets[pd.Timestamp(t)] = float(pd.Series(basket).mean()) * alloc
    return _swing_metrics(trades, config.backtest, baskets)


def _select_candidates(scored, tradable, frames, t, top_n, max_per_industry,
                       gate, entry_gate, fund_params=None) -> list:
    """按打分降序取候选：可成交 + 通过入场闸门 + 单行业≤上限，最多 top_n 只。
    fund_params 非空时，在价格门之上再叠加基本面安全门（ROE/PE）。"""
    from .engine.strategy_rules import fundamental_safety
    has_ind = "industry" in scored.columns
    per_ind: dict = {}
    picks: list = []
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
        picks.append(sym)
        if ind is not None:
            per_ind[ind] = per_ind.get(ind, 0) + 1
        if len(picks) >= top_n:
            break
    return picks


def latest_candidates(store: Storage, config: Config, top_n: int = 10) -> list:
    """最新截面：打分排序取 top_n，并标注每只是否通过反追高入场闸门（供通知层）。

    返回 [{symbol, name, total_score, gate_passed, industry}]。纯读，无副作用。
    """
    from .engine.strategy_rules import entry_gate

    adjust = config.datasource.get("adjust", "hfq")
    universe = build_universe(store, include_delisted=True)
    cross = build_cross_section(store, config, symbols=universe)   # as_of=None → 最新
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


def _ohlc_frames(store: Storage, symbols, adjust, start, end) -> dict:
    """每 symbol 一张按日期索引、含技术指标（ma/rsi/atr…）的 OHLC 表。

    供事件驱动周级回测的入场闸门与逐仓离场逐日读取。空表跳过。
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
    return frames


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


def _tradable(panel: pd.DataFrame, t, limit_pct: float) -> set:
    """t 日可成交：有价、非停牌(前一交易日也有价)、且当日未涨跌停锁死。"""
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
        if abs(p / q - 1) >= limit_pct:               # 涨跌停锁死 → 无法成交
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
