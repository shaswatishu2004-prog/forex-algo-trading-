"""
CLI script to ingest historical OHLCV data using the Infoway API.

Usage:
  # Ingest 1m bars for all symbols
  python scripts/ingest_history.py

  # Specific symbols, custom bar count
  python scripts/ingest_history.py --symbols XAUUSD EURUSD --bars 1000

  # Daily bars
  python scripts/ingest_history.py --timeframe 1d
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_layer.ingestion.pipeline import IngestionPipeline


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest historical FX/Commodity bars using Infoway API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbols", nargs="+",
        default=["XAUUSD", "EURUSD", "USDJPY", "DXY"],
        help="Symbols to ingest.",
    )
    parser.add_argument(
        "--timeframe", choices=["1m", "5m", "15m", "1h", "1d"], default="1m",
        help="Timeframe to fetch.",
    )
    parser.add_argument(
        "--bars", type=int, default=1000,
        help="Target number of bars per symbol to fetch.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch data but do NOT write to the database.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logs.")

    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("ingest_history")

    logger.info("=" * 60)
    logger.info("Forex Data Ingestion — Infoway API")
    logger.info("Symbols   : %s", args.symbols)
    logger.info("Timeframe : %s", args.timeframe)
    logger.info("Bars/sym  : %d", args.bars)
    logger.info("Dry run   : %s", args.dry_run)
    logger.info("=" * 60)

    pipeline = IngestionPipeline()
    results = pipeline.run(
        symbols=args.symbols,
        timeframe=args.timeframe,
        bars_per_symbol=args.bars,
        dry_run=args.dry_run,
    )

    logger.info("\n--- Ingestion Summary ---")
    for sym, stats in results.items():
        logger.info(
            "  %-8s  fetched=%6d  inserted=%6d",
            sym, stats["fetched"], stats["inserted"],
        )
    logger.info("Done.")


if __name__ == "__main__":
    main()
