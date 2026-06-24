"""数据管道：采集（增量）→ 清洗 → 存储，以及截面因子表构建。"""
from __future__ import annotations

import logging

import pandas as pd

from ..config import Config
from ..datasource import DataSource
from ..engine.factors import add_price_factors
from ..storage import Storage
from .clean import clean_daily
from .symbols import classify_board, status_label

log = logging.getLogger(__name__)


def update_symbols(ds: DataSource, store: Storage) -> int:
    df = ds.list_symbols()
    store.upsert_symbols(df)
    log.info("更新股票列表 %d 只", len(df))
    return len(df)


def update_daily(ds: DataSource, store: Storage, symbols: list[str],
                 adjust: str, start: str | None = None) -> int:
    """增量拉取日线：从已存最后日期之后继续（分批 + 缓存 + 增量，第 3.1 节）。"""
    n = 0
    for sym in symbols:
        last = store.last_daily_date(sym, adjust)
        s = start
        if last:
            s = (pd.to_datetime(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            raw = ds.daily(sym, adjust, start=s)
        except Exception as e:  # noqa: BLE001
            log.warning("拉取 %s 失败: %s", sym, e)
            continue
        cleaned = clean_daily(raw)
        store.upsert_daily(sym, cleaned, adjust)
        n += len(cleaned)
    log.info("增量写入日线 %d 行（adjust=%s）", n, adjust)
    return n


def update_index(ds: DataSource, store: Storage, code: str) -> int:
    """拉取并入库基准指数日线（如沪深300=000300），供回测真实基准对比。"""
    try:
        df = ds.index_daily(code)
    except Exception as e:  # noqa: BLE001
        log.warning("指数 %s 拉取失败: %s", code, e)
        return 0
    if df is None or df.empty or "close" not in df.columns:
        return 0
    store.upsert_index(code, df[["date", "close"]])
    log.info("更新指数 %s 共 %d 行", code, len(df))
    return len(df)


def build_universe(store: Storage, include_delisted: bool = True) -> list[str]:
    """回测/选股股票池。include_delisted=True 以避免幸存者偏差（第 3.1 节）。"""
    df = store.get_symbols(include_delisted=include_delisted)
    return df["symbol"].tolist()


def build_cross_section(store: Storage, config: Config,
                        symbols: list[str] | None = None,
                        as_of: str | None = None) -> pd.DataFrame:
    """构建截面因子表：基本面 + 价格因子(动量/波动) + AI 特征，一行一只股票。

    供 engine.factors.score_factors 与 screener.screen 直接消费。
    AI 特征若不存在则缺列 —— 引擎对缺列做中性处理，不影响运行（优雅降级）。

    as_of（point-in-time，铁律2 防前视）：给定时，基本面/AI特征只取**截至该日已披露**
    的记录；每只再取「已披露中报告期最新」的一条。as_of=None 为实时模式，取最新已披露。
    回测中务必传入回测当日的 as_of，否则会用到未来才公布的财报，收益虚高。
    """
    syms = symbols or build_universe(store)
    fund = store.get_fundamentals(syms, as_of=as_of)
    if fund.empty:
        base = pd.DataFrame({"symbol": syms})
    else:
        # 每只取「已披露记录中报告期最新」的一条（PIT 下 fund 已按 ann_date 过滤）
        sort_keys = [c for c in ("date", "ann_date") if c in fund.columns]
        base = (fund.sort_values(sort_keys)
                    .groupby("symbol", as_index=False).tail(1)
                    .reset_index(drop=True))

    # 价格因子（PIT：as_of 给定时只用 ≤as_of 的行情，否则动量/均线会偷看未来）
    adjust = config.datasource.get("adjust", "hfq")
    price_rows = []
    for sym in base["symbol"]:
        daily = store.get_daily(sym, adjust, end=as_of)
        row = {"symbol": sym}
        if not daily.empty:
            row.update(add_price_factors(daily))
            row["close"] = float(daily["close"].iloc[-1])
            # 简易均线，供 close>ma60 这类筛选
            if len(daily) >= 60:
                row["ma60"] = float(daily["close"].rolling(60).mean().iloc[-1])
        price_rows.append(row)
    price = pd.DataFrame(price_rows)

    cross = base.merge(price, on="symbol", how="left")

    # AI 特征（情绪/事件），缺失即缺列 → 引擎中性处理。PIT 下按 as_of 过滤防前视
    feats = store.get_features(syms, as_of=as_of)
    if not feats.empty:
        feat_sort = [c for c in ("date", "as_of") if c in feats.columns]
        latest = (feats.sort_values(feat_sort)
                       .groupby("symbol", as_index=False).tail(1))
        cross = cross.merge(
            latest[["symbol", "sentiment", "confidence", "event_type"]],
            on="symbol", how="left")

    # 附股票名 + 板块标注 + 状态(正常/ST/退市)
    meta = store.get_symbols()[["symbol", "name", "status"]]
    cross = cross.merge(meta, on="symbol", how="left")
    cross["board"] = cross["symbol"].map(classify_board)
    cross["status_label"] = cross["status"].map(status_label)
    return cross
