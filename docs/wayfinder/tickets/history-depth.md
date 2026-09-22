---
label: wayfinder:grilling
status: open
blocked_by: []
---

# 1m history depth

## Question

Now that the source comparison exists, adopt HistData for multi-decade 1m history or stay at yfinance's 29-day window? HistData reaches May 2000 for EURUSD, March 2009 for XAUUSD, and November 2010 for UDX, free with no account, but its M1 volume column reads 0, so it buys depth and not volume. Staying at yfinance keeps the pipeline as it is and caps every 1m VWAP backtest at one month.

The answer sets how many training windows the walk-forward validation can hold, so it decides the scale of the backtest before the design ticket can close.

Evidence: [docs/minute-data-sources-research.md](../../minute-data-sources-research.md).
