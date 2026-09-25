"""历史 PIT 数据回填：逐日估值 / 全历史财报 / 行业归属历史 / 简称历史。

为什么需要（P0 修复）：日常 sync 每次只写"今天的估值 + 最新一期财报"，库里没有历史，
按披露日回看 2021~2025 时价值/成长/质量因子与行业/市值全为空 → 历史回测实际上只剩
价格因子，且没有做行业/市值中性化。本模块把历史补齐。

全部幂等、可断点续跑：已存在的交易日/标的会跳过。数据源不支持的项记日志后跳过。
"""
from __future__ import annotations

import logging

import pandas as pd

from ..datasource import DataSource
from ..storage import Storage

log = logging.getLogger(__name__)


def backfill_valuation(ds: DataSource, store: Storage, start: str, end: str,
                       symbols: list[str] | None = None, progress=print) -> int:
    """逐日估值。优先按交易日一次拉全市场（tushare daily_basic）；否则按标的拉（baostock）。"""
    n = 0
    try:
        dates = ds.trade_dates(start, end)
        done = store.valuation_dates()
        todo = [d for d in dates if d not in done]
        progress(f"[估值] 交易日 {len(dates)} 个，待补 {len(todo)} 个")
        for i, d in enumerate(todo, 1):
            df = ds.valuation_by_date(d)
            if df is not None and not df.empty:
                store.upsert_valuation(df)
                n += len(df)
            if i % 50 == 0 or i == len(todo):
                progress(f"[估值] {i}/{len(todo)}（{d}）")
        return n
    except NotImplementedError:
        pass
    for i, sym in enumerate(symbols or [], 1):
        try:
            df = ds.valuation_history(sym, start, end)
        except NotImplementedError:
            log.warning("数据源 %s 不支持估值历史，跳过", ds.name)
            return n
        except Exception as e:  # noqa: BLE001
            log.warning("估值 %s 失败: %s", sym, e)
            continue
        if df is not None and not df.empty:
            store.upsert_valuation(df)
            n += len(df)
        if i % 200 == 0:
            progress(f"[估值] 标的 {i}/{len(symbols)}")
    return n


def backfill_fundamentals(ds: DataSource, store: Storage, symbols: list[str],
                          start: str, progress=print) -> int:
    """全历史财报（报告期 + 披露日，同期多次披露保留最早一次）。已覆盖 start 附近的标的跳过。"""
    have = store.get_fundamentals(symbols)
    first = (have.groupby("symbol")["date"].min() if not have.empty
             else pd.Series(dtype=object))
    cutoff = (pd.Timestamp(start) + pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    todo = [s for s in symbols if not (s in first.index and str(first[s]) <= cutoff)]
    progress(f"[财报] 标的 {len(symbols)} 只，待补 {len(todo)} 只")
    n = 0
    for i, sym in enumerate(todo, 1):
        try:
            df = ds.fundamentals_history(sym, start)
        except NotImplementedError:
            log.warning("数据源 %s 不支持财报历史，跳过", ds.name)
            return n
        except Exception as e:  # noqa: BLE001
            log.warning("财报 %s 失败: %s", sym, e)
            continue
        if df is not None and not df.empty:
            store.upsert_fundamentals(df)
            n += len(df)
        if i % 200 == 0 or i == len(todo):
            progress(f"[财报] {i}/{len(todo)}")
    return n


def backfill_industry(ds: DataSource, store: Storage, progress=print) -> int:
    try:
        df = ds.industry_history()
    except NotImplementedError:
        log.warning("数据源 %s 不支持行业历史，截面回退 symbols.industry（当前行业）", ds.name)
        return 0
    if df is None or df.empty:
        return 0
    store.upsert_industry_history(df)
    progress(f"[行业] 写入 {len(df)} 条归属记录")
    return len(df)


def backfill_names(ds: DataSource, store: Storage, symbols: list[str],
                   progress=print) -> int:
    try:
        df = ds.name_history(None if ds.name == "tushare" else symbols)
    except NotImplementedError:
        log.warning("数据源 %s 不支持简称历史，ST 将无法按日判定", ds.name)
        return 0
    if df is None or df.empty:
        return 0
    store.upsert_name_history(df)
    progress(f"[简称] 写入 {len(df)} 条更名记录")
    return len(df)


def run_backfill(ds: DataSource, store: Storage, start: str, end: str | None = None,
                 what: tuple = ("valuation", "fundamentals", "industry", "names"),
                 symbols: list[str] | None = None, progress=print) -> dict:
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    syms = symbols or store.get_symbols(include_delisted=True)["symbol"].tolist()
    out = {}
    if "valuation" in what:
        out["valuation"] = backfill_valuation(ds, store, start, end, syms, progress)
        if out["valuation"] and hasattr(store, "clear_fundamental_valuation"):
            k = store.clear_fundamental_valuation()
            progress(f"[估值] 已清除财报行遗留估值 {k} 行（改由逐日估值表提供）")
    if "fundamentals" in what:
        out["fundamentals"] = backfill_fundamentals(ds, store, syms, start, progress)
    if "industry" in what:
        out["industry"] = backfill_industry(ds, store, progress)
    if "names" in what:
        out["names"] = backfill_names(ds, store, syms, progress)
    return out
