"""Clean and format raw historical data into canonical CSVs under data/processed.

Cleaning rules (decision: docs/wayfinder/tickets/cleaning-rules.md):

- Timestamps normalize to timezone-aware UTC. Naive user-export times get the
  per-symbol offset from config ``sources.user_csv_utc_offset_hours`` first.
  HistData stamps are New York wall clock, so
  ``sources.histdata_utc_offset_hours`` takes an integer (fixed hours added to
  the file clock) or the sentinel ``us_eastern_dst`` (+4h summer / +5h winter,
  US DST dates before 2019 and EU DST dates from 2019 on — see
  ``_dst_offset_hours``; evidence in data/raw/histdata/*/FETCH_REPORT.md and
  scripts/dst_probe.py).
- A source can declare ``histdata_valid_from``: earlier rows drop as
  outside_valid_range. The UDXUSD feed ships a mislabeled Dow Jones series
  before 2018-12-16, which must never reach the dollar-index outputs.
- Rows sort ascending; duplicate timestamps keep the last occurrence.
- Invalid bars drop: high below low, high below max(open, close), low above
  min(open, close), non-positive prices, or missing OHLC.
- Volume fills missing or negative values with 0. Zero and constant volume are
  kept as-is; the VWAP layer decides whether a session's volume is usable.
- Missing minute bars stay missing. Weekends and thin sessions are structure,
  not data loss, so nothing is forward-filled.
- Daily bars aggregate from cleaned 1m bars at the 21:00 UTC session boundary,
  labeled by the session date. Native daily sources (investing.com, yfinance)
  fill dates the 1m history does not cover; the user's file wins its dates.
- Outlier capping is deliberately absent: only structurally invalid bars drop.
- Output: data/processed/{SYMBOL}_1m.csv, {SYMBOL}_1d.csv, manifest.json.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fx_strategy.data.database import MarketDataDB

logger = logging.getLogger(__name__)

MINUTE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
REPO_ROOT = Path(__file__).resolve().parents[3]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
RAW_USER_DIR = REPO_ROOT / "data" / "raw" / "user"
HISTDATA_DIR = REPO_ROOT / "data" / "raw" / "histdata"
DST_OFFSET_MODE = "us_eastern_dst"
_DST_CROSSOVER_YEAR = 2019  # stamps before: US DST dates; from: EU DST dates

_DROP_REASONS = [
    "duplicate_timestamp",
    "missing_ohlc",
    "invalid_range",
    "non_positive_price",
    "outside_valid_range",
]


def _empty_drops() -> dict[str, int]:
    return {reason: 0 for reason in _DROP_REASONS}


def _finalize_minute_frame(frame: pd.DataFrame, source: str) -> tuple[pd.DataFrame, dict[str, int]]:
    """Sort, dedupe, and reject structurally invalid minute bars."""
    drops = _empty_drops()
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)

    missing = data[["open", "high", "low", "close"]].isna().any(axis=1)
    drops["missing_ohlc"] = int(missing.sum())
    data = data[~missing]

    # Non-positive prices report under their own reason before the range rules
    # see them: a bar with close -1.10 also fails low <= min(open, close), and
    # "non_positive_price" is the more precise diagnosis.
    non_positive = data[["open", "high", "low", "close"]].min(axis=1) <= 0
    drops["non_positive_price"] = int(non_positive.sum())
    data = data[~non_positive]

    invalid = (
        (data["high"] < data["low"])
        | (data["high"] < data[["open", "close"]].max(axis=1))
        | (data["low"] > data[["open", "close"]].min(axis=1))
    )
    drops["invalid_range"] = int(invalid.sum())
    data = data[~invalid]

    # Validate before dedupe so a structurally invalid row can never win a
    # duplicate-timestamp contest against a valid row for the same stamp.
    duplicated = data.duplicated(subset="timestamp", keep="last")
    drops["duplicate_timestamp"] = int(duplicated.sum())
    data = data[~duplicated]

    data["volume"] = data["volume"].fillna(0).clip(lower=0)
    data = data.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if not data.empty:
        logger.info("Cleaned %s: %d bars kept, dropped %s", source, len(data), drops)
    return data[MINUTE_COLUMNS], drops


def read_user_minute_csv(path: str | Path, offset_hours: int = 0) -> tuple[pd.DataFrame, dict[str, int]]:
    """Read a tab-separated broker export with no header: time o h l c v."""
    raw = pd.read_csv(path, sep="\t", header=None, names=MINUTE_COLUMNS)
    naive = pd.to_datetime(raw["timestamp"]) + pd.Timedelta(hours=offset_hours)
    raw["timestamp"] = naive.dt.tz_localize("UTC")
    return _finalize_minute_frame(raw, source=str(path))


def _nth_sunday(year: int, month: int, nth: int) -> pd.Timestamp:
    """Nth Sunday of the month as a midnight Timestamp (1 = first, -1 = last)."""
    first = pd.Timestamp(year=year, month=month, day=1)
    first_sunday = first + pd.Timedelta(days=(6 - first.weekday()) % 7)
    sundays = []
    day = first_sunday
    while day.month == month:
        sundays.append(day)
        day += pd.Timedelta(days=7)
    return sundays[-1] if nth == -1 else sundays[nth - 1]


def _spring_gap_present(stamps: pd.Series, spring_day: pd.Timestamp) -> bool:
    """True when the EU spring Sunday shows its skipped 19:00 stamp hour."""
    day = stamps[(stamps >= spring_day) & (stamps < spring_day + pd.Timedelta(days=1))]
    if day.empty:
        return False
    hours = set(day.dt.hour)
    return 18 in hours and 19 not in hours and 20 in hours


def _fall_step_position(stamps: pd.Series, fall_day: pd.Timestamp) -> int | None:
    """Position of the 19:59 -> 19:00 repeat split on the EU fall Sunday."""
    day = stamps[(stamps >= fall_day) & (stamps < fall_day + pd.Timedelta(days=1))]
    if day.empty:
        return None
    stepped = (day.diff() < pd.Timedelta(0))[lambda mask: mask]
    if stepped.empty:
        return None
    return int(stepped.index[0])


def _dst_offset_hours(stamps: pd.Series) -> pd.Series:
    """Hours to add to each New York wall-clock stamp to reach UTC (+4/+5).

    Summer stamps (EDT) get +4, winter stamps (EST) +5. The switch instants
    follow the US DST schedule (2nd Sunday March, 1st Sunday November — both
    inside the weekend closure) for stamps before 2019 and the EU DST schedule
    (last Sunday March/October, switching 00:00 UTC the Monday after) from
    2019 on, where spring leaves a skipped 19:00 hour and fall a repeated one.
    The repeated fall block is split by row order: the first 19:xx block is
    still summer. A file lacking the expected spring gap or fall step falls
    back to the US schedule date (inside the closure, so any instant in it
    agrees with the data) — EURUSD's 2024 fall switch leaves no trace while
    XAUUSD's repeats normally, so detection is per file, never a year table.
    """
    if stamps.empty:
        return pd.Series(dtype="int8")
    stamps = stamps.reset_index(drop=True)
    offsets = pd.Series(5, index=stamps.index, dtype="int8")
    for year in sorted(int(y) for y in stamps.dt.year.unique()):
        rows = stamps.dt.year == year
        if year < _DST_CROSSOVER_YEAR:
            summer = (
                rows
                & (stamps >= _nth_sunday(year, 3, 2))
                & (stamps < _nth_sunday(year, 11, 1))
            )
            offsets[summer] = 4
            continue
        spring_day = _nth_sunday(year, 3, -1)
        if _spring_gap_present(stamps, spring_day):
            spring_at = spring_day + pd.Timedelta(hours=19)
        else:
            spring_at = _nth_sunday(year, 3, 2)
        fall_day = _nth_sunday(year, 10, -1)
        step_at = _fall_step_position(stamps, fall_day)
        if step_at is None:
            summer = rows & (stamps >= spring_at) & (stamps < _nth_sunday(year, 11, 1))
        else:
            summer = (
                rows
                & (stamps >= spring_at)
                & (stamps < fall_day + pd.Timedelta(hours=19))
            )
            first_block = rows & (stamps >= fall_day) & (stamps.index < step_at)
            summer = summer | first_block
        offsets[summer] = 4
    return offsets


def read_histdata_minute_csv(
    path: str | Path,
    offset_hours: int | str = 0,
    valid_from: str | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Read one HistData ASCII 1m extract.

    Detects the delimiter, an optional header row, and either a combined
    datetime column or separate date and time columns. ``offset_hours`` is an
    integer (fixed hours added to the file clock) or the sentinel
    ``us_eastern_dst``: HistData stamps are New York wall clock, so each row
    gets +4h (EDT) or +5h (EST) per ``_dst_offset_hours`` — US DST dates
    before 2019, EU DST dates after. When ``valid_from`` is set, earlier rows
    are dropped as outside_valid_range: the UDXUSD feed carries a mislabeled
    Dow Jones series before 2018-12-16.
    If most rows fail the high-below-low check, the high and low columns are
    swapped once and re-validated.
    """
    raw_text = pd.read_csv(path, header=None, dtype=str, sep=None, engine="python")
    first_row = raw_text.iloc[0].str.lower()
    has_header = any(cell in {"date", "time", "open", "high", "low", "close", "volume"} for cell in first_row)
    if has_header:
        header_names = [str(cell).strip().lower() for cell in raw_text.iloc[0]]
        raw_text = raw_text.iloc[1:].reset_index(drop=True)
        raw_text.columns = header_names

    width = raw_text.shape[1]
    if "open" not in getattr(raw_text, "columns", []):
        if width == 7:
            raw_text.columns = ["date", "time", "open", "high", "low", "close", "volume"]
        elif width == 6:
            raw_text.columns = ["timestamp", "open", "high", "low", "close", "volume"]
        else:
            raise ValueError(f"Unexpected HistData column count {width} in {path}")

    if "date" in raw_text.columns:
        stamps = raw_text["date"].astype(str).str.strip() + " " + raw_text["time"].astype(str).str.strip()
    else:
        stamps = raw_text["timestamp"].astype(str).str.strip()
    parsed = pd.to_datetime(stamps, format="mixed", utc=False)

    if isinstance(offset_hours, str):
        if offset_hours != DST_OFFSET_MODE:
            raise ValueError(
                f"Unknown histdata offset mode {offset_hours!r}; "
                f"expected an integer or {DST_OFFSET_MODE!r}"
            )
        converted = parsed + pd.to_timedelta(_dst_offset_hours(parsed).to_numpy(), unit="h")
        cutoff_offset = 0  # converted stamps are already UTC
    else:
        converted = parsed + pd.Timedelta(hours=int(offset_hours))
        cutoff_offset = int(offset_hours)

    frame = pd.DataFrame(
        {
            "timestamp": converted,
            "open": pd.to_numeric(raw_text["open"], errors="coerce"),
            "high": pd.to_numeric(raw_text["high"], errors="coerce"),
            "low": pd.to_numeric(raw_text["low"], errors="coerce"),
            "close": pd.to_numeric(raw_text["close"], errors="coerce"),
            "volume": pd.to_numeric(raw_text["volume"], errors="coerce"),
        }
    )

    swapped = frame["high"] < frame["low"]
    if len(frame) > 0 and swapped.mean() > 0.5:
        logger.warning("High/low appear swapped in %s, correcting column order", path)
        frame[["high", "low"]] = frame[["low", "high"]].to_numpy()

    early_count = 0
    if valid_from is not None:
        cutoff = pd.Timestamp(valid_from) + pd.Timedelta(hours=cutoff_offset)
        early = frame["timestamp"] < cutoff
        early_count = int(early.sum())
        if early_count:
            logger.info(
                "Dropping %d rows before %s in %s (outside valid instrument range)",
                early_count,
                valid_from,
                path,
            )
        frame = frame[~early]

    frame["timestamp"] = frame["timestamp"].dt.tz_localize("UTC")
    data, drops = _finalize_minute_frame(frame, source=str(path))
    drops["outside_valid_range"] = early_count
    return data, drops


