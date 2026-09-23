---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: [visual-prototype]
---

# Signal combination rule

## Question

Does a signal require both a VWAP condition and an agreeing regime state, or are VWAP touches and regime changes emitted separately? What does a touch of the 1-sigma band mean against a touch of the 3-sigma band: mean reversion entry, breakout continuation, or exit only? What marks entry and what marks exit? This decision defines the product.

## Resolution

User answers, 2026-09-23:

- Emit separately, no conjunction. VWAP position records and regime records stay independent; the backtest combines them at decision time.
- A 1-sigma touch means mean reversion: expect travel back to VWAP. A 3-sigma touch means breakout continuation: expect travel further in the break direction.
- Entry and exit stay undefined here. Records keep `action: "observe"`, and [backtest-design](backtest-design.md) owns the trigger definitions.

Implementation today: `build_vwap_records` and `build_regime_records` in `src/fx_strategy/analysis/signals.py` already emit the two record types independently with `action: "observe"`, so this decision needs no code change.
