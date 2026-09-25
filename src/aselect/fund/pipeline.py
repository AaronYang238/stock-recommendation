"""基金数据同步管道：名单 → 初筛 → 净值历史落库（带限速/重试/进度日志）。

复用 data/pipeline.py 的"分阶段容错"模式：单基金失败只告警不中断。
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from . import datasource as fds

log = logging.getLogger(__name__)


def sync_universe(store, cfg_fund: dict) -> pd.DataFrame:
    """拉取股混基金+ETF 名单 → 初筛 → 落 fund_info。返回初筛后候选表。"""
    scr = (cfg_fund or {}).get("screen", {}) or {}
    universes = []
    for sym in ("股票型", "混合型"):
        try:
            u = fds.fetch_open_fund_universe(symbol=sym)
            universes.append(u)
            log.info("名单 %s：%d 只", sym, len(u))
            time.sleep(1.0)
        except Exception as e:  # noqa: BLE001
            log.warning("名单 %s 拉取失败（跳过）：%s", sym, e)
    try:
        etf = fds.fetch_etf_universe()
        # ETF 统一挂 etf: 前缀以区分场内/场外（同一 6 位代码可能是基金或股票）
        etf["fund_code"] = "etf:" + etf["fund_code"]
        universes.append(etf)
        log.info("名单 ETF：%d 只", len(etf))
    except Exception as e:  # noqa: BLE001
        log.warning("ETF 名单拉取失败（跳过）：%s", e)
    if not universes:
        raise RuntimeError("全部名单接口失败，无法同步")
    uni = pd.concat(universes, ignore_index=True)

    # 落库快照（updated_at 便于追溯）
    uni["updated_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    store.upsert_fund_info(uni)

    # 初筛需要规模/成立日期：名单接口不带 → 对通过"业绩初筛"的基金再查基本信息。
    # 先按业绩粗筛（近1年收益、净值日期存在）减少 xq 调用量
    cand = uni.copy()
    if "ret_1y" in cand.columns:
        cand = cand[cand["ret_1y"].notna() & (cand["ret_1y"] >= float(scr.get("min_ret_1y", 0.0)))]
    log.info("业绩粗筛后 %d 只，逐只补规模/成立日期…", len(cand))
    infos = []
    for i, r in enumerate(cand.to_dict("records")):
        code = r["fund_code"]
        try:
            b = fds.fetch_fund_basic(code, retries=2)
        except Exception:  # noqa: BLE001
            b = {}
        infos.append({"fund_code": code, **b})
        if (i + 1) % 100 == 0:
            log.info("  基本信息 %d/%d", i + 1, len(cand))
        time.sleep(float((cfg_fund or {}).get("sync", {}).get("sleep_s", 0.8)) / 2.0)
    info_df = pd.DataFrame(infos)
    # 只回写 basic 增量列（setup_date/scale_yi/fund_type），避免覆盖名单行
    # 的 kind/业绩快照（upsert 是整行 OR-REPLACE）。
    basic_cols = [c for c in ("fund_code", "setup_date", "scale_yi", "fund_type")
                  if c in info_df.columns]
    info_df = info_df[basic_cols]
    # 与库内已有行合并：先取库内全部列，再覆盖 basic 三列
    existing = store.get_fund_info().set_index("fund_code")
    for c in ("setup_date", "scale_yi", "fund_type"):
        if c in info_df.columns:
            existing[c] = existing.index.map(
                dict(zip(info_df["fund_code"], info_df[c]))).where(
                existing.index.isin(info_df["fund_code"]), existing.get(c))
    store.upsert_fund_info(existing.reset_index())

    full = store.get_fund_info()
    from .strategy import screen_universe
    screened = screen_universe(
        full, min_scale_yi=float(scr.get("min_scale_yi", 2.0)),
        min_age_days=int(scr.get("min_age_days", 365)),
        min_ret_1y=float(scr.get("min_ret_1y", 0.0)))
    return screened


def sync_nav(store, fund_codes: list[str], cfg_fund: dict,
             progress_every: int = 20) -> int:
    """逐只拉净值历史落库（幂等）。返回成功只数。"""
    sleep_s = float((cfg_fund or {}).get("sync", {}).get("sleep_s", 0.8))
    ok = 0
    for i, code in enumerate(fund_codes):
        raw = code[4:] if code.startswith("etf:") else code
        try:
            nav = fds.fetch_fund_nav(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("净值拉取失败 %s：%s（跳过）", code, e)
            continue
        if nav.empty:
            log.warning("净值空 %s（跳过）", code)
            continue
        if code.startswith("etf:"):
            nav["fund_code"] = code
        nav = nav.copy()
        nav["nav_date"] = pd.to_datetime(nav["nav_date"]).dt.strftime("%Y-%m-%d")
        store.upsert_fund_nav(nav)
        ok += 1
        if (i + 1) % progress_every == 0 or i + 1 == len(fund_codes):
            log.info("净值进度 %d/%d（成功 %d）", i + 1, len(fund_codes), ok)
        time.sleep(sleep_s)
    return ok


def sync_benchmark(store, code: str = "000300") -> bool:
    """基准指数日线 → index_daily（新浪源）。"""
    try:
        df = fds.fetch_index_daily(code)
    except Exception as e:  # noqa: BLE001
        log.warning("基准指数拉取失败 %s：%s", code, e)
        return False
    store.upsert_index(code, df)
    return True
