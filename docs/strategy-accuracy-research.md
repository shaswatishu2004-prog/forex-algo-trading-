# Strategy accuracy and win-rate research

Background: the research brief was to raise this strategy's accuracy, use
real-time data, and reach a win rate close to 90 percent. Five background
agents worked in parallel on 2026-09-23. This file is the single consolidated
record, and every claim keeps the source link the agent that found it.

Headline conclusion: no credible published result shows a 90 percent win rate
for FX band reversion at honest risk-reward. Published two-sigma-plus-
confirmation designs win 44.2 to 53.5 percent across 532 to 798 trades. The
measurable target for this repo is 55 to 65 percent at 1:1 or better over 300
or more trades, and confirming it takes about 385 to 400 closed trades for
plus or minus 3 to 5 percentage points of precision. The staged 29-day minute
window cannot measure any of this, so deeper history, the signals schema, the
backtest, and a paper loop come first.

Sections, in the order the pipeline meets them: real-time data sources, VWAP
band entry design, regime and session filters, win-rate measurement, and the
execution and live loop.


## Real-time and near-real-time data for EURUSD, XAUUSD, and DXY at 1-minute granularity

### Why this matters

The strategy runs on 1-minute VWAP bands, so it needs three things: 1-minute bars deep enough to compute a session VWAP, a live quote loop that ticks at least once a minute for paper trading, and a DXY series that lines up with the FX bars on the same clock. Section 02 to 05 cover backtest design and execution; this section picks the pipe that feeds them.

