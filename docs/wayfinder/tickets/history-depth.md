---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: []
---

# 1m history depth

## Question

Now that the source comparison exists, adopt HistData for multi-decade 1m history or stay at yfinance's 29-day window? HistData reaches May 2000 for EURUSD, March 2009 for XAUUSD, and November 2010 for UDX, free with no account, but its M1 volume column reads 0, so it buys depth and not volume. Staying at yfinance keeps the pipeline as it is and caps every 1m VWAP backtest at one month.

The answer sets how many training windows the walk-forward validation can hold, so it decides the scale of the backtest before the design ticket can close.

Evidence: [docs/minute-data-sources-research.md](../../minute-data-sources-research.md).

## Resolution

Adopt HistData for multi-decade 1m history. The user chose depth over yfinance's 29-day cap on 2026-09-22, and the fetch landed on 2026-09-23: ASCII M1 zips and extracted CSVs under `data/raw/histdata/`, about 1.15 GB total.

- EURUSD: 35 files, 2000 through 2026-09.
- XAUUSD: 26 files, 2009 through 2026-09.
- DXY: 25 files, 2010 through 2026-09. Rows before 2018-12-16 reject as mislabeled Dow Jones data, see the fetch report.

yfinance keeps two jobs: daily bars deeper than 1m aggregation reaches, and the weekly refresh inside its 29-day 1m window. HistData M1 volume reads 0, so the synthetic activity weight from [VWAP band math](vwap-band-math.md) covers those bars.

Fetch mechanics, timezone offsets, and data-quality findings live in the per-symbol `FETCH_REPORT.md` next to each download. Start with [DXY](../../../data/raw/histdata/DXY/FETCH_REPORT.md).