def read_user_dxy_daily_csv(path: str | Path) -> tuple[pd.DataFrame, dict[str, int]]:
    """Read the investing.com DXY daily export (DD-MM-YYYY, descending)."""
    raw = pd.read_csv(path)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(raw["Date"], format="%d-%m-%Y").dt.tz_localize("UTC"),
            "open": pd.to_numeric(raw["Open"], errors="coerce"),
            "high": pd.to_numeric(raw["High"], errors="coerce"),
            "low": pd.to_numeric(raw["Low"], errors="coerce"),
            "close": pd.to_numeric(raw["Price"], errors="coerce"),
            "volume": pd.to_numeric(raw["Vol."], errors="coerce").fillna(0.0),
        }
    )
    frame = frame.sort_values("timestamp", kind="mergesort").drop_duplicates(
        subset="timestamp", keep="last"
    )
    return frame.reset_index(drop=True), _empty_drops()


def aggregate_daily(minute_frame: pd.DataFrame, anchor_hour_utc: int = 21) -> pd.DataFrame:
    """Aggregate 1m bars into session-day daily bars at the anchor boundary.

    Session D covers [D 00:00 - (24 - anchor)h, D 00:00 + anchor h), which for
    anchor 21 spans the prior 21:00 UTC to the current 21:00 UTC: the standard
    FX trading day. The stored timestamp is midnight UTC of the session date,
    so aggregated bars dedupe against native daily bars on the same date.
    """
    if minute_frame.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    shift = pd.Timedelta(hours=(24 - anchor_hour_utc) % 24)
    session = (minute_frame["timestamp"] + shift).dt.floor("D")
    session.name = "timestamp"
    grouped = minute_frame.groupby(session, sort=True)
    daily = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
        }
    ).reset_index()
    return daily[MINUTE_COLUMNS]


