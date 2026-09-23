---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: []
---

# Analysis engine folder layout

## Question

Does the analysis engine live in a new subfolder under `src/fx_strategy/`, for example `src/fx_strategy/analysis/`, or in a new top-level package? Which modules go in, how config wires up, and does `config/default.yaml` move from its current 1h regime and 5m signal timeframes to the 1d regime and 1m signal this strategy uses? The user asked for a new subfolder for the analysis engine.

## Resolution

It lives in `src/fx_strategy/analysis/` under the existing package, one module per concern: `config.py`, `cleaning.py`, `vwap.py`, `regime.py`, `signals.py`. No top-level package.

It runs as a batch command. `scripts/run_analysis.py` does one pass over `data/processed/`, and `scripts/weekly_refresh.py` does the refresh either with `--once` or with `--loop` until `refresh.loop_days` (365), wired to the Monday 06:00 UTC cron in `.github/workflows/weekly-refresh.yml`. Each run rewrites the signals folder and prunes files older than `signals.archive_days` (365).

`config/default.yaml` moves to `timeframes.regime: 1d` and `timeframes.signal: 1m` and gains `vwap`, `regime`, `refresh`, `sources`, and `signals` sections. The 1h and 5m settings are gone.

Tests: `tests/test_analysis_engine.py`, 25 passing at resolution time, run with pytest from the repository root.
