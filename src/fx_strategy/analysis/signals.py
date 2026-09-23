"""Signals JSON: schema, emission, and the signals/ output folder.

Decision: docs/wayfinder/tickets/signals-json-schema.md.

Scope boundary: this module emits raw observations only. A band touch and a
regime label each arrive as their own record with independent meaning. What a
touch *signals* for entry, exit, or trade direction stays undefined until
signal-combination-rule resolves, so every record carries
``"action": "observe"`` and no strength or trade fields are invented here.

Schema (version 1.0):

- Folder: ``signals/`` at the repository root. JSON outputs are gitignored;
  ``signals/README.md`` carries the human summary and the ticket holds the
  full contract.
- One file per symbol per run: ``{SYMBOL}_{YYYY-MM-DD}.json`` keyed by the UTC
  run date. A run removes the symbol's previous files first, so the folder
  holds the latest run per symbol; strays older than
  ``signals.archive_days`` (365) get pruned by ``--prune-signals``.
- One file holds one run: a header (schema_version, generated_at, symbol,
  anchor, timeframes, source window, last_state) and a ``records`` array.
- A ``vwap_position`` record is emitted when close's position relative to the
  bands CHANGES (plus the first bar of every anchor session), not for every
  bar: the full per-bar band state lives in data/processed CSVs, and
  emitting 300k+ records per run would make the folder unusable. Fields:
  type, symbol, timeframe (1m), timestamp (ISO-8601 UTC), action, session,
  close, vwap, sigma, band1/band3 upper and lower, weight_mode, position
  (indeterminate | inside_band1 | between_1_and_3_upper |
  between_1_and_3_lower | above_band3 | below_band3).
- A ``regime`` record is one labeled daily bar: type, symbol, timeframe (1d),
  timestamp, action, regime (uptrend | downtrend | high_vol | low_vol) and
  its inputs (atr_percentile, adx, plus_di, minus_di, ema_slope,
  efficiency_ratio).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "1.0"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _band_position(
    close: pd.Series,
    band1_upper: pd.Series,
    band3_upper: pd.Series,
    band1_lower: pd.Series,
    band3_lower: pd.Series,
    sigma: pd.Series,
) -> pd.Series:
    """Classify where close sits relative to the 1 and 3 sigma bands."""
    position = pd.Series("inside_band1", index=close.index, dtype="object")
    position = position.mask((close >= band1_upper) & (close <= band3_upper), "between_1_and_3_upper")
    position = position.mask((close <= band1_lower) & (close >= band3_lower), "between_1_and_3_lower")
    position = position.mask(close > band3_upper, "above_band3")
    position = position.mask(close < band3_lower, "below_band3")
    # A fresh anchor session has sigma 0 until a second bar arrives; a position
    # claim against collapsed bands would be meaningless.
    position = position.mask(sigma <= 0, "indeterminate")
    return position


def build_vwap_records(vwap_frame: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Records for band-position changes plus the last bar's state snapshot."""
    if vwap_frame.empty:
        return [], {}
    frame = vwap_frame.dropna(subset=["vwap", "sigma"]).reset_index(drop=True)
    if frame.empty:
        return [], {}
    position = _band_position(
        frame["close"],
        frame["band1_upper"],
        frame["band3_upper"],
        frame["band1_lower"],
        frame["band3_lower"],
        frame["sigma"],
    )
    changes = position.ne(position.shift(1))
    records: list[dict[str, Any]] = []
    for idx in frame.index[changes]:
        row = frame.loc[idx]
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "type": "vwap_position",
                "symbol": row.get("symbol", ""),
                "timeframe": "1m",
                "timestamp": row["timestamp"].isoformat(),
                "action": "observe",
                "session": row["session"].date().isoformat(),
                "close": round(float(row["close"]), 6),
                "vwap": round(float(row["vwap"]), 6),
                "sigma": round(float(row["sigma"]), 6),
                "band1_upper": round(float(row["band1_upper"]), 6),
                "band1_lower": round(float(row["band1_lower"]), 6),
                "band3_upper": round(float(row["band3_upper"]), 6),
                "band3_lower": round(float(row["band3_lower"]), 6),
                "weight_mode": row["weight_mode"],
                "position": position.loc[idx],
            }
        )

    last = frame.iloc[-1]
    last_state = {
        "timestamp": last["timestamp"].isoformat(),
        "session": last["session"].date().isoformat(),
        "position": position.iloc[-1],
        "close": round(float(last["close"]), 6),
        "vwap": round(float(last["vwap"]), 6),
        "sigma": round(float(last["sigma"]), 6),
        "band1_upper": round(float(last["band1_upper"]), 6),
        "band1_lower": round(float(last["band1_lower"]), 6),
        "band3_upper": round(float(last["band3_upper"]), 6),
        "band3_lower": round(float(last["band3_lower"]), 6),
        "weight_mode": last["weight_mode"],
    }
    return records, last_state


