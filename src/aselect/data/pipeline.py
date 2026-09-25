"""数据管道：采集（增量）→ 清洗 → 存储，以及截面因子表构建。"""
from __future__ import annotations

import logging

import pandas as pd

from ..config import Config
from ..datasource import DataSource
from ..engine.factors import add_price_factors
from ..storage import Storage
from .clean import clean_daily
from .hotspot import add_hotspot_factor
from .symbols import classify_board, status_label

log = logging.getLogger(__name__)


VALUATION_COLS = ["pe", "pb", "ps", "total_mv", "circ_mv", "turnover_rate"]


def update_symbols(ds: DataSource, store: Storage) -> int:
    df = ds.list_symbols()
    try:                                   # 当前行业（行业历史缺失时的中性化兜底）
        from .symbols import attach_industry
        df = attach_industry(df, ds.industry_map())
    except Exception as e:  # noqa: BLE001
        log.warning("行业映射获取失败（symbols.industry 留空）：%s", e)
    store.upsert_symbols(df)
    log.info("更新股票列表 %d 只", len(df))
    return len(df)


def update_daily(ds: DataSource, store: Storage, symbols: list[str],
                 adjust: str, start: str | None = None) -> int:
    """增量拉取日线：从已存最后日期之后继续（分批 + 缓存 + 增量，第 3.1 节）。

    常驻不变量（hfq，2026-09，防"换基不重标"静默损坏复发）：
    拉取窗口从 last-10 天开始（而非 last+1），一次查询既取新数据又取重叠窗口；
    对重叠窗口逐日比对 拉取值 vs 存库值，close 相对差 >1% → 判为复权基准改变，
    拒写该股并记 ERROR（baostock 后复权 anchor-stable，正常时重叠窗口应逐位一致）。
    """
    n = 0
    quarantined: list[tuple[str, float]] = []
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
        # ── hfq 常驻不变量：append 边界连续性 guard ──
        if adjust == "hfq" and last and not cleaned.empty:
            ov_start = (pd.to_datetime(last) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
            try:
                ov_fetched = clean_daily(ds.daily(sym, adjust, start=ov_start, end=last))
                ov_stored = store.get_daily(sym, adjust, start=ov_start, end=last)
                if not ov_fetched.empty and not ov_stored.empty:
                    ov_fetched["date"] = pd.to_datetime(ov_fetched["date"])
                    m = pd.merge(ov_fetched[["date", "close"]], ov_stored[["date", "close"]],
                                 on="date", suffixes=("_f", "_s"))
                    if not m.empty:
                        rel = (m["close_f"] / m["close_s"] - 1).abs()
                        if float(rel.max()) > 0.01:
                            log.error(
                                "hfq 不变量违反 %s: 重叠窗口 close 相对差 max=%.1f%% — "
                                "疑似复权基准改变，拒写该股", sym, float(rel.max()) * 100)
                            quarantined.append((sym, float(rel.max())))
                            continue
            except Exception as e:  # noqa: BLE001
                log.warning("hfq 不变量检查 %s 失败(放行): %s", sym, e)
        store.upsert_daily(sym, cleaned, adjust)
        n += len(cleaned)
    if quarantined:
        log.error("hfq 不变量拒写 %d 只: %s", len(quarantined), quarantined[:10])
    log.info("增量写入日线 %d 行（adjust=%s）", n, adjust)
    return n


def save_fundamentals_snapshot(store: Storage, fund: pd.DataFrame) -> int:
    """把数据源的"估值+最新财报"快照拆开入库（PIT 修复）。

    估值(pe/pb/ps/市值)属于**估值日**，写 valuation_daily(date=val_date，缺省今天)；
    财务属于**报告期/披露日**，写 fundamentals 且不带估值列。旧实现把今天的估值
    挂在最新财报行上，按披露日回看时会用到披露日之后的股价（前视）。
    """
    if fund is None or fund.empty:
        return 0
    fund = fund.copy()
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    val_cols = [c for c in VALUATION_COLS if c in fund.columns]
    if val_cols:
        val = fund[["symbol"] + val_cols].copy()
        val["date"] = (fund["val_date"] if "val_date" in fund.columns
                       else pd.Series(today, index=fund.index)).fillna(today)
        val = val.dropna(subset=val_cols, how="all")
        if hasattr(store, "upsert_valuation") and not val.empty:
            try:
                store.upsert_valuation(val)
            except NotImplementedError:
                pass
    fin = fund.drop(columns=val_cols + ["val_date"], errors="ignore")
    store.upsert_fundamentals(fin)
    return len(fin)


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


def filter_tradable_universe(store: Storage, config: Config,
                             universe: list[str]) -> list[str]:
    """按可交易口径过滤股票池（用户无科创板/北交所权限，且回避 ST）。

    由 config.backtest 的 exclude_star_market / exclude_bse / exclude_st 控制，
    默认全部开启。不影响"含退市防幸存者偏差"——只在 board/status 维度过滤。
    保持相对顺序。"""
    bt = config.backtest or {}
    ex_star = bool(bt.get("exclude_star_market", True))
    ex_bse = bool(bt.get("exclude_bse", True))
    ex_st = bool(bt.get("exclude_st", True))
    df = store.get_symbols(include_delisted=True)
    meta = {}
    for r in df.itertuples(index=False):
        meta[getattr(r, "symbol", None)] = r
    out = []
    for s in universe:
        # 科创板(688/689)：用户无交易权限
        if ex_star and str(s).startswith(("688", "689")):
            continue
        # 北交所(4/8)：普通账户不可交易
        if ex_bse and str(s).startswith(("4", "8")):
            continue
        r = meta.get(s)
        if ex_st:
            name = str(getattr(r, "name", "") or "").upper()
            status = str(getattr(r, "status", "") or "")
            if status == "ST" or "ST" in name:
                continue
        out.append(s)
    return out


def build_cross_section(store: Storage, config: Config,
                        symbols: list[str] | None = None,
                        as_of: str | None = None,
                        frames: dict | None = None) -> pd.DataFrame:
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
        fund_latest = (fund.sort_values(sort_keys)
                            .groupby("symbol", as_index=False).tail(1)
                            .reset_index(drop=True))
        # 必须保留**全部**符号：无已披露基本面的票也用行情参与截面（基本面列留 NaN）。
        # 否则 PIT 下基本面稀疏时截面会塌缩成"仅有基本面的那几只"，全宇宙被误丢。
        base = pd.DataFrame({"symbol": syms}).merge(
            fund_latest, on="symbol", how="left")

    # 估值（PIT）：有逐日估值表就以其 ≤as_of 的最近值为准，并丢弃财报行上的估值列
    # （财报行上的估值是"同步当天"的快照，回看历史即前视）。无估值表的旧库/合成库
    # 才沿用财报行估值（合成源按季生成、与披露日一致）。
    if store.has_valuation():
        base = base.drop(columns=[c for c in VALUATION_COLS if c in base.columns])
        val = store.get_valuation(syms, as_of=as_of)
        if not val.empty:
            base = base.merge(val[["symbol"] + [c for c in VALUATION_COLS
                                                if c in val.columns]],
                              on="symbol", how="left")
        for c in ("pe", "pb", "ps", "total_mv"):
            if c not in base.columns:
                base[c] = float("nan")

    # 行业（PIT）：行业历史(as_of) > 财报行行业 > symbols 当前行业
    ind_hist = store.get_industry(syms, as_of=as_of)
    sym_meta = store.get_symbols()
    cur_ind = (dict(zip(sym_meta["symbol"], sym_meta["industry"]))
               if "industry" in sym_meta.columns else {})
    ind = base["symbol"].map(ind_hist) if ind_hist else pd.Series(None, index=base.index)
    if "industry" in base.columns:
        ind = ind.fillna(base["industry"])
    base["industry"] = ind.fillna(base["symbol"].map(cur_ind))

    # 价格因子（PIT：as_of 给定时只用 ≤as_of 的行情，否则动量/均线会偷看未来）
    adjust = config.datasource.get("adjust", "hfq")
    # 性能：回测循环里已把全量行情加载进 frames，直接复用（按 as_of 切片），
    # 避免每周对全宇宙重读 2.4GB daily 表（截面塌缩 bug 修复后此路径会是瓶颈）。
    as_of_ts = pd.Timestamp(as_of) if as_of else None
    price_rows = []
    for sym in base["symbol"]:
        if frames is not None and sym in frames:
            fr = frames[sym]
            daily = fr[fr.index <= as_of_ts] if as_of_ts is not None else fr
        else:
            daily = store.get_daily(sym, adjust, end=as_of)
        row = {"symbol": sym}
        if not daily.empty:
            row.update(add_price_factors(daily))
            row["close"] = float(daily["close"].iloc[-1])
            # 简易均线，供 close>ma60 这类筛选
            if len(daily) >= 60:
                row["ma60"] = float(daily["close"].rolling(60).mean().iloc[-1])
            # 热点因子所需：最近一日资金流/换手/涨跌幅（PIT：仅用 ≤as_of 的行情）
            if "net_inflow" in daily.columns:
                try:
                    row["net_inflow"] = float(daily["net_inflow"].iloc[-1])
                except (TypeError, ValueError):
                    pass  # 资金流未拉取时跳过该因子（不崩溃）
            if "turnover" in daily.columns:
                try:
                    row["turnover"] = float(daily["turnover"].iloc[-1])
                except (TypeError, ValueError):
                    pass  # 换手缺失时跳过
            if len(daily) >= 2:
                row["pct_chg"] = float(daily["close"].iloc[-1] / daily["close"].iloc[-2] - 1)
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
    meta = sym_meta[["symbol", "name", "status"]]
    cross = cross.merge(meta, on="symbol", how="left")
    cross["board"] = cross["symbol"].map(classify_board)
    cross["status_label"] = cross["status"].map(status_label)

    # 热点因子（板块聚合，正交因子；缺输入列时该列记 NaN）
    cross = add_hotspot_factor(cross)
    return cross
