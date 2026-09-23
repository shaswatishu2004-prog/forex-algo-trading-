"""1-minute data accuracy report for the trailing window.

Prints, per symbol, how complete and how consistent the 1m history is:

- rows and weekday-session coverage inside the window
- which source supplied each window row (user file, HistData, yfinance)
- gaps: holes inside the week (data loss) versus weekend closures (structure)
- duplicate and invalid bar counts from data/processed/manifest.json
- price agreement where two raw sources share a timestamp

Run from the repository root:

    python scripts/data_accuracy.py
    python scripts/data_accuracy.py --window-days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fx_strategy.analysis import cleaning  # noqa: E402
from fx_strategy.analysis.config import load_config  # noqa: E402
from fx_strategy.analysis.vwap import session_labels  # noqa: E402
from fx_strategy.data.database import MarketDataDB  # noqa: E402

SYMBOLS = ["EURUSD", "XAUUSD", "DXY"]
EMPTY_COLUMNS = cleaning.MINUTE_COLUMNS


def _read_processed_window(symbol: str, start: pd.Timestamp) -> pd.DataFrame:
    path = cleaning.PROCESSED_DIR / f"{symbol}_1m.csv"
    if not path.exists():
        raise SystemExit(f"Missing {path}; run scripts/run_analysis.py first.")
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame[frame["timestamp"] >= start].reset_index(drop=True)


def _load_user_window(cfg: dict[str, Any], symbol: str, start: pd.Timestamp) -> pd.DataFrame | None:
    if symbol not in cfg["sources"]["user_files"] or symbol == "DXY":
        return None
    staged = cleaning.RAW_USER_DIR / cfg["sources"]["user_files"][symbol]
    if not staged.exists():
        return None
    offset = int(cfg["sources"].get("user_csv_utc_offset_hours", {}).get(symbol, 0))
    frame, _ = cleaning.read_user_minute_csv(staged, offset_hours=offset)
    return frame[frame["timestamp"] >= start].reset_index(drop=True)


def _load_histdata_window(cfg: dict[str, Any], symbol: str, start: pd.Timestamp) -> pd.DataFrame:
    offsets = cfg["sources"].get("histdata_utc_offset_hours", {})
    valid_from = cfg["sources"].get("histdata_valid_from", {}).get(symbol)
    offset = offsets.get(symbol, 0)
    years = [str(year) for year in range(start.year, pd.Timestamp.now(tz="UTC").year + 1)]
    frames = [
        cleaning.read_histdata_minute_csv(path, offset_hours=offset, valid_from=valid_from)[0]
        for path in cleaning._histdata_files(symbol)
        if any(year in path.name for year in years)
    ]
    if not frames:
        return pd.DataFrame(columns=EMPTY_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset="timestamp", keep="last")
    combined = combined[combined["timestamp"] >= start]
    return combined.reset_index(drop=True)


def _load_db_window(symbol: str, start: pd.Timestamp) -> pd.DataFrame:
    db = MarketDataDB(cleaning.REPO_ROOT / "data" / "forex_market_data.db")
    frame = cleaning._db_frame(db, symbol, "1m")
    frame = frame[frame["timestamp"] >= start]
    return frame.reset_index(drop=True)


def _continuity(window: pd.DataFrame, anchor: int) -> dict[str, Any]:
    """Continuity metrics: session coverage, in-week holes, weekend closures."""
    stamps = window["timestamp"]
    labels = session_labels(stamps, anchor)

    session_count = int(labels.nunique())
    weekdays = pd.date_range(labels.min(), labels.max(), freq="D").to_series()
    weekday_sessions = int((weekdays.dt.weekday < 5).sum())

    # Minutes inside a session's own first..last span that hold no bar.
    span = stamps.groupby(labels).agg(["min", "max", "count"])
    span_minutes = (span["max"] - span["min"]) / pd.Timedelta(minutes=1) + 1
    internal_gap_minutes = int((span_minutes - span["count"]).sum())

    # Consecutive-row gaps. Weekend closures are structure, the rest is loss.
    diff = stamps.diff()
    gap_mask = (diff > pd.Timedelta(minutes=1)).fillna(False).astype(bool)
    positions = [index for index, flag in enumerate(gap_mask) if flag]

    def zeroed() -> dict[str, Any]:
        return {
            "sessions": session_count,
            "weekday_sessions": weekday_sessions,
            "internal_gap_minutes": internal_gap_minutes,
            "hole_gaps": 0,
            "hole_minutes": 0,
            "longest_hole_minutes": 0,
            "longest_hole_start": "",
            "top_holes": [],
            "weekend_closures": 0,
            "long_closures": [],
        }

    if not positions:
        return zeroed()

    gaps = pd.DataFrame(
        {
            "start": [stamps.iloc[index - 1] for index in positions],
            "end": [stamps.iloc[index] for index in positions],
            "minutes": [
                int(diff.iloc[index] / pd.Timedelta(minutes=1)) - 1 for index in positions
            ],
        }
    )

    def is_structure(row: pd.Series) -> bool:
        weekday = row["start"].weekday()
        return weekday >= 5 or (weekday == 4 and row["start"].hour >= 20)

    structure = gaps.apply(is_structure, axis=1)
    holes = gaps[~structure].sort_values("minutes", ascending=False)
    closures = gaps[structure]
    longest = holes.iloc[0] if not holes.empty else None
    top = [
        f"{row['start']:%Y-%m-%d %H:%M} -> {row['end']:%H:%M} ({int(row['minutes'])} min)"
        for _, row in holes.head(5).iterrows()
    ]
    long_closures = [
        f"{row['start']:%Y-%m-%d %H:%M} ({int(row['minutes']) // 60} h)"
        for _, row in closures[closures["minutes"] > 60 * 60].iterrows()
    ]
    return {
        "sessions": session_count,
        "weekday_sessions": weekday_sessions,
        "internal_gap_minutes": internal_gap_minutes,
        "hole_gaps": int(len(holes)),
        "hole_minutes": int(holes["minutes"].sum()) if not holes.empty else 0,
        "longest_hole_minutes": int(longest["minutes"]) if longest is not None else 0,
        "longest_hole_start": f"{longest['start']:%Y-%m-%d %H:%M}" if longest is not None else "",
        "top_holes": top,
        "weekend_closures": int(len(closures)),
        "long_closures": long_closures,
    }


def _composition(
    user: pd.DataFrame | None, hist: pd.DataFrame, db: pd.DataFrame
) -> dict[str, int]:
    """Which source wins each window timestamp under merge precedence."""
    user_stamps = (
        user["timestamp"] if user is not None else pd.Series(dtype="datetime64[ns, UTC]")
    )
    hist_stamps = hist["timestamp"] if not hist.empty else pd.Series(dtype="datetime64[ns, UTC]")
    db_stamps = db["timestamp"] if not db.empty else pd.Series(dtype="datetime64[ns, UTC]")
    hist_unique = hist_stamps[~hist_stamps.isin(user_stamps)]
    db_unique = db_stamps[~db_stamps.isin(user_stamps) & ~db_stamps.isin(hist_stamps)]
    return {
        "user_csv": int(len(user_stamps)),
        "histdata": int(len(hist_unique)),
        "yfinance_db": int(len(db_unique)),
    }


def _overlap_line(a: pd.DataFrame, b: pd.DataFrame, a_name: str, b_name: str) -> str | None:
    if a.empty or b.empty:
        return None
    joined = a.merge(b, on="timestamp", suffixes=("_a", "_b"))
    if joined.empty:
        return f"overlap {a_name} vs {b_name}: no shared timestamps"
    diff = (joined["close_a"] - joined["close_b"]).abs()
    exact = float((diff == 0).mean()) * 100
    signed = (joined["close_a"] - joined["close_b"]).median()
    return (
        f"overlap {a_name} vs {b_name}: n={len(joined):,} exact={exact:.1f}% "
        f"median|dC|={diff.median():.6g} p99={diff.quantile(0.99):.6g} "
        f"max={diff.max():.6g} median signed={signed:+.6g}"
    )


def _manifest_drops(manifest: dict[str, Any], symbol: str) -> str:
    totals: dict[str, int] = {}
    for source in manifest["symbols"].get(symbol, {}).get("sources", {}).values():
        for reason, count in source.get("dropped", {}).items():
            totals[reason] = totals.get(reason, 0) + int(count)
    nonzero = {key: value for key, value in totals.items() if value}
    return json.dumps(nonzero) if nonzero else "none"


def main() -> None:
    parser = argparse.ArgumentParser(description="1m data accuracy report")
    parser.add_argument("--config", default=None)
    parser.add_argument("--window-days", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    window_days = args.window_days or int(cfg["refresh"]["window_days"])
    anchor = int(cfg["vwap"]["anchor_hour_utc"])
    start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=window_days)
    manifest_path = cleaning.PROCESSED_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"1m accuracy, trailing {window_days} days from {start:%Y-%m-%d} UTC, anchor {anchor}:00 UTC")
    print(f"generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} from data/processed/")

    for symbol in SYMBOLS:
        window = _read_processed_window(symbol, start)
        user = _load_user_window(cfg, symbol, start)
        hist = _load_histdata_window(cfg, symbol, start)
        db = _load_db_window(symbol, start)
        stats = _continuity(window, anchor)
        comp = _composition(user, hist, db)
        comp_sum = sum(comp.values())
        processed_rows = len(window)

        print(f"\n{symbol}")
        print(f"  rows in window        {processed_rows:,}")
        print(
            f"  sessions              {stats['sessions']} present of "
            f"{stats['weekday_sessions']} weekday sessions in span"
        )
        print(
            "  composition           "
            + " | ".join(f"{key} {value:,}" for key, value in comp.items())
            + (
                ""
                if comp_sum == processed_rows
                else f"  (sum {comp_sum:,} != rows {processed_rows:,})"
            )
        )
        print(f"  in-session empty min  {stats['internal_gap_minutes']:,}")
        print(
            f"  week holes            {stats['hole_gaps']} gaps, "
            f"{stats['hole_minutes']:,} min total, longest "
            f"{stats['longest_hole_minutes']} min at {stats['longest_hole_start']}"
        )
        for hole in stats["top_holes"]:
            print(f"      {hole}")
        print(
            f"  weekend closures      {stats['weekend_closures']}"
            + (
                f", long closures: {'; '.join(stats['long_closures'])}"
                if stats["long_closures"]
                else ""
            )
        )
        print(f"  drops (manifest)      {_manifest_drops(manifest, symbol)}")

        line = _overlap_line(user if user is not None else pd.DataFrame(columns=EMPTY_COLUMNS), hist, "user", "histdata")
        if line:
            print(f"  {line}")
        line = _overlap_line(hist, db, "histdata", "yfinance")
        if line:
            print(f"  {line}")


if __name__ == "__main__":
    main()
