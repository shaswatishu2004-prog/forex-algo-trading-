# Strategy specification

## Scope

This project investigates whether a multi-timeframe FX system can produce positive **net** expectancy after execution costs. It does not assume that a 51–54% directional accuracy rate is sufficient.

## Signal hypothesis

Currency pairs share information through common currencies, interest-rate differentials, macroeconomic events, and correlated risk sentiment. A higher-timeframe model estimates market context and regime. Lower-timeframe models are restricted to entry timing and execution rather than being asked to predict the entire market direction.

## Candidate model families

1. Naive direction and buy/hold baselines.
2. Logistic regression and tree-based models.
3. HMM or other filtered regime models.
4. Stacked models trained separately by regime.
5. Graph models only after simpler models establish a net out-of-sample benchmark.

## Research risks

- **Data leakage:** revised macro data, future HMM states, random splits, or simultaneous timestamps.
- **Backtest overfitting:** tuning pairs, features, windows, stops, and thresholds until one historical result looks attractive.
- **Execution mismatch:** using mid prices, fixed spreads, zero latency, or fills that could not occur.
- **Regime instability:** historical relationships may break during crises.
- **Legging risk:** multi-pair trades can become unintentionally directional when one leg is delayed.
- **Tail risk:** news, gaps, spread widening, outages, and broker rejection can exceed stop assumptions.
- **Leverage risk:** a small price move can create a large account loss.

## Required performance report

Every experiment must report:

- number of trades and exposure time;
- gross return and every cost component;
- net return, volatility, Sharpe/Sortino with caveats;
- profit factor, expectancy, hit rate, average win/loss;
- maximum drawdown and recovery time;
- worst day, month, and losing streak;
- results by pair, session, regime, volatility bucket, and news bucket;
- sensitivity to wider spreads, larger slippage, delayed execution, and missing trades.

## Go/no-go rule

Do not proceed to live trading if net profitability disappears under modestly worse execution assumptions, if the final test was used for tuning, or if the system cannot enforce its risk limits independently of the prediction model.

