---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: [signal-combination-rule]
---

# Signals JSON schema and folder

## Question

Which fields every signal carries: symbol, timeframe, timestamp, signal type, direction, VWAP and band values, regime state, strength, and schema version? What is the new subfolder called, how are files named and rotated, and what does one file hold: one signal, one day, or one run? The user asked for a new subfolder with JSON signals.

## Resolution

Resolved 2026-09-24. Implemented in `src/fx_strategy/analysis/signals.py`, written by `scripts/run_analysis.py`.

- **Folder and naming**: `signals/`, one file per symbol per run, named `SYMBOL_YYYY-MM-DD.json` from the run date. `signals.archive_days: 365` rotates files older than a year out. The folder is gitignored; `signals/README.md` documents it in-tree.
- **One file holds one run, not one signal.** A header block carries `schema_version`, `generated_at`, `symbol`, `anchor_hour_utc`, the `timeframes` pair, the source window, a `last_state` snapshot of the newest VWAP and band values, and `record_counts`. The `records` array below it holds every record of that run, sorted by `(timestamp, type)`.
- **Two record types, no conjunction.** `vwap_position` carries the 1m band state (position, vwap, sigma, and all four band edges); `regime` carries one labeled 1d bar. They stay separate because signal-combination-rule keeps them independent; the conjunction happens at backtest time, not in this file.
- **Strength is deliberately absent.** There is no defensible magnitude to publish while the combination lives elsewhere, so records keep `action: "observe"` instead. Adding a `strength` field later is a minor schema change under `schema_version` `"1.0"`.
- **Versioning**: `schema_version` is stamped on the header and on every record, sourced from `signals.schema_version` in config. A breaking field change bumps that string; readers must ignore unknown fields rather than fail.
