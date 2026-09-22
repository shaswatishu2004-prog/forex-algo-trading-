---
label: wayfinder:research
status: resolved
blocked_by: []
claimed: background-agent
---

# Four-state daily regime definitions

## Resolution

Resolved 2026-09-22 by the background research agent. Full findings with a link on every source: [docs/vwap-regime-research.md](../../vwap-regime-research.md).

- Volatility state: `ATR(14)` percentile over a trailing 252 bars on 1d. Percentile >= 70 gives `high_vol`, percentile <= 30 gives `low_vol`.
- Trend state: `ADX(14) >= 25` with `+DI > -DI` and a positive `EMA(50)` slope over 5 bars gives `uptrend`, the mirror condition gives `downtrend`. When ADX fails the test, `ER(10) >= 0.35` with the same EMA slope sign substitutes. Anything left over is labeled `low_vol`.
- Priority: `high_vol` beats the trend states, trend beats the rest, and every bar gets exactly one of the four labels.
- Point-in-time rules: compute every input from bars at or before `t`, set the label at `t`'s close, trade from `t+1`'s open, use trailing windows only, and fit any scaler on training data only.
- Consequence to confirm with the user: a strong trend during high volatility reads as `high_vol`, not `uptrend`, because of the single-label priority. Two independent axes would be a different design.
