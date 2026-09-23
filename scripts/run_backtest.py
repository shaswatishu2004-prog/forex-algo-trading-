"""Run the trailing one-year backtest over one-minute history.

Run from the repository root:

    python scripts/run_backtest.py           # one pass -> data/backtest/results.json
    python scripts/run_backtest.py --loop    # re-run the same fixed window continuously

Decision: docs/wayfinder/tickets/backtest-design.md.

The window is exactly ``backtest.window_days`` (365) of one-minute bars ending
at the last bar on disk, so a run does not drift with the wall clock. Each
data/processed CSV is opened at a binary-searched byte offset: timestamps sort
the same way as their bytes, so the search skips the hundreds of megabytes of
history ahead of the window instead of parsing it.

``--loop`` re-runs the identical fixed window with no state carried between
passes. The dashboard animates that cycle; see
scripts/generate_backtest_page.py.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fx_strategy.analysis.cleaning import PROCESSED_DIR  # noqa: E402
from fx_strategy.analysis.config import load_config  # noqa: E402
from fx_strategy.backtest.engine import (  # noqa: E402
    assign_folds,
    compute_metrics,
    prepare_symbol,
    sample_points,
    serialize_trades,
    sensitivity,
    simulate,
    walk_forward_report,
)

logger = logging.getLogger("run_backtest")

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
DEFAULT_SYMBOLS = ["EURUSD", "XAUUSD", "DXY"]
RESULTS_PATH = Path(__file__).resolve().parents[1] / "data" / "backtest" / "results.json"


def sanitize(value: Any) -> Any:
    """Recursively make numpy and pandas scalars JSON serialisable."""
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, (bool, str)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, int):
        return value
    return str(value)


# ---------------------------------------------------------------- reading

def last_timestamp(path: Path) -> pd.Timestamp:
    """Timestamp of the final data row, read from the tail of the file only."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 8192))
        lines = [line for line in handle.read().split(b"\n") if line.strip()]
    if not lines:
        raise ValueError(f"{path} holds no rows")
    index = -1
    if lines[-1].split(b",", 1)[0] == b"timestamp":
        index = -2
    if abs(index) > len(lines):
        raise ValueError(f"{path} holds only a header")
    return pd.Timestamp(lines[index].split(b",", 1)[0].decode("ascii"))


def _first_offset_at_or_after(handle: Any, cutoff: bytes) -> int:
    """Byte offset of the first line whose timestamp reaches ``cutoff``.

    The predicate "the first line starting at or after p reaches cutoff" is
    monotone in p, so a plain binary search lands one byte past the previous
    line's start; the trailing alignment step walks to the real line start.
    """
    handle.seek(0, 2)
    size = handle.tell()
    lo, hi = 0, size
    while lo < hi:
        mid = (lo + hi) // 2
        handle.seek(mid)
        if mid > 0:
            handle.readline()  # discard the partial line under the probe
        raw = handle.readline()
        if not raw:
            hi = mid
            continue
        if raw.split(b",", 1)[0] >= cutoff:
            hi = mid
        else:
            following = handle.tell()
            lo = following if following > lo else mid + 1
    handle.seek(lo)
    if lo > 0:
        handle.seek(lo - 1)
        if handle.read(1) != b"\n":
            handle.readline()
    return handle.tell()


def read_minute_window(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """One-minute rows in [start, end], read from the tail of ``path``."""
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    cutoff = start.strftime("%Y-%m-%d %H:%M:%S").encode("ascii") + b"+00:00"
    with path.open("rb") as handle:
        handle.seek(_first_offset_at_or_after(handle, cutoff))
        payload = handle.read()
    if not payload.strip():
        return pd.DataFrame(columns=COLUMNS)

    frame = pd.read_csv(io.BytesIO(payload), header=None, names=COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    frame = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= end)]

    if not frame.empty and not frame["timestamp"].is_monotonic_increasing:
        # The offset search assumes ascending rows, so re-read from the top.
        logger.warning("%s tail is not ascending, falling back to a full read", path.name)
        frame = pd.read_csv(path)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
        frame = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= end)]

    frame = frame.reset_index(drop=True)
    for column in COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def read_daily(path: Path) -> pd.DataFrame:
    """Full daily frame; regime needs the whole trailing window to warm up."""
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    return frame.sort_values("timestamp").reset_index(drop=True)


