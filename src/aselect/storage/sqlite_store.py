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
    date   TEXT NOT NULL,     -- 报告期 / 快照日
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
"""


class SQLiteStorage(Storage):
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

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

    def get_fundamentals(self, symbols=None) -> pd.DataFrame:
        sql = "SELECT * FROM fundamentals"
        params: list = []
        if symbols:
            placeholders = ",".join("?" * len(symbols))
            sql += f" WHERE symbol IN ({placeholders})"
            params = list(symbols)
        return pd.read_sql(sql, self.conn, params=params)

    # ── features (AI 产出) ──
    def upsert_features(self, df: pd.DataFrame) -> None:
        self._upsert("features", df, ["symbol", "date"])

    def get_features(self, symbols=None) -> pd.DataFrame:
        sql = "SELECT * FROM features"
        params: list = []
        if symbols:
            placeholders = ",".join("?" * len(symbols))
            sql += f" WHERE symbol IN ({placeholders})"
            params = list(symbols)
        return pd.read_sql(sql, self.conn, params=params)

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
