---
label: wayfinder:research
status: resolved
blocked_by: []
claimed: background-agent
---

# Minute data sources beyond 29 days

## Resolution

Resolved 2026-09-22 by the background research agent. Full comparison with a provider table and links on every row: [docs/minute-data-sources-research.md](../../minute-data-sources-research.md).

- Primary recommendation: HistData. Free, no account, plain HTTP, and the only source covering all three instruments at 1m depth: EURUSD from May 2000, XAU/USD from March 2009, UDX/USD from November 2010.
- Backup: OANDA. Its M1 candle `volume` field is a tick count, so it feeds VWAP weighting directly, behind an account and API token.
- The catch that shapes the decision: HistData's M1 volume column reads 0, so HistData buys history depth but not volume. Weights there still come from the synthetic activity weight, or from tick counts pulled from OANDA later.
- Ruled out with reasons: Yahoo for the 29-day cap, Alpha Vantage because FX_INTRADAY turned premium with 25 free requests a day, Twelve Data for having no DXY symbol and no volume in demo 1m bars, TrueFX and Dukascopy as tick feeds needing aggregation, and DX-Y.NYB as a calculated index with no volume. FXCM and Stooq returned transport errors on every fetch and stay unverified.
- The decision this research existed for, adopt HistData for deeper 1m history or stay at yfinance's 29 days, is now sharp and lives in [1m history depth](history-depth.md).
