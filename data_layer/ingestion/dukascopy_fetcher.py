"""
data_layer/ingestion/dukascopy_fetcher.py
Integration bridge for Dukascopy 1-minute historical datasets.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from data_layer.repository import MarketDataRepository

logger = logging.getLogger(__name__)


class DukascopyFetcher:
    """
    Bridge fetcher that serves Dukascopy 1-minute historical parquet data.
    """

    def __init__(
        self,
        repo: Optional[MarketDataRepository] = None,
        data_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.repo = repo or MarketDataRepository(data_dir=data_dir)

    def sync_symbol(
        self,
        symbol: str,
        force_lookback_days: int = 365,
    ) -> Dict[str, Any]:
        """
        Verify symbol availability and ensure in-memory cache is primed.
        """
        sym = symbol.upper()
        df = self.repo.get_ohlcv(sym, timeframe="1m")
        bars_count = len(df)

        if not df.empty:
            logger.info("Dukascopy dataset ready for %s: %d bars available", sym, bars_count)
            return {
                "status": "ok",
                "synced": True,
                "symbol": sym,
                "provider": "dukascopy_parquet",
                "bars_added": bars_count,
                "bars_inserted": bars_count,
                "earliest": str(df.index.min())[:19],
                "latest": str(df.index.max())[:19],
            }

        logger.warning("No data found for symbol %s in Dukascopy parquet store", sym)
        return {
            "status": "not_found",
            "synced": False,
            "symbol": sym,
            "provider": "dukascopy_parquet",
            "bars_added": 0,
            "bars_inserted": 0,
        }
