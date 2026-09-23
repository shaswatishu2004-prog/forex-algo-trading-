"""Weekly 1m data refresh, year-round: fetch, store, clean, re-run the engine.

The loop runs every week for 365 days without intervention:

- Local long-run: ``python scripts/weekly_refresh.py --loop`` keeps the
  schedule for refresh.loop_days (default 365), sleeping between weekly runs.
- Scheduled: .github/workflows/weekly-refresh.yml fires the same script once a
  week via cron for the life of the repository; ``--loop`` is not needed there.
- Each cycle fetches the last ``refresh.fetch_days_1m`` days of 1m bars (yfinance
  serves at most 29 days, 8 leaves headroom), upserts into SQLite, re-cleans
  into data/processed, re-runs the analysis engine over the trailing
  ``refresh.window_days`` (365) of 1m data, and prunes signal files past
  ``signals.archive_days``.

One failed cycle logs and exits non-zero so the scheduler surfaces it; the next
scheduled run starts fresh from the database, which holds prior weeks.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fx_strategy.analysis.cleaning import run_cleaning  # noqa: E402
from fx_strategy.analysis.config import load_config  # noqa: E402
from fx_strategy.data.database import MarketDataDB  # noqa: E402
from fx_strategy.data.fetcher import ForexDataFetcher  # noqa: E402

logger = logging.getLogger("weekly_refresh")

SYMBOLS = ["EURUSD", "XAUUSD", "DXY"]


def fetch_latest(cfg: dict[str, Any], db: MarketDataDB) -> dict[str, int]:
    """Fetch recent 1m bars and full daily bars for every symbol into SQLite."""
    fetcher = ForexDataFetcher()
    days_back = int(cfg.get("refresh", {}).get("fetch_days_1m", 8))
    counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        for timeframe in ("1m", "1d"):
            try:
                count = fetcher.fetch_and_store(
                    symbol,
                    timeframe,
                    db,
                    period_daily="max",
                    days_back_1m=days_back,
                )
                counts[f"{symbol}_{timeframe}"] = count
            except Exception as exc:  # noqa: BLE001
                logger.error("Fetch failed for %s [%s]: %s", symbol, timeframe, exc)
                counts[f"{symbol}_{timeframe}"] = -1
    return counts


def run_cycle(
    cfg: dict[str, Any],
    db_path: str | Path,
    engine_args: list[str] | None = None,
    config_path: str | Path | None = None,
) -> int:
    """One weekly cycle: fetch, clean, analyze. Returns a process exit code."""
    db = MarketDataDB(db_path)
    counts = fetch_latest(cfg, db)
    failures = [key for key, value in counts.items() if value < 0]
    logger.info("Fetch counts: %s", counts)

    run_cleaning(cfg, db_path=db_path)

    # Reuse the engine CLI so both entry points stay identical.
    from run_analysis import main as engine_main  # noqa: PLC0415

    original_argv = sys.argv
    try:
        sys.argv = ["run_analysis.py", "--skip-clean", "--prune-signals", *(engine_args or [])]
        engine_main()
    finally:
        sys.argv = original_argv

    # Refresh the static backtest page from the same window. A page failure
    # must not fail the data cycle: the signals and CSVs are already written.
    try:
        from generate_prototype_page import main as prototype_main  # noqa: PLC0415

        prototype_args = ["--config", str(config_path)] if config_path else []
        prototype_main(prototype_args)
        logger.info("Prototype page regenerated: dashboard/prototype.html")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Prototype page regeneration failed (data cycle unaffected): %s", exc)

    # A daily-only failure still leaves usable data in SQLite; only a fully
    # failed fetch cycle is fatal.
    all_failed = all(value < 0 for value in counts.values())
    if all_failed or (len(failures) == len(counts)):
        logger.error("All fetches failed: %s", failures)
        return 1
    if failures:
        logger.warning("Partial fetch failure: %s (database keeps prior weeks)", failures)
    return 0


def seconds_until_next_week(reference: datetime | None = None) -> float:
    """Seconds from now until the next Monday 06:00 UTC (the workflow cron time)."""
    now = reference or datetime.now(timezone.utc)
    days_ahead = (0 - now.weekday()) % 7  # Monday is 0
    target_date = now.date() + timedelta(days=days_ahead)
    target = datetime(target_date.year, target_date.month, target_date.day, 6, 0, tzinfo=timezone.utc)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly 1m refresh loop (default window: 365 days).")
    parser.add_argument("--config", default=None)
    parser.add_argument("--db-path", default="data/forex_market_data.db")
    parser.add_argument("--loop", action="store_true",
                        help="Run weekly cycles for refresh.loop_days instead of one cycle")
    parser.add_argument("--once", action="store_true", help="Run a single cycle (default)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config(args.config)

    if not args.loop:
        sys.exit(run_cycle(cfg, args.db_path, config_path=args.config))

    loop_days = int(cfg.get("refresh", {}).get("loop_days", 365))
    stop_at = datetime.now(timezone.utc) + timedelta(days=loop_days)
    logger.info("Starting weekly loop for %d days (until %s)", loop_days, stop_at.date())
    cycle = 0
    while datetime.now(timezone.utc) < stop_at:
        cycle += 1
        logger.info("Cycle %d starting", cycle)
        try:
            code = run_cycle(cfg, args.db_path, config_path=args.config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Cycle %d crashed: %s", cycle, exc)
            code = 1
        if datetime.now(timezone.utc) >= stop_at:
            break
        wait = seconds_until_next_week()
        # Never sleep past the loop horizon.
        wait = min(wait, (stop_at - datetime.now(timezone.utc)).total_seconds())
        logger.info("Cycle %d done (exit %d), sleeping %.0f h until next week", cycle, code, wait / 3600)
        if wait <= 0:
            break
        time.sleep(wait)
    logger.info("Weekly loop finished after %d cycles", cycle)


if __name__ == "__main__":
    main()
