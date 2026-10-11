"""
scripts/download_all_data.py — Direct HTTP API bulk historical market data downloader.

Pulls multi-year historical candles from Infoway HTTP API:
  - Endpoint: https://data.infoway.io/common/v2/batch_kline
  - Authentication: Header apikey: <INFOWAY_API_KEY>
  - Symbols: XAUUSD (COMEX Gold), EURUSD, USDJPY, DXY
  - Timeframes: 1d (2+ years from Jan 2024), 1h, 15m, 5m, 1m
  - Writes directly to local market database for future backtesting.

Usage:
  python scripts/download_all_data.py
  python scripts/download_all_data.py --symbols EURUSD XAUUSD --timeframes 1d 1h
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv()

from data_layer.repository import MarketDataRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("infoway_downloader")

INFOWAY_URL = "https://data.infoway.io/common/v2/batch_kline"
TARGET_SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "DXY"]

TIMEFRAME_KLINE_MAP = {
    "1m": 1,
    "5m": 2,
    "15m": 3,
    "30m": 4,
    "1h": 5,
    "2h": 6,
    "4h": 7,
    "1d": 8,
    "1w": 9,
}


def download_candles_http(
    symbol: str,
    timeframe: str,
    total_bars: int,
    api_key: str,
    client: httpx.Client,
) -> pd.DataFrame:
    """
    Download candles via direct HTTP API paginating backwards.
    """
    kline_type = TIMEFRAME_KLINE_MAP.get(timeframe.lower(), 1)
    all_records: List[Dict[str, Any]] = []
    needed = total_bars
    current_ts: Optional[int] = None

    headers = {"apikey": api_key, "Accept": "application/json"}

    while needed > 0:
        fetch_num = min(needed, 500)
        payload: Dict[str, Any] = {
            "codes": symbol.upper(),
            "klineType": kline_type,
            "klineNum": fetch_num,
        }
        if current_ts is not None:
            payload["timestamp"] = current_ts

        # Rate-limit delay (0.35s between requests)
        time.sleep(0.35)

        success = False
        for attempt in range(3):
            try:
                resp = client.post(INFOWAY_URL, headers=headers, json=payload, timeout=15.0)
                if resp.status_code == 429:
                    logger.warning("Rate limit on %s [%s], retrying in 1.5s...", symbol, timeframe)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    logger.error("HTTP error %d on %s: %s", resp.status_code, symbol, resp.text[:100])
                    break

                data = resp.json()
                if data.get("ret") != 200 or not data.get("data"):
                    break

                resp_list = data["data"][0].get("respList", [])
                if not resp_list:
                    break

                all_records.extend(resp_list)
                needed -= len(resp_list)

                # Set next timestamp to earliest bar timestamp - 1 second
                earliest_epoch = min(int(r["t"]) for r in resp_list)
                current_ts = earliest_epoch - (86400 if timeframe == "1d" else 60)

                success = True
                if len(resp_list) < 50:
                    needed = 0  # reached boundary
                break

            except Exception as e:
                logger.warning("Fetch error (attempt %d): %s", attempt + 1, e)
                time.sleep(1.0)

        if not success:
            break

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    # Deduplicate and format
    df["timestamp"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
    df["open"] = df["o"].astype(float)
    df["high"] = df["h"].astype(float)
    df["low"] = df["l"].astype(float)
    df["close"] = df["c"].astype(float)
    df["volume"] = df["v"].astype(float)

    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", ascending=True, inplace=True)
    df.set_index("timestamp", inplace=True)
    return df


def download_all_historical_data(
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    daily_bars: int = 1000,
    intraday_bars: int = 3000,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Download complete historical datasets for all target symbols and timeframes.
    """
    key = api_key or os.environ.get("INFOWAY_API_KEY", "35c29c9d304b4e8182d0de763f98f5be-infoway")
    targets = [s.upper() for s in (symbols or TARGET_SYMBOLS)]
    tfs = timeframes or ["1d", "1h", "15m", "1m"]

    repo = MarketDataRepository()
    results: Dict[str, Any] = {}

    with httpx.Client(timeout=20.0) as client:
        for sym in targets:
            results[sym] = {}
            for tf in tfs:
                target_bars = daily_bars if tf == "1d" else intraday_bars
                logger.info("Downloading %s [%s] (%d bars requested)...", sym, tf, target_bars)
                try:
                    df = download_candles_http(sym, tf, target_bars, key, client)
                    if df.empty:
                        logger.warning("  %s [%s]: No data received", sym, tf)
                        results[sym][tf] = {"bars": 0, "status": "empty"}
                        continue

                    inserted = repo.upsert_ohlcv(df, sym, tf, source="infoway_http")
                    date_range = f"{df.index.min().strftime('%Y-%m-%d')} -> {df.index.max().strftime('%Y-%m-%d')}"
                    logger.info("  [OK] %s [%s]: %d bars saved (%s)", sym, tf, inserted, date_range)
                    results[sym][tf] = {
                        "bars": inserted,
                        "start": str(df.index.min())[:10],
                        "end": str(df.index.max())[:10],
                        "status": "ok",
                    }
                except Exception as e:
                    logger.error("Error processing %s [%s]: %s", sym, tf, e)
                    results[sym][tf] = {"bars": 0, "error": str(e), "status": "error"}

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk download historical candles using Infoway HTTP API.")
    parser.add_argument("--symbols", nargs="+", default=TARGET_SYMBOLS, help="Symbols to download.")
    parser.add_argument("--timeframes", nargs="+", default=["1d", "1h", "1m"], help="Timeframes to download.")
    parser.add_argument("--daily-bars", type=int, default=1000, help="Daily bars to pull (1000 = 3+ years).")
    parser.add_argument("--intraday-bars", type=int, default=3000, help="Intraday bars to pull.")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Infoway Direct HTTP API Bulk Downloader")
    logger.info("Target Symbols   : %s", args.symbols)
    logger.info("Target Timeframes: %s", args.timeframes)
    logger.info("=" * 60)

    res = download_all_historical_data(
        symbols=args.symbols,
        timeframes=args.timeframes,
        daily_bars=args.daily_bars,
        intraday_bars=args.intraday_bars,
    )

    logger.info("\n" + "=" * 60)
    logger.info("BULK DOWNLOAD COMPLETE — All data saved for future backtesting.")
    logger.info("=" * 60)
    for sym, tf_data in res.items():
        for tf, info in tf_data.items():
            if info.get("status") == "ok":
                logger.info("  %-8s [%-3s]: %5d bars  (%s to %s)", sym, tf, info["bars"], info["start"], info["end"])


if __name__ == "__main__":
    main()
