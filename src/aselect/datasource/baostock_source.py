"""baostock 数据源适配器（免费、无需 token/积分、API 走自有服务，不被东财封 IP）。

适合"被封网云服务器常驻自动更新"的场景，且**免费提供真实财务 + 披露日**：
  行情/估值：query_history_k_data_plus（含 peTTM/pbMRQ/psTTM）
  财务：query_profit_data(roeAvg/gpMargin/totalShare + pubDate 披露日/statDate 报告期)
        query_growth_data(YOYNI 净利润同比)、query_balance_data(liabilityToAsset 资产负债率)
  行业：query_stock_industry

注意：baostock 比率字段多为小数（0.15），本适配器统一 ×100 转成与系统一致的百分数。
无 token，但每次会话需 login()。代码格式为 'sh.600000'，对外统一转 '600000'。
"""
from __future__ import annotations

import pandas as pd

from .base import DataSource

_ADJUST = {"hfq": "1", "qfq": "2", "none": "3"}     # baostock 复权标志


class BaostockSource(DataSource):
    name = "baostock"

    def __init__(self, retry: int = 3, retry_backoff_s: float = 2.0):
        import baostock as bs  # 延迟导入
        self.bs = bs
        lg = bs.login()
        if getattr(lg, "error_code", "0") != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        self.retry = retry
        self.backoff = retry_backoff_s

    def close(self) -> None:
        try:
            self.bs.logout()
        except Exception:  # noqa: BLE001
            pass

    # ── 列表 ──
    def list_symbols(self) -> pd.DataFrame:
        from ..data.symbols import classify_status

        day = pd.Timestamp.today().strftime("%Y-%m-%d")
        df = self._df(self.bs.query_all_stock(day=day))
        # 节假日/未开盘 → 回溯几日
        for back in range(1, 8):
            if not df.empty:
                break
            d = (pd.Timestamp.today() - pd.Timedelta(days=back)).strftime("%Y-%m-%d")
            df = self._df(self.bs.query_all_stock(day=d))
        if df.empty:
            return pd.DataFrame(columns=["symbol", "name", "exchange",
                                         "list_date", "delist_date", "status"])
        df = df.rename(columns={"code_name": "name"})
        df["symbol"] = df["code"].map(self._from_bs)
        # 过滤掉指数（query_all_stock 含指数）：只留 6 位股票代码
        df = df[df["symbol"].str.match(r"^\d{6}$") & df["code"].str.startswith(("sh.6", "sz.0", "sz.3"))]
        df["exchange"] = df["code"].str[:2].str.upper()
        df["status"] = df["name"].map(lambda n: classify_status(n))
        df["list_date"] = None
        df["delist_date"] = None
        return df[["symbol", "name", "exchange", "list_date", "delist_date", "status"]]

    def industry_map(self) -> dict[str, str]:
        df = self._df(self.bs.query_stock_industry())
        if df.empty or "industry" not in df.columns:
            return {}
        df["symbol"] = df["code"].map(self._from_bs)
        return {r.symbol: r.industry for r in df.itertuples()
                if getattr(r, "industry", "")}

    # ── 行情 ──
    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        code = self._to_bs(symbol)
        if not code:
            return pd.DataFrame(columns=["date", "open", "high", "low",
                                         "close", "volume", "amount"])
        rs = self.bs.query_history_k_data_plus(
            code, "date,open,high,low,close,volume,amount",
            start_date=start or "", end_date=end or "",
            frequency="d", adjustflag=_ADJUST.get(adjust, "1"))
        df = self._df(rs)
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low",
                                         "close", "volume", "amount"])
        for c in ("open", "high", "low", "close", "volume", "amount"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["date", "open", "high", "low", "close", "volume", "amount"]]

    def index_daily(self, index_code, start=None, end=None) -> pd.DataFrame:
        code = "sh.000300" if index_code.startswith(("000", "999")) else "sz." + index_code
        rs = self.bs.query_history_k_data_plus(
            code, "date,close", start_date=start or "", end_date=end or "",
            frequency="d", adjustflag="3")
        df = self._df(rs)
        if df.empty:
            return pd.DataFrame(columns=["date", "close"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df[["date", "close"]]

    # ── 基本面：估值 + 财务（含披露日）──
    def fundamentals(self, symbols=None) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()
        imap = self.industry_map()
        rows = []
        for sym in symbols:
            code = self._to_bs(sym)
            row = {"symbol": sym, "industry": imap.get(sym)}
            if code:
                row.update(self._valuation(code))
                row.update(self._financials(code))
            close = row.pop("_close", None)
            share = row.pop("_share", None)
            if close and share:                       # 总市值 = 收盘价 × 总股本
                row["total_mv"] = round(close * share, 0)
            row.setdefault("date", pd.Timestamp.today().strftime("%Y-%m-%d"))
            row.setdefault("ann_date", row["date"])
            rows.append(row)
        return pd.DataFrame(rows)

    def _valuation(self, code: str) -> dict:
        rs = self.bs.query_history_k_data_plus(
            code, "date,close,peTTM,pbMRQ,psTTM",
            start_date="", end_date="", frequency="d", adjustflag="3")
        df = self._df(rs)
        if df.empty:
            return {}
        last = df.iloc[-1]
        return {"pe": _f(last.get("peTTM")), "pb": _f(last.get("pbMRQ")),
                "ps": _f(last.get("psTTM")), "_close": _f(last.get("close"))}

    def _financials(self, code: str) -> dict:
        out: dict = {}
        for year, quarter in _recent_quarters():
            prof = self._df(self.bs.query_profit_data(code=code, year=year, quarter=quarter))
            if prof.empty:
                continue
            p = prof.iloc[0]
            out["date"] = p.get("statDate")          # 报告期
            out["ann_date"] = p.get("pubDate")        # 披露日（point-in-time）
            out["roe"] = _pct(p.get("roeAvg"))
            out["gross_margin"] = _pct(p.get("gpMargin"))
            out["_share"] = _f(p.get("totalShare"))   # 总股本，供算总市值
            try:
                g = self._df(self.bs.query_growth_data(code=code, year=year, quarter=quarter))
                if not g.empty:
                    out["profit_yoy"] = _pct(g.iloc[0].get("YOYNI"))
            except Exception:  # noqa: BLE001
                pass
            try:
                b = self._df(self.bs.query_balance_data(code=code, year=year, quarter=quarter))
                if not b.empty:
                    out["debt_ratio"] = _pct(b.iloc[0].get("liabilityToAsset"))
            except Exception:  # noqa: BLE001
                pass
            break
        return out

    # ── 工具 ──
    @staticmethod
    def _df(rs) -> pd.DataFrame:
        if rs is None or getattr(rs, "error_code", "1") != "0":
            return pd.DataFrame()
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)

    @staticmethod
    def _to_bs(symbol: str) -> str | None:
        s = str(symbol).zfill(6)
        if s.startswith(("6", "9")):
            return f"sh.{s}"
        if s.startswith(("0", "2", "3")):
            return f"sz.{s}"
        return None        # 北交所(4/8)等 baostock 不覆盖

    @staticmethod
    def _from_bs(code: str) -> str:
        return str(code).split(".")[-1]


def _recent_quarters(n: int = 6):
    """最近 n 个季度的 (year, quarter)，由近及远。"""
    now = pd.Timestamp.today()
    y, q = now.year, (now.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return out


def _f(v):
    try:
        if v in (None, "") or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v):
    """baostock 比率多为小数 → ×100 转百分数，与系统一致。"""
    f = _f(v)
    return round(f * 100, 4) if f is not None else None
