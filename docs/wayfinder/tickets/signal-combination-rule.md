---
label: wayfinder:grilling
status: open
blocked_by: [visual-prototype]
---

# Signal combination rule

## Question

Does a signal require both a VWAP condition and an agreeing regime state, or are VWAP touches and regime changes emitted separately? What does a touch of the 1-sigma band mean against a touch of the 3-sigma band: mean reversion entry, breakout continuation, or exit only? What marks entry and what marks exit? This decision defines the product.

Partial answer, user on 2026-09-23: emit separately, no conjunction. VWAP position records and regime records stay independent, and the combination happens at backtest time. The remaining two questions, the meaning of a 1-sigma versus a 3-sigma touch and the entry and exit triggers, are still open.