# ---------------------------------------------------------------- building

def load_window(
    cfg: dict[str, Any],
    symbols: list[str],
    window_days: int,
    warmup_days: int,
) -> tuple[dict[str, pd.DataFrame], pd.Timestamp, pd.Timestamp, dict[str, int]]:
    """Prepared frames clipped to the window, plus its bounds and bar counts."""
    ends: dict[str, pd.Timestamp] = {}
    for symbol in symbols:
        path = PROCESSED_DIR / f"{symbol}_1m.csv"
        if path.exists():
            ends[symbol] = last_timestamp(path)
    if not ends:
        raise SystemExit(f"no {PROCESSED_DIR}/*_1m.csv files to run against")

    window_end = max(ends.values())
    window_start = window_end - pd.Timedelta(days=window_days)
    read_start = window_start - pd.Timedelta(days=warmup_days)
    logger.info(
        "window %s to %s (%d days), reading from %s with %d warm-up days",
        window_start.date(),
        window_end.date(),
        window_days,
        read_start.date(),
        warmup_days,
    )

    prepared: dict[str, pd.DataFrame] = {}
    counts: dict[str, int] = {}
    for symbol in symbols:
        minute_path = PROCESSED_DIR / f"{symbol}_1m.csv"
        minute = read_minute_window(minute_path, read_start, window_end)
        if minute.empty:
            logger.warning("%s: no 1m bars inside the window, skipping", symbol)
            continue
        daily = read_daily(PROCESSED_DIR / f"{symbol}_1d.csv")
        full = prepare_symbol(symbol, minute, daily, cfg)
        window = full[full["timestamp"] >= window_start].reset_index(drop=True)
        if window.empty:
            logger.warning("%s: nothing left after clipping to the window", symbol)
            continue
        counts[symbol] = len(window)
        prepared[symbol] = window
        logger.info("%s: %d 1m bars prepared (%d warm-up dropped)", symbol, len(window), len(full) - len(window))
    if not prepared:
        raise SystemExit("every symbol came back empty")
    return prepared, window_start, window_end, counts


def run_once(
    cfg: dict[str, Any],
    symbols: list[str],
    window_days: int,
    warmup_days: int,
) -> dict[str, Any]:
    """One full pass: simulate, report, stress, and assemble the result."""
    backtest_cfg = cfg.get("backtest", {})
    initial = float(backtest_cfg.get("initial_equity", 100_000))
    blocks = int(backtest_cfg.get("fold_blocks", 6))
    entry_delay = int(backtest_cfg.get("entry_delay_bars", 1))
    delayed_delay = int(backtest_cfg.get("delayed_entry_delay_bars", 5))

    prepared, window_start, window_end, counts = load_window(cfg, symbols, window_days, warmup_days)

    started = time.perf_counter()
    trades, points, diagnostics = simulate(prepared, cfg, initial, entry_delay_bars=entry_delay)
    folds = assign_folds(trades, window_start, window_end, blocks)
    metrics = compute_metrics(trades, points, initial, window_start, window_end)
    walk_forward = walk_forward_report(trades, folds, initial)
    costs = sensitivity(trades, initial, cfg, metrics)

    delayed_trades, delayed_points, _ = simulate(
        prepared, cfg, initial, entry_delay_bars=delayed_delay
    )
    delayed_metrics = compute_metrics(
        delayed_trades, delayed_points, initial, window_start, window_end
    )
    elapsed = time.perf_counter() - started

    diagnostics["per_symbol_bars"] = counts
    diagnostics["elapsed_seconds"] = round(elapsed, 2)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": window_days,
            "source": "trailing one-minute bars in data/processed",
        },
        "symbols": sorted(prepared),
        "initial_equity": initial,
        "entry_delay_bars": entry_delay,
        "metrics": metrics,
        "walk_forward": walk_forward,
        "sensitivity": costs,
        "delayed_execution": {
            "entry_delay_bars": delayed_delay,
            "trades": delayed_metrics["trades"],
            "net_return_pct": delayed_metrics["net_return_pct"],
            "hit_rate_pct": delayed_metrics["hit_rate_pct"],
            "profit_factor": delayed_metrics["profit_factor"],
            "max_drawdown_pct": delayed_metrics["max_drawdown_pct"],
        },
        "diagnostics": diagnostics,
        "equity_curve": sample_points(points, 1500),
        "trades": serialize_trades(trades, 5000),
        "trade_count_total": len(trades),
        "delayed_trade_count": len(delayed_trades),
        "delayed_equity_curve": sample_points(delayed_points, 600),
    }


