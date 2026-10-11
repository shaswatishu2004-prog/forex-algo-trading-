"""
TimescaleDB & SQLite Unified Repository — single gateway between Python and market data storage.

Supports:
  1. TimescaleDB (PostgreSQL) — Primary high-performance time-series hypertable store.
  2. SQLite (local fallback)  — Seamless fallback to data/forex_market_data.db when Docker / TimescaleDB is offline.

This module is intentionally ANALYSIS-FREE. It only handles:
  - Read OHLCV bars (with optional time/symbol filtering)
  - Write raw OHLCV bars (upsert)
  - Read/write ingestion log
  - Coverage queries (per-day bar counts)
  - Summary metadata queries
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional, Union

import pandas as pd

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = REPO_ROOT / "data" / "forex_market_data.db"


def _build_dsn(cfg: dict) -> str:
    """Build a psycopg2 DSN string from a config dict."""
    db = cfg.get("database", cfg)
    return (
        f"host={db.get('host', 'localhost')} "
        f"port={db.get('port', 5432)} "
        f"dbname={db.get('name', 'forex_data')} "
        f"user={db.get('user', 'forex_user')} "
        f"password={db.get('password', 'forex_pass')}"
    )


class MarketDataRepository:
    """
    Unified gateway to market data.
    Attempts TimescaleDB / PostgreSQL first; gracefully falls back to local SQLite if unavailable.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        config: Optional[dict] = None,
        sqlite_path: Optional[Union[str, Path]] = None,
        force_sqlite: bool = False,
    ) -> None:
        self.sqlite_path = Path(sqlite_path or DEFAULT_SQLITE_PATH)
        self._dsn = dsn or (_build_dsn(config) if config else os.environ.get(
            "FOREX_DB_DSN",
            "host=localhost port=5432 dbname=forex_data user=forex_user password=forex_pass",
        ))

        self.backend = "sqlite" if force_sqlite else self._detect_backend()
        logger.info("MarketDataRepository initialized using [%s] backend", self.backend)

        if self.backend == "sqlite":
            self._init_sqlite()

    def _detect_backend(self) -> str:
        """Test PostgreSQL / TimescaleDB connection; fall back to SQLite if unreachable."""
        if os.environ.get("FOREX_DB_BACKEND", "").lower() == "sqlite":
            return "sqlite"

        try:
            import psycopg2
            conn = psycopg2.connect(self._dsn, connect_timeout=2)
            conn.close()
            return "timescaledb"
        except Exception:
            logger.info("TimescaleDB not reachable at %s. Falling back to local SQLite at %s", self._dsn, self.sqlite_path)
            return "sqlite"

    def _init_sqlite(self) -> None:
        """Ensure SQLite tables and indices exist."""
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.sqlite_path)) as conn:
            cur = conn.cursor()
            cur.execute("""
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
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
                ON ohlcv (symbol, timeframe, timestamp)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    bars_fetched INTEGER DEFAULT 0,
                    bars_inserted INTEGER DEFAULT 0,
                    date_from TEXT,
                    date_to TEXT,
                    status TEXT NOT NULL,
                    error_msg TEXT
                )
            """)
            conn.commit()

    @contextmanager
    def _pg_conn(self):
        import psycopg2
        conn = psycopg2.connect(self._dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _sqlite_conn(self):
        conn = sqlite3.connect(str(self.sqlite_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_ohlcv(self, df: pd.DataFrame, symbol: str, timeframe: str, source: str) -> int:
        """Upsert OHLCV rows. Skips duplicates via ON CONFLICT DO NOTHING / INSERT OR REPLACE."""
        if df.empty:
            return 0

        data = df.copy()
        if "timestamp" not in [c.lower() for c in data.columns]:
            data = data.reset_index()
            first_col = data.columns[0]
            data.rename(columns={first_col: "timestamp"}, inplace=True)

        data.columns = [c.lower() for c in data.columns]
        for col in ("open", "high", "low", "close"):
            if col not in data.columns:
                raise ValueError(f"Missing required column '{col}'")
        if "volume" not in data.columns:
            data["volume"] = 0.0

        data["volume"] = data["volume"].fillna(0.0)
        data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)

        if self.backend == "timescaledb":
            import psycopg2.extras
            records = [
                (
                    row.timestamp.to_pydatetime(),
                    symbol.upper(),
                    timeframe.lower(),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume) if row.volume is not None else 0.0,
                    source,
                )
                for row in data.itertuples()
            ]
            insert_sql = """
                INSERT INTO ohlcv (ts, symbol, timeframe, open, high, low, close, volume, source)
                VALUES %s
                ON CONFLICT (symbol, timeframe, ts) DO NOTHING
            """
            with self._pg_conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, insert_sql, records, page_size=1000)
                    inserted = cur.rowcount
            logger.info("Upserted %d/%d bars for %s [%s] into TimescaleDB", inserted, len(records), symbol, timeframe)
            return inserted
        else:
            records = [
                (
                    symbol.upper(),
                    timeframe.lower(),
                    row.timestamp.strftime("%Y-%m-%d %H:%M:%S+00:00"),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    source,
                )
                for row in data.itertuples()
            ]
            with self._sqlite_conn() as conn:
                cur = conn.cursor()
                cur.executemany("""
                    INSERT OR REPLACE INTO ohlcv (
                        symbol, timeframe, timestamp, open, high, low, close, volume, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
            logger.info("Upserted %d bars for %s [%s] into SQLite", len(records), symbol, timeframe)
            return len(records)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[Union[str, datetime, date]] = None,
        end: Optional[Union[str, datetime, date]] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars as a pandas DataFrame indexed by UTC timestamp."""
        symbol_upper = symbol.upper()
        tf_lower = timeframe.lower()

        if self.backend == "timescaledb":
            table = self._resolve_table(tf_lower)
            conditions = ["symbol = %s"]
            params: list = [symbol_upper]

            if start is not None:
                conditions.append("ts >= %s")
                params.append(pd.Timestamp(start, tz="UTC").to_pydatetime())
            if end is not None:
                conditions.append("ts <= %s")
                params.append(pd.Timestamp(end, tz="UTC").to_pydatetime())

            where = " AND ".join(conditions)
            limit_clause = f"LIMIT {limit}" if limit else ""
            query = f"""
                SELECT ts, open, high, low, close, volume
                FROM {table}
                WHERE {where}
                ORDER BY ts ASC
                {limit_clause}
            """
            with self._pg_conn() as conn:
                df = pd.read_sql_query(query, conn, params=params, parse_dates=["ts"])
            if not df.empty:
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df.set_index("ts", inplace=True)
            return df
        else:
            query = ["SELECT timestamp as ts, open, high, low, close, volume FROM ohlcv WHERE symbol = ? AND timeframe = ?"]
            params_sqlite: list = [symbol_upper, tf_lower]

            if start is not None:
                query.append("AND timestamp >= ?")
                params_sqlite.append(str(start)[:19])
            if end is not None:
                query.append("AND timestamp <= ?")
                params_sqlite.append(str(end)[:19] if len(str(end)) >= 19 else f"{str(end)} 23:59:59")

            query.append("ORDER BY timestamp ASC")
            if limit:
                query.append(f"LIMIT {limit}")

            sql = " ".join(query)
            with self._sqlite_conn() as conn:
                df = pd.read_sql_query(sql, conn, params=params_sqlite)

            if not df.empty:
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df.set_index("ts", inplace=True)
            return df

    def _resolve_table(self, timeframe: str) -> str:
        mapping = {
            "1m": "ohlcv",
            "5m": "ohlcv_5m",
            "15m": "ohlcv_15m",
            "1h": "ohlcv_1h",
            "1d": "ohlcv",
        }
        tf = timeframe.lower()
        if tf not in mapping:
            raise ValueError(f"Unsupported timeframe '{timeframe}'. Choose from {list(mapping)}")
        return mapping[tf]

    # ------------------------------------------------------------------
    # Coverage / metadata
    # ------------------------------------------------------------------

    def get_coverage(
        self,
        symbol: str,
        timeframe: str = "1m",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return per-day bar counts for coverage analysis."""
        expected = 1440 if timeframe.lower() == "1m" else 1

        if self.backend == "timescaledb":
            conditions = ["symbol = %s", "timeframe = %s"]
            params: list = [symbol.upper(), timeframe.lower()]
            if start:
                conditions.append("ts >= %s")
                params.append(start)
            if end:
                conditions.append("ts <= %s")
                params.append(end)

            where = " AND ".join(conditions)
            query = f"""
                SELECT
                    DATE(ts AT TIME ZONE 'UTC') AS date,
                    COUNT(*) AS bar_count,
                    {expected} AS expected_bars,
                    ROUND(COUNT(*) * 100.0 / {expected}, 1) AS coverage_pct
                FROM ohlcv
                WHERE {where}
                GROUP BY DATE(ts AT TIME ZONE 'UTC')
                ORDER BY date ASC
            """
            with self._pg_conn() as conn:
                return pd.read_sql_query(query, conn, params=params, parse_dates=["date"])
        else:
            query = [
                f"""
                SELECT
                    SUBSTR(timestamp, 1, 10) AS date,
                    COUNT(*) AS bar_count,
                    {expected} AS expected_bars,
                    ROUND(COUNT(*) * 100.0 / {expected}, 1) AS coverage_pct
                FROM ohlcv
                WHERE symbol = ? AND timeframe = ?
                """
            ]
            params = [symbol.upper(), timeframe.lower()]
            if start:
                query.append("AND timestamp >= ?")
                params.append(start)
            if end:
                query.append("AND timestamp <= ?")
                params.append(end)
            query.append("GROUP BY SUBSTR(timestamp, 1, 10) ORDER BY date ASC")

            sql = " ".join(query)
            with self._sqlite_conn() as conn:
                df = pd.read_sql_query(sql, conn, params=params)
            return df

    def get_summary(self) -> pd.DataFrame:
        """High-level summary of all symbols/timeframes in the DB."""
        if self.backend == "timescaledb":
            query = """
                SELECT
                    symbol,
                    timeframe,
                    source,
                    COUNT(*)                        AS total_bars,
                    MIN(ts)::DATE                   AS earliest,
                    MAX(ts)::DATE                   AS latest,
                    ROUND(MIN(low)::NUMERIC, 5)     AS min_price,
                    ROUND(MAX(high)::NUMERIC, 5)    AS max_price
                FROM ohlcv
                GROUP BY symbol, timeframe, source
                ORDER BY symbol, timeframe
            """
            with self._pg_conn() as conn:
                return pd.read_sql_query(query, conn)
        else:
            query = """
                SELECT
                    symbol,
                    timeframe,
                    source,
                    COUNT(*) AS total_bars,
                    SUBSTR(MIN(timestamp), 1, 10) AS earliest,
                    SUBSTR(MAX(timestamp), 1, 10) AS latest,
                    ROUND(MIN(low), 5) AS min_price,
                    ROUND(MAX(high), 5) AS max_price
                FROM ohlcv
                GROUP BY symbol, timeframe, source
                ORDER BY symbol, timeframe
            """
            with self._sqlite_conn() as conn:
                return pd.read_sql_query(query, conn)

    def get_symbols(self) -> List[str]:
        """List distinct symbols in the DB."""
        if self.backend == "timescaledb":
            query = "SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol"
            with self._pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    return [row[0] for row in cur.fetchall()]
        else:
            query = "SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol"
            with self._sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(query)
                return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Ingestion log
    # ------------------------------------------------------------------

    def log_ingestion_start(self, symbol: str, timeframe: str, source: str) -> int:
        """Insert a new ingestion_log row and return its id."""
        now_iso = datetime.now(timezone.utc).isoformat()
        if self.backend == "timescaledb":
            query = """
                INSERT INTO ingestion_log (symbol, timeframe, source, status, started_at)
                VALUES (%s, %s, %s, 'running', NOW())
                RETURNING id
            """
            with self._pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (symbol.upper(), timeframe.lower(), source))
                    row = cur.fetchone()
                    return row[0] if row is not None else 1
        else:
            query = """
                INSERT INTO ingestion_log (symbol, timeframe, source, started_at, status)
                VALUES (?, ?, ?, ?, 'running')
            """
            with self._sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(query, (symbol.upper(), timeframe.lower(), source, now_iso))
                return cur.lastrowid or 1

    def log_ingestion_finish(
        self,
        log_id: int,
        bars_fetched: int,
        bars_inserted: int,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        status: str = "success",
        error_msg: Optional[str] = None,
    ) -> None:
        """Update an ingestion_log row with completion details."""
        now_iso = datetime.now(timezone.utc).isoformat()
        df_str = date_from.isoformat() if date_from else None
        dt_str = date_to.isoformat() if date_to else None

        if self.backend == "timescaledb":
            query = """
                UPDATE ingestion_log SET
                    finished_at   = NOW(),
                    bars_fetched  = %s,
                    bars_inserted = %s,
                    date_from     = %s,
                    date_to       = %s,
                    status        = %s,
                    error_msg     = %s
                WHERE id = %s
            """
            with self._pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (bars_fetched, bars_inserted, date_from, date_to, status, error_msg, log_id))
        else:
            query = """
                UPDATE ingestion_log SET
                    finished_at   = ?,
                    bars_fetched  = ?,
                    bars_inserted = ?,
                    date_from     = ?,
                    date_to       = ?,
                    status        = ?,
                    error_msg     = ?
                WHERE id = ?
            """
            with self._sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute(query, (now_iso, bars_fetched, bars_inserted, df_str, dt_str, status, error_msg, log_id))

    def get_ingestion_log(self, limit: int = 50) -> pd.DataFrame:
        """Fetch recent ingestion log entries."""
        if self.backend == "timescaledb":
            query = """
                SELECT id, symbol, timeframe, source, started_at, finished_at,
                       bars_fetched, bars_inserted, date_from, date_to, status, error_msg
                FROM ingestion_log
                ORDER BY started_at DESC
                LIMIT %s
            """
            with self._pg_conn() as conn:
                return pd.read_sql_query(query, conn, params=(limit,))
        else:
            query = f"""
                SELECT id, symbol, timeframe, source, started_at, finished_at,
                       bars_fetched, bars_inserted, date_from, date_to, status, error_msg
                FROM ingestion_log
                ORDER BY id DESC
                LIMIT {limit}
            """
            with self._sqlite_conn() as conn:
                return pd.read_sql_query(query, conn)
