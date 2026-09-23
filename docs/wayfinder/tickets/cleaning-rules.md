---
label: wayfinder:grilling
status: resolved
claimed: this-session
blocked_by: [stage-raw-data]
---

# Data cleaning rules and canonical format

## Question

Which rules enter the cleaning pass: duplicate handling, timestamp normalization to UTC, out-of-order fixes, invalid bar rejection such as high below low, zero or missing volume policy, outlier caps, missing minute bars, and the day boundary for daily bars? Where does cleaned data live: new tables in the SQLite database, or files under `data/processed/`? The output contract feeds every later ticket.

## Resolution

Resolved 2026-09-23. The cleaning pass lives in `src/fx_strategy/analysis/cleaning.py` (`run_cleaning`). It writes files, not database tables: `data/processed/{SYMBOL}_1m.csv`, `data/processed/{SYMBOL}_1d.csv`, and `manifest.json` with per-reason drop counts. SQLite (`data/forex_market_data.db`) stays the raw staging store and only feeds the merge.

### Rules

- Timestamps normalize to timezone-aware UTC. The user's broker exports are already UTC and take `sources.user_csv_utc_offset_hours` (0 for all three files; measured from session anchors in the fetch reports).
- HistData stamps are New York wall clock, not the fixed UTC-5 its spec page claims. `sources.histdata_utc_offset_hours` holds the sentinel `us_eastern_dst`: +4h summer, +5h winter, US DST dates before 2019 and EU DST dates from 2019 on (`_dst_offset_hours`; evidence in the fetch reports and `scripts/dst_probe.py`).
- Invalid bars drop with separate manifest reasons, checked in this order: `missing_ohlc`, `non_positive_price`, `invalid_range` (high below low, high below max(open, close), low above min(open, close)). Non-positive is checked before the range rules so close -1.10 reports as `non_positive_price`. A file whose rows mostly fail high-below-low gets its high and low columns swapped once and revalidated.
- Duplicates: validate first, then sort ascending and keep the last row per timestamp, so an invalid row can never win a duplicate contest.
- Out-of-order rows: sort ascending with a stable sort. The EU fall-back hour becomes distinct stamps under `us_eastern_dst`; a fixed offset would collapse it and drop real minutes as duplicates.
- Volume: fill missing or negative with 0, keep zero volume, drop no column. HistData M1 volume is all zeros and the user's XAUUSD export is all ones, neither is trade volume; the VWAP layer applies the synthetic activity weight for EURUSD and DXY per [VWAP band math and volume proxies](vwap-band-math.md).
- Outlier caps: none. Only structurally invalid rows drop. The one price-scale event is handled as source validity: `sources.histdata_valid_from.DXY = 2018-12-16` drops 2,612,055 rows of mislabeled Dow Jones data.
- Missing minute bars stay missing, nothing forward-fills. Weekends, holidays, and early Friday stops are structure; holes inside a session stay holes.
- Daily bars aggregate from cleaned 1m at the 21:00 UTC session boundary and carry the session date; native daily fills dates the 1m history misses. Precedence: user file over HistData over yfinance_db for 1m, user file over aggregated 1m over yfinance for 1d.

### Source evidence

The three fetch reports are the record: [EURUSD](../../../data/raw/histdata/EURUSD/FETCH_REPORT.md), [XAUUSD](../../../data/raw/histdata/XAUUSD/FETCH_REPORT.md), [DXY](../../../data/raw/histdata/DXY/FETCH_REPORT.md). Facts they establish:

- EURUSD: 8,987,022 rows across 35 CSVs; zero high-below-low rows; exactly one non-positive row; 364 duplicate stamps across 7 dates, non-adjacent EU fall-back hour replays plus the 4 stamps of the 2026-07-05 mid-summer feed glitch; no timezone token anywhere in the data; the user zone measures UTC from session anchors.
- XAUUSD: 6,135,749 rows with zero invalid rows; 385 intra-file backward steps all classified (7 full-hour EU fall-back replays 2019-2025 plus 378 pair replays in the 2026 monthlies); the scheduled-news anchor matches New York wall clock in both seasons against the spec page's fixed-UTC-5 claim; the user export is a hard 100,000-row tail.
- DXY: 4,978,109 rows across 25 zips; the 2,612,055 pre-2018-12-16 rows are a clean single-step scale switch to a series whose last close (24,052 on 2018-12-14) tracks the Dow Jones Industrial Average that day, not the dollar index; 2026 Friday last bars have median file-clock 16.98 h.

The suspect-window dump of Friday last-bar hours around every DST boundary shows the early Friday stops in June and July weeks are real thin-market closures, so they stay holes.

### DXY clock probe and the two fixes

The move to `us_eastern_dst` took DXY's trailing-year week holes from 60,019 to 69,307 minutes, over the `<= 60,019` revert gate. The probe verdict was artifact, not loss: the DST reconstruction equals the processed CSV (2,370,108 rows, identical stamps), the DST row set strictly contains the fixed +5 set (+391 fall-hour minutes), and 9,287 of the +9,288 hole-minute delta (99.99%) are structure-to-hole reclassifications with no row loss. Two real defects caused the flips:

1. 9,240 minutes: the spring fallback used the US second-Sunday-March date, about three weeks before the source's EU spring switch, so the three March-2026 weekends in the window stamped their Friday closes an hour early. Fixed in `_dst_offset_hours`: the spring boundary is now unconditionally the EU last-Sunday-March at raw 20:00, the first hour the source numbers as EDT. Raw stamps prove the EU schedule: the source switches EST to EDT between the 2026-03-22 and 2026-03-29 Sundays.
2. 47 minutes: 42 Friday micro-gaps with correct stamps fell under the hardcoded `hour >= 20` in `is_structure`. Fixed in `scripts/data_accuracy.py`: the threshold now derives from the frame's own median Friday close hour minus one (20 - 1 = 19 for DXY), with fallback 20 when the window holds no Friday stamps. The summer boundary lands where the fixed +5 baseline measured them (raw 15:00); the winter boundary sits one raw hour earlier, about 25 minutes a year of slack noted in the code comment.

User decision 2026-09-23: keep `us_eastern_dst` and apply both fixes. Reverting DXY to fixed +5 stays the fallback only if the fixes miss the gate.

### Final validation

`scripts/data_accuracy.py` over the trailing 365 days from 2025-09-23, after both fixes and a full engine rerun:

| metric | before clock fix (fixed offsets) | after both fixes | gate |
|---|---|---|---|
| EURUSD user vs HistData median abs close diff | 0.00067 | 0 (99.9% exact) | about 0, pass |
| XAUUSD user vs HistData median abs close diff | 14.22 | 0 (99.0% exact) | about 0, pass |
| DXY week holes | 60,019 min | 59,995 min | <= 60,019, pass |
| DXY weekend closures | 271 | 271 | unchanged, pass |
| DXY duplicate drops | 391 per year | 0 | no real loss, pass |

The measured 59,995 matches the probe's simulated figure exactly. The four Friday closures over 3,000 minutes (longest: 2026-04-03 15:15 for 3,404 min and 2025-11-28 18:13 for 3,286 min) remain holes under the derived threshold. Full-history manifest drops after the rerun: EURUSD 4 duplicates (the feed glitch) and 1 non-positive; XAUUSD 756 duplicates (the 2026 pair replays); DXY 0 duplicates and 2,612,055 outside_valid_range. The engine rerun wrote fresh signals for all three symbols and the full test suite passes (30 tests).