def build_regime_records(regime_frame: pd.DataFrame) -> list[dict[str, Any]]:
    """One regime record per labeled daily bar."""
    labeled = regime_frame.dropna(subset=["regime"])
    records: list[dict[str, Any]] = []
    for _, row in labeled.iterrows():
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "type": "regime",
                "symbol": row.get("symbol", ""),
                "timeframe": "1d",
                "timestamp": row["timestamp"].isoformat(),
                "action": "observe",
                "regime": row["regime"],
                "atr_percentile": round(float(row["atr_percentile"]), 2),
                "adx": round(float(row["adx"]), 2),
                "plus_di": round(float(row["plus_di"]), 2),
                "minus_di": round(float(row["minus_di"]), 2),
                "ema_slope": round(float(row["ema_slope"]), 6),
                "efficiency_ratio": round(float(row["efficiency_ratio"]), 4),
            }
        )
    return records


def build_symbol_signals(
    symbol: str,
    vwap_frame: pd.DataFrame,
    regime_frame: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full signals document for one symbol and run."""
    config = cfg or {}
    minute_window = {"first": None, "last": None, "bars": int(len(vwap_frame))}
    if not vwap_frame.empty:
        minute_window["first"] = vwap_frame["timestamp"].iloc[0].isoformat()
        minute_window["last"] = vwap_frame["timestamp"].iloc[-1].isoformat()

    vwap_records, last_state = build_vwap_records(vwap_frame.assign(symbol=symbol))
    regime_records = build_regime_records(regime_frame.assign(symbol=symbol))
    records = sorted(
        vwap_records + regime_records,
        key=lambda record: (record["timestamp"], record["type"]),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "symbol": symbol,
        "anchor_hour_utc": int(config.get("vwap", {}).get("anchor_hour_utc", 21)),
        "signal_timeframe": config.get("timeframes", {}).get("signal", "1m"),
        "regime_timeframe": config.get("timeframes", {}).get("regime", "1d"),
        "minute_window": minute_window,
        "last_state": last_state,
        "record_counts": {
            "total": len(records),
            "vwap_position": len(vwap_records),
            "regime": len(regime_records),
        },
        "records": records,
    }


def signals_dir(cfg: dict[str, Any] | None = None) -> Path:
    configured = (cfg or {}).get("signals", {}).get("directory", "signals")
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def write_symbol_signals(document: dict[str, Any], cfg: dict[str, Any] | None = None) -> Path:
    """Write the document and drop the symbol's older run files (latest wins)."""
    target_dir = signals_dir(cfg)
    target_dir.mkdir(parents=True, exist_ok=True)
    symbol = document["symbol"]
    for stale in target_dir.glob(f"{symbol}_*.json"):
        stale.unlink()
    target = target_dir / f"{symbol}_{document['run_date']}.json"
    target.write_text(
        json.dumps(document, indent=1, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return target


def prune_signal_files(cfg: dict[str, Any] | None = None) -> list[Path]:
    """Delete any signal file older than signals.archive_days. Returns removed paths."""
    days = int((cfg or {}).get("signals", {}).get("archive_days", 365))
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    removed: list[Path] = []
    target_dir = signals_dir(cfg)
    if not target_dir.exists():
        return removed
    for path in sorted(target_dir.glob("*.json")):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    return removed
