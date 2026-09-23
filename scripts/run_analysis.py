"""Batch analysis engine: clean data, compute VWAP bands, label regime, emit signals.

Run from the repository root:

    python scripts/run_analysis.py                # full pass over all symbols
    python scripts/run_analysis.py --symbols DXY  # one symbol
    python scripts/run_analysis.py --skip-clean   # reuse data/processed

The engine is a batch command (decision recorded in
docs/wayfinder/tickets/engine-folder-layout.md). It reads data/processed,
writes signals/{SYMBOL}_{date}.json, and prints a summary. The weekly refresh
wraps this same path, see scripts/weekly_refresh.py.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fx_strategy.analysis.cleaning import PROCESSED_DIR, run_cleaning  # noqa: E402
from fx_strategy.analysis.config import load_config  # noqa: E402
from fx_strategy.analysis.regime import compute_regime  # noqa: E402
from fx_strategy.analysis.signals import (  # noqa: E402
    build_symbol_signals,
    prune_signal_files,
    write_symbol_signals,
)
from fx_strategy.analysis.vwap import compute_vwap_bands  # noqa: E402

logger = logging.getLogger("run_analysis")


def _read_processed(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def run_engine(cfg: dict[str, Any], symbols: list[str], window_days: int | None) -> dict[str, Any]:
    """Compute bands and regime for each symbol and write signals JSON."""
    anchor = int(cfg["vwap"]["anchor_hour_utc"])
    volume_min_unique = int(cfg["vwap"].get("volume_min_unique_per_session", 2))
    cutoff = None
    if window_days:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=window_days)

    summary: dict[str, Any] = {}
    for symbol in symbols:
        minute = _read_processed(PROCESSED_DIR / f"{symbol}_1m.csv")
        daily = _read_processed(PROCESSED_DIR / f"{symbol}_1d.csv")
        if cutoff is not None and not minute.empty:
            minute = minute[minute["timestamp"] >= cutoff].reset_index(drop=True)
        if minute.empty:
            logger.warning("%s: no 1m bars in data/processed, skipping", symbol)
            continue

        banded = compute_vwap_bands(
            minute, anchor_hour_utc=anchor, volume_min_unique=volume_min_unique
        )
        regime = compute_regime(daily, cfg) if not daily.empty else pd.DataFrame()

        document = build_symbol_signals(symbol, banded, regime, cfg)
        target = write_symbol_signals(document, cfg)
        labeled_regime = 0 if regime.empty else int(regime["regime"].notna().sum())
        summary[symbol] = {
            "minute_bars": len(minute),
            "daily_bars": len(daily),
            "labeled_regime_bars": labeled_regime,
            "records": document["record_counts"]["total"],
            "signals_file": str(target),
        }
        logger.info(
            "%s: %d minute bars, %d labeled daily bars, %d records -> %s",
            symbol,
            len(minute),
            labeled_regime,
            document["record_counts"]["total"],
            target.name,
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean historical data, compute VWAP bands and daily regime, emit signals JSON."
    )
    parser.add_argument("--config", default=None, help="Path to config file (default: config/default.yaml)")
    parser.add_argument("--symbols", default="EURUSD,XAUUSD,DXY", help="Comma-separated symbols")
    parser.add_argument("--skip-clean", action="store_true", help="Reuse existing data/processed files")
    parser.add_argument("--window-days", type=int, default=None, help="Limit 1m input to the last N days (default: config refresh.window_days, 0 = all)")
    parser.add_argument("--prune-signals", action="store_true", help="Delete signal files older than signals.archive_days")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if not args.skip_clean:
        logger.info("Cleaning raw data into %s ...", PROCESSED_DIR)
        run_cleaning(cfg)

    window = args.window_days
    if window is None:
        window = int(cfg.get("refresh", {}).get("window_days", 365))
    if window < 0:
        window = 0

    summary = run_engine(cfg, symbols, window_days=window)

    if args.prune_signals:
        removed = prune_signal_files(cfg)
        if removed:
            logger.info("Pruned %d signal files older than archive window", len(removed))

    print("\n" + "=" * 70)
    print("ANALYSIS ENGINE SUMMARY")
    print("=" * 70)
    if not summary:
        print("  No symbols processed.")
    for symbol, stats in summary.items():
        print(f"  {symbol}: {stats['minute_bars']:,} 1m bars, "
              f"{stats['labeled_regime_bars']:,}/{stats['daily_bars']:,} regime labels, "
              f"{stats['records']:,} records")
        print(f"    -> {stats['signals_file']}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
