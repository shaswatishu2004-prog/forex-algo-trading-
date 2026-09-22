---
label: wayfinder:grilling
status: open
blocked_by: [cleaning-rules, signal-combination-rule, history-depth]
---

# Backtest design and report contents

## Question

Does validation follow `anchored_walk_forward` as `config/default.yaml` states, or a single chronological split with a locked final test? Which costs from `src/fx_strategy/costs.py` apply to 1-minute FX entries, and which items from the required performance report in `docs/strategy-spec.md` must the output include? How do 1-minute entries interact with 1-day regime states when the two timeframes disagree?
