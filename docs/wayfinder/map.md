---
label: wayfinder:map
status: open
tracker: local-markdown
---

# Institutional VWAP signal engine

## Destination

Cleaned historical data, an analysis engine that marks institutional VWAP with the 1-sigma and 3-sigma bands on 1-minute bars, a four-state regime channel on daily bars, a signals JSON output folder, and backtest statistics, all working in this repository for EURUSD, XAUUSD, and DXY with passing tests.

## Notes

- Domain: Python 3.11, pandas, yfinance, SQLite. The repository stays a research prototype.
- Settled with the user on 2026-09-22: symbols are EURUSD, XAUUSD, and DXY. The VWAP anchor is UTC midnight daily, contested by the research recommendation of 21:00 UTC; [Anchor time conflict](tickets/anchor-time-conflict.md) decides. Backtest statistics belong inside this map. Deeper 1m history is researched, see [Minute data sources beyond 29 days](tickets/minute-data-sources.md), and adopting it is the open [1m history depth](tickets/history-depth.md) decision. The 1-minute window past yfinance's 29-day cap gets researched before any data work assumes more history.
- Apply the unslop skill to every line of prose written into this repository: no em dashes, sentence case headings, active voice, plain words, straight quotes, no filler.
- Use the research skill for facts from outside the repository. Findings land in one markdown file under `docs/`, every claim linked to its source. No throwaway branches, the working tree holds uncommitted work.
- No API keys in tracked files. The key pasted into chat on 2026-09-22 was retracted by the user and must never be stored anywhere.
- Tests run with pytest from the repository root. The package imports from `src/`.

### Wayfinding operations for this local-markdown tracker

- A session claims a ticket by setting `claimed` in its frontmatter before any work.
- The frontier is every ticket with `status: open` and an empty `blocked_by`.
- Blocking lists ticket slugs in `blocked_by`. A ticket unblocks when every slug it lists carries `status: resolved`.
- Resolution adds a `## Resolution` section holding the decision, flips `status` to `resolved`, and adds one line under Decisions so far below. Assets link from the ticket, they never get pasted in.
- Ruling a ticket out of scope flips `status` to `out-of-scope`, leaves Decisions so far untouched, and adds a gist with the reason under Out of scope.

## Decisions so far

<!-- one line per resolved ticket, enough to judge relevance, then follow the link for detail -->

- [VWAP band math and volume proxies](tickets/vwap-band-math.md): hlc3 price, cumulative VWAP with volume-weighted sigma bands; GC=F uses real volume, EURUSD and DXY use a synthetic activity weight of range plus price change.
- [Four-state daily regime definitions](tickets/regime-definitions.md): ATR(14) percentile over 252 bars splits high and low volatility, ADX(14) with DI direction and EMA(50) slope splits trend, point-in-time labels set at bar close, trades from next open.
- [Stage raw historical data](tickets/stage-raw-data.md): 126,394 bars staged in `data/forex_market_data.db`; 1m gaps are weekends and one holiday only; volume exists solely on XAUUSD; stored timezone offsets are mixed per source and must be normalized before string filters.
- [Minute data sources beyond 29 days](tickets/minute-data-sources.md): HistData is the primary source for deeper 1m history with OANDA as backup for tick-count volume; Yahoo, Alpha Vantage, Twelve Data, TrueFX, and Dukascopy ruled out with reasons.

## Not yet specified

- How the engine runs over data: one batch command, a notebook loop, or a scheduled job, and how the signals JSON refreshes.
- What consumes the signals JSON next: an execution layer or the dashboard.
- Whether the dashboard draws VWAP bands and the regime channel.
- How symbols aggregate into one portfolio view if the effort grows past single-symbol signals.

## Out of scope

- Live trading, broker adapters, and order execution. The README fixes this repository as a research prototype.
- Machine learning and stacked prediction models. Separate future research, not part of this map.
- Dashboard redesign.
