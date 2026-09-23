---
label: wayfinder:grilling
status: resolved
blocked_by: [cleaning-rules, signal-combination-rule, history-depth]
---

# Backtest design and report contents

## Question

Does validation follow `anchored_walk_forward` as `config/default.yaml` states, or a single chronological split with a locked final test? Which costs from `src/fx_strategy/costs.py` apply to 1-minute FX entries, and which items from the required performance report in `docs/strategy-spec.md` must the output include? How do 1-minute entries interact with 1-day regime states when the two timeframes disagree?

## Resolution

Resolved 2026-09-23. All four questions are settled; the runner is `scripts/run_backtest.py`.

- **Validation**: follow `config/default.yaml` — `validation.method: anchored_walk_forward` with `final_test_is_locked: true`, plus a `minimum_stress_cost_multiplier: 2.0` stress pass. The go/no-go rule turns on whether the final test was used for tuning, so the final fold stays locked and never feeds parameter choice.
- **Costs**: use `TradeCosts` from `src/fx_strategy/costs.py` (spread + commission + slippage + financing, summed by `.total` and subtracted via `net_trade_return`). `execution:` in config enables spread, commission, slippage, and latency. Financing applies only to positions held across the 21:00 UTC session boundary; purely intraday 1-minute entries pay no financing.
- **Report contents**: every item in `docs/strategy-spec.md` §Required performance report — trades and exposure time, gross return with each cost component, net return/volatility/Sharpe/Sortino with caveats, profit factor/expectancy/hit rate/average win-loss, maximum drawdown and recovery time, worst day/month/losing streak, breakdowns by pair/session/regime/volatility bucket/news bucket, and sensitivity to wider spreads, larger slippage, delayed execution, and missing trades.
- **1-minute vs 1-day interaction**: the 1-day regime filters 1-minute entries — the slower layer sets direction, the 1-minute VWAP layer times the entry. A 1-minute signal whose direction conflicts with the regime is skipped, not taken. The four regime states map as: `uptrend` admits longs only, `downtrend` admits shorts only, `low_vol` admits mean-reversion (1-sigma touch) signals in either direction, and `high_vol` admits continuation (3-sigma touch) signals in either direction. This is the combination `signal-combination-rule` deferred: signals still emit separately, and the conjunction happens here at backtest time.

Entries fill at the next bar open after the signal, matching `regime-definitions`. Loop semantics for the dashboard: the runner iterates bar-by-bar over the trailing 365-day 1-minute window, wraps to the start and re-runs at the end, and the dashboard animates that cycle over the fixed one-year dataset.
