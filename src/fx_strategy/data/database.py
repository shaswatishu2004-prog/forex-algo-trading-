"""SQLite database manager for local market data storage."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


class MarketDataDB:
    """Manages local SQLite database for historical OHLCV data."""

    def __init__(self, db_path: str | Path = "data/forex_market_data.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Create and return a SQLite connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initialize database schema with primary key and search indices."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL DEFAULT 0.0,
                    source TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
                ON ohlcv (symbol, timeframe, timestamp)
                """
            )
            conn.commit()

    def save_ohlcv(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        source: str = "yfinance",
    ) -> int:
        """Save or update OHLCV DataFrame in the database.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame with datetime index or 'timestamp' column, and columns:
            ['open', 'high', 'low', 'close', 'volume'] (case-insensitive).
        symbol : str
            Symbol identifier, e.g. 'EURUSD', 'XAUUSD', 'DXY'.
        timeframe : str
            Timeframe, e.g. '1m', '1d'.
        source : str
            Source tag for auditability.

        Returns
        -------
        int
            Number of rows inserted/updated.
        """
        if df.empty:
            return 0

        data = df.copy()

        # Handle DatetimeIndex
        if "timestamp" not in [str(c).lower() for c in data.columns]:
            data = data.reset_index()
            # The reset index column could be 'Date', 'Datetime', or 'index'
            first_col = data.columns[0]
            data.rename(columns={first_col: "timestamp"}, inplace=True)

        # Standardize column names
        col_map = {}
        for col in data.columns:
            # Handle possible MultiIndex tuple from yfinance (e.g. ('Open', 'EURUSD=X'))
            name = col[0] if isinstance(col, tuple) else str(col)
            name_lower = str(name).strip().lower()
            if name_lower in ("open", "high", "low", "close", "volume", "timestamp"):
                col_map[col] = name_lower

        data = data.rename(columns=col_map)
        required = ["timestamp", "open", "high", "low", "close"]
        for r in required:
            if r not in data.columns:
                raise ValueError(f"Missing required column '{r}' in DataFrame. Found: {list(data.columns)}")

        if "volume" not in data.columns:
            data["volume"] = 0.0

        # Format timestamps to ISO-8601 string
        data["timestamp"] = pd.to_datetime(data["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S%z")
        # Ensure UTC or format consistently
        data["timestamp"] = data["timestamp"].apply(
            lambda t: t if t.endswith("+0000") or "+" in t or "-" in t[10:] else f"{t}+00:00"
        )

        data["symbol"] = symbol.upper()
        data["timeframe"] = timeframe.lower()
        data["source"] = source
        data["volume"] = data["volume"].fillna(0.0)

        # Prepare records for bulk insert
        records = data[
            ["symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume", "source"]
        ].itertuples(index=False, name=None)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR REPLACE INTO ohlcv (
                    symbol, timeframe, timestamp, open, high, low, close, volume, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                list(records),
            )
            conn.commit()

        return len(data)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data as a sorted pandas DataFrame indexed by timestamp.

        Parameters
        ----------
        symbol : str
            Symbol, e.g. 'EURUSD', 'XAUUSD', 'DXY'.
        timeframe : str
            '1m' or '1d'.
        start : str, optional
            Inclusive start timestamp filter (ISO string).
        end : str, optional
            Inclusive end timestamp filter (ISO string).

        Returns
        -------
        pd.DataFrame
            Sorted DataFrame with Open, High, Low, Close, Volume, and DatetimeIndex.
        """
        query = ["SELECT timestamp, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND timeframe = ?"]
        params: list[str] = [symbol.upper(), timeframe.lower()]

        if start is not None:
            query.append("AND timestamp >= ?")
            params.append(start)
        if end is not None:
            query.append("AND timestamp <= ?")
            params.append(end)

        query.append("ORDER BY timestamp ASC")
        sql = " ".join(query)

        with self.get_connection() as conn:
            df = pd.read_sql_query(sql, conn, params=params)

        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df.set_index("timestamp", inplace=True)

        return df

    def get_summary(self) -> pd.DataFrame:
        """Return a statistical overview of all symbols and timeframes in the DB."""
        sql = """
        SELECT
            symbol,
            timeframe,
            COUNT(*) AS total_bars,
            MIN(timestamp) AS earliest_timestamp,
            MAX(timestamp) AS latest_timestamp,
            ROUND(MIN(low), 5) AS min_price,
            ROUND(MAX(high), 5) AS max_price,
            SUM(CASE WHEN volume > 0 THEN 1 ELSE 0 END) AS bars_with_volume,
            source
        FROM ohlcv
        GROUP BY symbol, timeframe, source
        ORDER BY symbol, timeframe
        """
        with self.get_connection() as conn:
            return pd.read_sql_query(sql, conn)

    def export_csv(self, symbol: str, timeframe: str, output_path: str | Path) -> Path:
        """Export a symbol and timeframe dataset to CSV."""
        df = self.get_ohlcv(symbol, timeframe)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out)
        return out
