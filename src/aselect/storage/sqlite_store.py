"""SQLite 存储实现（几千只 × 十年日线约数百 MB，足够）。

设计：每条日线带 adjust 维度（none/qfq/hfq），便于回测取后复权、看图取前复权。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from .base import Storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    exchange    TEXT,
    list_date   TEXT,
    delist_date TEXT,
    status      TEXT          -- L(上市) / D(退市) / ST
);

CREATE TABLE IF NOT EXISTS daily (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    adjust  TEXT NOT NULL,    -- none / qfq / hfq
    open    REAL, high REAL, low REAL, close REAL,
    volume  REAL, amount REAL,
    PRIMARY KEY (symbol, date, adjust)
);
CREATE INDEX IF NOT EXISTS idx_daily_symbol ON daily(symbol, adjust);

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,     -- 报告期（财务数据"属于"哪一期）
    ann_date TEXT,            -- 披露日(公告日)：信息真正公开的日期，point-in-time 对齐用
    industry TEXT,            -- 行业分类（供因子行业中性化）
    pe REAL, pb REAL, ps REAL,
    roe REAL, roa REAL,
    revenue_yoy REAL, profit_yoy REAL,
    gross_margin REAL, debt_ratio REAL,
    total_mv REAL,
    PRIMARY KEY (symbol, date)
);

-- AI 产出特征：与其它数据同库，引擎层当普通因子读取。
-- as_of = 该特征可信的历史起点，用于回测时隔离前视污染（第6节）。
CREATE TABLE IF NOT EXISTS features (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    sentiment REAL,
    event_type TEXT,
    confidence REAL,
    as_of     TEXT,
    source    TEXT,           -- 产出来源（provider/model），便于复现追溯
    PRIMARY KEY (symbol, date)
);

-- 指数日线（基准对比用，如沪深300=000300）
CREATE TABLE IF NOT EXISTS index_daily (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,
    close  REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_index_code ON index_daily(code);

-- 每日推荐落库（含快照理由）+ 事后前向收益跟踪（用真实表现证明高回报）
CREATE TABLE IF NOT EXISTS recommendations (
    date    TEXT NOT NULL,        -- 推荐生成日
    symbol  TEXT NOT NULL,
    name    TEXT,
    rank    INTEGER,
    score   REAL,                 -- 多因子综合得分
    board   TEXT, status TEXT,
    pe REAL, roe REAL,            -- 快照（推荐理由）
    fwd_5d  REAL,                 -- 事后填充：5/20 个交易日前向收益
    fwd_20d REAL,
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_reco_date ON recommendations(date);

-- 因子快照缓存：sync 预计算当日打分截面，/api/candidates 直接读，免每请求重扫全市场
CREATE TABLE IF NOT EXISTS factor_snapshot (
    snap_date TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    name TEXT, board TEXT, status_label TEXT,
    pe REAL, pb REAL, roe REAL, revenue_yoy REAL, mom_60 REAL,
    score_value REAL, score_quality REAL, total_score REAL,
    PRIMARY KEY (snap_date, symbol)
);

-- 轻量 KV（last_sync 等运行状态，供 /api/health）
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class SQLiteStorage(Storage):
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        # WAL：web 读 + 调度写 并发更稳（生产高可用）
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """对已存在的旧库补加后来新增的列（CREATE TABLE IF NOT EXISTS 不会改表）。"""
        wanted = {"fundamentals": [("industry", "TEXT"), ("ann_date", "TEXT")]}
        for table, cols in wanted.items():
            existing = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

    # ── symbols ──
    def upsert_symbols(self, df: pd.DataFrame) -> None:
        self._upsert("symbols", df, ["symbol"])

    def get_symbols(self, include_delisted: bool = True) -> pd.DataFrame:
        sql = "SELECT * FROM symbols"
        if not include_delisted:
            sql += " WHERE status != 'D' OR status IS NULL"
        return pd.read_sql(sql, self.conn)

    # ── daily ──
    def upsert_daily(self, symbol: str, df: pd.DataFrame, adjust: str) -> None:
        if df.empty:
            return
        out = df.copy()
        out["symbol"] = symbol
        out["adjust"] = adjust
        cols = ["symbol", "date", "adjust", "open", "high", "low",
                "close", "volume", "amount"]
        out = out[[c for c in cols if c in out.columns]]
        self._upsert("daily", out, ["symbol", "date", "adjust"])

    def get_daily(self, symbol, adjust, start=None, end=None) -> pd.DataFrame:
        sql = "SELECT * FROM daily WHERE symbol=? AND adjust=?"
        params: list = [symbol, adjust]
        if start:
            sql += " AND date>=?"; params.append(start)
        if end:
            sql += " AND date<=?"; params.append(end)
        sql += " ORDER BY date"
        df = pd.read_sql(sql, self.conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def last_daily_date(self, symbol, adjust) -> str | None:
        cur = self.conn.execute(
            "SELECT MAX(date) FROM daily WHERE symbol=? AND adjust=?",
            (symbol, adjust),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    # ── fundamentals ──
    def upsert_fundamentals(self, df: pd.DataFrame) -> None:
        self._upsert("fundamentals", df, ["symbol", "date"])

    def get_fundamentals(self, symbols=None, as_of: str | None = None) -> pd.DataFrame:
        """as_of 给定时只返回**截至该日已披露**(ann_date <= as_of)的记录（防前视）。
        ann_date 为空的记录在 PIT 模式下被排除（无法确认披露时点，不可用于历史）。"""
        return self._select_pit("fundamentals", "ann_date", symbols, as_of)

    # ── features (AI 产出) ──
    def upsert_features(self, df: pd.DataFrame) -> None:
        self._upsert("features", df, ["symbol", "date"])

    def get_features(self, symbols=None, as_of: str | None = None) -> pd.DataFrame:
        """as_of 给定时只返回 as_of(可信起点) <= 该日的特征（防前视）。"""
        return self._select_pit("features", "as_of", symbols, as_of)

    def _select_pit(self, table: str, asof_col: str,
                    symbols, as_of: str | None) -> pd.DataFrame:
        sql = f"SELECT * FROM {table}"
        conds, params = [], []
        if symbols:
            conds.append(f"symbol IN ({','.join('?' * len(symbols))})")
            params += list(symbols)
        if as_of:
            conds.append(f"{asof_col} IS NOT NULL AND {asof_col} <= ?")
            params.append(as_of)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return pd.read_sql(sql, self.conn, params=params)

    # ── 指数日线（基准） ──
    def upsert_index(self, code: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        out = df.copy()
        out["code"] = code
        self._upsert("index_daily", out[["code", "date", "close"]], ["code", "date"])

    def get_index(self, code: str, start: str | None = None,
                  end: str | None = None) -> pd.DataFrame:
        sql = "SELECT date, close FROM index_daily WHERE code=?"
        params: list = [code]
        if start:
            sql += " AND date>=?"; params.append(start)
        if end:
            sql += " AND date<=?"; params.append(end)
        sql += " ORDER BY date"
        return pd.read_sql(sql, self.conn, params=params)

    # ── 数据新鲜度（供 /api/meta 显示陈旧度） ──
    def data_status(self) -> dict:
        cur = self.conn.execute("SELECT MAX(date) FROM daily")
        last_daily = (cur.fetchone() or [None])[0]
        n_fund = self.conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM fundamentals "
            "WHERE roe IS NOT NULL").fetchone()[0]
        return {"last_daily_date": last_daily, "n_with_fundamentals": int(n_fund)}

    # ── 推荐落库 + 战绩跟踪 ──
    def upsert_recommendations(self, df: pd.DataFrame) -> None:
        self._upsert("recommendations", df, ["date", "symbol"])

    def get_recommendations(self, start: str | None = None,
                            end: str | None = None,
                            date: str | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM recommendations"
        conds, params = [], []
        if date:
            conds.append("date=?"); params.append(date)
        if start:
            conds.append("date>=?"); params.append(start)
        if end:
            conds.append("date<=?"); params.append(end)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY date DESC, rank ASC"
        return pd.read_sql(sql, self.conn, params=params)

    def recommendation_dates(self) -> list[str]:
        cur = self.conn.execute("SELECT DISTINCT date FROM recommendations ORDER BY date")
        return [r[0] for r in cur.fetchall()]

    def set_recommendation_returns(self, date: str, symbol: str,
                                   fwd_5d=None, fwd_20d=None) -> None:
        self.conn.execute(
            "UPDATE recommendations SET fwd_5d=?, fwd_20d=? WHERE date=? AND symbol=?",
            (fwd_5d, fwd_20d, date, symbol))
        self.conn.commit()

    # ── 因子快照缓存 ──
    def replace_factor_snapshot(self, snap_date: str, df: pd.DataFrame) -> None:
        self.conn.execute("DELETE FROM factor_snapshot WHERE snap_date=?", (snap_date,))
        self.conn.commit()
        out = df.copy()
        out["snap_date"] = snap_date
        self._upsert("factor_snapshot", out, ["snap_date", "symbol"])

    def get_factor_snapshot(self) -> pd.DataFrame:
        cur = self.conn.execute("SELECT MAX(snap_date) FROM factor_snapshot")
        latest = (cur.fetchone() or [None])[0]
        if not latest:
            return pd.DataFrame()
        return pd.read_sql("SELECT * FROM factor_snapshot WHERE snap_date=?",
                           self.conn, params=[latest])

    # ── 运行状态 KV ──
    def set_state(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO app_state(key, value) VALUES (?, ?)",
                          (key, value))
        self.conn.commit()

    def get_state(self, key: str) -> str | None:
        cur = self.conn.execute("SELECT value FROM app_state WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    # ── 内部：基于主键的 upsert ──
    def _upsert(self, table: str, df: pd.DataFrame, keys: list[str]) -> None:
        if df is None or df.empty:
            return
        # 仅保留表中存在的列
        existing_cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        cols = [c for c in df.columns if c in existing_cols]
        df = df[cols]
        placeholders = ",".join("?" * len(cols))
        collist = ",".join(cols)
        sql = (f"INSERT OR REPLACE INTO {table} ({collist}) "
               f"VALUES ({placeholders})")
        self.conn.executemany(sql, df.itertuples(index=False, name=None))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
