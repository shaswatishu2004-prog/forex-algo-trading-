---
label: wayfinder:task
status: resolved
blocked_by: []
claimed: this-session
---

# Stage raw historical data

## Resolution

Resolved 2026-09-22. Ran `scripts/download_forex_data.py` with defaults into `data/forex_market_data.db`, exit code 0, then audited gaps and volume coverage with a direct SQL pass.

Row counts and ranges:

| symbol | timeframe | bars | earliest (UTC) | latest (UTC) | bars with volume |
|---|---|---|---|---|---|
| EURUSD | 1m | 28,388 | 2026-08-24 23:00 | 2026-09-21 22:58 | 0 |
| EURUSD | 1d | 5,918 | 2003-12-01 | 2026-09-22 | 0 |
| XAUUSD | 1m | 25,798 | 2026-08-25 04:00 | 2026-09-22 03:58 | 25,776 |
| XAUUSD | 1d | 6,540 | 2000-08-30 | 2026-09-22 | 6,122 |
| DXY | 1m | 25,602 | 2026-08-25 04:00 | 2026-09-22 03:58 | 0 |
| DXY | 1d | 14,148 | 1971-01-04 | 2026-09-22 | 2 |

Facts later tickets depend on:

- The chunked fetch returned the full 28-day window, so the claim in `docs/vwap-regime-research.md` that yfinance serves only 7 days of 1m bars does not match observed behavior. The 29-day cap in the fetcher is the real limit.
- 1m gaps are session-shaped, not data loss. EURUSD and DXY miss only Saturdays. XAUUSD also misses Sundays and 2026-09-07, the Labor Day holiday. The thin days are Sundays at 60 bars for EURUSD, 110 for XAUUSD, and 120 for DXY, which is the market opening late. No weekday is missing.
- Volume exists only on XAUUSD, which is the GC=F futures contract. EURUSD and DXY are zero-volume on both timeframes, and DXY 1d carries volume on 2 of 14,148 bars. The synthetic activity weight from [VWAP band math and volume proxies](vwap-band-math.md) is mandatory for EURUSD and DXY, not a fallback.
- Stored timestamp offsets are mixed per source: EURUSD 1m uses `+0100`, XAUUSD and DXY 1m use `-0400`, daily bars use `+0000`. The read path parses to UTC and is safe, but string range filters in SQL compare raw text and will misorder across offsets. Cleaning must normalize before any string comparison.
- Added `data/*.db` to `.gitignore` so the staged binary stays out of git.
