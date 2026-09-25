"""基金数据源适配（akshare 直连，重试+限速）。

接口：
  fetch_open_fund_universe()  全市场开放式基金名单+近1年业绩（初筛用）
  fetch_etf_universe()        场内 ETF 名单
  fetch_fund_nav(code)        单基金净值历史
  fetch_fund_basic(code)      单基金基本信息（成立日期/规模/类型）
  fetch_fund_holds(code, year) 单基金重仓股（季度，相关性提示用）
  fetch_index_daily(code)     基准指数日线（新浪源，东财指数接口经代理不稳）

akshare 接口偶发超时/断连：统一 _retry 包裹（指数退避）+ 调用方限速。
"""
from __future__ import annotations

import logging
import time

import pandas as pd

log = logging.getLogger(__name__)

_OPEN_COLS = {
    "基金代码": "fund_code", "基金简称": "name", "日期": "nav_date",
    "单位净值": "nav", "累计净值": "accum_nav", "手续费": "fee_txt",
    "近1周": "ret_1w", "近1月": "ret_1m", "近3月": "ret_3m",
    "近6月": "ret_6m", "近1年": "ret_1y", "近2年": "ret_2y", "近3年": "ret_3y",
}


def _retry(fn, retries: int = 3, backoff: float = 2.0, what: str = ""):
    """akshare 网络调用统一重试（指数退避）。"""
    last: Exception | None = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("akshare %s 第 %d/%d 次失败: %s", what, i + 1, retries, e)
            if i < retries - 1:
                time.sleep(backoff * (2 ** i))
    raise RuntimeError(f"akshare {what} 重试 {retries} 次仍失败") from last


def _pct(v) -> float | None:
    """百分比字符串/数值 → 小数。'0.15%' → 0.0015；NaN → None。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace("%", "")
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def fetch_open_fund_universe(symbol: str = "全部", retries: int = 3) -> pd.DataFrame:
    """全市场开放式基金名单+业绩快照（fund_open_fund_rank_em）。

    symbol: 全部 | 股票型 | 混合型 | 债券型 | ...（akshare 原生参数）。
    返回列：fund_code/name/kind/nav_date/nav/accum_nav/fee_rate/ret_*（小数）。
    """
    df = _retry(lambda: _ak().fund_open_fund_rank_em(symbol=symbol),
                retries=retries, what=f"fund_open_fund_rank_em({symbol})")
    out = df.rename(columns=_OPEN_COLS)
    keep = [c for c in _OPEN_COLS.values() if c in out.columns]
    out = out[keep].copy()
    out["fund_code"] = out["fund_code"].astype(str).str.zfill(6)
    for c in ("ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_1y", "ret_2y", "ret_3y"):
        if c in out.columns:
            # rank 接口给的是百分数值（如 73.29 表示 +73.29%）
            out[c] = pd.to_numeric(out[c], errors="coerce") / 100.0
    if "fee_rate" not in out.columns and "fee_txt" in out.columns:
        out["fee_rate"] = out["fee_txt"].map(_pct)
    elif "fee_txt" in out.columns:
        fr = out["fee_txt"].map(_pct)
        out["fee_rate"] = out["fee_rate"].map(_pct).combine_first(fr) \
            if out["fee_rate"].dtype == object else out["fee_rate"]
    if "nav_date" in out.columns:
        out["nav_date"] = pd.to_datetime(out["nav_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["kind"] = "open"
    return out.drop_duplicates(subset=["fund_code"]).reset_index(drop=True)


def fetch_etf_universe(retries: int = 3) -> pd.DataFrame:
    """场内 ETF 名单（fund_etf_fund_daily_em，仅名单+最新净值快照）。"""
    df = _retry(lambda: _ak().fund_etf_fund_daily_em(),
                retries=retries, what="fund_etf_fund_daily_em")
    cols = {c: c for c in df.columns}
    day = sorted([c for c in df.columns if str(c).endswith("-单位净值")])
    out = df.rename(columns={"基金代码": "fund_code", "基金简称": "name",
                             "类型": "fund_type"}).copy()
    out["fund_code"] = out["fund_code"].astype(str).str.zfill(6)
    out["nav"] = pd.to_numeric(out[day[-1]], errors="coerce") if day else None
    out["nav_date"] = day[-1].split("-单位净值")[0] if day else None
    out["kind"] = "etf"
    keep = ["fund_code", "name", "fund_type", "kind", "nav", "nav_date"]
    return out[keep].drop_duplicates(subset=["fund_code"]).reset_index(drop=True)


def fetch_fund_nav(code: str, retries: int = 3) -> pd.DataFrame:
    """单基金单位净值历史（fund_open_fund_info_em）。

    返回：nav_date(datetime64), nav(float)。空序列视为该基金不可用。
    """
    df = _retry(lambda: _ak().fund_open_fund_info_em(symbol=code,
                                                     indicator="单位净值走势"),
                retries=retries, what=f"fund_open_fund_info_em({code})")
    if df is None or df.empty:
        return pd.DataFrame(columns=["fund_code", "nav_date", "nav"])
    out = pd.DataFrame({
        "fund_code": code,
        "nav_date": pd.to_datetime(df["净值日期"], errors="coerce"),
        "nav": pd.to_numeric(df["单位净值"], errors="coerce"),
    }).dropna()
    out["nav"] = out["nav"].astype(float)
    return out.reset_index(drop=True)


def fetch_fund_basic(code: str, retries: int = 3) -> dict:
    """单基金基本信息（fund_individual_basic_info_xq）：成立日期/规模/类型。

    新成立/雪球无数据的基金会抛 KeyError('data') → 视为"无信息"返回空 dict，
    由调用方按规模缺失处理（初筛剔除）。
    """
    try:
        rows = _retry(lambda: _ak().fund_individual_basic_info_xq(symbol=code),
                      retries=retries, what=f"fund_individual_basic_info_xq({code})")
    except KeyError:
        log.info("基金 %s 无基本信息（新成立或未收录），按缺规模处理", code)
        return {}
    kv = dict(zip(rows["item"], rows["value"]))
    scale = kv.get("最新规模")
    scale_yi = None
    if scale:
        s = str(scale)
        try:
            val = float("".join(ch for ch in s if ch.isdigit() or ch == "."))
            scale_yi = val / 10000.0 if "万" in s and "亿" not in s else val
        except ValueError:
            pass
    return {"setup_date": kv.get("成立时间"), "scale_yi": scale_yi,
            "fund_type": kv.get("基金类型")}


def fetch_fund_holds(code: str, year: str, retries: int = 3) -> pd.DataFrame:
    """单基金重仓股（fund_portfolio_hold_em，季度）。仅用于相关性提示，不进回测。"""
    df = _retry(lambda: _ak().fund_portfolio_hold_em(symbol=code, date=str(year)),
                retries=retries, what=f"fund_portfolio_hold_em({code})")
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.rename(columns={"股票代码": "symbol", "股票名称": "stock_name",
                             "占净值比例": "pct_nav"}).copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    out["pct_nav"] = pd.to_numeric(out["pct_nav"], errors="coerce")
    return out


def fetch_index_daily(code: str = "000300", retries: int = 3) -> pd.DataFrame:
    """基准指数日线（新浪源 sh+code；东财 index_zh_a_hist 经代理不稳）。"""
    df = _retry(lambda: _ak().stock_zh_index_daily(symbol=f"sh{code}"),
                retries=retries, what=f"index_daily({code})")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
    }).dropna()
    return out.reset_index(drop=True)


def _ak():
    import akshare as ak  # 延迟导入：测试/离线环境不强依赖网络库行为
    return ak
