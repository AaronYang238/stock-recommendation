"""命令行入口：数据更新 / 选股 / 回测 / 演示填充。

  python -m aselect.cli seed       # 用合成数据填充本地库（离线可跑）
  python -m aselect.cli update     # 增量更新真实数据（akshare）
  python -m aselect.cli screen     # 跑一次默认筛选 + 因子打分
  python -m aselect.cli backtest 600519
"""
from __future__ import annotations

import argparse
import logging

from .config import load_config
from .data import build_cross_section, update_daily, update_index, update_symbols
from .data.symbols import attach_industry
from .datasource import get_datasource
from .datasource.synthetic_source import SyntheticSource
from .engine import score_factors, screen
from .engine.backtest import run_ma_backtest
from .engine.factors import DEFAULT_FACTORS
from .engine.screener import Condition, FilterSpec
from .storage import get_storage


def _seed(args):
    """离线演示：合成源 → 写入库。不联网，可复现。"""
    cfg = load_config()
    store = get_storage(cfg)
    ds = SyntheticSource()
    update_symbols(ds, store)
    syms = [r["symbol"] for r in ds.list_symbols().to_dict("records")]
    store.upsert_fundamentals(ds.fundamentals(syms))
    update_daily(ds, store, syms, adjust=cfg.datasource.get("adjust", "hfq"))
    print(f"已用合成数据填充 {len(syms)} 只股票。")
    store.close()


def _update(args):
    cfg = load_config()
    store = get_storage(cfg)
    ds = get_datasource(cfg)
    print(f"数据源: {ds.name}")

    # 各阶段相互独立、单阶段失败不拖垮整体（需求 §3.1：失败重试 + 告警，不中断）
    try:
        update_symbols(ds, store)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 股票列表更新失败（用已有列表继续）：{e}")

    syms = store.get_symbols()["symbol"].tolist()[: args.limit]
    if not syms:
        print("本地无股票列表，先确认网络可达 akshare 后重试。")
        store.close()
        return

    # 行情优先：逐只拉取，已在 update_daily 内对单只失败做容错
    n = update_daily(ds, store, syms, adjust=cfg.datasource.get("adjust", "hfq"))

    # 基本面 + 行业（行业供因子行业中性化）；失败仅告警，不影响行情
    try:
        from .data.symbols import attach_industry
        fund = ds.fundamentals(syms)
        try:
            fund = attach_industry(fund, ds.industry_map())
        except Exception as ie:  # noqa: BLE001
            print(f"⚠️ 行业映射获取失败，行业中性将退化：{ie}")
        store.upsert_fundamentals(fund)
        print("基本面 + 行业已更新。")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 基本面拉取失败，已跳过（PE/ROE 等暂缺，不影响行情/技术指标）：{e}")

    print(f"已处理 {len(syms)} 只，新增/更新日线 {n} 行。")
    store.close()


def _screen(args):
    cfg = load_config()
    store = get_storage(cfg)
    cross = build_cross_section(store, cfg)
    scored = score_factors(cross)
    spec = FilterSpec(
        name="价值+质量示例",
        conditions=[Condition("pe", "<", 30), Condition("roe", ">", 10)],
        sort_by="total_score", ascending=False, limit=args.top,
    )
    result = screen(scored, spec)
    cols = [c for c in ["symbol", "name", "board", "status_label", "pe", "roe",
                        "total_score"]
            if c in result.columns]
    print(f"\n[{spec.name}] 命中 {len(result)} 只：")
    print(result[cols].to_string(index=False))
    print(f"\n{cfg.disclaimer}")
    store.close()


def _sync(args):
    """收盘后全量同步（cron/调度入口）：列表→日线→基本面+行业→基准指数。各阶段独立容错。"""
    cfg = load_config()
    store = get_storage(cfg)
    ds = get_datasource(cfg)
    print(f"[sync] 数据源: {ds.name}")
    try:
        update_symbols(ds, store)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 股票列表失败（用已有列表继续）：{e}")

    syms = store.get_symbols()["symbol"].tolist()
    if args.limit:
        syms = syms[: args.limit]
    n = update_daily(ds, store, syms, adjust=cfg.datasource.get("adjust", "hfq"))

    try:
        fund = ds.fundamentals(syms)
        try:
            fund = attach_industry(fund, ds.industry_map())
        except Exception as ie:  # noqa: BLE001
            print(f"  ⚠️ 行业映射失败：{ie}")
        store.upsert_fundamentals(fund)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 基本面失败（PE/ROE 暂缺）：{e}")

    bench = str(cfg.backtest.get("benchmark", "")).strip()
    if bench:
        try:
            update_index(ds, store, bench)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 基准指数 {bench} 失败：{e}")

    st = store.data_status()
    print(f"[sync] 完成。最新日线 {st.get('last_daily_date')} | "
          f"有财务 {st.get('n_with_fundamentals')} 只 | 本次写入日线 {n} 行")
    store.close()


def _schedule(args):
    """启动 APScheduler 守护：每个交易日收盘后自动跑 sync。"""
    from .scheduler import main as sched_main
    sched_main()


