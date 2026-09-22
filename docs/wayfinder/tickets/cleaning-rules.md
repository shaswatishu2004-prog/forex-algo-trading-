---
label: wayfinder:grilling
status: open
blocked_by: [stage-raw-data]
---

# Data cleaning rules and canonical format

## Question

Which rules enter the cleaning pass: duplicate handling, timestamp normalization to UTC, out-of-order fixes, invalid bar rejection such as high below low, zero or missing volume policy, outlier caps, missing minute bars, and the day boundary for daily bars? Where does cleaned data live: new tables in the SQLite database, or files under `data/processed/`? The output contract feeds every later ticket.
