# signals

Output folder for observation JSON written by `scripts/run_analysis.py`.

- File per symbol per run: `{SYMBOL}_{YYYY-MM-DD}.json`, UTC run date. Each run
  removes that symbol's older files, so the folder always holds the latest run
  per symbol; `--prune-signals` clears strays past `signals.archive_days` (365).
- `vwap_position` records fire when close's band position changes, not on every
  bar; the full per-bar band state stays in `data/processed` CSVs.
- Schema version 1.0, two record types: `vwap_position` (1m band state) and
  `regime` (1d label). Every record carries `action: "observe"`; entry, exit,
  and strength semantics wait for the signal-combination-rule ticket.
- The weekly loop (`scripts/weekly_refresh.py`, workflow cron every Monday
  06:00 UTC, `refresh.loop_days: 365`) rewrites these files year-round.

Full field list: `docs/wayfinder/tickets/signals-json-schema.md`.
