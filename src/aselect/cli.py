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
from .data import build_cross_section, update_daily, update_symbols
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
    update_symbols(ds, store)
    syms = store.get_symbols()["symbol"].tolist()[: args.limit]
    store.upsert_fundamentals(ds.fundamentals(syms))
    update_daily(ds, store, syms, adjust=cfg.datasource.get("adjust", "hfq"))
    print(f"已更新 {len(syms)} 只。")
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

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
