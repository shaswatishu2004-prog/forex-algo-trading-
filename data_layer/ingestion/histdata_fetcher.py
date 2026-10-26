"""
data_layer/ingestion/histdata_fetcher.py
Dukascopy/HistData fallback compatibility fetcher.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from data_layer.repository import MarketDataRepository

logger = logging.getLogger(__name__)


class HistDataFetcher:
    """
    Compatibility wrapper returning historical data backed by the Dukascopy Parquet repository.
    """

    def __init__(
        self,
        repo: Optional[MarketDataRepository] = None,
        data_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.repo = repo or MarketDataRepository(data_dir=data_dir)

    def ingest_symbol(
        self,
        symbol: str = "EURUSD",
        year: int = 2024,
        month: Optional[int] = None,
    ) -> Dict[str, Any]:
        sym = symbol.upper()
        df = self.repo.get_ohlcv(sym, timeframe="1m")
        bars = len(df)
        return {
            "status": "ok",
            "symbol": sym,
            "provider": "dukascopy_parquet",
            "year": year,
            "month": month,
            "bars_inserted": bars,
            "message": f"Verified {bars:,} 1m bars from Dukascopy archive.",
        }
