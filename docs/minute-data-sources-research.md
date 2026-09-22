# Minute data sources research

Background research for the repo against primary sources: provider docs, pricing and limit pages, license pages, and official Python clients. The question: which source gives multi-month 1-minute history for EURUSD, XAUUSD, and DXY, and what does each one carry for volume or tick count.

## Provider comparison

| Provider | 1m history: EURUSD | 1m history: XAUUSD | 1m history: DXY | Volume input | Limits and account | License | Python pull |
|---|---|---|---|---|---|---|---|
| HistData | From 2000/May | From 2009/May (XAU/USD file set from 2009/March) | UDX/USD from 2010/Nov | M1 CSV has a volume column but rows show 0 | Free, no account, plain HTTP downloads | No dedicated license page found; footer terms only | Plain `requests` + zipfile, no client needed |
| Polygon (now Massive) | Forex bars from 2009-09-25 | No spot metal under currencies; CME/CBOT/NYMEX/COMEX futures only, no ICE DX either | No DXY; currencies coverage is forex + crypto | Bars built from quotes, fields `v`, `vw`, `n` present | Currencies Starter = all history + real-time; Basic = 2 years + EOD | Paid subscription terms | Official `massive` RESTClient, plus S3 flat files |
| Twelve Data | Demo earliest 1min timestamp 2020-04-07; paid tiers reach further back | XAU/USD exists but demo key returned 401; depth unverified | No DXY symbol; symbol_search returned only unrelated stocks | Volume field is in the schema but demo 1min EUR/USD rows carry no volume key | Free Basic: 8 API credits/min, 800/day, 3 markets; Grow $79/mo | Subscription terms | Official `twelvedata` SDK |
| Alpha Vantage | FX_INTRADAY `outputsize=full` gives the full-length series | No spot gold; FX pairs only | No DXY minute data; FX pairs only | Time series has no volume field for FX | FX_INTRADAY is now Premium; free tier 25 requests/day | Terms on site | Plain `requests.get` to alphavantage.co/query |
| OANDA | M1 candles available; depth not stated on a fetched page | Metals (XAU) available as instruments | No Dollar Index instrument listed | Candle `volume` = number of prices created in the range, a tick count usable for VWAP | Account and API token required; about 2 new connections/s, 100 requests/s on a persistent connection | Account terms | Plain HTTP v20 REST, no official SDK required |
| TrueFX | Tick-by-tick only, no minute bars | Metals sourced from Integral OCX | No DXY mentioned | Top-of-book ticks with millisecond detail, from which 1m bars are aggregated | Downloads free but need registration/login; streaming plans run to $4,950/mo | Terms of service on registration page | Plain HTTP after login |
| Dukascopy | Free CSV export, tick-by-tick to monthly timeframes across forex, commodities, indices | Same export page covers commodities | Instrument list for DXY not confirmed; datafeed directory probes returned 503/403 | Tick and bar data; exact per-instrument volume field unverified | Free historical export, no stated account requirement | Export page terms | Plain HTTP to datafeed.dukascopy.com `.bi5` files, decompress with lzma |
| IBKR | 1 min bar size valid, but Forex has no TRADES volume (MIDPOINT/BID/ASK only) | Metals support TRADES with volume | DXY only via DX (ICE Dollar Index) futures, which carry TRADES volume | Forex volume is N/A; metals and index futures have trade volume | Account required; historical depth limits page could not be fetched (deprecated IBKR Campus migration) | Customer agreement | Official `ib_insync` / `ibapi` TWS or gateway |
| MetaTrader broker export | History Center (Tools > History Center) downloads M1 per broker | Depends on broker symbol availability | Depends on broker symbol availability | Tick volume only, broker-dependent | Free with terminal, depth depends on the broker feed | Broker terms | `MetaTrader5` package on Windows, or terminal export |
| Stooq | Docs pages returned empty or 404 on every attempt | Unverified | Unverified | Unverified | Unverified | Unverified | Unverified |
| FXCM | Docs hosts unreachable, transport errors on every attempt | Unverified | Unverified | Unverified | Unverified | Unverified | Unverified |
| Yahoo Finance (baseline) | 1m capped at 29 days, yfinance docs say 7 days for 1m and 60 days below 1d | GC=F carries real COMEX volume | DX-Y.NYB shows volume `--`, avg volume 0, and is an index not a contract | EURUSD=X volume column is zero | Free, no account | Yahoo terms | `yfinance` |

## Notes on the risky instruments

DX-Y.NYB is a calculated index, not a traded contract, so it shows no volume and may carry no minute bars at all. The Dollar Index route with real trade volume is ICE DX futures, which IBKR exposes. XAUUSD spot has no consolidated tape; HistData publishes an XAU/USD M1 file set from 2009 and OANDA lists metals with tick-count volume, while Polygon's currencies subscription covers forex and crypto only. Spot FX in general has no consolidated trade volume, so tick count or quote counts stand in for volume.

## Recommended default

- Primary: **HistData**. It is free, needs no account, and covers 1m bars for EURUSD from 2000, XAUUSD from 2009, and UDX/USD (Dollar Index) from 2010, which is all three instruments in one source.
- Backup: **OANDA**. Its M1 candles carry a tick-count `volume` field that feeds VWAP directly, with an account as the only barrier.

The catch: HistData's M1 volume column is 0, so weight by tick count you compute yourself or treat the bars as TWAP until OANDA tick counts are pulled. Polygon is the better structured option for EURUSD alone, since its forex bars carry `v`, `vw`, and `n` back to 2009, but it covers neither XAUUSD spot nor DXY.

## Sources

- HistData, ASCII 1-min for FX: https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes
- HistData, ASCII 1-min for gold: https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/XAUUSD
- HistData, ASCII 1-min for US Dollar Index: https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/UDXUSD
- Polygon forex aggregates endpoint: https://polygon.io/docs/rest/stocks/aggregates/custom-bars
- Polygon knowledge base, currencies subscription: https://polygon.io/kb
- Twelve Data time_series endpoint: https://twelvedata.com/docs#time-series
- Twelve Data rate limits: https://twelvedata.com/docs#rate-limiting
- Twelve Data pricing: https://twelvedata.com/pricing
- Alpha Vantage FX_INTRADAY: https://www.alphavantage.co/documentation/#fx-intraday
- Alpha Vantage pricing and free tier: https://www.alphavantage.co/support/#api-key
- OANDA v20 instrument definitions (M1 granularity, candle volume): https://developer.oanda.com/rest-live-v20/instrument-ep/
- OANDA API rate guidance: https://developer.oanda.com/rest-live-v20/use-the-api-best-practices/
- OANDA instruments list: https://www.oanda.com/us-en/trading/markets/
- TrueFX historical data registration: https://truefx.com/#registration
- Dukascopy historical data export: https://www.dukascopy.com/swiss/english/marketwatch/historical-data/
- IBKR historical bars: https://guides.interactivebrokers.com/tws/historical_bars.htm
- MetaTrader 5 timeframes: https://www.metatrader5.com/en/terminal/help/charts_timeframes
- MetaTrader 5 History Center: https://www.metatrader5.com/en/terminal/help/charts_advanced/history
- yfinance download limits: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
