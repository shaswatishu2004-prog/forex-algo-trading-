"""
Ingestion pipeline — orchestrates Infoway API → Repository.

Usage:
    python scripts/ingest_history.py --symbols XAUUSD EURUSD USDJPY DXY
                                     --timeframe 1m

This pipeline:
  1. Loads symbol config and Infoway API key from .env
  2. For each symbol, fetches real-time & historical bars from Infoway API
  3. Upserts into database (TimescaleDB or SQLite) via MarketDataRepository
  4. Keeps live market data 100% current
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
from typing import List, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from data_layer.ingestion.infoway_fetcher import InfowayFetcher
from data_layer.repository import MarketDataRepository

logger = logging.getLogger(__name__)

TARGET_SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "DXY"]


class IngestionPipeline:
    """
    Coordinates data ingestion into the database using Infoway API.
    """

    def __init__(
        self,
        config_path: str = "config/data.yaml",
        api_key: Optional[str] = None,
    ) -> None:
        self.repo = MarketDataRepository()
        key = api_key or os.environ.get("INFOWAY_API_KEY") or os.environ.get("POLYGON_API_KEY", "")
        self.fetcher = InfowayFetcher(api_key=key)

    def run(
        self,
        symbols: Optional[List[str]] = None,
        timeframe: str = "1m",
        bars_per_symbol: int = 500,
        dry_run: bool = False,
    ) -> dict:
        """
        Ingest historical bars from Infoway API.
        """
        targets = [s.upper() for s in (symbols or TARGET_SYMBOLS)]
        results = {}

        for sym in targets:
            logger.info("Fetching %d bars for %s [%s] from Infoway...", bars_per_symbol, sym, timeframe)
            df = self.fetcher.fetch_history(sym, timeframe=timeframe, total_bars=bars_per_symbol)

            if df.empty:
                logger.warning("No bars returned for %s from Infoway", sym)
                results[sym] = {"fetched": 0, "inserted": 0}
                continue

            inserted = 0
            if not dry_run:
                inserted = self.repo.upsert_ohlcv(df, sym, timeframe, source="infoway")
                logger.info("  %s: inserted %d/%d bars into DB", sym, inserted, len(df))
            else:
                inserted = len(df)

            results[sym] = {"fetched": len(df), "inserted": inserted}

        return results

    def sync_latest(self, symbols: Optional[List[str]] = None, days_back: int = 7) -> dict:
        """
        Sync latest bars for target symbols using Infoway API.
        """
        targets = [s.upper() for s in (symbols or TARGET_SYMBOLS)]
        results = {}

        for sym in targets:
            try:
                # 1. Fetch extended 1-minute bars (1500 bars)
                df_1m = self.fetcher.fetch_history(sym, timeframe="1m", total_bars=1500)
                inserted_1m = 0
                if not df_1m.empty:
                    inserted_1m = self.repo.upsert_ohlcv(df_1m, sym, "1m", source="infoway")

                # 2. Fetch 2-year daily history starting Jan 2024 (800 daily bars)
                df_1d = self.fetcher.fetch_history(sym, timeframe="1d", total_bars=800)
                inserted_1d = 0
                if not df_1d.empty:
                    inserted_1d = self.repo.upsert_ohlcv(df_1d, sym, "1d", source="infoway")

                total = inserted_1m + inserted_1d
                results[sym] = {"synced": True, "bars_added": total}
                logger.info("  %s synced via Infoway (+%d bars)", sym, total)
            except Exception as exc:
                logger.error("Error syncing %s via Infoway: %s", sym, exc)
                results[sym] = {"synced": False, "error": str(exc)}

        return results
