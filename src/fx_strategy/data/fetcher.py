"""Data fetcher module using yfinance for Forex, Gold, and Dollar Index data."""

from __future__ import annotations

import datetime
import logging
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from fx_strategy.data.database import MarketDataDB

logger = logging.getLogger(__name__)

# Default ticker mapping
# EURUSD: Spot FX feed (EURUSD=X)
# XAUUSD: COMEX Gold continuous futures contract (GC=F) tracking spot gold with real trade volume
# DXY: ICE US Dollar Index benchmark (DX-Y.NYB)
DEFAULT_TICKER_MAP: Dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "XAUUSD": "GC=F",
    "DXY": "DX-Y.NYB",
}


class ForexDataFetcher:
    """Fetches historical market data for FX pairs, Gold, and DXY."""

    def __init__(self, ticker_map: Optional[Dict[str, str]] = None) -> None:
        self.ticker_map = ticker_map or DEFAULT_TICKER_MAP.copy()

    def get_ticker(self, symbol: str) -> str:
        """Resolve canonical symbol to yfinance ticker."""
        sym = symbol.upper()
        if sym in self.ticker_map:
            return self.ticker_map[sym]
        return sym

    def fetch_daily(self, symbol: str, period: str = "max") -> pd.DataFrame:
        """Fetch historical daily OHLCV bars.

        Parameters
        ----------
        symbol : str
            Canonical symbol, e.g. 'EURUSD', 'XAUUSD', 'DXY'.
        period : str
            Yahoo Finance period string ('max', '10y', '5y', etc.).

        Returns
        -------
        pd.DataFrame
            Daily OHLCV DataFrame.
        """
        ticker = self.get_ticker(symbol)
        logger.info("Fetching daily data for %s (ticker: %s, period: %s)...", symbol, ticker, period)
        df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)

        if df.empty:
            logger.warning("No daily data returned for %s (%s)", symbol, ticker)
            return pd.DataFrame()

        # Handle MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]

        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        return df

    def fetch_1m(self, symbol: str, days_back: int = 28, chunk_days: int = 7) -> pd.DataFrame:
        """Fetch 1-minute intraday bars in rolling chunks up to days_back.

        Yahoo Finance restricts 1m queries to the past 30 days. To maximize history,
        we query consecutive rolling chunks of chunk_days and merge them.

        Parameters
        ----------
        symbol : str
            Canonical symbol, e.g. 'EURUSD', 'XAUUSD', 'DXY'.
        days_back : int
            Total days to retrieve (capped at 29 to comply with Yahoo's 30d limit).
        chunk_days : int
            Size of each window chunk in days (7 is standard).

        Returns
        -------
        pd.DataFrame
            Merged and sorted 1-minute OHLCV DataFrame.
        """
        ticker = self.get_ticker(symbol)
        days_back = min(days_back, 29)
        now = datetime.datetime.now(datetime.timezone.utc)
        all_chunks: List[pd.DataFrame] = []

        logger.info(
            "Fetching 1m data for %s (%s) across the past %d days in %d-day chunks...",
            symbol,
            ticker,
            days_back,
            chunk_days,
        )

        offset = 0
        while offset < days_back:
            chunk_end = now - datetime.timedelta(days=offset)
            chunk_start = now - datetime.timedelta(days=min(offset + chunk_days, days_back))

            # Format for yfinance
            start_str = chunk_start.strftime("%Y-%m-%d")
            end_str = chunk_end.strftime("%Y-%m-%d")

            try:
                chunk_df = yf.download(
                    ticker,
                    start=start_str,
                    end=end_str,
                    interval="1m",
                    progress=False,
                    auto_adjust=False,
                )
                if not chunk_df.empty:
                    if isinstance(chunk_df.columns, pd.MultiIndex):
                        chunk_df.columns = [c[0] for c in chunk_df.columns]
                    all_chunks.append(chunk_df)
                    logger.debug("Fetched %d 1m bars from %s to %s", len(chunk_df), start_str, end_str)
            except Exception as e:
                logger.warning("Error fetching chunk %s to %s for %s: %s", start_str, end_str, symbol, e)

            offset += chunk_days

        if not all_chunks:
            logger.warning("No 1m data could be retrieved for %s (%s)", symbol, ticker)
            return pd.DataFrame()

        merged = pd.concat(all_chunks)
        # Drop duplicates on timestamp index
        merged = merged[~merged.index.duplicated(keep="first")]
        merged.sort_index(inplace=True)
        merged = merged.dropna(subset=["Open", "High", "Low", "Close"])
        return merged

    def fetch_and_store(
        self,
        symbol: str,
        timeframe: str,
        db: MarketDataDB,
        period_daily: str = "max",
        days_back_1m: int = 28,
    ) -> int:
        """Fetch data for a symbol and timeframe, then save into local SQLite database."""
        sym = symbol.upper()
        tf = timeframe.lower()
        ticker = self.get_ticker(sym)

        if tf == "1m":
            df = self.fetch_1m(sym, days_back=days_back_1m)
        elif tf == "1d":
            df = self.fetch_daily(sym, period=period_daily)
        else:
            raise ValueError(f"Unsupported timeframe '{timeframe}'. Use '1m' or '1d'.")

        if df.empty:
            logger.warning("No records to save for %s (%s)", sym, tf)
            return 0

        source_tag = f"yfinance:{ticker}"
        count = db.save_ohlcv(df, symbol=sym, timeframe=tf, source=source_tag)
        logger.info("Successfully stored %d bars for %s [%s] in local database.", count, sym, tf)
        return count

    def fetch_all_and_store(
        self,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        db: Optional[MarketDataDB] = None,
    ) -> Dict[str, int]:
        """Fetch all specified symbols and timeframes and store them in database."""
        symbols = symbols or ["EURUSD", "XAUUSD", "DXY"]
        timeframes = timeframes or ["1m", "1d"]
        target_db = db or MarketDataDB()

        results: Dict[str, int] = {}
        for sym in symbols:
            for tf in timeframes:
                key = f"{sym.upper()}_{tf.lower()}"
                try:
                    count = self.fetch_and_store(sym, tf, target_db)
                    results[key] = count
                except Exception as e:
                    logger.error("Failed to fetch/store %s: %s", key, e)
                    results[key] = 0

        return results