The repo today stages data from yfinance, which caps 1-minute history at roughly 30 days and cannot stream at all ([yfinance download docstring](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html), [issue about the 30-day 1m window](https://github.com/ranaroussi/yfinance/issues/2451)). That is fine for a smoke test, not for a live loop. The honest headline for this whole research: for a 1-minute mean-reversion strategy, feed latency measured in tens or hundreds of milliseconds is irrelevant, while feed identity, session alignment, and bid/ask consistency matter a lot.

One structural fact drives the DXY problem: DXY is an ICE-calculated index, not a tradable spot pair, and ICE publishes a new level every 1 second from component midpoints ([ICE FX index methodology](https://www.ice.com/publicdocs/nyse/indices/ICE_FX_Indexes_Methodology.pdf), [ICE developer portal](https://developer.ice.com/fixed-income-data-services/catalog/ice-data-indices-currency-indices)). Most retail FX APIs sell pairs, not the index, so a DXY plan is a separate decision.

### What each source offers

| Source | EURUSD, XAUUSD | DXY | Live or delayed | Streaming | 1m bars or ticks | History depth | Free tier limits | Paid cost |
|---|---|---|---|---|---|---|---|---|
| OANDA v20 | Both (XAU not offered to US clients) | Not confirmed on public instrument list | Real-time, account-specific prices | Yes, HTTP chunked stream | Ticks in stream, M1 candles in REST | Back to 2005 | Practice account, no data fee, [30 polling rate limit, 20 streams](https://developer.oanda.com/rest-live-v20/api-comparison/) | Free with account |
| IG | Both | IG publishes a US Dollar Index market | Real-time | Yes, Lightstreamer | Minute prices, REST history | Finite weekly datapoint quota | Demo API exists, [~40 trade requests/min](https://www.ig.com/en/trading-platforms/trading-apis/how-to-use-ig-api) | Free with account |
| Dukascopy | Both | Not confirmed in datafeed | Real-time via polling, delayed for aggregated intervals | No true push, generator poll | Ticks and M1 | Deep, per-instrument start date | [Free historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/) | Free |
| MetaTrader 5 | Broker-dependent, both usually present | Brokers commonly list a symbol | Real-time, poll `symbol_info_tick` | No websocket, poll only | Ticks and M1 | Limited by "Max bars in chart" | Free with broker demo | Free |
| Twelve Data | Both | No index symbol found, component pairs only | Real-time forex on all tiers | Yes, websocket, [Pro plan required for full WS](https://support.twelvedata.com/en/articles/5335783-trial) | Tick WS plus OHLCV | Limited vs Ultra tier | [8 API credits/min, 800/day, 8 trial WS credits](https://twelvedata.com/pricing.md) | From [$99/mo](https://twelvedata.com/pricing) |
| Massive (Polygon) | Both, ~1,200 pairs | Not confirmed under currencies | Currencies tier pricing not readable on page | Yes, websocket cluster | Minute and second aggregates, quotes | Currencies coverage start not published on page | [5 API calls/min](https://massive.com/pricing) | Currencies price unverified, see note |
| Finnhub | Pairs, XAUUSD not confirmed | Not confirmed | Real-time FX quotes | Yes, one connection per key | FX OHLC endpoints | Not published for FX free tier | [60 calls/min, 50 WS symbols, personal use](https://finnhub.io/pricing) | FX tiers listed at $0, All-In-One $3,500/mo |
| Tiingo | Both, 140+ pairs | No index symbol, component pairs only | Real-time top-of-book | Yes, FX firehose websocket | Tick quotes plus OHLCV to 1min | FX history back to 2020 | [500 symbols/mo, 50 req/hr, 1,000 req/day, 1 GB](https://www.tiingo.com/about/pricing) | $30/mo individual, $50/mo commercial |
| Databento | 100+ spot FX pairs from 40+ LPs | Not a spot FX pair | Real-time on subscription plans | Yes, streaming API | Ticks, L1, 1m aggregates | Metered catalog | No free data tier, account needed | Metered $/GB, quote required |
| dxFeed | Ask sales | Ask sales | Real-time and delayed entitlements | Yes, dxLink websocket | Candles and quotes | Contract-dependent | Trial only, with usage bans | Not published |
| yfinance | `EURUSD=X`, gold ETF-style symbols | Unofficial `DX-Y.NYB` | Delayed, poll-only | No streaming at all | M1 bars only, ~30 days | Daily bars deep | Free, undocumented, no SLA | Free |

#### Broker APIs

**OANDA v20.** The pricing stream gives account-specific bid and ask buckets with liquidity sizes, so you get a real best-bid-offer rather than a single mid ([pricing endpoint docs](https://developer.oanda.com/rest-live-v20/pricing-ep/)). It streams over HTTP chunked JSON with heartbeats every 5 seconds. You must hold a v20 trading account, which is unavailable from the OANDA Global Markets and OANDA TMS divisions ([introduction](https://developer.oanda.com/rest-live-v20/introduction/)). The stream is capped at 4 prices per instrument per second, meaning you see the last price in each 250ms window rather than every print ([pricing endpoint docs](https://developer.oanda.com/rest-live-v20/pricing-ep/)). Each `ClientPrice` carries arrays of bids and asks with liquidity, and the development guide calls this the way to get real-time rates ([pricing definitions](https://developer.oanda.com/rest-live-v20/pricing-df/), [development guide](https://developer.oanda.com/rest-live-v20/development-guide/)). Historical candles go back to 2005 ([introduction](https://developer.oanda.com/rest-live-v20/introduction/)), and the comparison table lists 1-minute granularity plus bid, ask, mid components ([api comparison](https://developer.oanda.com/rest-live-v20/api-comparison/)). Gold is unavailable to US-based clients ([OANDA help](https://help.oanda.com/us/en/faqs/find-gold-and-silver.htm)). A practice account gives the same API without funding. Check current signup terms on the [OANDA instruments page](https://www.oanda.com/us-en/trading/instruments/).

**IG.** Prices stream through Lightstreamer, not a REST push, and you subscribe by EPIC with BID and OFFER fields ([streaming API guide](https://labs.ig.com/streaming-api-guide.html)). REST snapshots are throttled to roughly 40 trade requests per minute with a finite weekly datapoint quota ([how to use IG API](https://www.ig.com/en/trading-platforms/trading-apis/how-to-use-ig-api)), and the history endpoint returns minute prices for the last 10 minutes by default ([REST reference](https://labs.ig.com/rest-trading-api-reference.html)). IG documents a US Dollar Index market you can open an account to trade ([IG dollar index outlook](https://www.ig.com/en/news-and-trade-ideas/_us-dollar-index-fundamental-and-technical-analysis-outlook-for--241129)). API access needs an IG account and API key ([IG API overview](https://www.ig.com/en/trading-platforms/trading-apis)), so expect KYC on a live account.

**Dukascopy.** The bank publishes a free historical export for tick through monthly CSVs ([historical data page](https://www.dukascopy.com/swiss/english/marketwatch/historical/)). The community [dukascopy-python package](https://pypi.org/project/dukascopy-python/4.0.1/) wraps that datafeed with `fetch` for history and `live_fetch` for a generator that keeps returning updated frames. It is not a websocket: aggregated intervals from `fetch` come back delayed, and the package docs tell you to use `live_fetch`, which pulls tick data and reshapes it, for up-to-date bars ([package docs](https://pypi.org/project/dukascopy-python/4.0.1/)). Per-instrument history depth starts at a date the datafeed reports in metadata, so query it rather than trusting a fixed number.

**MetaTrader 5.** The official [MetaTrader 5 Python package](https://www.mql5.com/en/docs/python_metatrader5) reads bars and ticks from a running terminal on Windows only. You get `copy_rates_from` for M1 bars and `copy_ticks_range` for tick history ([copy_rates_from](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py), [copy_ticks_range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)). There is no push stream in the package, so a live loop means polling `symbol_info_tick`. Bar depth is capped by the terminal's "Max bars in chart" setting ([copy_rates_from](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py)). You need a broker account, a demo is enough for data, and symbol coverage including any dollar index symbol depends on the broker's server.

#### Data vendors

**Twelve Data.** Real-time forex is included even on the free Basic tier, but the free tier gets only 8 API credits per minute with an 800 request per day cap and 8 trial websocket credits that work only on trial symbols ([pricing](https://twelvedata.com/pricing.md)). Full websocket access starts at the Pro plan, which begins at $99 per month with 610 API credits and 500 WS credits ([pricing](https://twelvedata.com/pricing), [trial article](https://support.twelvedata.com/en/articles/5335783-trial)). One WS credit is consumed per subscribed symbol, you may hold up to 3 connections, and the vendor asks for a heartbeat message every 10 seconds to keep the socket alive ([credits](https://support.twelvedata.com/en/articles/5615854-credits), [websocket FAQ](https://support.twelvedata.com/en/articles/5194610-websocket-faq), [how to stream](https://support.twelvedata.com/en/articles/5620516-how-to-stream-the-data)). The free license is internal non-display use only ([pricing](https://twelvedata.com/pricing)), and historical depth on Pro is explicitly limited compared to Ultra ([trial article](https://support.twelvedata.com/en/articles/5335783-trial)).

**Massive (formerly Polygon.io).** The Currencies product covers about 1,200 forex pairs and 600 crypto pairs with its own websocket cluster, and the vendor states forex quotes come from institutional bank feeds because FX has no consolidated tape ([currencies page](https://massive.com/currencies), [source FAQ](https://massive.com/knowledge-base/article/where-does-massives-currencies-data-come-from)). The free tier allows 5 API calls per minute ([pricing](https://massive.com/pricing)). I could not read the Currencies tier prices from the official pricing page because it renders tier cards in JavaScript, so treat any specific number as unverified: a third-party 2026 review reads Basic $0 with end-of-day data and Starter at $49 per month for real-time plus websockets ([third-party pricing review](https://aifinhub.io/articles/polygon-io-forex-api-2026/)). Confirm on the pricing page before budgeting.

**Finnhub.** The free tier gives 60 API calls per minute, 50 websocket symbols, and a personal-use license ([pricing](https://finnhub.io/pricing)). The dedicated forex pricing page lists a free Basic tier at 150 calls per minute and a free Standard tier at 300, both personal use, with forex OHLC and websocket rows checked ([forex pricing](https://api2.finnhub.io/pricing-forex-api)). The websocket is a single connection per API key carrying trades and price updates for FX symbols, and for venues that do not stream trades it sends a price update with zero volume ([websocket spec](https://raw.githubusercontent.com/api-evangelist/finnhub/refs/heads/main/asyncapi/finnhub-asyncapi.yml)). Rate limits are documented separately ([limits](https://finnhub.io/docs/api/rate-limit)), and FX coverage includes reference rates across currencies ([forex rates docs](https://finnhub.io/docs/api/forex-rates)). I could not confirm XAUUSD or a DXY symbol in Finnhub's FX docs, and the All-In-One plan is listed at $3,500 per month, billed annually ([pricing](https://finnhub.io/pricing)).

**Tiingo.** This is the strongest free streaming candidate. The FX websocket pushes top-of-book updates on every change, sourced from raw binary feeds to tier-1 banks and FX dark pools, and Tiingo states its Free, Power, Commercial, and Redistribution plans all get firehose access ([FX websocket docs](https://www.tiingo.com/documentation/websockets/forex)). The REST side gives top-of-book snapshots and historical intraday bars with a minimum resample frequency of 1 minute, across 140+ pairs ([FX docs](https://www.tiingo.com/documentation/forex)). The free Starter plan allows 500 unique symbols per month, 50 requests per hour, 1,000 per day, and 1 GB bandwidth, licensed internal use only ([pricing](https://www.tiingo.com/about/pricing)). Power is $30 per month, Commercial $50 per month for internal commercial use, and FX history runs back to 2020 ([Tiingo FX post](https://www.tiingo.com/blog/forex-api/)).

**Databento.** Spot FX coverage is 100+ pairs from 40+ liquidity providers, with nanosecond timestamps captured at colocation sites ([tick data page](https://databento.com/tick-data)). Pricing is metered by uncompressed gigabyte for historical pulls, live data adds pass-through license fees plus per-message billing, and you must complete a license questionnaire for live ([pricing](https://databento.com/pricing), [historical API basics](https://databento.com/docs/api-reference-historical)). I could not find a published spot-FX price or a DXY symbol, so request an estimate through the data catalog before committing. This is institutional-grade and heavier than a 3-symbol paper loop needs.

**dxFeed.** Streaming goes through dxLink over websocket, and access requires a sales-approved trial or subscription with entitlements in a welcome email ([dxLink docs](https://kb.dxfeed.com/en/market-data-api/dxlink.html), [getting started](https://kb.dxfeed.com/en/getting-started.html)). No public pricing is published. dxFeed describes its ticker delivery as the most recent known data with minimal delay and quotes typical API throughput around 100,000 updates per second ([FAQ](https://kb.dxfeed.com/en/faq.html)). Critically for this repo, dxFeed's retail eShop onboarding states that algo-trading, real-time data streaming, and real-time data export are strongly prohibited in that environment and must be deactivated ([retail eShop onboarding](https://kb.dxfeed.com/en/dxfeed-retail/retail-eshop-onboarding-guide.html)). Read that as a hard filter: the retail channel is not licensed for this use case.

### Latency and quality

"Real-time" in retail FX means something weaker than an exchange feed, and the differences are documented rather than hidden.

First, retail FX prices are aggregated and account-specific, not a consolidated tape. OANDA's stream is a per-account price with liquidity buckets, and the vendor states it sends at most 4 prices per instrument per second, with window alignment differing between connections, so two subscribers can observe different prices in the same fast market ([pricing endpoint docs](https://developer.oanda.com/rest-live-v20/pricing-ep/)). That 250ms conflation is the single most concrete latency number published by any broker API I checked.

Second, quote arrival is fast but execution still runs through last look. Tier-1 banks publish FX Global Code disclosures with response times in single-digit to low-double-digit milliseconds: Citi reports spot FX responses between 0 and 15 milliseconds at the 99th percentile ([Citi disclosure](https://www.citigroup.com/rcs/citigpa/storage/public/icpublic/citi-liquidity-provider-disclosure-cover-sheet-FINAL.pdf)), Goldman Sachs reports pre-trade review generally under 10 milliseconds ([Goldman Sachs disclosure](https://www.goldmansachs.com/disclosures/FX-Global-Code-Liquidity-Provider-DisclosureCover-Sheet.pdf)). A published industry paper notes the fastest FX venue feeds update roughly every 5 milliseconds while some liquidity providers historically applied hold times up to 200 milliseconds ([XTX Markets last look paper](https://www.datocms-assets.com/10954/1621331501-xtx-ll-paper-pbc-finf-may-2021.pdf)). Vendors publish their own numbers too: Twelve Data claims about 170ms websocket streaming ([Twelve Data stocks page](https://twelvedata.com/stocks)), and Tiingo says it forwards FX updates before writing them to its own database from machines about 15 miles from the NY5 datacenter ([FX websocket docs](https://www.tiingo.com/documentation/websockets/forex)).

Third, what actually matters for this strategy. A 1-minute VWAP band compares a price to a band built from the current session, so what you need is one clean, timestamped, session-aligned series per minute, and a stable mid or top-of-book for entry checks. Hundred-millisecond differences do not change a 1-minute band. What does change results:

- Choosing bid, ask, or mid. This one is arithmetic rather than vendor behavior: a VWAP built on mid and a VWAP built on broker bid differ by about half the spread, so fix one convention and use it in both the backtest and the live loop, and remember broker streams are account-specific rather than a consolidated tape ([OANDA pricing docs](https://developer.oanda.com/rest-live-v20/pricing-ep/)).
- Mixing feeds across symbols, for example EURUSD from one vendor and DXY computed from another vendor's six pairs, which puts timestamps and session breaks out of alignment.
- Misreading DXY timing, because ICE recalculates and publishes the index every 1 second and uses component midpoints ([ICE methodology](https://www.ice.com/publicdocs/nyse/indices/ICE_FX_Indexes_Methodology.pdf)), so a DXY series sampled from a slower source will lag the FX legs by construction.
- Weekend and session gaps, since FX runs continuously from Sunday evening to Friday evening with no daily close ([Massive currencies FAQ](https://massive.com/knowledge-base/article/what-are-the-forex-market-hours)) and Tiingo's FX market hours run 8pm Sunday to 5pm EST Friday ([FX docs](https://www.tiingo.com/documentation/forex)).
- MT5 timestamp confusion. The official docs say bar and tick times come back in UTC with no shift ([copy_rates_from](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py)), while community wrappers report that those epochs are actually the trade server's wall clock, typically UTC+2 or UTC+3 ([pdmt5 docs](https://dceoy.github.io/pdmt5/)). If your VWAP session buckets are cut on a two-hour-slid clock, bands will not match a vendor feed, so normalize MT5 timestamps explicitly before comparing series.

### Historical backfill vs live quotes

These are two different products and the cheapest source for one is often not the cheapest for the other.

**Backfill, 1-minute bars and daily DXY.** For EURUSD and XAUUSD, Dukascopy's free export gives tick and 1-minute history with no API key and no request metering ([historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/)), and OANDA's candles endpoint gives 1-minute bid, ask, or mid bars back to 2005 through a practice account ([introduction](https://developer.oanda.com/rest-live-v20/introduction/), [api comparison](https://developer.oanda.com/rest-live-v20/api-comparison/)). Twelve Data's free tier can serve modest backfills at 800 requests per day, with depth explicitly limited on lower tiers ([pricing](https://twelvedata.com/pricing.md), [trial article](https://support.twelvedata.com/en/articles/5335783-trial)). For DXY daily bars, either pull an unofficial index ticker from yfinance or compute the index from the six component pairs yourself, since the formula, the constant 50.14348112, and the weights (EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%) are published by ICE ([ICE methodology](https://www.ice.com/publicdocs/nyse/indices/ICE_FX_Indexes_Methodology.pdf)).

**Live quotes for a paper loop.** You need roughly one to a few dozen messages per second across three symbols, which almost every option can carry. Tiingo's free FX firehose is the cheapest credible vendor path because it delivers real top-of-book over websocket with a published free-tier limit that 3 to 6 pairs fit inside ([FX websocket docs](https://www.tiingo.com/documentation/websockets/forex), [pricing](https://www.tiingo.com/about/pricing)). OANDA's practice pricing stream is the cheapest broker path, with BBO plus liquidity and no separate data fee ([pricing endpoint docs](https://developer.oanda.com/rest-live-v20/pricing-ep/)). Twelve Data's free tier will not carry production streaming, since its websocket credits on free are trial-only on trial symbols ([trial article](https://support.twelvedata.com/en/articles/5335783-trial)).

#### What yfinance can and cannot do

yfinance cannot stream. It has no websocket and no subscription API, only HTTP polling of Yahoo's chart endpoint, which is why the repo's staging job is a poller by design ([yfinance download docstring](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)). Three constraints matter:

- History caps. Yahoo refuses intraday ranges beyond the last 60 days, and 1-minute data is tighter still at about 30 days, which matches the repo's observed 29-day 1m cap ([yfinance docstring](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html), [issue discussion of the 30-day 1m window](https://github.com/ranaroussi/yfinance/issues/2451)).
- Reliability. Yahoo rate-limits with 429 responses that yfinance surfaces as `YFRateLimitError`, and the project has repeatedly added TLS fingerprint impersonation and cookie workarounds to cope ([rate limit issue](https://github.com/ranaroussi/yfinance/issues/2422), [curl_cffi pull request](https://github.com/ranaroussi/yfinance/pull/2430)). In February 2026 users reported intraday history collapsing to roughly the last day for a period, with no SLA behind any of it ([intraday outage issue](https://github.com/ranaroussi/yfinance/issues/2706)).
- Licensing. Yahoo's terms do not grant a commercial data license, and yfinance is an unofficial scraper, so treat it as a convenience for daily bars and demos, not as the backbone of a live loop.

### Recommendation

Cheapest credible path for these three symbols only, split by job.

**(a) Daily and 1-minute historical backfill.**

1. Use Dukascopy's free export for deep EURUSD and XAUUSD tick or 1-minute history, pulled once and stored locally, since there is no API key, no metered cost, and no daily quota ([historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/), [dukascopy-python](https://pypi.org/project/dukascopy-python/4.0.1/)).
2. Cross-check the first year against an OANDA practice account's 1-minute candles back to 2005, which also gives you a second feed identity to compare against ([introduction](https://developer.oanda.com/rest-live-v20/introduction/)).
3. Keep yfinance only for daily bars, including any dollar index ticker, where the 1-minute and 60-day intraday caps do not bite ([yfinance docstring](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)).
4. Build DXY 1-minute bars yourself from the six component pairs using the ICE published formula and weights, then label the series a synthetic DXY proxy rather than the official index ([ICE methodology](https://www.ice.com/publicdocs/nyse/indices/ICE_FX_Indexes_Methodology.pdf)).

**(b) Near-real-time paper-trading quotes.**

1. Start with Tiingo's free tier: real FX top-of-book over websocket, 3 to 6 pairs, no KYC beyond an email signup, and a published limit your symbol count fits inside ([FX websocket docs](https://www.tiingo.com/documentation/websockets/forex), [pricing](https://www.tiingo.com/about/pricing)). Total cost: $0. If you later need commercial internal use, budget $50 per month for the Commercial tier ([pricing](https://www.tiingo.com/about/pricing)).
2. If you want broker-grade BBO with liquidity sizes and a straight line to eventual live execution, open an OANDA practice account and consume the pricing stream at 4 prices per instrument per second, total cost $0 while practicing ([pricing endpoint docs](https://developer.oanda.com/rest-live-v20/pricing-ep/)).
3. Keep Twelve Data free as a REST fallback for snapshots and small bar pulls, and upgrade only if you outgrow the 800 per day cap, at which point budget from $99 per month for Pro with real websocket access ([pricing](https://twelvedata.com/pricing)).
4. Skip dxFeed for this repo, because its retail channel explicitly prohibits algo-trading and real-time export ([retail eShop onboarding](https://kb.dxfeed.com/en/dxfeed-retail/retail-eshop-onboarding-guide.html)), and skip Databento until you have a measured need, since its metered live FX requires license pass-through and a sales questionnaire ([pricing](https://databento.com/pricing)).
5. Whatever you pick, feed all three symbols from one provider where possible, stamp every bar in UTC with the vendor's own event time, and record whether the series is bid, ask, mid, or top-of-book, because that choice is what makes backtest and paper-trading bands comparable.

### Sources

1. https://developer.oanda.com/rest-live-v20/pricing-ep/
2. https://developer.oanda.com/rest-live-v20/pricing-df/
3. https://developer.oanda.com/rest-live-v20/api-comparison/
4. https://developer.oanda.com/rest-live-v20/introduction/
5. https://developer.oanda.com/rest-live-v20/development-guide/
6. https://help.oanda.com/us/en/faqs/find-gold-and-silver.htm
7. https://www.oanda.com/us-en/trading/instruments/
8. https://labs.ig.com/streaming-api-guide.html
9. https://labs.ig.com/rest-trading-api-reference.html
10. https://www.ig.com/en/trading-platforms/trading-apis/how-to-use-ig-api
11. https://www.ig.com/en/trading-platforms/trading-apis
12. https://www.ig.com/en/news-and-trade-ideas/_us-dollar-index-fundamental-and-technical-analysis-outlook-for--241129
13. https://www.mql5.com/en/docs/python_metatrader5
14. https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py
15. https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py
16. https://www.dukascopy.com/swiss/english/marketwatch/historical/
17. https://pypi.org/project/dukascopy-python/4.0.1/
18. https://twelvedata.com/pricing
19. https://twelvedata.com/pricing.md
20. https://support.twelvedata.com/en/articles/5615854-credits
21. https://support.twelvedata.com/en/articles/5194610-websocket-faq
22. https://support.twelvedata.com/en/articles/5620516-how-to-stream-the-data
23. https://support.twelvedata.com/en/articles/5335783-trial
24. https://twelvedata.com/stocks
25. https://massive.com/pricing
26. https://massive.com/currencies
27. https://massive.com/knowledge-base/article/what-are-the-forex-market-hours
28. https://massive.com/knowledge-base/article/where-does-massives-currencies-data-come-from
29. https://aifinhub.io/articles/polygon-io-forex-api-2026/
30. https://finnhub.io/pricing
31. https://api2.finnhub.io/pricing-forex-api
32. https://finnhub.io/docs/api/rate-limit
33. https://finnhub.io/docs/api/forex-rates
34. https://raw.githubusercontent.com/api-evangelist/finnhub/refs/heads/main/asyncapi/finnhub-asyncapi.yml
35. https://www.tiingo.com/documentation/forex
36. https://www.tiingo.com/documentation/websockets/forex
37. https://www.tiingo.com/about/pricing
38. https://www.tiingo.com/blog/forex-api/
39. https://databento.com/pricing
40. https://databento.com/docs/api-reference-historical
41. https://databento.com/tick-data
42. https://kb.dxfeed.com/en/getting-started.html
43. https://kb.dxfeed.com/en/market-data-api/dxlink.html
44. https://kb.dxfeed.com/en/dxfeed-retail/retail-eshop-onboarding-guide.html
45. https://kb.dxfeed.com/en/faq.html
46. https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
47. https://github.com/ranaroussi/yfinance/issues/2451
48. https://github.com/ranaroussi/yfinance/issues/2422
49. https://github.com/ranaroussi/yfinance/issues/2706
50. https://github.com/ranaroussi/yfinance/pull/2430
51. https://www.ice.com/publicdocs/nyse/indices/ICE_FX_Indexes_Methodology.pdf
52. https://developer.ice.com/fixed-income-data-services/catalog/ice-data-indices-currency-indices
53. https://www.citigroup.com/rcs/citigpa/storage/public/icpublic/citi-liquidity-provider-disclosure-cover-sheet-FINAL.pdf
54. https://www.goldmansachs.com/disclosures/FX-Global-Code-Liquidity-Provider-DisclosureCover-Sheet.pdf
55. https://www.datocms-assets.com/10954/1621331501-xtx-ll-paper-pbc-finf-may-2021.pdf
56. https://dceoy.github.io/pdmt5/

## VWAP band entry design

### Anchoring evidence

- CME Globex defines its trading day as 17:00 ET to 17:00 ET with a 60-minute daily maintenance break starting at 17:00 ET, so a 17:00 New York anchor (21:00 UTC during US daylight saving, 22:00 UTC in winter) matches the exchange's own daily reset for GC futures (primary, CFTC filing): https://www.cftc.gov/filings/ptc/ptc032720comexdcm001.pdf
- Spot FX rolls at 17:00 New York time, an interbank convention, but the fixed-clock UTC value shifts between 21:00 and 22:00 with US DST, so a hardcoded 21:00 UTC anchor drifts against the FX day for part of the year (practitioner blog): https://forexmechanics.com/trading-hours/24-hour-market/
- Practitioner guidance on anchor choice argues a level only matters if counterparties measure it the same way, and recommends the cash-session open over the electronic open because overnight volume distorts the line (practitioner blog): https://traderprofesional.com/en/vwap-settings/
- TradingView's VWAP documentation lists Session, Week, Month, Quarter, Year, Decade, Century, Earnings, Dividends and Splits anchors and states the anchor period must be higher than the chart timeframe (platform doc): https://in.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/
- TradingView VWAP Auto Anchored adds Session/Week/Month anchors tied to the last completed period, and the docs describe Session as starting at the beginning of the last daily session (platform doc): https://www.tradingview.com/support/solutions/43000652199-vwap-auto-anchored/
- thinkorswim's VWAP study resets on a DAY/WEEK/MONTH time frame input and defines bands as standard deviations of the difference between price and VWAP (platform doc): https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/V-Z/VWAP
- Sierra Chart defines the VWAP variance as sqrt(sum(V*(X-VWAP)^2)/sum(V)) with four multiplier bands, which is the same formula family this repo implements (platform doc): https://www.sierrachart.com/index.php?ID=108&Name=Volume_Weighted_Average_Price_-_VWAP_-_with_Standard_Deviation_Lines&page=doc%2FStudiesReference.php
- Session-anchored VWAP scripts plot separate Sydney, London, Tokyo and New York anchors with custom UTC start hours, so London 07:00/08:00 and NY 13:30 anchors are configuration, not new math (TradingView script): https://www.tradingview.com/script/zNkHo9a3-VWAP-Market-Session-Anchored/
- Gold publishes two intraday reference events: LBMA gold auctions start at 10:30 and 15:00 London time (primary, ICE/LBMA): https://www.lbma.org.uk/prices-and-data/lbma-precious-metal-prices
- COMEX settles active-month gold in the 13:29 to 13:30 ET window, using the VWAP of trades in that minute when any exist (primary, CME wiki): https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457088147/Gold
- No published head-to-head test of 21:00 UTC versus 00:00 UTC versus weekly anchors for intraday mean reversion on EURUSD and XAUUSD surfaced in this research; any claim that one anchor is more reliable for FX reversion is unverified.
- Intraday volatility is U-shaped with spikes at session opens and closes, and FX shows the same periodicity, which argues against anchoring or trading in the thinnest boundary minutes (academic): https://finance.martinsewell.com/stylized-facts/volatility/AndersenBollerslev1997b.pdf
- The dollar appreciates into FX fixes and depreciates after, with pervasive, statistically significant reversals for the top nine currencies over 21 years (academic, Journal of Finance): https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/jofi.13306

### Band-touch trade design

- A friction-modeled vendor backtest of enter-at-2-sigma on a confirmation candle, target VWAP, stop at 3-sigma (about 2:1 reward-to-risk) reports 47.1% win rate over n=760 trades with profit factor 1.40 on ES, Jan 2023 to Mar 2026 (tool-vendor backtest): https://pinescriptforge.com/ES/vwap-deviation/backtest
- The same rules report 53.5% over n=570 with profit factor 1.71 on NQ, 48.6% over n=798 with profit factor 1.14 on M2K, and 44.2% over n=532 with profit factor 1.39 on FDAX, all with $4.50 round-turn commission and 1-tick slippage (tool-vendor backtests): https://pinescriptforge.com/nq/vwap-deviation/backtest
- A practitioner blog attributes to a 2022 QuantConnect study a 63% win rate shorting the upper 2-sigma band at 1.5:1 average reward-to-risk across 100 liquid NASDAQ stocks, 61% at 1.4:1 on the lower band, and about 71% at the 3-sigma band; the primary study is not linked, so treat it as unverified (practitioner blog): https://www.tradealgo.com/trading-guides/technical-analysis/vwap-trading-strategy-the-institutional-benchmark-every-trader-should-know
- A practitioner claims its own SPY test over 180 sessions produced 64% win rate with 1.8:1 average reward-to-risk on band-2 bounces and 71% on band-3 touches; sample conditions and code are unverified (practitioner blog): https://pineify.app/resources/blog/vwap-stdev-bands-v2-indicator-tradingview-pine-script
- A practitioner reversion template claims 55-65% win rate with ADX, news and session filters versus about 45% without, average winner 0.8-1.2 times average loser, profit factor 1.2-1.6; unverified (practitioner blog): https://crosstrade.io/learn/trading-strategies/vwap-reversion
- Platform education says to enter on the rejection candle rather than the band touch itself, with the stop on the other side of the band and the target at VWAP or the session extreme (practitioner education): https://tradeology.app/academy/day-trading/vwap-trading-strategy
- The same practitioner page states that the most common error is entering at a VWAP or band touch before a rejection bar prints (practitioner education): https://tradeology.app/academy/day-trading/vwap-trading-strategy
- Broker education frames the band trade as short above 2+ standard deviations on a reversal candle or oscillator divergence with target back at VWAP, using HLC3 as the standard source (broker education): https://www.thinkmarkets.com/en/trading-academy/indicators-and-patterns/volume-weighted-average-price-vwap-indicator-trading-guide/
- The 1-sigma band sits inside the roughly 68% value area, so a practitioner band table rates 1-sigma reversion at only 55-58% and calls 1-sigma best for trend continuation, versus 61-63% at 2-sigma (unverified, practitioner blog): https://www.tradealgo.com/trading-guides/technical-analysis/vwap-trading-strategy-the-institutional-benchmark-every-trader-should-know
- Stop placement options with published rules: stop beyond the 3-sigma band (2-sigma entry, VWAP target gives about 2:1) or 1 ATR beyond the trigger bar (practitioner blogs): https://crosstrade.io/learn/trading-strategies/vwap-reversion
- Practitioner material warns that price riding a band without reverting marks a strong trend, and fading band-walks is the standard failure mode (practitioner): https://www.tradesafeai.io/modules/vwap

### Confluence filters that raise hit rate

- An SSRN preprint conditions intraday FX mean reversion on higher-timeframe momentum states because reversion deteriorates in persistent trending regimes, which is direct support for gating band trades on the daily regime label (academic preprint): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6087107
- A practitioner template hard-skips reversion entries when ADX exceeds 25 and claims the regime screen is what separates 55-65% from about 45% (unverified, practitioner blog): https://crosstrade.io/learn/trading-strategies/vwap-reversion
- A second anchor is native: thinkorswim VWAP takes DAY/WEEK/MONTH resets, so prior-day and weekly VWAP need no custom code (platform doc): https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/V-Z/VWAP
- A TradingView script fires buy signals only when price sits above both start-of-day and previous-day VWAP, and sell signals only below both, implementing dual-anchor agreement by default (TradingView script): https://www.tradingview.com/script/L1plSdoS-Anchored-VWAP-Pro-Multi-Timeframe-Analysis-Tool/
- Practitioner education treats confluence between weekly or monthly VWAP and intraday levels as the higher-probability version of the single-anchor trade (practitioner education): https://chartchampions.com/vwap-and-anchored-vwap/
- Deeper sigma raises claimed reversion probability while cutting frequency: 1-sigma 55-58%, 2-sigma 61-63%, 3-sigma 69-71% (unverified, practitioner blog): https://www.tradealgo.com/trading-guides/technical-analysis/vwap-trading-strategy-the-institutional-benchmark-every-trader-should-know
- ATR-scaled risk appears in published rules as 1 ATR beyond the trigger for the stop, or 2-3 ATR beyond the band, instead of fixed pips (practitioner blogs): https://tradingonramp.com/my-intraday-vwap-bands-trading-strategy/
- A killzone study buffers invalidation 1.2 to 1.5 times the 15-minute ATR and reports that the final 45 minutes of a session produce up to 60% less directional displacement than the opening 90 minutes (practitioner blog, unverified): https://fxnx.com/en/blog/ict-killzone-volatility-data-average-range-session-hour
- OANDA data show 37% of daily FX volume occurs in the London-New York overlap though it is 19% of the trading day, with EUR/USD at 41% (broker education): https://www.oanda.com/us-en/skills-and-insights/education/market-timing-and-volatility/when-to-trade/best-time-to-trade-forex-volume-insights/
- OANDA also shows volume highest at 08:30-09:00 New York time (7% of the day in a half hour) and lowest 17:00-18:00, which supports skipping the first and last minutes of the UTC day (broker education): https://www.oanda.com/us-en/skills-and-insights/education/market-timing-and-volatility/when-to-trade/best-time-to-trade-forex-volume-insights/
- A practitioner measurement finds the 13:00-16:00 UTC overlap averages 25.92 pips against 21.45 for London alone, 1.21x, over N=432,411 pair-hour observations (practitioner research with sample size): https://fxeresearch.substack.com/p/session-volatility-in-fx-where-price
- Spreads widen for one to three minutes at the 21:00-22:00 UTC rollover and quotes can drop, so a band touch in that window measures execution noise, not value (practitioner blogs): https://brokerchampion.com/trading-forex-hours
- Academic time-of-day evidence shows currencies depreciate during their local trading hours with matching order-flow patterns across 10 years of high-frequency data (academic working paper): https://www.snb.ch/public/asset/de/www-snb-ch/publications/research/working-papers/2011/working_paper_2011_04/publications0/working_paper_2011_04.n.pdf
- Intraday mean reversion itself is documented in 1-minute EURUSD data, where ADF rejects the unit root and deviations from the moving average average near zero (academic): https://doi.org/10.1515/foli-2015-0014

### Why high win rate can still lose money

- Break-even win rate equals 1/(1+R): at 0.5R average winners against 1R losers you need more than 66.7% before costs, so a target at VWAP from 1-sigma with a 3-sigma stop (0.33:1 reward-to-risk) would need about 75% to break even (practitioner math, unverified against your data): https://chartmini.com/blog/risk-reward-ratio-explained
- Expectancy equals win rate times average win minus loss rate times average loss, so a 90% win rate with 0.2R winners and 2R losers yields -0.02R per trade (practitioner worked example): https://curvedtrading.com/articles/en/trading/expected-value-trading-high-win-rate-losing-money/
- A 70% win rate with 0.8R average winners and 1.5R average losers produces -0.09R per trade, or -9R over 100 trades before costs (practitioner table): https://completetradersedge.com/trading-expectancy-formula/
- The mirror case appears in the vendor backtests: sub-50% hit rates still show 1.14 to 1.71 profit factors because winners run 2R to VWAP and stops lose 1R at 3-sigma (tool-vendor backtests with n): https://pinescriptforge.com/nq/vwap-deviation/backtest
- The disposition effect documents taking winners early and holding losers, the behavioral path that manufactures high win rates with negative expectancy (academic, Shefrin and Statman 1985): https://doi.org/10.1016/0304-405X(85)90119-0
- Costs bite low-reward targets hardest: a 0.25R target paying 0.05R round-trip costs gives up 20% of gross, while a 2R target gives up 2.5% (practitioner illustration): https://traderizz.com/guides/negative-risk-reward-ratio-trading
- Win rate and reward-to-risk pull against each other, so pushing the target to VWAP from deeper bands raises reward-to-risk but lowers the hit rate you can realistically hold (practitioner analysis): https://daytradingtoolkit.com/beginners-guide/win-rate-vs-risk-reward-expectancy

### Highest lift candidates

1. Replace raw 1-sigma touches with a 2-sigma-or-deeper extension plus a rejection bar: the 1-sigma zone is value-area (about 68% coverage) and rated 55-58% reversion, while published 2-sigma designs with a confirmation candle cluster at 44.2% to 53.5% over n=532 to 798 at roughly 2:1 reward-to-risk, and pure-touch entries are called out as the common failure (evidence: https://pinescriptforge.com/ES/vwap-deviation/backtest and https://crosstrade.io/learn/trading-strategies/vwap-reversion).
2. Gate signals on daily regime agreement, skipping band fades when the regime label says high_vol or a conflicting trend: an SSRN preprint shows FX reversion deteriorates in trending regimes, and a practitioner template claims the ADX gate separates 55-65% from about 45% (evidence: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6087107 and https://crosstrade.io/learn/trading-strategies/vwap-reversion).
3. Trade only the 13:00-17:00 UTC London-New York overlap and skip boundary minutes and the 21:00-22:00 UTC rollover: OANDA measures 37% of daily volume in the overlap, an N=432,411 study finds a 1.21x range premium, and academic work shows U-shaped volatility spikes at session edges (evidence: https://www.oanda.com/us-en/skills-and-insights/education/market-timing-and-volatility/when-to-trade/best-time-to-trade-forex-volume-insights/ and https://fxeresearch.substack.com/p/session-volatility-in-fx-where-price).

## Filters that remove losing trades

### Regime filter evidence

- Ang and Timmermann estimate a two-state model on monthly FX excess returns: the high-volatility state averages 0.46% per month versus 0.01% per month in the low-volatility state, so for FX the high-volatility state is not the low-return state ([NBER w17182, PDF](https://www.nber.org/system/files/working_papers/w17182/w17182.pdf)).
- The same estimates say regimes are mostly identified by volatility: the authors cannot reject equal regime means but overwhelmingly reject equal regime variances, and both persistence parameters p00 and p11 are close to one ([NBER w17182, PDF](https://www.nber.org/system/files/working_papers/w17182/w17182.pdf)).
- For equities the opposite sign holds: the high-volatility regime has low average returns, and regime switching alone implies skew of -0.09, kurtosis of 3.62, and first-order autocorrelation of squared returns of 0.77 ([NBER w17182, PDF](https://www.nber.org/system/files/working_papers/w17182/w17182.pdf)).
- The review cites Henkel, Martin and Nardari showing return predictability is very weak in expansions and very strong in recessions, so a regime gate changes which predictor works, not just trade count ([NBER w17182, PDF](https://www.nber.org/system/files/working_papers/w17182/w17182.pdf)).
- Ang, Bekaert and Wei report a stable probability above 70% for the calm state in their four-state term structure model, which is the order of magnitude a four-state channel must beat to add information ([NBER w17182, PDF](https://www.nber.org/system/files/working_papers/w17182/w17182.pdf)).
- No peer-reviewed study reports a hit-rate delta for ADX(14)>=25 or an ATR percentile cut on EURUSD; a vendor page claims an ADX>25 filter lifts EUR/USD trend-strategy win rates "by over 25%" without showing sample or code (practitioner marketing) ([fazencapital](https://fazencapital.com/learn/en/adx-indicator-trend-strength)).
- A practitioner threshold study varies ADX entry level and lookback but publishes no win-rate table in its summary, so ADX 25 remains a convention from Wilder-era education rather than a published FX result (practitioner backtest site) ([Oxford Strategy](https://oxfordstrat.com/trading-strategies/average-directional-index/)).
- A forum backtest reports a regime filter moved win rate from 32.8% to 32.1% and left drawdown nearly unchanged (forum post, not peer reviewed) ([r/algotrading](https://www.reddit.com/r/algotrading/comments/1skdizm/most_regime_filters_dont_improve_trading/)).
- On the momentum side, the cited Moskowitz, Ooi and Pedersen paper reports all 58 futures with positive 12-month trend returns, 52 significant at 5%, a diversified TSMOM alpha of 1.58% per month (t = 7.99), an annual Sharpe above 1 (about 2.5 times equities), and a currency-forward TSMOM(12,1) alpha t-statistic of 3.41 ([Time series momentum PDF](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf)).
- Their earlier 1966-1985 sample gives an annualized Sharpe of 1.1 out of sample, which is the honest ceiling for trend conditioning, not a 90% hit rate ([Time series momentum PDF](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf)).

### Session and time-of-day filters

- The London-New York overlap 13:00-17:00 UTC is the deep window: broker-education quotes put EURUSD at 0.5-1.5 pips in the overlap against 2-3 pips in the quiet Asian session (broker education) ([startrader](https://www.startrader.com/knowledge-intermediate/forex-spreads-explained-what-they-are-why-they-change/)).
- An ECN desk quotes EURUSD at roughly 0.3-0.8 pips during the overlap and 1.5-2.5 pips in the Asian session (practitioner) ([traderssecondbrain](https://traderssecondbrain.com/guides/which-session-most-profitable)).
- A 2026 vendor study of 25 regulated brokers measured Asian-session spreads 114% wider on average than London-session spreads (vendor spread study) ([fx-brokers.eu](https://fx-brokers.eu/research/spread-study-2026)).
- A broker quotes EURUSD at 0.1-0.5 pips in peak hours with the overlap delivering the highest intraday volume (broker page) ([afterprime](https://afterprime.com/forex/eurusd)).
- Dukascopy publishes per-session average spreads for Asian, European and North American sessions, which is the vendor dataset to pull for an hour-by-hour cost table (vendor docs) ([Dukascopy average spreads](https://www.dukascopy.com/swiss/english/marketwatch/average-spreads/)).
- Academic intraday seasonality work on EBS quotes for USD-JPY and EUR-USD documents strong time-of-day patterns in trading and volatility, so hour fixed effects belong in any hit-rate comparison ([Ito, NBER w12413](https://www.nber.org/system/files/working_papers/w12413/w12413.pdf)).
- Andersen and Bollerslev characterize five-minute DM-dollar volatility with intraday seasonality and macro announcement effects, the standard citation for why 1-minute bars are not identically distributed across hours ([Journal of Finance 1998 PDF](https://public.econ.duke.edu/~boller/Published_Papers/jf_98.pdf)).
- US tier-1 data lands at 08:30 ET, which is 13:30 UTC in summer and 12:30 UTC in winter; the BLS release schedule is the primary source and the UTC offset shifts with daylight saving ([BLS schedule](https://www.bls.gov/schedule/news_release/empms.htm)).
- COMEX gold runs a 60-minute Globex break each day beginning 17:00 ET (22:00 UTC in winter, 23:00 UTC in summer) inside the repo's 22:00-00:00 UTC window, so bars there straddle a hard halt and a reopen ([CME gold futures page](https://www.cmegroup.com/markets/metals/precious/gold.html)).
- CME staff settles gold futures from Globex trading in a defined daily settlement period, another reason not to model the New York close as continuous liquidity ([CME settlement procedure](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457088147/Gold?redirect=%2Ftrading%2Fmetals%2Ffiles%2Fdaily-settlement-procedure-gold-futures.pdf)).
- A 12-month tick-data study of gold hourly ATR shows true range swinging from about $3.00 per hour in the quietest hour to $25.00+ per hour in the strongest (practitioner tick study) ([fxnx hourly ATR](https://fxnx.com/en/blog/xauusd-hourly-atr-table-12-months-tick-data)).
- Broker education puts the gold session break at roughly 22:00-23:00 UTC around the New York close and XAUUSD opening Sunday at 22:00 UTC (broker education) ([tmgm](https://www.tmgm.com/en/academy/trading-academy/gold-trading-hours)).
- Practitioner reading of the intraday shape: mean-reversion entries work best in deep two-way flow (overlap) and worst in the thin Asian hours and the last hour of Friday, where stops get hit by spread, not by direction (practitioner blog) ([bookmap](https://bookmap.com/blog/why-friday-afternoon-matters-more-than-you-think-in-trading)).

### News filters

- The FOMC statement releases at 14:00 ET, 18:00 UTC in summer and 19:00 UTC in winter, with a press conference following on scheduled meetings ([Federal Reserve FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)).
- ECB monetary policy decisions publish at 14:15 CET (12:15 UTC in summer) and the press conference follows, which matches the repo's 12:15/12:45 UTC pair only in summer time ([ECB Governing Council calendar](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html)).
- Ederington and Lee report that prices absorb announcements quickly, with volatility substantially above normal for roughly fifteen minutes and slightly elevated for several hours ([Journal of Finance 1993 abstract](https://ideas.repec.org/a/bla/jfinan/v48y1993i4p1161-91.html)).
- Follow-up work reports price adjustment to major announcements completes in 40-50 seconds while volatility takes about 30 minutes to normalize, which fixes the blackout width at the seconds scale for entries and the minutes scale for exits ([Daigler 1999 summary](https://www.jstor.org/stable/797995)).
- Fed research finds option-implied volatility rises and widens FX bid-ask spreads before announcements, so part of the pre-release loss is paid in spread, not in price ([Wei 1991, Federal Reserve IFDP 409](https://www.federalreserve.gov/pubs/ifdp/1991/409/ifdp409.pdf)).
- Practitioner tick observations around CPI and NFP print EURUSD and GBPUSD spreads widening from 0.6-0.8 pips to 4-6 pips inside 8:29:55 to 8:31:00 ET (practitioner blog) ([fortraders](https://fortraders.com/blog/fundamental-analysis-in-forex-a-beginners-guide)).
- Broker education quotes EURUSD widening from about 1 pip to 10 pips or more on NFP, with requotes and delays (broker education) ([tradetaurex](https://www.tradetaurex.com/forex-insights/forex-news-trading-strategy/)).
- Practitioner monitoring of XAUUSD puts the spread at 0.2-0.5 pips normally and 30-50 pips for the first 10-30 seconds after a major release (practitioner blog) ([pro-scalper](https://www.pro-scalper.com/xauusd-strategies/news-trading-gold)).
- A liquidity-provider vendor describes quote withdrawal and aggregation fixes around NFP as normal design, which is why the spike hits retail fills regardless of broker tier (vendor article) ([b2broker](https://b2broker.com/news/how-nfp-affects-trading/)).
- I found no peer-reviewed or exchange study quantifying what share of a backtest's losses a calendar filter removes; the honest numbers are the fifteen-minute elevated-volatility window and the pip widening above, so the loss share has to be measured on the repo's own trade log.

### Day-of-week and holiday effects

- Gold and FX open thin: XAUUSD trading starts Sunday 22:00 UTC (broker education), consistent with the repo's measured 60 to 120 bars in the first session segment ([tmgm](https://www.tmgm.com/en/academy/trading-academy/gold-trading-hours)).
- CME's published hours show the Sunday-to-Friday Globex session with a daily 60-minute break, so the week opens into a market that just came out of a halt ([CME trading hours](https://www.cmegroup.com/trading-hours.html)).
- Day-of-week volatility in FX is real and currency specific: one study finds the highest volatility on Mondays in Germany and Japan, on Fridays in Canada and the United States, and on Thursdays in the United Kingdom ([Santillan Salgado 2019](http://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S1665-53462019000500485)).
- A GARCH day-of-week study on the Turkish lira finds significant weekday differences in both depreciation and its volatility, so a single global day rule will mislead ([Berument and Kaplanslan](https://repository.bilkent.edu.tr/bitstreams/fc762214-bc77-4175-9df4-15458be5caae/download)).
- Hilliard and Tucker find significantly lower spot returns on Tuesday and Friday afternoons, the classic intraday weekday drag on afternoon mean reversion (book chapter) ([Emerald chapter](https://www.emerald.com/books/edited-volume/15901/chapter/87558163/DOES_THE_DAY-OF-THE-WEEK-EFFECT_IN_FOREIGN)).
- Dissecting Monday returns, Pigorsch shows the reversal shows up in the afternoon rather than the morning, which argues against a blanket Monday ban (equity data, Finance Research Letters) ([Pigorsch 2024](https://www.sciencedirect.com/science/article/pii/S1544612324005555)).
- Holiday liquidity decays measurably: a year-end study reports consistent liquidity declines from late November through early January (practitioner research) ([Russell Investments](https://russellinvestments.com/content/ri/us/en/insights/russell-research/2025/11/holiday-trading-effect.html)).
- Friday afternoon is flagged by practitioners as thin, erratic order flow after the New York lunch, matching the repo's intention to decay filters late Friday (practitioner blog) ([bookmap](https://bookmap.com/blog/why-friday-afternoon-matters-more-than-you-think-in-trading)).

### Filter stack to test first

1. News blackout: no entries from 15 minutes before to 15 minutes after 13:30 UTC (12:30 UTC winter) tier-1 data, plus FOMC at 18:00 UTC and ECB at 12:15/12:45 UTC, because volatility stays elevated about fifteen minutes and spreads print 4-6 pips on EURUSD and 30-50 pips on gold in that window ([Ederington and Lee 1993](https://ideas.repec.org/a/bla/jfinan/v48y1993i4p1161-91.html), [fortraders](https://fortraders.com/blog/fundamental-analysis-in-forex-a-beginners-guide), [pro-scalper](https://www.pro-scalper.com/xauusd-strategies/news-trading-gold)).
2. Session gate: allow entries only in the 13:00-17:00 UTC overlap, where EURUSD prints 0.3-0.8 to 0.5-1.5 pips against 1.5-2.5 pips in Asia, and Asian spreads measure 114% wider than London in a 25-broker study ([traderssecondbrain](https://traderssecondbrain.com/guides/which-session-most-profitable), [startrader](https://www.startrader.com/knowledge-intermediate/forex-spreads-explained-what-they-are-why-they-change/), [fx-brokers.eu](https://fx-brokers.eu/research/spread-study-2026)).
3. Halt and re-open block: exclude 22:00-00:00 UTC for gold while the 60-minute Globex break and the reopen sit inside that window, and exclude the first hour of the Sunday session that the repo already measured at 60 to 120 bars ([CME gold futures](https://www.cmegroup.com/markets/metals/precious/gold.html), [tmgm](https://www.tmgm.com/en/academy/trading-academy/gold-trading-hours)).
4. Regime gate with an FX sign check: condition entries on the four-state channel, but test rather than assume that high_vol is bad, because Ang and Timmermann estimate 0.46% per month in the high-volatility FX state against 0.01% in the low-volatility state, and a forum replication shows an untested regime filter moving win rate only 32.8% to 32.1% ([NBER w17182](https://www.nber.org/system/files/working_papers/w17182/w17182.pdf), [r/algotrading](https://www.reddit.com/r/algotrading/comments/1skdizm/most_regime_filters_dont_improve_trading/)).
5. Day and holiday gate: drop Friday after 17:00 UTC, drop year-end holiday sessions, and split Monday into morning and afternoon, since weekday volatility differs by currency, Friday afternoons show lower spot returns, and the Monday reversal lands in the afternoon ([Santillan Salgado 2019](http://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S1665-53462019000500485), [Emerald chapter](https://www.emerald.com/books/edited-volume/15901/chapter/87558163/DOES_THE_DAY-OF-THE-WEEK-EFFECT_IN_FOREIGN), [Pigorsch 2024](https://www.sciencedirect.com/science/article/pii/S1544612324005555)).

## What a win rate must be measured against

### Win rate is not expectancy
- Expectancy per trade: `E = p * avg_win - (1 - p) * avg_loss`, so win rate `p` is one factor of two, never the metric, per QuantConnect's definition of expected return per trade [link](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/glossary).
- A 90% win rate with a 10:1 adverse payoff yields `0.9 * 1 - 0.1 * 10 = -0.10` units per trade, a losing system with a great-looking hit rate, arithmetic you can check yourself [link](https://alvarezquanttrading.com/blog/category/mean-reversion/).
- Break-even win rate is `p* = 1 / (1 + payoff_ratio)`, so 10:1 adverse payoff needs 90.9% just to scratch, and spread plus slippage pushes the bar higher still [link](https://www.quantifiedstrategies.com/profitable-trading-strategies/).
- Lopez de Prado and Bailey treat selection bias and non-normality as the two sources that inflate any reported performance statistic, including hit rate [link](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551).
- Report expectancy in R-multiples alongside win rate, because payoff asymmetry is where high-win-rate systems quietly die [link](https://www.thinkcapital.com/trading-strategies/).

### How high win rates get manufactured
- Grid and martingale buy until the market turns: position size doubles per loss, so 6 consecutive losses expose 64x the first trade's size, and at a 50% base rate a 6-loss run appears about 1.5 times per 100 trades [link](https://capital.com/en-int/learn/trading-strategies/martingale-trading).
- A 2026 peer-reviewed study of ML-augmented grid trading names the "drawdown dichotomy": reporting only balance drawdown hides equity and liquidation risk that the win rate never shows [link](https://www.mdpi.com/1999-4893/19/6/442).
- Grid trading is structurally the same family as this repo's band mean reversion, which Quantpedia states plainly while warning about uncontrolled leverage growth up to 450% in its own example [link](https://quantpedia.com/how-to-build-mean-reversion-strategies-in-currencies/).
- Wide stop plus tiny target, or no stop at all, converts every loss into an open drawdown that only resolves favorably most of the time, which is negative expectancy reversal with the loss deferred, not avoided [link](https://quantpedia.com/a-primer-on-grid-trading-strategy/).
- Martingale is a position-sizing scheme with no forecasting content, so it converts a 50/50 bet into a 90%+ hit rate with unbounded tail loss [link](https://www.ebc.com/forex/martingale-trading-strategy).
- Tail-risk arithmetic: with a 90% win rate, the probability of at least one loss in 50 trades is `1 - 0.9^50 = 99.5%`, so the rare loss is frequent, not rare [link](https://optionalpha.com/blog/probability-theory-how-many-trades-to-be-successful).

### What mean-reversion band strategies actually deliver
- Cesar Alvarez reports mean-reversion winning trades holding steady near 65% across years of equity data, with 3 to 7 day holds, which is the credible band for this strategy family [link](https://alvarezquanttrading.com/blog/how-is-mean-reversion-doing-dead-shrinking-or-doing-just-fine/).
- Alvarez and Connors-style mean reversion is typically described at win rates in the mid-60s, explicitly paired with modest reward-to-risk, per a practitioner interview [link](https://bettersystemtrader.com/127-building-mean-reversion-strategies-with-cesar-alvarez-part-1/).
- Quantpedia's own FX mean-reversion backtest (2007 to 2024) reports Sharpe 0.12 for linear sizing and 0.35 for exponential sizing, and reports no win-rate figure at all, because Sharpe is the claim that survives [link](https://quantpedia.com/how-to-build-mean-reversion-strategies-in-currencies/).
- I found no credible published or peer-reviewed 90% win rate for intraday FX mean reversion with an attached reward-to-risk ratio above 1:1; claims at 90% appear only on video and marketing pages [link](https://lunefi.com/blog/mean-reversion-trading-strategy-2026-backtests-win-rates-risks-hybrid-tips).
- If 90% were real at 1:1 payoff, expectancy is `0.9 - 0.1 = 0.8R` per trade, a result that would be immediately obvious and widely replicated, which is why the absence of such replication is itself evidence [link](https://quantpedia.com/screener/).

### Measurement hygiene before any claim
- Walk-forward analysis re-optimizes in sample and scores out of sample on rolling windows, so a single full-sample backtest never counts as evidence [link](https://en.wikipedia.org/wiki/Walk_forward_optimization).
- A 2025 arXiv framework shows the market norm: strong backtest results fail in live because validation was not genuinely out of sample [link](https://arxiv.org/html/2512.12924v1).
- The Deflated Sharpe Ratio corrects reported Sharpe for number of trials and non-normal returns, so log every parameter combination you tried, not just the winner [link](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
- Probability of Backtest Overfitting (PBO) estimates the chance that your in-sample-optimal configuration underperforms the median out of sample, via combinatorially symmetric cross-validation [link](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
- Harvey and Liu's backtesting work underpins the field's rule that a t-statistic below 3.0 is not a discovery, because multiple testing inflates apparent significance [link](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF).
- Confidence interval for a win rate: `p ± 1.96 * sqrt(p(1-p)/n)` at 95%, so `p = 0.90` over 50 trades gives roughly ±8.3 points, and ±5 points worst case needs `n = 1.96^2 * 0.25 / 0.05^2 = 385` trades [link](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval).
- Use the Wilson interval instead of the Wald interval when win rate is near 90%, because Wald coverage collapses at extreme proportions [link](https://statisticsfundamentals.com/confidence-intervals/wilson-score-interval/).
- A practitioner study of convergence found that even at a true 70% rate, observed win rates swing between 85% and 55% after only 50 trades [link](https://optionalpha.com/blog/probability-theory-how-many-trades-to-be-successful).
- This repo has 29 days of 1-minute history. At one signal per day that is n=29, giving a 95% interval of about ±11 points around an observed 90%, so no win-rate claim is statistically available yet [link](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval).
- Expect live results to degrade versus backtest from spread, slippage and regime shift; practitioner estimates put the commonly seen degradation at 10 to 20% [link](https://www.quantifiedstrategies.com/what-can-you-expect-from-trading-strategy-backtest-when-you-are-trading-it-live/).
- Run paper or forward trading for a predeclared trade count before any capital, and predeclare the metric and threshold so you cannot p-hack the outcome [link](https://www.buildalpha.com/walk-forward-optimization/).
- Log the number of trials behind every result: a strategy picked from N tested variants carries a false-positive rate that the deflated Sharpe Ratio exists to price [link](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551).

### Probability of profit flatters wide payoff systems
- Probability of profit (POP) is the chance of making at least one cent at expiry, so a $0.01 win and a $500 win count identically while a $501 loss is invisible in the metric [link](https://www.tastylive.com/concepts-strategies/probability-of-profit).
- POP rises mechanically as payoff narrows: a credit spread collected at $0.30 on $0.70 risk has break-even win odds of 30% to 70%, so a high POP means a small, frequent win against a large, rare loss [link](https://www.reddit.com/r/options/comments/zml7yg/probability_of_profit_returnrisk/).
- Treat POP as the same vanity metric as raw win rate when reward-to-risk is not reported next to it, which is exactly how wide-stop FX systems sell themselves [link](https://www.macroption.com/good-risk-reward-ratio-options/).
- The metric that cannot be gamed this way is expectancy times payoff minus loss, or equivalently Sharpe and Sortino with tail stats attached [link](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/glossary).

### Honest target for this repo
- Success here is a predeclared out-of-sample win rate of 55% to 65% at reward-to-risk of at least 1:1, measured over a walk-forward stitched equity curve of 300+ trades, giving expectancy of `+0.10R` to `+0.30R` per trade [link](https://alvarezquanttrading.com/blog/how-is-mean-reversion-doing-dead-shrinking-or-doing-just-fine/).
- A defensible claim also requires a Wilson 95% interval whose lower bound stays above 50%, a deflated Sharpe Ratio above the threshold implied by your trial count, and paper-trading confirmation at the same parameters [link](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
- A 90% win rate on 29 days of 1-minute data, on parameters tuned to that same data, is an illusion: it is within noise, untested out of sample, and unpriced for multiple testing [link](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
- A 90% win rate achieved by widening stops or removing stops is not accuracy, it is deferred tail loss, and it will show up as a single trade that gives back months of wins [link](https://www.mdpi.com/1999-4893/19/6/442).
- Report expectancy in R, payoff ratio, max consecutive losses, max drawdown and trade count next to every win rate, or do not report the win rate at all [link](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/glossary).

## From signal to measured result

### Cost reality

- EURUSD ECN round trip costs about 0.8 to 1.0 pips, or $8 to $10 per standard lot, versus 1.0 to 1.6 pips on a standard account, per [Pepperstone costs and fees](https://pepperstone.com/en/trading/costs-and-fees).
- IC Markets lists EURUSD spreads near 0.0 pips on raw accounts plus commission, so the quoted spread alone understates your real cost, per [IC Markets spreads](https://www.ic.com/en/trading-pricing/spreads).
- XAUUSD round-trip cost runs about $17 to $28 per lot depending on account type, per [compareforexbrokers.com.au gold pricing](https://www.compareforexbrokers.com.au/gold/spreads/) (practitioner blog).
- Typical slippage on retail FX market orders ranges from 0.05 to 0.9 pips, so budget at least 0.1 pips of adverse slippage per fill, per [LMAX FX transaction cost analysis](https://www.lmax.com/documents/LMAXExchange-FX-TCA-Transaction-Cost-Analysis-price-variation.pdf).
- Retail brokers report execution speeds of 32 to 47 ms from order receipt to fill, per [IC Markets execution speed](https://www.ic.com/en/start-forex-trading) and the [FXOpen execution quality statement](https://static.fxopen.net/download/cabinet-frontend/assets/fxoeu/documents/Execution_Quality_Summary_Statement_2022.pdf).
- One broker's quality statement shows price improvement on 35.7% of orders with an average improvement of 0.09 pips, so record signed slippage, not just fill price, per [FXOpen execution quality statement](https://static.fxopen.net/download/cabinet-frontend/assets/fxoeu/documents/Execution_Quality_Summary_Statement_2022.pdf).
- A VWAP-band scalp that targets 5 pips of edge loses 20% of its gross to a 1.0-pip round trip, so compute cost drag before you tune entries, per [forexmechanics.com cost guide](https://forexmechanics.com/) (practitioner blog).

### Order handling

- MetaTrader 5 supports fill policies including immediate-or-cancel and fill-or-kill, and market orders can fill partially, so your state machine must handle partial quantities, per [MetaTrader 5 order execution docs](https://www.metatrader5.com/en/terminal/help/trading/performing_deals).
- Broker order policies state that limit and stop orders may execute partially or not at all during fast markets, per [FxPro order execution policy](https://fxpro-cdn.cloud/repo/datarepo/legal/cysec/Order_Execution_Policy.pdf).
- Retail limit orders fill roughly 65% of the time in the studied sample, so treat a resting band exit as a probabilistic event, per [retail limit order study](https://microstructure.exchange/papers/Retail%20Limit%20Orders%2004082025.pdf).
- SEC staff found that aggressive marketable orders beat passive ones after costs in liquid options, which is the same tradeoff a 1-minute VWAP strategy faces, per [SEC DERA price improvement study](https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf).
- NFA Rule 2-29 requires 3 months of actual account results before a firm may advertise hypothetical performance, which sets a floor on how long you must run the live loop before publishing numbers, per [NFA Rule 2-29](https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=RULE+2-29&Section=4).
- Log order type, fill policy, requested price, fill price, and timestamp for every order so you can attribute 0.1-pip losses to handling rather than signal, per [Nautilus Trader order model docs](https://nautilustrader.io/docs/).

### Backtest engines

- vectorbt handles intrabar stops and sells under Apache 2.0 plus the Commons Clause, which restricts commercial redistribution, per [vectorbt license](https://github.com/polakowo/vectorbt/blob/master/LICENSE.md).
- backtesting.py models spread and commission directly and reached 0.6.6 in July 2026 under AGPL-3.0, so internal-only use is fine but distribution triggers copyleft, per [backtesting.py docs](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html) and [PyPI release history](https://pypi.org/project/backtesting/#history).
- backtesting.py fixed a same-bar stop-loss-before-take-profit ordering bug, which matters because your ±1 and ±3 sigma bands can both trigger inside one 1-minute bar, per [backtesting.py changelog](https://kernc.github.io/backtesting.py/CHANGELOG/).
- Nautilus Trader is LGPL and event-driven, which gives realistic fill simulation at the cost of more setup than a pandas script, per [Nautilus Trader docs](https://nautilustrader.io/docs/).
- bt is MIT licensed but the maintainer confirms it prices at bar close only, which cannot represent an intrabar band touch, per [bt issue 7](https://github.com/pmorissette/bt/issues/7) and [bt license](https://github.com/pmorissette/bt/blob/master/LICENSE).
- Plain pandas remains viable for 106,394 bars of 1-minute history if you accept that close-only fills understate slippage, per [vectorbt performance benchmarks](https://vectorbt.dev/docs/) (practitioner documentation).

### Paper trading methodology

- Backtest overfitting inflates apparent Sharpe ratios, so split data into train and test blocks by time rather than by row, per [Bailey and Lopez de Prado on backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
- A 90% win rate measured over 100 trades carries a Wilson 95% interval of roughly ±6 percentage points, and about 400 trades are needed to tighten that to ±3 points, per [Wilson score interval](https://doi.org/10.1080/00031305.1998.10480550).
- At 2 to 4 signals per day, 400 closed trades take 100 to 200 trading days, roughly 5 to 10 months, so publish intervals, not point estimates, before then, per [Wilson score interval](https://doi.org/10.1080/00031305.1998.10480550).
- Compare live results to backtest with a paper account on the same broker feed first, because yfinance mid prices and broker executable prices differ by the full spread, per [IC Markets spreads](https://www.ic.com/en/trading-pricing/spreads).
- Persist each session's trades in SQLite with WAL enabled so a crash mid-session loses at most one write, per [SQLite WAL documentation](https://www.sqlite.org/wal.html).
- Report cost per trade in dollars and slippage in pips alongside win rate, since a 60% win rate at 0.8-pip cost and one at 1.6 pips are different systems, per [Pepperstone costs and fees](https://pepperstone.com/en/trading/costs-and-fees).

### Atomic signal architecture

- Write signal JSON to a temporary file and rename it with `os.replace`, which is atomic on POSIX and Windows, so a dashboard never reads a half-written file, per [Python os.replace docs](https://docs.python.org/3/library/os.html).
- Keep the writer single-threaded and one file per decision, because two writers to one path create torn reads even with atomic rename, per [Python os.replace docs](https://docs.python.org/3/library/os.html).
- Timestamp signals with the bar close time in UTC, not local time, so the 1-minute VWAP anchored at UTC midnight recomputes identically in backtest and live, per [vectorbt documentation](https://vectorbt.dev/docs/).
- Version each signal with a schema field such as `"v": 1` so the dashboard can reject files it does not understand, per [JSON schema versioning practice](https://json-schema.org/).
- Record the decision inputs, including band values, regime state, and spread at decision time, so you can replay a disputed trade later, per [Nautilus Trader cache design](https://nautilustrader.io/docs/nightly/concepts/cache/).
- Keep secrets out of the repo and out of signal files; load broker credentials from environment variables at runtime, per [Python os.environ docs](https://docs.python.org/3/library/os.html).

### Recommended loop

1. Fix the cost model first: set spread, commission, and 0.1-pip slippage per fill from your broker's published schedule, per [Pepperstone costs and fees](https://pepperstone.com/en/trading/costs-and-fees).
2. Backtest the VWAP-band rules on the 106,394-bar history with an intrabar-capable engine, splitting train and test by time, per [Bailey and Lopez de Prado](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
3. Emit each decision as an atomically renamed JSON file with bar close time, band values, regime, and spread, per [Python os.replace docs](https://docs.python.org/3/library/os.html).
4. Run a paper account on the broker feed for at least 3 months and log every order's requested price, fill price, and latency in milliseconds, per [NFA Rule 2-29](https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=RULE+2-29&Section=4).
5. Store trades in SQLite with WAL, then compute win rate with a Wilson interval and cost in dollars per lot, per [Wilson score interval](https://doi.org/10.1080/00031305.1998.10480550).
6. Only after 400 closed trades, compare live against backtest and either size up or retire the strategy, per [Bailey and Lopez de Prado](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
