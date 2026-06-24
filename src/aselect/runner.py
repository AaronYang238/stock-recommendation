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
                        end: str | None = None) -> dict:
    """对每个因子做 walk-forward 单因子 IC 研究（逐期 PIT 截面 → 因子处理值 → IC）。
    返回 {因子名: FactorICReport}。"""
    from .engine.factors import DEFAULT_FACTORS, process_factor
    from .engine.factor_research import summarize

    factors = factors or DEFAULT_FACTORS
    adjust = config.datasource.get("adjust", "hfq")
    universe = build_universe(store, include_delisted=True)
    panel = _price_panel(store, universe, adjust, start, end)
    if panel.shape[0] < 2 or panel.shape[1] == 0:
        return {}
    schedule = _rebalance_dates(panel.index, freq)

    # 逐调仓日构建一次 PIT 截面（各因子共用）
    cross_by_t = {t: build_cross_section(store, config, symbols=universe,
                                         as_of=pd.Timestamp(t).strftime("%Y-%m-%d"))
                  for t in schedule}

    reports = {}
    for defs in factors.values():
        for d in defs:
            sbd = {}
            for t, cross in cross_by_t.items():
                if cross.empty or d.field not in cross.columns:
                    continue
                ind = cross["industry"] if "industry" in cross.columns else None
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
    return {cat: w / total for cat, w in raw.items()}


def run_validated_strategy(store: Storage, config: Config, freq: str = "M",
                           top_n: int = 20, oos_split: float = 0.7) -> dict:
    """样本外纪律（铁律3）：训练段拟合 IC 权重，**只在样本外段测一次**。

    返回 {split_date, weights, train, oos}，oos 为对外头条指标（禁止在其上反复调参）。
    """
    adjust = config.datasource.get("adjust", "hfq")
    universe = build_universe(store, include_delisted=True)
    panel = _price_panel(store, universe, adjust, None, None)
    schedule = _rebalance_dates(panel.index, freq)
    if len(schedule) < 4:
        return {"error": "样本太短，无法切分训练/样本外。"}
    k = max(1, int(len(schedule) * oos_split))
    split_date = pd.Timestamp(schedule[k]).strftime("%Y-%m-%d")

    # 仅用训练段研究 IC → 定权重
    train_reports = run_factor_research(store, config, freq=freq, end=split_date)
    weights = ic_category_weights(train_reports)

    train = run_strategy_backtest(store, config, freq=freq, top_n=top_n,
                                  weights=weights, end=split_date)
    oos = run_strategy_backtest(store, config, freq=freq, top_n=top_n,
                                weights=weights, start=split_date)
    return {"split_date": split_date, "weights": weights, "train": train, "oos": oos}


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