def _factor_ic(args):
    """单因子 walk-forward IC 研究（CLAUDE：新因子先单独验 IC 再纳入加权）。"""
    from .runner import run_factor_research
    cfg = load_config()
    store = get_storage(cfg)
    reps = run_factor_research(store, cfg, freq=args.freq, start=args.start, end=args.end)
    if not reps:
        print("数据不足，先 seed/sync 后再试。")
        store.close(); return
    print("\n单因子 walk-forward IC（|IC|稳定 0.03~0.05 即好因子，ICIR>0.5 佳）：")
    print(f"{'因子':<14}{'IC均值':>8}{'ICIR':>8}{'IC胜率':>8}{'分层多空':>10}{'样本':>6}")
    for name, r in sorted(reps.items(), key=lambda kv: -abs(kv[1].ic_mean)):
        print(f"{name:<14}{r.ic_mean:>8}{r.icir:>8}{r.ic_win_rate:>8.0%}"
              f"{r.quantile_spread:>10}{r.n:>6}")
    print(f"\n{cfg.disclaimer}")
    store.close()


def _strategy(args):
    """股票池级·walk-forward·多因子回测（含摩擦/涨跌停/基准/IC，PIT 防前视）。
    --oos 给定时走样本外纪律：训练段拟合 IC 权重、只在样本外段测一次。"""
    from .runner import run_strategy_backtest, run_validated_strategy
    cfg = load_config()
    store = get_storage(cfg)

    if args.oos:
        v = run_validated_strategy(store, cfg, freq=args.freq, top_n=args.top,
                                   oos_split=args.oos)
        if "error" in v:
            print(v["error"]); store.close(); return
        w = ", ".join(f"{k}:{x:.2f}" for k, x in v["weights"].items())
        print(f"\n[样本外验证] 切分日 {v['split_date']} | 训练段拟合 IC 权重: {w}")
        for tag, rep in (("训练段", v["train"]), ("样本外(只测一次)", v["oos"])):
            print(f"  [{tag}] 总收益 {rep.total_return:.2%} | 年化 {rep.annual_return:.2%}"
                  f" | 夏普 {rep.sharpe} | 超额 {rep.excess_return:.2%}"
                  f" | IC {rep.ic_mean} | 盈亏比 {rep.profit_loss_ratio}")
        print(f"\n{cfg.disclaimer}")
        store.close(); return

    rep = run_strategy_backtest(store, cfg, start=args.start, end=args.end,
                                freq=args.freq, top_n=args.top)
    print(f"\n[策略回测] 调仓 {args.freq} · 持仓 top{args.top} · "
          f"股票池含退市/ST（防幸存者偏差）")
    print(f"  调仓次数 {rep.n_rebalances} | 平均持仓 {rep.avg_positions} 只 | "
          f"平均换手 {rep.avg_turnover:.2f}")
    print(f"  总收益 {rep.total_return:.2%} | 年化 {rep.annual_return:.2%} | "
          f"夏普 {rep.sharpe} | 最大回撤 {rep.max_drawdown:.2%}")
    print(f"  基准收益 {rep.benchmark_return:.2%} | 超额 {rep.excess_return:.2%}")
    print(f"  IC 均值 {rep.ic_mean} | ICIR {rep.ic_ir} | IC胜率 {rep.ic_win_rate:.0%}")
    print(f"  期望值 {rep.expectancy:.4f}/期 | 盈亏比 {rep.profit_loss_ratio} | "
          f"期胜率 {rep.win_rate:.0%}")
    print(f"\n{cfg.disclaimer}")
    store.close()


def _backtest(args):
    cfg = load_config()
    store = get_storage(cfg)
    daily = store.get_daily(args.symbol, cfg.datasource.get("adjust", "hfq"))
    if daily.empty:
        print(f"无 {args.symbol} 数据，请先 seed/update。")
        return
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    res = run_ma_backtest(daily, cfg.backtest)
    print(f"回测 {args.symbol}（engine={res.engine}）:")
    print(f"  总收益 {res.total_return:.2%} | 年化 {res.annual_return:.2%} | "
          f"夏普 {res.sharpe} | 最大回撤 {res.max_drawdown:.2%} | 交易 {res.trades} 次")
    print(f"\n{cfg.disclaimer}")
    store.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="aselect")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed").set_defaults(func=_seed)

    up = sub.add_parser("update")
    up.add_argument("--limit", type=int, default=50)
    up.set_defaults(func=_update)

    sc = sub.add_parser("screen")
    sc.add_argument("--top", type=int, default=20)
    sc.set_defaults(func=_screen)

    bt = sub.add_parser("backtest")
    bt.add_argument("symbol")
    bt.set_defaults(func=_backtest)

    sy = sub.add_parser("sync", help="全量同步数据（cron 入口）")
    sy.add_argument("--limit", type=int, default=0, help="0=全部")
    sy.set_defaults(func=_sync)

    sub.add_parser("schedule", help="启动调度守护（收盘后自动 sync）").set_defaults(func=_schedule)

    fic = sub.add_parser("factor-ic", help="单因子 walk-forward IC 研究")
    fic.add_argument("--freq", default="M")
    fic.add_argument("--start")
    fic.add_argument("--end")
    fic.set_defaults(func=_factor_ic)

    stg = sub.add_parser("strategy", help="股票池级·walk-forward·多因子回测")
    stg.add_argument("--top", type=int, default=20)
    stg.add_argument("--freq", default="M", help="调仓频率：M/W/Q 或整数交易日")
    stg.add_argument("--start")
    stg.add_argument("--end")
    stg.add_argument("--oos", type=float, default=0.0,
                     help="样本外比例(如0.7)：训练段拟合IC权重，样本外段只测一次")
    stg.set_defaults(func=_strategy)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
