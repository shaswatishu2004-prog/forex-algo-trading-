---
label: wayfinder:research
status: resolved
blocked_by: []
claimed: background-agent
---

# VWAP band math and volume proxies

## Resolution

Resolved 2026-09-22 by the background research agent. Full findings with a link on every source: [docs/vwap-regime-research.md](../../vwap-regime-research.md).

- Price source: `hlc3 = (H+L+C)/3` on all six series.
- Core formula: `VWAP_t = sum(w_i * p_i) / sum(w_i)` over the bars since the current anchor reset. Both sums reset on anchor timestamps only.
- Bands: `sigma_t = sqrt(sum(w_i * (p_i - VWAP_t)^2) / sum(w_i))` over the same anchor. Band 1 plots `VWAP +/- 1*sigma`, band 3 plots `VWAP +/- 3*sigma`. Sigma resets with the anchor, so bands start narrow after each reset and widen as bars accumulate.
- Weights: `GC=F` uses reported COMEX volume. `EURUSD=X` and `DX-Y.NYB` use the synthetic activity weight `w_t = (H_t - L_t) + |C_t - C_{t-1}|`, floored at 0, with fallback `w_t = 1` when the whole window is zero. The substitution gets documented in code comments.
- Anchor timing stays open. The user settled UTC midnight before the research landed, the research recommends the 21:00 UTC trading-day reset, and the conflict now lives in [Anchor time conflict](anchor-time-conflict.md).
