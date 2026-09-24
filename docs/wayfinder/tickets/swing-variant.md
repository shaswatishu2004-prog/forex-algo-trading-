---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: [backtest-design, signal-combination-rule]
---

# Multi-day swing variant spec and success criteria

## Question

The trailing-year one-minute run shows gross edge near zero after costs on both the baseline and the filtered pass, so cost reduction alone cannot make that horizon pay. Does the research move to a multi-day swing system on daily bars, and under exactly what spec and success criteria, fixed before any result is seen?

## Resolution

Resolved 2026-09-24, approved by the user before any run. Engine: `src/fx_strategy/backtest/swing.py`. Runner: `scripts/run_swing_backtest.py`. Output: `data/backtest/swing_results.json` (never `results.json`, which stays the one-minute report).

- **Scope**: EURUSD, XAUUSD, DXY; one trailing year (`backtest.window_days`, 365) of daily bars ending at the last bar on disk; `--loop` re-runs the same fixed window for the dashboard.
- **Signal**: strict 20-day Donchian breakout. Long when close exceeds the highest high of the prior 20 daily bars, short when close breaks the lowest low of that window. The window excludes the signal bar, and equality does not fire.
- **Gate**: trend-only admission through a new `swing_admits(regime, direction)`: `uptrend` admits longs, `downtrend` admits shorts, `high_vol` and `low_vol` admit nothing. This is a declared subset of the `REGIME_ADMITS` continuation rows from `signal-combination-rule`. The one-minute `filters` block does not apply to this variant; diagnostics record that instead of silently reusing it.
- **Exits**: a chandelier trail at 3 x ATR(14) recomputed at each bar close but ratcheted so it never loosens (deliberately not textbook; the stop must keep matching the risk-per-trade sizing). The breach tests against the level known before the bar trades, and the fill is gap-aware: `min(trail, open)` for longs. An opposite-trend regime flip is decided at close and fills at the next open. A 20-session hold cap works the same way. `end_of_window` closes at the last close and includes financing, fixing the one-minute engine's omission.
- **Mechanics**: decisions at bar close, fills at the next daily bar open (the 21:00 UTC roll); `entry_delay_bars: 1` with a delayed pass at `swing.delayed_entry_delay_bars: 2`. Stop distance is 3 x ATR at the signal bar; notional is `equity x 0.0025 / stop_fraction` capped at `equity x max_gross_leverage`. One position per symbol at a time. Halts, equity points and drawdown recording mirror the one-minute engine: `max_daily_loss` halts entries for the session, `max_portfolio_drawdown` records without enforcing unless configured.
- **Costs**: round-trip spread, commission and slippage from the cost table, plus `costs.financing_per_session` charged once for every daily bar held. The flat 0.00025 rate is a placeholder (2.5 bp per session, up to 50 bp on a 20-session trade, against about 1.3 bp round-trip trading costs), so financing dominates and per-symbol broker swap rates are required before live. Results report financing sensitivity at 0x, 1x and 2x alongside the usual one-minute-parity sensitivity (which excludes financing) and an all-in stress that doubles all four cost lines.
- **Pre-registered success criteria**, fixed now and evaluated by the runner: gross return greater than three times total costs; profit factor above 1.30; positive skew of trade pnl; every one of the six folds net positive at base costs, where an empty fold counts as not agreeing; locked final test net positive under the all-in 2x stress. All five must pass. The outcome is reported honestly either way.
- **Known limitation**: all fills land on the 21:00 UTC roll, where the flat spread model understates real spreads; the 2x all-in stress is the partial mitigation and a London-open fill is the noted live refinement. Intrabar trail fills are stamped at the close of the bar they filled in, so reported exposure is overstated by less than one session per trade.