def write_results(results: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(results), indent=2), encoding="utf-8")
    logger.info("wrote %s", path)


def _recovery(days: Any) -> str:
    """Render drawdown recovery as 'never' rather than a misleading zero."""
    return "never" if days is None else f"{days} days"


def print_summary(results: dict[str, Any]) -> None:
    metrics = results["metrics"]
    window = results["window"]
    print("\n" + "=" * 70)
    print("BACKTEST SUMMARY")
    print("=" * 70)
    print(f"  Window      {window['start'][:10]} to {window['end'][:10]} ({window['days']} days)")
    print(f"  Symbols     {', '.join(results['symbols'])}")
    print(f"  Trades      {metrics['trades']:,}")
    print(f"  Gross       {metrics['gross_return_pct']:+.3f}%")
    costs = metrics["costs"]
    print(
        "  Costs       spread {spread_pct:+.3f}%  commission {commission_pct:+.3f}%  "
        "slippage {slippage_pct:+.3f}%  financing {financing_pct:+.3f}%  "
        "total {total_pct:+.3f}%".format(**costs)
    )
    print(f"  Net         {metrics['net_return_pct']:+.3f}%")
    print(f"  Hit rate    {metrics['hit_rate_pct']:.1f}%   profit factor {metrics['profit_factor']:.2f}")
    print(f"  Sharpe      {metrics['sharpe']:.2f}   Sortino {metrics['sortino']:.2f}")
    print(f"  Max DD      {metrics['max_drawdown_pct']:.2f}%   recovery {_recovery(metrics['recovery_days'])}")
    print(f"  Worst day   {metrics['worst_day_pct']:+.2f}%   worst month {metrics['worst_month_pct']:+.2f}%")
    locked = results["walk_forward"]["locked_final_test"]
    print(f"  Locked test {locked['trades']:,} trades, {locked['net_pct']:+.3f}% net")
    print(f"  Stress 2x   {results['sensitivity']['stress_costs']['net_pct']:+.3f}% net")
    print(f"  Elapsed     {results['diagnostics']['elapsed_seconds']}s")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the trailing one-year backtest over one-minute history."
    )
    parser.add_argument("--config", default=None, help="Path to config file (default: config/default.yaml)")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols")
    parser.add_argument("--window-days", type=int, default=None, help="Window length (default: config backtest.window_days)")
    parser.add_argument("--out", default=str(RESULTS_PATH), help="Results JSON path")
    parser.add_argument("--loop", action="store_true", help="Re-run the same fixed window continuously")
    parser.add_argument("--interval", type=float, default=0.0, help="Seconds to sleep between loop passes")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)
    backtest_cfg = cfg.get("backtest", {})
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    window_days = args.window_days if args.window_days is not None else int(backtest_cfg.get("window_days", 365))
    warmup_days = int(backtest_cfg.get("warmup_days", 2))
    out_path = Path(args.out)

    iteration = 0
    while True:
        iteration += 1
        if args.loop:
            logger.info("loop pass %d", iteration)
        results = run_once(cfg, symbols, window_days, warmup_days)
        results["loop_iteration"] = iteration
        write_results(results, out_path)
        print_summary(results)
        if not args.loop:
            break
        if args.interval > 0:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
