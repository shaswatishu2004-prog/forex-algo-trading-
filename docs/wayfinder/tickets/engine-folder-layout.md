---
label: wayfinder:grilling
status: open
blocked_by: []
---

# Analysis engine folder layout

## Question

Does the analysis engine live in a new subfolder under `src/fx_strategy/`, for example `src/fx_strategy/analysis/`, or in a new top-level package? Which modules go in, how config wires up, and does `config/default.yaml` move from its current 1h regime and 5m signal timeframes to the 1d regime and 1m signal this strategy uses? The user asked for a new subfolder for the analysis engine.
