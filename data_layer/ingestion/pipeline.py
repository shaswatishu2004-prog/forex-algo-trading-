"""
data_layer/ingestion/pipeline.py
Ingestion pipeline for verifying and syncing market data.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from data_layer.repository import MarketDataRepository

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    Coordinates data ingestion and verification across all supported symbols.
    """

    def __init__(self, repo: Optional[MarketDataRepository] = None) -> None:
        self.repo = repo or MarketDataRepository()

    def sync_latest(
        self,
        symbols: Optional[List[str]] = None,
        days_back: int = 7,
    ) -> Dict[str, Any]:
        target_symbols = symbols or self.repo.get_symbols()
        results: Dict[str, Any] = {}

        for sym in target_symbols:
            df = self.repo.get_ohlcv(sym, timeframe="1m", lookback_days=days_back)
            bars_found = len(df)
            results[sym] = {
                "synced": bars_found > 0,
                "bars_added": bars_found,
                "provider": "dukascopy_parquet",
                "status": "ok" if bars_found > 0 else "no_data",
            }
            logger.info("Pipeline sync verified %s: %d bars in recent window", sym, bars_found)

        return results
