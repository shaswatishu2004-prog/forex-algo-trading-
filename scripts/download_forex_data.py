"""CLI script to download historical OHLCV data for Forex, Gold, and DXY and store in SQLite."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add src to sys.path so it works without installing package
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fx_strategy.data.database import MarketDataDB
from fx_strategy.data.fetcher import ForexDataFetcher


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def print_summary(db: MarketDataDB) -> None:
    summary_df = db.get_summary()
    print("\n" + "=" * 90)
    print("                    LOCAL MARKET DATA DATABASE SUMMARY")
    print(f" Database Path: {db.db_path.resolve()}")
    print("=" * 90)
    if summary_df.empty:
        print("  Database is currently empty. Run with --download to fetch data.")
    else:
        print(summary_df.to_string(index=False))
    print("=" * 90 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and store historical OHLCV data for EURUSD, XAUUSD, and DXY in local SQLite."
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="EURUSD,XAUUSD,DXY",
        help="Comma-separated list of symbols (default: EURUSD,XAUUSD,DXY)",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default="1m,1d",
        help="Comma-separated list of timeframes (default: 1m,1d)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/forex_market_data.db",
        help="Path to SQLite database file (default: data/forex_market_data.db)",
    )
    parser.add_argument(
        "--days-back-1m",
        type=int,
        default=28,
        help="Number of days of 1-minute historical data to retrieve (max 29, default 28)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Display summary table of existing stored data and exit.",
    )
    parser.add_argument(
        "--query",
        nargs=2,
        metavar=("SYMBOL", "TIMEFRAME"),
        help="Query and print latest 10 bars for a symbol and timeframe (e.g. --query EURUSD 1m)",
    )
    parser.add_argument(
        "--export-csv",
        nargs=3,
        metavar=("SYMBOL", "TIMEFRAME", "OUTPUT_PATH"),
        help="Export data for a symbol and timeframe to CSV (e.g. --export-csv EURUSD 1d data/eurusd_1d.csv)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed debug logs",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    db = MarketDataDB(args.db_path)

    if args.summary:
        print_summary(db)
        return

    if args.query:
        sym, tf = args.query[0].upper(), args.query[1].lower()
        df = db.get_ohlcv(sym, tf)
        if df.empty:
            print(f"No records found for {sym} [{tf}] in {db.db_path}")
        else:
            print(f"\n--- Latest 10 bars for {sym} [{tf}] (Total: {len(df)} bars) ---")
            print(df.tail(10))
            print("-----------------------------------------------------------------\n")
        return

    if args.export_csv:
        sym, tf, out_file = args.export_csv[0].upper(), args.export_csv[1].lower(), args.export_csv[2]
        saved_path = db.export_csv(sym, tf, out_file)
        print(f"Exported {sym} [{tf}] to {saved_path.resolve()}")
        return

    # Default action: Download and ingest data
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip().lower() for t in args.timeframes.split(",") if t.strip()]

    print(f"\nStarting historical data download for: {symbols}")
    print(f"Timeframes: {timeframes}")
    print(f"Database destination: {Path(args.db_path).resolve()}\n")

    fetcher = ForexDataFetcher()
    for sym in symbols:
        for tf in timeframes:
            print(f"-> Fetching {sym} [{tf}]...")
            try:
                count = fetcher.fetch_and_store(
                    sym, tf, db, period_daily="max", days_back_1m=args.days_back_1m
                )
                print(f"   Saved {count:,} bars for {sym} [{tf}].")
            except Exception as e:
                print(f"   [ERROR] Failed to download {sym} [{tf}]: {e}")

    print_summary(db)
    print("Download and ingestion complete! Local database is ready.\n")


if __name__ == "__main__":
    main()