def merge_frames(named_frames: list[tuple[str, pd.DataFrame]]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Concatenate frames in precedence order and keep the first source per stamp."""
    parts = [frame for _, frame in named_frames if frame is not None and not frame.empty]
    if not parts:
        return pd.DataFrame(columns=MINUTE_COLUMNS), {}
    merged = pd.concat(parts, ignore_index=True)
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True)
    merged = merged.drop_duplicates(subset="timestamp", keep="first")
    merged = merged.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    return merged[MINUTE_COLUMNS], {}


def _stage_user_files(cfg: dict[str, Any]) -> dict[str, Path]:
    """Copy the user's Downloads exports into data/raw/user and return staged paths."""
    downloads = Path(cfg["sources"]["user_downloads_dir"])
    staged: dict[str, Path] = {}
    for key, filename in cfg["sources"]["user_files"].items():
        source = downloads / filename
        if not source.exists():
            logger.warning("User file not found, skipping: %s", source)
            continue
        RAW_USER_DIR.mkdir(parents=True, exist_ok=True)
        target = RAW_USER_DIR / filename
        shutil.copy2(source, target)
        staged[key] = target
    return staged


def _histdata_files(symbol: str) -> list[Path]:
    directory = HISTDATA_DIR / symbol / "csv"
    if not directory.exists():
        return []
    return sorted(directory.glob("*.csv"))


def _db_frame(db: MarketDataDB, symbol: str, timeframe: str) -> pd.DataFrame:
    frame = db.get_ohlcv(symbol, timeframe)
    if frame.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    return frame.reset_index()[MINUTE_COLUMNS]


def _read_histdata_series(cfg: dict[str, Any], symbol: str) -> tuple[pd.DataFrame, dict[str, int], list[str]]:
    offsets = cfg["sources"].get("histdata_utc_offset_hours", {})
    valid_from_map = cfg["sources"].get("histdata_valid_from", {})
    offset = offsets.get(symbol, 0)
    valid_from = valid_from_map.get(symbol)
    frames: list[pd.DataFrame] = []
    drops_all = _empty_drops()
    failures: list[str] = []
    for file_path in _histdata_files(symbol):
        try:
            frame, drops = read_histdata_minute_csv(
                file_path, offset_hours=offset, valid_from=valid_from
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse %s: %s", file_path, exc)
            failures.append(str(file_path))
            continue
        frames.append(frame)
        for reason, count in drops.items():
            drops_all[reason] += count
    if not frames:
        return pd.DataFrame(columns=MINUTE_COLUMNS), drops_all, failures
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset="timestamp", keep="last")
    return combined[MINUTE_COLUMNS].reset_index(drop=True), drops_all, failures


def run_cleaning(cfg: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    """Clean every source into data/processed and write manifest.json."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    anchor = int(cfg["vwap"]["anchor_hour_utc"])
    offsets = cfg["sources"].get("user_csv_utc_offset_hours", {})
    staged = _stage_user_files(cfg)
    db = MarketDataDB(db_path or REPO_ROOT / "data" / "forex_market_data.db")
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anchor_hour_utc": anchor,
        "symbols": {},
    }

    for symbol in ["EURUSD", "XAUUSD", "DXY"]:
        entry: dict[str, Any] = {"sources": {}, "parse_failures": []}

        minute_sources: list[tuple[str, pd.DataFrame]] = []
        if symbol in staged:
            offset = int(offsets.get(symbol, 0))
            user_frame, user_drops = read_user_minute_csv(staged[symbol], offset_hours=offset)
            minute_sources.append(("user_csv", user_frame))
            entry["sources"]["user_csv"] = {
                "rows": len(user_frame),
                "dropped": user_drops,
                "utc_offset_hours": offset,
            }

        hist_frame, hist_drops, failures = _read_histdata_series(cfg, symbol)
        if not hist_frame.empty:
            minute_sources.append(("histdata", hist_frame))
            entry["sources"]["histdata"] = {
                "rows": len(hist_frame),
                "dropped": hist_drops,
                "utc_offset_hours": cfg["sources"].get(
                    "histdata_utc_offset_hours", {}
                ).get(symbol, 0),
            }
        entry["parse_failures"].extend(failures)

        db_minute = _db_frame(db, symbol, "1m")
        if not db_minute.empty:
            minute_sources.append(("yfinance_db", db_minute))
            entry["sources"]["yfinance_db_1m"] = {"rows": len(db_minute)}

        minute, _ = merge_frames(minute_sources)
        if not minute.empty:
            path = PROCESSED_DIR / f"{symbol}_1m.csv"
            minute.to_csv(path, index=False)
            entry["minute"] = {
                "rows": len(minute),
                "first": minute["timestamp"].iloc[0].isoformat(),
                "last": minute["timestamp"].iloc[-1].isoformat(),
                "file": str(path.relative_to(REPO_ROOT)),
            }
            aggregated = aggregate_daily(minute, anchor_hour_utc=anchor)
            now_utc = pd.Timestamp.now(tz="UTC")
            incomplete = aggregated[aggregated["timestamp"] + pd.Timedelta(hours=anchor) > now_utc]
            entry["aggregated_daily"] = {
                "rows": len(aggregated),
                "incomplete_session_dates": [
                    stamp.date().isoformat() for stamp in incomplete["timestamp"]
                ],
            }
        else:
            aggregated = pd.DataFrame(columns=MINUTE_COLUMNS)

        # Daily precedence: user file wins its dates, aggregated 1m fills its
        # range, the staged yfinance daily supplies deeper history.
        daily_sources: list[tuple[str, pd.DataFrame]] = []
        if symbol == "DXY" and "DXY_DAILY" in staged:
            user_daily, _ = read_user_dxy_daily_csv(staged["DXY_DAILY"])
            daily_sources.append(("user_csv", user_daily))
            entry["sources"]["user_csv_daily"] = {"rows": len(user_daily)}
        if not aggregated.empty:
            daily_sources.append(("aggregated_1m", aggregated))
        db_daily = _db_frame(db, symbol, "1d")
        if not db_daily.empty:
            daily_sources.append(("yfinance_db", db_daily))
            entry["sources"]["yfinance_db_1d"] = {"rows": len(db_daily)}

        daily, _ = merge_frames(daily_sources)
        if not daily.empty:
            daily_path = PROCESSED_DIR / f"{symbol}_1d.csv"
            daily.to_csv(daily_path, index=False)
            entry["daily"] = {
                "rows": len(daily),
                "first": daily["timestamp"].iloc[0].date().isoformat(),
                "last": daily["timestamp"].iloc[-1].date().isoformat(),
                "file": str(daily_path.relative_to(REPO_ROOT)),
            }
        else:
            entry["daily"] = {"rows": 0}

        manifest["symbols"][symbol] = entry

    manifest_path = PROCESSED_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Wrote manifest to %s", manifest_path)
    return manifest
