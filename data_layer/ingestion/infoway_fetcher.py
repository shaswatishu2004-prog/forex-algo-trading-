"""
Infoway Market Data Fetcher — primary source for real-time and historical Forex & Commodity bars.

References:
  https://docs.infoway.io/en-docs/readme/forex-market-data-api
  https://docs.infoway.io/sdk-kai-fa-gong-ju/infoway-python-sdk
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

try:
    from infoway import InfowayClient, KlineType
    INFOWAY_AVAILABLE = True
except ImportError:
    INFOWAY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Canonical map to Infoway symbols
DEFAULT_INFOWAY_SYMBOLS: Dict[str, str] = {
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "XAUUSD": "XAUUSD",
    "DXY": "DXY",
    "GBPUSD": "GBPUSD",
    "AUDUSD": "AUDUSD",
}

TIMEFRAME_MAP = {
    "1m": KlineType.MIN_1 if INFOWAY_AVAILABLE else 1,
    "5m": KlineType.MIN_5 if INFOWAY_AVAILABLE else 2,
    "15m": KlineType.MIN_15 if INFOWAY_AVAILABLE else 3,
    "30m": KlineType.MIN_30 if INFOWAY_AVAILABLE else 4,
    "1h": KlineType.HOUR_1 if INFOWAY_AVAILABLE else 5,
    "4h": KlineType.HOUR_4 if INFOWAY_AVAILABLE else 7,
    "1d": KlineType.DAY if INFOWAY_AVAILABLE else 8,
}


class InfowayFetcher:
    """
    Fetches official live and historical OHLCV candles from Infoway API.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        if not INFOWAY_AVAILABLE:
            raise ImportError("infoway-sdk not installed. Run: pip install infoway-sdk")

        self.api_key = (
            api_key
            or os.environ.get("INFOWAY_API_KEY")
            or os.environ.get("POLYGON_API_KEY")  # fallback if set under previous var
        )
        if not self.api_key:
            raise ValueError("Infoway API key required. Set INFOWAY_API_KEY in .env")

        self._client = InfowayClient(api_key=self.api_key)

    def get_info(self) -> dict:
        """Fetch package subscription info."""
        try:
            return self._client.packages.get_info()
        except Exception as e:
            logger.warning("Could not fetch Infoway package info: %s", e)
            return {}

    def fetch_kline(
        self,
        symbol: str,
        timeframe: str = "1m",
        count: int = 500,
        timestamp: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles for a given currency pair or commodity.

        Parameters
        ----------
        symbol : str
            e.g. 'EURUSD', 'XAUUSD', 'USDJPY', 'DXY'
        timeframe : str
            '1m', '5m', '15m', '1h', '1d'
        count : int
            Number of candles (max 500 per call)
        timestamp : int, optional
            Unix epoch timestamp in seconds for historical pagination
        """
        sym = symbol.upper()
        k_type = TIMEFRAME_MAP.get(timeframe.lower(), KlineType.MIN_1)
        k_count = min(count, 500)

        # Respect Infoway rate-limits
        time.sleep(0.35)

        resp_list = []
        for attempt in range(3):
            try:
                kwargs = {}
                if timestamp:
                    kwargs["timestamp"] = int(timestamp)

                raw = self._client.common.get_kline(sym, k_type, k_count, **kwargs)
                if not raw or not isinstance(raw, list) or len(raw) == 0:
                    return pd.DataFrame()

                resp_list = raw[0].get("respList", [])
                if not resp_list:
                    return pd.DataFrame()
                break
            except Exception as exc:
                if "429" in str(exc) and attempt < 2:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                logger.error("Infoway fetch error for %s [%s] (attempt %d): %s", sym, timeframe, attempt + 1, exc)
                return pd.DataFrame()

        if not resp_list:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(resp_list)
            # Standardize columns: t, o, h, l, c, v
            df["timestamp"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
            df["open"] = df["o"].astype(float)
            df["high"] = df["h"].astype(float)
            df["low"] = df["l"].astype(float)
            df["close"] = df["c"].astype(float)
            df["volume"] = df["v"].astype(float)

            df = df[["timestamp", "open", "high", "low", "close", "volume"]]
            df.sort_values("timestamp", ascending=True, inplace=True)
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as exc:
            logger.error("Infoway parse error for %s [%s]: %s", sym, timeframe, exc)
            return pd.DataFrame()

    def fetch_history(
        self,
        symbol: str,
        timeframe: str = "1m",
        total_bars: int = 1500,
        delay_sec: float = 1.0,
    ) -> pd.DataFrame:
        """
        Fetch extended history by paginating backwards in time.
        """
        all_dfs: List[pd.DataFrame] = []
        needed = total_bars
        current_ts = None

        while needed > 0:
            fetch_count = min(needed, 500)
            df = self.fetch_kline(symbol, timeframe, count=fetch_count, timestamp=current_ts)
            if df.empty:
                break

            all_dfs.append(df)
            needed -= len(df)

            # Move timestamp pointer to the earliest bar in this batch minus 1 minute
            earliest_dt = df.index.min()
            current_ts = int(earliest_dt.timestamp()) - 60

            if len(df) < 50:  # reached the boundary
                break

            time.sleep(delay_sec)

        if not all_dfs:
            return pd.DataFrame()

        merged = pd.concat(all_dfs)
        merged = merged[~merged.index.duplicated(keep="first")]
        merged.sort_index(ascending=True, inplace=True)
        return merged
