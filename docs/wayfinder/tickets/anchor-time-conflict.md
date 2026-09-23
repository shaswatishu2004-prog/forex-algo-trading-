---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: []
---

# Anchor time conflict

## Question

The user settled a UTC midnight daily anchor on 2026-09-22, before the research landed. The research recommends the institutional trading-day reset at 21:00 UTC, which is 17:00 New York and the FX day boundary, with an optional New York session anchor at 13:00 UTC. Keep UTC midnight or switch to the convention? The answer fixes the reset timestamp in the analysis engine and changes which minutes fall into which anchor day for the 1m VWAP bands.

Evidence: [docs/vwap-regime-research.md](../../vwap-regime-research.md).

## Resolution

Anchor at 21:00 UTC. The user re-answered this question on 2026-09-22 with the 21:00 UTC FX trading-day reset, which supersedes the earlier UTC midnight settlement. The optional 13:00 UTC New York session anchor stays out: one anchor keeps session labels and VWAP resets unambiguous.

Corroboration: the daily-boundary scan in the [DXY fetch report](../../../data/raw/histdata/DXY/FETCH_REPORT.md) ranks six candidate boundaries by agreement with the user's daily export, and the 21:00 UTC boundary wins with median 0.0955, best of six.

Implementation: `vwap.anchor_hour_utc: 21` in `config/default.yaml`, `session_labels()` in `src/fx_strategy/analysis/vwap.py`, covered by `test_session_labels_use_21_utc_boundary` and `test_aggregate_daily_boundary`.

Session semantics: session D covers [D-1 21:00, D 21:00) UTC, labeled by its end date at midnight UTC so aggregated daily stamps dedupe against native daily sources.
