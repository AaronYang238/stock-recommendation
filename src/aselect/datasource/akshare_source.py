"""akshare 数据源适配器（主源，免费无 Key）。

健壮性：分批 + 重试 + 列名标准化。akshare 接口随上游改版易失效，
故所有调用都包重试，并把原始中文列映射成统一 schema。
"""
from __future__ import annotations

import time

import pandas as pd

from .base import DataSource

_ADJUST_MAP = {"none": "", "qfq": "qfq", "hfq": "hfq"}

# akshare 历史行情中文列 → 统一英文列
_HIST_COLS = {
    "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
    "收盘": "close", "成交量": "volume", "成交额": "amount",
}


def _retry(fn, retry: int, backoff: float):
    last = None
    for i in range(max(1, retry)):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — 上游异常类型不稳定
            last = e
            time.sleep(backoff * (i + 1))
    raise last  # type: ignore[misc]


class AkshareSource(DataSource):
    name = "akshare"

    def __init__(self, retry: int = 3, retry_backoff_s: float = 2.0):
        import akshare as ak  # 延迟导入：未装也不影响其它源
        self.ak = ak
        self.retry = retry
        self.backoff = retry_backoff_s

    def list_symbols(self) -> pd.DataFrame:
        """全市场代码 + 状态。合并在市(含 ST) 与历史退市标的，避免幸存者偏差。"""
        from ..data.symbols import classify_status, merge_symbols

        active = _retry(self.ak.stock_info_a_code_name, self.retry, self.backoff)
        active = active.rename(columns={"code": "symbol", "name": "name"})
        active["exchange"] = active["symbol"].map(self._exchange_of)
        active["status"] = active["name"].map(lambda n: classify_status(n))
        active["list_date"] = None
        active["delist_date"] = None

        delisted = self._delisted()
        return merge_symbols(active, delisted)

    def _delisted(self) -> pd.DataFrame:
        """拉取沪/深历史退市标的。各接口独立 try，单个失效不影响整体。"""
        frames = []
        # 上交所终止上市
        for sym in ("终止上市",):
            frames.append(self._safe_delist(
                lambda: self.ak.stock_info_sh_delist(symbol=sym), "SH"))
        # 深交所终止上市 / 暂停上市
        for sym in ("终止上市公司", "暂停上市公司"):
            frames.append(self._safe_delist(
                lambda s=sym: self.ak.stock_info_sz_delist(symbol=s), "SZ"))
        frames = [f for f in frames if f is not None and not f.empty]
        if not frames:
            return pd.DataFrame(columns=["symbol", "name", "exchange",
                                         "list_date", "delist_date", "status"])
        return pd.concat(frames, ignore_index=True)

    def _safe_delist(self, fn, exchange: str) -> pd.DataFrame | None:
        """调用退市接口并标准化列。接口改版/失效时返回 None（告警，不中断）。"""
        try:
            df = _retry(fn, self.retry, self.backoff)
        except Exception:  # noqa: BLE001 — 退市接口尤其易随上游改版失效
            return None
        if df is None or df.empty:
            return None
        df = df.rename(columns=self._fuzzy_delist_cols(df.columns))
        if "symbol" not in df.columns:
            return None
        df["exchange"] = exchange
        df["status"] = "D"
        df["list_date"] = df.get("list_date")
        df["delist_date"] = df.get("delist_date")
        keep = ["symbol", "name", "exchange", "list_date", "delist_date", "status"]
        for c in keep:
            if c not in df.columns:
                df[c] = None
        return df[keep]

    @staticmethod
    def _fuzzy_delist_cols(cols) -> dict[str, str]:
        """退市接口中文列名易变，模糊匹配到统一英文列。"""
        mapping = {}
        for c in cols:
            s = str(c)
            if "代码" in s:
                mapping[c] = "symbol"
            elif "简称" in s or "名称" in s:
                mapping[c] = "name"
            elif "终止上市" in s or "退市" in s or "暂停上市" in s:
                mapping[c] = "delist_date"
            elif "上市日期" in s:
                mapping[c] = "list_date"
        return mapping

    def daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        adj = _ADJUST_MAP.get(adjust, "")
        kwargs = dict(symbol=symbol, period="daily", adjust=adj)
        if start:
            kwargs["start_date"] = start.replace("-", "")
        if end:
            kwargs["end_date"] = end.replace("-", "")
        df = _retry(lambda: self.ak.stock_zh_a_hist(**kwargs), self.retry, self.backoff)
        if df is None or df.empty:
            return pd.DataFrame(columns=list(_HIST_COLS.values()))
        df = df.rename(columns=_HIST_COLS)
        keep = [c for c in _HIST_COLS.values() if c in df.columns]
        df = df[keep]
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df

    def fundamentals(self, symbols=None) -> pd.DataFrame:
        # 全市场快照指标（PE/PB/总市值等）。不同 akshare 版本字段略有差异，做容错映射。
        df = _retry(self.ak.stock_zh_a_spot_em, self.retry, self.backoff)
        colmap = {
            "代码": "symbol", "市盈率-动态": "pe", "市净率": "pb", "总市值": "total_mv",
        }
        df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
        if symbols:
            df = df[df["symbol"].isin(symbols)]
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        # 实时快照：报告期与披露日都记为今日 —— 它只在「当下」可信，
        # PIT 模式下用于历史回测会被正确排除（真实 PIT 财务需接 stock_financial_* 的公告日）。
        df["date"] = today
        df["ann_date"] = today
        cols = ["symbol", "date", "ann_date", "pe", "pb", "total_mv"]
        return df[[c for c in cols if c in df.columns]]

    def index_daily(self, index_code, start=None, end=None) -> pd.DataFrame:
        df = _retry(lambda: self.ak.stock_zh_index_daily(symbol=self._index_sym(index_code)),
                    self.retry, self.backoff)
        df = df.rename(columns={"date": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        return df

    def industry_map(self) -> dict[str, str]:
        """遍历东方财富行业板块 → 成分股，建 symbol→行业 映射。

        约 90 个板块、每板块一次成分查询；单板块失败跳过不影响整体。
        网络/接口异常时返回已得到的部分映射（可能为空）。
        """
        mapping: dict[str, str] = {}
        try:
            names = _retry(self.ak.stock_board_industry_name_em, self.retry, self.backoff)
        except Exception:  # noqa: BLE001
            return mapping
        col = "板块名称" if "板块名称" in names.columns else names.columns[0]
        for ind in names[col].tolist():
            try:
                cons = _retry(lambda i=ind: self.ak.stock_board_industry_cons_em(symbol=i),
                              self.retry, self.backoff)
            except Exception:  # noqa: BLE001
                continue
            code_col = "代码" if "代码" in cons.columns else None
            if not code_col:
                continue
            for code in cons[code_col].astype(str):
                mapping[code.zfill(6)] = ind
        return mapping

    @staticmethod
    def _exchange_of(symbol: str) -> str:
        if symbol.startswith(("60", "68", "9")):
            return "SH"
        if symbol.startswith(("00", "30", "20")):
            return "SZ"
        if symbol.startswith(("43", "83", "87", "88", "92")):
            return "BJ"
        return "?"

    @staticmethod
    def _index_sym(code: str) -> str:
        # 沪深300=000300 在上交所；按前缀判断
        return ("sh" if code.startswith(("000", "999")) else "sz") + code
