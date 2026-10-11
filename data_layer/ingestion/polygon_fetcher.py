"""
Polygon.io fetcher — primary data source for 1m OHLCV bars.

Fetches aggregate (OHLCV) bars for Forex pairs and COMEX gold from
the Polygon.io REST v2 API. Free tier allows ~5 req/min and provides
full minute-bar history back to January 2024 (and earlier).

References:
  https://polygon.io/docs/forex/get_v2_aggs_ticker__forexticker__range__multiplier__timespan__from__to
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from typing import List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"


class PolygonFetcher:
    """
    Fetches OHLCV minute bars from Polygon.io.

    Parameters
    ----------
    api_key : str, optional
        Polygon API key. Falls back to POLYGON_API_KEY env var.
    requests_per_minute : int
        Throttle to stay within free-tier limit (default: 4).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        requests_per_minute: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("POLYGON_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Polygon API key required. Set POLYGON_API_KEY env var or pass api_key."
            )
        self._min_interval = 60.0 / requests_per_minute
        self._last_request_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_1m(
        self,
        ticker: str,
        start: str | datetime.date,
        end: str | datetime.date,
    ) -> pd.DataFrame:
        """
        Fetch 1-minute aggregate bars for a Polygon ticker.

        Parameters
        ----------
        ticker : str
            Polygon ticker, e.g. 'C:EURUSD', 'C:XAUUSD', 'I:DXY'.
        start : str or date
            Inclusive start date (ISO format 'YYYY-MM-DD').
        end : str or date
            Inclusive end date (ISO format 'YYYY-MM-DD').

        Returns
        -------
        pd.DataFrame
            DatetimeIndex (UTC), columns: open, high, low, close, volume.
            Empty DataFrame if no data returned.
        """
        all_results: List[dict] = []
        url = self._build_url(ticker, start, end, multiplier=1, timespan="minute")

        page = 0
        while url:
            page += 1
            logger.debug("[Polygon] Page %d — %s", page, url[:80])
            data = self._get(url)

            results = data.get("results", [])
            all_results.extend(results)
            logger.debug("[Polygon] Got %d bars (total so far: %d)", len(results), len(all_results))

            # Polygon paginates via next_url
            url = data.get("next_url")
            if url:
                # next_url already includes apiKey; don't add again
                url = url if "apiKey" in url else url + f"&apiKey={self.api_key}"

        if not all_results:
            logger.warning("[Polygon] No bars returned for %s (%s → %s)", ticker, start, end)
            return pd.DataFrame()

        return self._to_dataframe(all_results)

    def fetch_daily(
        self,
        ticker: str,
        start: str | datetime.date,
        end: str | datetime.date,
    ) -> pd.DataFrame:
        """Fetch daily OHLCV bars."""
        all_results: List[dict] = []
        url = self._build_url(ticker, start, end, multiplier=1, timespan="day")

        while url:
            data = self._get(url)
            all_results.extend(data.get("results", []))
            url = data.get("next_url")
            if url and "apiKey" not in url:
                url += f"&apiKey={self.api_key}"

        if not all_results:
            return pd.DataFrame()

        return self._to_dataframe(all_results)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_url(
        self,
        ticker: str,
        start,
        end,
        multiplier: int,
        timespan: str,
    ) -> str:
        start_str = str(start)[:10]
        end_str = str(end)[:10]
        return (
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}"
            f"/{start_str}/{end_str}"
            f"?adjusted=false&sort=asc&limit=50000&apiKey={self.api_key}"
        )

    def _get(self, url: str) -> dict:
        """Rate-limited GET request."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        resp = requests.get(url, timeout=30)
        self._last_request_at = time.monotonic()

        if resp.status_code == 429:
            logger.warning("[Polygon] Rate-limited (429). Sleeping 60s.")
            time.sleep(60)
            return self._get(url)

        resp.raise_for_status()
        return resp.json()

    def _to_dataframe(self, results: List[dict]) -> pd.DataFrame:
        """Convert Polygon aggregate results list to a clean DataFrame."""
        df = pd.DataFrame(results)
        # Polygon columns: t (ms epoch), o, h, l, c, v, vw, n
        df["ts"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df = df[["ts", "open", "high", "low", "close", "volume"]]
        df = df.set_index("ts").sort_index()
        df = df.dropna(subset=["open", "high", "low", "close"])
        return df
