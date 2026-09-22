# VWAP and regime research

Background research for the repo against primary sources: platform docs, exchange data, broker docs, and academic papers.

## Institutional VWAP on 1-minute bars

### Price source and cumulative formula

Typical price is (H+L+C)/3. TradingView's VWAP uses hlc3 by default and calls it the bar's average value ([TradingView](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap)). Close-only is a normal variant: the price source is a user input, and Pine's `ta.vwap(source)` accepts any series ([Pine reference](https://www.tradingview.com/pine-script-reference/v5/#fun_ta.vwap)). Sierra Chart documents the cumulative sums: `V_P = sum(V_i)` over the anchor period, then `VWAP = sum(X_i * V_i) / V_P` ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)). Both sums reset at the start of each new period ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)). On 1-minute bars this runs every bar of the anchor window.

### Standard deviation bands

Sierra Chart accumulates squared deviations from the running VWAP weighted by bar volume, divides by period volume, and takes the square root to get sigma ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)). thinkorswim states its bands sit a given number of standard deviations from VWAP and that deviations are based on the difference between price and VWAP ([thinkorswim](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/V-Z/VWAP)). So numerically the first band is VWAP +/- 1 sigma and the third is VWAP +/- 3 sigma ([NinjaTrader](https://www.ninjatrader.com/futures/blogs/what-is-volume-weighted-average-price-vwap)). Sigma uses the same running volume sums as the anchor, so it starts small after each reset and widens as bars and volume accumulate ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)). Common band multipliers are 1, 2, and 3 ([NinjaTrader](https://www.ninjatrader.com/futures/blogs/what-is-volume-weighted-average-price-vwap)).

### Anchoring conventions

The trading-day anchor resets at session start. Sierra Chart begins a 1-day VWAP at the start of each trading day defined by the chart's session times ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)). TradingView offers Session, Week, Month, Quarter, and Year anchor periods ([TradingView](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap)). thinkorswim computes cumulative VWAP over a day, week, or month frame ([thinkorswim](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/V-Z/VWAP)). Weekly VWAP starts on the first trading day of the week and only becomes meaningful from the second session onward ([LizardTrader](https://lizardtrader.com/vwap-trading)). For FX, the New York session runs about 13:00 to 22:00 UTC and the London-New York overlap runs 13:00 to 17:00 UTC ([Dukascopy](https://www.dukascopy.com/swiss/english/fx-market-tools/forex-market-hours); [FOREX.com](https://www.forex.com/en-sg/forex-trading/forex-market-hours)). The FX trading day boundary sits at 5pm New York, since broker hours run 5pm Sunday to 5pm Friday New York time ([OANDA](https://www.oanda.com/us-en/trading)).

### Volume availability in this repo

`EURUSD=X` returns a zero volume column in yfinance, and spot FX feeds publish no consolidated trade volume ([yfinance issue #1071](https://github.com/ranaroussi/yfinance/issues/1071); [QuantInsti](https://blog.quantinsti.com/download-forex-price-data-yfinance-library-python)). `GC=F` carries real COMEX exchange volume, shown live on its quote page ([Yahoo GC=F](https://finance.yahoo.com/quote/GC%3DF/)), and CME publishes official cleared volume for COMEX gold ([CME](https://www.prnewswire.com/news-releases/cme-group-announces-trading-volume-record-for-its-gold-futures-contracts-181433141.html)). `DX-Y.NYB` is an index, not a traded contract, and shows Volume `--` and Avg. Volume 0 ([Yahoo DX-Y.NYB](https://finance.yahoo.com/quote/DX-Y.NYB/)). yfinance also limits 1m data to the last 7 days and any interval below 1d to 60 days ([yfinance docs](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)).

### What practitioners substitute for volume in FX

- **Tick volume.** MetaTrader defines Forex volume as the number of ticks, meaning price changes in the interval ([MetaTrader 5 help](https://www.metatrader5.com/en/terminal/help/indicators/volume_indicators)). cTrader defines tick volume the same way ([cTrader help](https://help.ctrader.com/indicators/built-in/volume/tick-volume)). A broker-side study by Caspar Marney reported correlations above 90% between tick counts and real volume on an institutional feed ([Global Prime](https://globalprime.medium.com/why-is-tick-volume-important-to-monitor-56a936eea70d)).
- **Quote volume.** Sierra Chart's VWAP accepts Bid Volume or Ask Volume as the weighting input, so order-book quantities replace executed volume ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)).
- **Dollar volume.** VWAP equals total dollars transacted divided by total units traded ([NinjaTrader](https://www.ninjatrader.com/futures/blogs/what-is-volume-weighted-average-price-vwap)). Bars can be weighted by traded notional when a liquidity-provider feed supplies it.
- **Aggregated venue VWAP.** CLS publishes aggregated FX spot VWAP and TWAP values from its settlement flow, because no consolidated tape exists ([LeapRate on CLS](https://www.leaprate.com/forex/institutional/cls-launches-new-fx-settlement-trade-monitoring-reporting-tools)). Global FX turnover is measured only by periodic survey, most recently US$7.5 trillion per day in April 2022 ([BIS Triennial](https://data.bis.org/topics/DER)).
- **Synthetic weights.** VWAP execution needs a schedule of session volume weights ([Kissell, Glantz, Malamut 2003](https://books.google.com/books/about/Optimal_Trading_Strategies.html?id=P-Z1swEACAAJ), as cited by [Busseti and Boyd](https://web.stanford.edu/~boyd/papers/pdf/vwap_opt_exec.pdf)). With uniform weights the cumulative sum reduces to the simple average of the price source, which is the TWAP behavior used when no volume profile is trusted ([LSEG](https://developers.lseg.com/en/article-catalog/article/build-end-to-end-transaction-cost-analysis-framework)).

### Academic and agency origins

Kissell, Glantz, and Malamut's *Optimal Trading Strategies* (AMACOM, 2003) is the standard agency reference for VWAP benchmarking and volume-profile scheduling ([Google Books](https://books.google.com/books/about/Optimal_Trading_Strategies.html?id=P-Z1swEACAAJ)). Busseti and Boyd formalize optimal execution against the VWAP benchmark over one-minute intervals ([Stanford PDF](https://web.stanford.edu/~boyd/papers/pdf/vwap_opt_exec.pdf)). Cesari and coauthors survey VWAP, TWAP, and implementation shortfall as execution benchmarks ([arXiv:1206.5324](https://arxiv.org/pdf/1206.5324)). Implementation shortfall dates to Perold (1988) and agencies use it alongside VWAP because VWAP slippage ignores the chosen window and the trade's own market share ([Quantitative Brokers](https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall)). LSEG's TCA guide defines VWAP slippage as executed price minus market VWAP ([LSEG developers](https://developers.lseg.com/en/article-catalog/article/build-end-to-end-transaction-cost-analysis-framework)).

## Daily regime channel with four states

### Trend states

ADX comes from Wilder's 1978 book and measures trend strength without direction ([Internet Archive](https://archive.org/details/newconceptsintec00wild)). Wilder's reading rule: ADX above 25 means a strong trend, below 20 means no trend, with a gray zone between ([StockCharts ChartSchool](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx)). Direction comes from +DI versus -DI, and ADX uses a 14-period average of directional movement ([IG academy](https://www.ig.com/uk/learn-to-trade/ig-academy/fundamentals-of-trend-trading/trend-trading-strategies)). IG states an ADX value above 25 is a strong trend ([IG](https://www.ig.com/sg/trading-strategies/how-the-adx-indicator-reveals-hidden-market-trends-worth-trading-250619)). The Donchian alternative buys a 20-day high and sells a 20-day low, with a 55-day variant ([original Turtle rules](https://oxfordstrat.com/coasdfASD32/uploads/2016/01/turtle-rules.pdf)). Kaufman's efficiency ratio is `|C - C_n| / sum(|C_i - C_{i-1}|)` over n periods, introduced in *Smarter Trading* (1995) ([StrategyQuant](https://strategyquant.com/codebase/kaufmans-efficiency-ratio-ker)). On a plus/minus 100 scale, readings above 30 indicate an uptrend and below -30 a downtrend ([TC2000](https://help.tc2000.com/m/69404/l/749623-kaufman-efficiency-ratio)).

### Volatility states

ATR comes from Wilder (1978), which standard implementations read over 14 periods ([Internet Archive](https://archive.org/details/newconceptsintec00wild); [ChartSchool ATR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr)). Percentile rank normalizes any volatility gauge against its own history: count readings at or below the current one, divide by window length, scale to 100 ([LuxAlgo](https://www.luxalgo.com/library/concept/volatility-percentile-rank)). A window near 252 bars is customary, inherited from option IV-rank practice ([LuxAlgo](https://www.luxalgo.com/library/concept/volatility-percentile-rank)). A public reference implementation uses a 20-bar volatility window, a 120-bar lookback, and thresholds at the 20th and 80th percentiles ([HKUDS Vibe-Trading](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/agent/src/skills/volatility/SKILL.md)). Parkinson's 1980 estimator uses highs and lows instead of closes to estimate return variance ([Parkinson 1980, DOI 10.1086/296071](https://ideas.repec.org/a/ucp/jnlbus/v53y1980i1p61-65.html)). Realized volatility is the rolling standard deviation of returns ([HKUDS](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/agent/src/skills/volatility/SKILL.md)).

### FX-specific regime work

Regime-switching models report that means and volatilities differ sharply across regimes and persist for several periods ([Ang and Timmermann 2012](https://www.annualreviews.org/doi/10.1146/annurev-financial-110311-101808)). Time-series momentum holds for currency futures: the past 12-month excess return predicts future returns across 58 instruments ([Moskowitz, Ooi, Pedersen 2012](https://www.sciencedirect.com/science/article/pii/S0304405X11002613)). Broker documentation publishes session liquidity windows and ADX thresholds, not an official regime taxonomy ([Dukascopy](https://www.dukascopy.com/swiss/english/fx-market-tools/forex-market-hours); [IG](https://www.ig.com/sg/trading-strategies/how-the-adx-indicator-reveals-hidden-market-trends-worth-trading-250619)).

### Point-in-time labeling rules

Look-ahead bias occurs when a backtest uses data that was not available at the simulated decision time, and single-choice backtests hide the damage ([Bailey et al. 2014](https://carmamaths.org/jon/backtest.pdf)). Label bar `t` at its close using only bars with timestamp at or before `t`. Use trailing windows only for percentiles, ADX, and ATR. Never normalize a feature with full-sample statistics. Execute at bar `t+1` open, never at the close that formed the label. Timestamp-based anchor resets are safe because the calendar is known in advance.

## Recommended defaults

Concrete choices for EURUSD, XAUUSD, and DXY on 1m and 1d bars. These numbers are ours; the cited conventional thresholds sit behind them.

1. **Price source:** `hlc3 = (H+L+C)/3` for all six series.
2. **Volume weights:** `GC=F` uses reported volume. `EURUSD=X` and `DX-Y.NYB` use a synthetic activity weight `w_t = (H_t - L_t) + |C_t - C_{t-1}|`, floored at 0, with fallback `w_t = 1` when the whole window is zero. Document the substitution in code comments.
3. **Anchors in UTC:** trading-day reset at 21:00 (17:00 New York), New York session anchor at 13:00, weekly anchor at Monday 00:00. Reset accumulators on those timestamps only.
4. **Bands:** `sigma_t = sqrt(sum_i w_i (p_i - VWAP_t)^2 / sum_i w_i)` over the current anchor. Plot `VWAP +/- 1*sigma` and `VWAP +/- 3*sigma`. Reset sigma with the anchor.
5. **Volatility state:** `ATR(14)` percentile over a trailing 252 bars on 1d and 390 bars (one session) on 1m. Percentile >= 70 gives `high_vol`. Percentile <= 30 gives `low_vol`.
6. **Trend state:** `ADX(14) >= 25` with `+DI > -DI` and `EMA(50)` slope positive over 5 bars gives `uptrend`. The mirror condition gives `downtrend`. If ADX fails, accept `ER(10) >= 0.35` with the same EMA slope sign instead. Otherwise label `low_vol`.
7. **Priority:** `high_vol` beats trend states, trend beats `low_vol`. Every bar gets exactly one of the four labels.
8. **Point-in-time:** compute all inputs from bars `<= t`, assign the label at `t`'s close, trade from `t+1` open. Fit any scaler on training data only.
9. **Data limits:** yfinance gives 1m bars for the last 7 days only, so 1m backtests need stored or third-party data.

## Sources

- [TradingView VWAP indicator documentation](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap)
- [Pine Script reference, ta.vwap](https://www.tradingview.com/pine-script-reference/v5/#fun_ta.vwap)
- [Sierra Chart, VWAP with standard deviation lines](https://www.sierrachart.com/index.php?page=doc/StudiesReference.php;ID=108)
- [thinkorswim Learning Center, VWAP](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/V-Z/VWAP)
- [NinjaTrader, what is VWAP](https://www.ninjatrader.com/futures/blogs/what-is-volume-weighted-average-price-vwap)
- [LizardTrader, VWAP sessions](https://lizardtrader.com/vwap-trading)
- [Dukascopy, FX market hours](https://www.dukascopy.com/swiss/english/fx-market-tools/forex-market-hours)
- [FOREX.com, forex market hours](https://www.forex.com/en-sg/forex-trading/forex-market-hours)
- [OANDA, hours of operation](https://www.oanda.com/us-en/trading)
- [yfinance issue 1071, EURUSD=X volume is zero](https://github.com/ranaroussi/yfinance/issues/1071)
- [yfinance API docs, interval limits](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
- [Yahoo Finance, GC=F](https://finance.yahoo.com/quote/GC%3DF/) and [DX-Y.NYB](https://finance.yahoo.com/quote/DX-Y.NYB/)
- [MetaTrader 5 help, volume indicators](https://www.metatrader5.com/en/terminal/help/indicators/volume_indicators)
- [cTrader help, tick volume](https://help.ctrader.com/indicators/built-in/volume/tick-volume)
- [Global Prime on Marney tick volume study](https://globalprime.medium.com/why-is-tick-volume-important-to-monitor-56a936eea70d)
- [CLS FX data products, via LeapRate](https://www.leaprate.com/forex/institutional/cls-launches-new-fx-settlement-trade-monitoring-reporting-tools)
- [BIS Triennial Survey topic page](https://data.bis.org/topics/DER)
- [Kissell, Glantz, Malamut, Optimal Trading Strategies](https://books.google.com/books/about/Optimal_Trading_Strategies.html?id=P-Z1swEACAAJ)
- [Busseti and Boyd, VWAP optimal execution](https://web.stanford.edu/~boyd/papers/pdf/vwap_opt_exec.pdf)
- [Cesari et al., Effective Trade Execution](https://arxiv.org/pdf/1206.5324)
- [Quantitative Brokers, history of implementation shortfall](https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall)
- [LSEG developers, TCA framework](https://developers.lseg.com/en/article-catalog/article/build-end-to-end-transaction-cost-analysis-framework)
- [Wilder, New Concepts in Technical Trading Systems](https://archive.org/details/newconceptsintec00wild)
- [StockCharts ChartSchool, ADX](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx)
- [StockCharts ChartSchool, ATR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr)
- [IG, ADX indicator guide](https://www.ig.com/sg/trading-strategies/how-the-adx-indicator-reveals-hidden-market-trends-worth-trading-250619)
- [IG academy, trend trading strategies](https://www.ig.com/uk/learn-to-trade/ig-academy/fundamentals-of-trend-trading/trend-trading-strategies)
- [Original Turtle trading rules](https://oxfordstrat.com/coasdfASD32/uploads/2016/01/turtle-rules.pdf)
- [StrategyQuant, Kaufman efficiency ratio](https://strategyquant.com/codebase/kaufmans-efficiency-ratio-ker)
- [TC2000, Kaufman efficiency ratio](https://help.tc2000.com/m/69404/l/749623-kaufman-efficiency-ratio)
- [LuxAlgo, volatility percentile rank](https://www.luxalgo.com/library/concept/volatility-percentile-rank)
- [HKUDS Vibe-Trading, volatility skill](https://raw.githubusercontent.com/HKUDS/Vibe-Trading/main/agent/src/skills/volatility/SKILL.md)
- [Parkinson 1980, extreme value method](https://ideas.repec.org/a/ucp/jnlbus/v53y1980i1p61-65.html)
- [Ang and Timmermann 2012, regime changes](https://www.annualreviews.org/doi/10.1146/annurev-financial-110311-101808)
- [Moskowitz, Ooi, Pedersen 2012, time series momentum](https://www.sciencedirect.com/science/article/pii/S0304405X11002613)
- [Bailey et al. 2014, pseudo-mathematics and financial charlatanism](https://carmamaths.org/jon/backtest.pdf)
