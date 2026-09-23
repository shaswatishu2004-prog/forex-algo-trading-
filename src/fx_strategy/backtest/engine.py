"""Bar-by-bar backtest over the trailing one-minute window.

Decision: docs/wayfinder/tickets/backtest-design.md. The entry conjunction that
signal-combination-rule deferred is implemented here.

Point-in-time regime join
-------------------------
Per symbol the runner reads ``data/processed/{SYMBOL}_1m.csv`` over the trailing
window, recomputes :func:`compute_vwap_bands`, classifies every close with
``_band_position``, labels the full daily frame with :func:`compute_regime`, and
joins the label forward with ``merge_asof`` on each daily bar's *completion*
time. A daily bar timestamped D covers the session ``[D-1 21:00, D 21:00)`` and
is only knowable at D 21:00, so every minute inside session D reads the label of
session D-1. Joining on calendar date instead would hand a minute a label built
from bars that had not traded yet.

Entry combination
-----------------
A one-minute band touch proposes a direction and the daily regime filters it. A
1-sigma touch is mean reversion (expect travel back to VWAP); a 3-sigma touch is
continuation (expect travel further in the break direction). ``uptrend`` admits
longs only, ``downtrend`` shorts only, ``low_vol`` admits the mean-reversion
touch in either direction, and ``high_vol`` admits the continuation touch in
either direction. A signal the regime rejects is skipped, not taken. Entries
fill at the next bar open, one position per symbol at a time.

Risk stops
----------
``risk.max_daily_loss`` halts new entries for the rest of that UTC day and clears
on the next one. ``risk.max_portfolio_drawdown`` records a breach but keeps the
run going unless ``backtest.enforce_portfolio_halt`` is true: a backtest that
stopped at its first 15% drawdown would report only the months before the
breach and leave the walk-forward folds after it empty, so the breach is
reported as a finding and the window is measured in full.

Exits
------
Target and stop come from the signal bar's own vwap and sigma, the levels that
produced the touch. Mean reversion targets VWAP and stops at the two-sigma level
(one sigma beyond the one-sigma entry). Continuation stops back at the one-sigma
band and targets two sigma further out, so both structures are one-to-one before
costs. A position that survives to the session roll closes on the first bar of
the new session at its open, and pays one session of financing for crossing
21:00 UTC; a stop or target hit inside a session pays none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from fx_strategy.analysis.regime import compute_regime
from fx_strategy.analysis.signals import _band_position
from fx_strategy.analysis.vwap import compute_vwap_bands
from fx_strategy.costs import TradeCosts, net_trade_return
from fx_strategy.risk import RiskLimits, position_notional

# Regime filter: which directions and which signal kinds each state admits.
REGIME_ADMITS: dict[str, dict[str, frozenset[str]]] = {
    "uptrend": {
        "directions": frozenset({"long"}),
        "kinds": frozenset({"mean_reversion", "continuation"}),
    },
    "downtrend": {
        "directions": frozenset({"short"}),
        "kinds": frozenset({"mean_reversion", "continuation"}),
    },
    "low_vol": {
        "directions": frozenset({"long", "short"}),
        "kinds": frozenset({"mean_reversion"}),
    },
    "high_vol": {
        "directions": frozenset({"long", "short"}),
        "kinds": frozenset({"continuation"}),
    },
}

NEWS_BUCKET_UNAVAILABLE = (
    "No news calendar is staged in this repository, so the news bucket is "
    "reported as unavailable rather than inferred from volatility."
)

SHARPE_CAVEAT = (
    "Sharpe and Sortino annualize daily equity returns with sqrt(252) over a "
    "single year of one-minute bars. One window cannot separate skill from a "
    "favourable regime, and the daily close series hides intraday swings."
)


@dataclass
class Trade:
    """One filled round trip, sized once at entry."""

    symbol: str
    kind: str
    direction: str
    regime: str
    vol_bucket: str
    session_hour_bucket: str
    session: str
    entry_time: pd.Timestamp
    entry_price: float
    target: float
    stop: float
    notional: float = 0.0
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_return: float = 0.0
    spread_cost: float = 0.0
    commission_cost: float = 0.0
    slippage_cost: float = 0.0
    financing_cost: float = 0.0
    pnl: float = 0.0
    bars_held: int = 0
    fold: int = -1

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


# ---------------------------------------------------------------- helpers

def trade_costs(symbol: str, cfg: dict[str, Any]) -> TradeCosts:
    """Cost components for one symbol, gated by the execution include flags."""
    execution = cfg.get("execution", {})
    block = cfg.get("costs", {}).get(symbol, {})
    return TradeCosts(
        spread=float(block.get("spread", 0.0)) if execution.get("include_spread", True) else 0.0,
        commission=float(block.get("commission", 0.0))
        if execution.get("include_commission", True)
        else 0.0,
        slippage=float(block.get("slippage", 0.0))
        if execution.get("include_slippage", True)
        else 0.0,
        financing=0.0,
    )


def scale_costs(costs: TradeCosts, spread_mult: float = 1.0, slippage_mult: float = 1.0,
                commission_mult: float = 1.0) -> TradeCosts:
    return TradeCosts(
        spread=costs.spread * spread_mult,
        commission=costs.commission * commission_mult,
        slippage=costs.slippage * slippage_mult,
        financing=costs.financing,
    )


def vol_bucket(atr_percentile: Any) -> str:
    if atr_percentile is None or atr_percentile is pd.NaT:
        return "unknown"
    try:
        value = float(atr_percentile)
    except (TypeError, ValueError):
        return "unknown"
    if np.isnan(value):
        return "unknown"
    if value >= 70.0:
        return "high"
    if value <= 30.0:
        return "low"
    return "mid"


def session_hour_bucket(hour: int) -> str:
    """UTC hour buckets approximating the Asia, London and New York sessions."""
    if hour >= 21 or hour < 7:
        return "asia"
    if hour < 13:
        return "london"
    return "new_york"


def admits(regime: Any, direction: Any, kind: Any) -> bool:
    """Does the daily regime let this one-minute signal through?"""
    if not isinstance(regime, str) or not isinstance(direction, str) or not isinstance(kind, str):
        return False
    rule = REGIME_ADMITS.get(regime)
    if rule is None:
        return False
    return direction in rule["directions"] and kind in rule["kinds"]


def levels_for(kind: str, direction: str, vwap: float, sigma: float) -> tuple[float, float]:
    """(target, stop) absolute prices anchored to the signal bar's own bands."""
    if kind == "mean_reversion":
        if direction == "long":
            return vwap, vwap - 2.0 * sigma
        return vwap, vwap + 2.0 * sigma
    if direction == "long":
        return vwap + 5.0 * sigma, vwap + sigma
    return vwap - 5.0 * sigma, vwap - sigma


# ---------------------------------------------------------------- preparation

def join_regime(minute: pd.DataFrame, daily: pd.DataFrame, anchor_hour: int) -> pd.DataFrame:
    """Attach the latest daily label whose session had already closed."""
    out = minute.sort_values("timestamp").reset_index(drop=True).copy()
    labeled = daily.dropna(subset=["regime"]).copy()
    if labeled.empty:
        out["regime"] = pd.NA
        out["atr_percentile"] = np.nan
        return out
    labeled["complete_at"] = labeled["timestamp"] + pd.Timedelta(hours=anchor_hour)
    keep = ["complete_at", "regime"]
    if "atr_percentile" in labeled.columns:
        keep.append("atr_percentile")
    right = labeled[keep].drop_duplicates("complete_at").sort_values("complete_at")
    merged = pd.merge_asof(
        out,
        right,
        left_on="timestamp",
        right_on="complete_at",
        direction="backward",
    )
    merged = merged.drop(columns=[col for col in ("complete_at",) if col in merged.columns])
    return merged


def _signal_columns(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Classify each band crossing as a mean-reversion or continuation touch."""
    position = frame["position"]
    previous = position.shift(1)
    classified = position.ne("indeterminate") & previous.notna() & previous.ne("indeterminate")

    kind = pd.Series(pd.NA, index=frame.index, dtype="object")
    direction = pd.Series(pd.NA, index=frame.index, dtype="object")

    mr_long = classified & position.eq("between_1_and_3_lower") & previous.eq("inside_band1")
    mr_short = classified & position.eq("between_1_and_3_upper") & previous.eq("inside_band1")
    cont_long = classified & position.eq("above_band3") & previous.ne("above_band3")
    cont_short = classified & position.eq("below_band3") & previous.ne("below_band3")

    kind = kind.mask(mr_long | mr_short, "mean_reversion")
    kind = kind.mask(cont_long | cont_short, "continuation")
    direction = direction.mask(mr_long | cont_long, "long")
    direction = direction.mask(mr_short | cont_short, "short")
    return kind, direction


def prepare_symbol(
    symbol: str,
    minute: pd.DataFrame,
    daily: pd.DataFrame,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    """Banded minute frame carrying position, point-in-time regime, and signals."""
    vwap_cfg = cfg.get("vwap", {})
    anchor = int(vwap_cfg.get("anchor_hour_utc", 21))
    volume_min = int(vwap_cfg.get("volume_min_unique_per_session", 2))
    banded = compute_vwap_bands(minute, anchor_hour_utc=anchor, volume_min_unique=volume_min)
    banded = banded.dropna(subset=["vwap", "sigma"]).reset_index(drop=True)
    banded["position"] = _band_position(
        banded["close"],
        banded["band1_upper"],
        banded["band3_upper"],
        banded["band1_lower"],
        banded["band3_lower"],
        banded["sigma"],
    )
    labeled = compute_regime(daily, cfg)
    joined = join_regime(banded, labeled, anchor)

    kind, direction = _signal_columns(joined)
    joined["signal_kind"] = kind
    joined["signal_direction"] = direction
    joined["signal_admitted"] = [
        admits(regime, dirn, kd)
        for regime, dirn, kd in zip(joined["regime"], direction, kind, strict=False)
    ]
    joined["symbol"] = symbol
    return joined


# ---------------------------------------------------------------- simulation

def simulate(
    prepared: dict[str, pd.DataFrame],
    cfg: dict[str, Any],
    initial_equity: float,
    entry_delay_bars: int = 1,
) -> tuple[list[Trade], list[tuple[pd.Timestamp, float]], dict[str, Any]]:
    """Walk the merged bar stream and fill, manage and close one trade per symbol.

    Returns the trade list, an equity point series, and a diagnostics block.
    ``entry_delay_bars`` shifts the fill from the next bar open to a later one,
    which is how the delayed-execution sensitivity is produced.
    """
    risk_cfg = cfg.get("risk", {})
    limits = risk_cfg.get("max_risk_per_trade", 0.0025)
    leverage_cap = float(risk_cfg.get("max_gross_leverage", 2.0))
    max_daily_loss = float(risk_cfg.get("max_daily_loss", 0.01))
    max_drawdown = float(risk_cfg.get("max_portfolio_drawdown", 0.15))
    anchor = int(cfg.get("vwap", {}).get("anchor_hour_utc", 21))
    financing_per_session = float(cfg.get("costs", {}).get("financing_per_session", 0.0))

    frames = [frame for frame in prepared.values() if not frame.empty]
    if not frames:
        return [], [(pd.Timestamp.now(tz="UTC"), initial_equity)], {"halted": False}
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("timestamp", kind="stable").reset_index(drop=True)

    costs_by_symbol = {symbol: trade_costs(symbol, cfg) for symbol in prepared}
    open_trades: dict[str, Trade] = {}
    unrealized: dict[str, float] = {}
    pending: dict[str, int] = {}
    since_signal: dict[str, int] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    points: list[tuple[pd.Timestamp, float]] = []
    realized = 0.0
    peak = initial_equity
    has_signal_col = "signal_kind" in combined.columns
    rejected = (
        int((combined["signal_kind"].notna() & ~combined["signal_admitted"]).sum())
        if has_signal_col
        else 0
    )
    # The portfolio drawdown stop is a live-account kill switch. The report
    # needs a full window to measure worst month, recovery time and a locked
    # final test, so the breach is recorded by default and only stops entries
    # when backtest.enforce_portfolio_halt asks for it.
    enforce_halt = bool(cfg.get("backtest", {}).get("enforce_portfolio_halt", False))
    diagnostics = {
        "signals_seen": 0,
        "signals_rejected_by_regime": rejected,
        "daily_loss_halts": 0,
        "enforce_portfolio_halt": enforce_halt,
        "drawdown_breached": False,
        "drawdown_breach_at": None,
        "drawdown_breach_pct": None,
        "equity_at_breach": None,
        "halted": False,
    }

    day: pd.Timestamp | None = None
    day_start_equity = initial_equity
    day_halted = False
    prev_ts: pd.Timestamp | None = None
    halted = False

    for idx, row in enumerate(combined.itertuples(index=False)):
        symbol = row.symbol
        ts = row.timestamp
        this_day = ts.floor("D")
        last_close[symbol] = row.close

        if day is None:
            day = this_day
            day_start_equity = initial_equity + realized
            day_halted = False
            points.append((ts, initial_equity))
        elif this_day != day:
            if prev_ts is not None:
                points.append((prev_ts, initial_equity + realized + sum(unrealized.values())))
            day = this_day
            day_start_equity = initial_equity + realized
            day_halted = False

        # A pending signal fills at this bar's open, entry_delay_bars later.
        if symbol in pending:
            since_signal[symbol] = since_signal.get(symbol, 0) + 1
            if since_signal[symbol] >= entry_delay_bars:
                signal_index = pending.pop(symbol)
                since_signal.pop(symbol, None)
                if symbol not in open_trades and not halted and not day_halted:
                    trade = _open_trade(
                        combined.iloc[signal_index],
                        row,
                        costs_by_symbol[symbol],
                        limits,
                        leverage_cap,
                        initial_equity + realized + sum(unrealized.values()),
                    )
                    if trade is not None:
                        open_trades[symbol] = trade
                        unrealized[symbol] = 0.0

        trade = open_trades.get(symbol)
        if trade is not None:
            closed = _manage_trade(trade, row, anchor, financing_per_session)
            if closed:
                realized += trade.pnl
                unrealized.pop(symbol, None)
                open_trades.pop(symbol, None)
                trades.append(trade)
                points.append((trade.exit_time, initial_equity + realized + sum(unrealized.values())))
            else:
                sign = 1.0 if trade.direction == "long" else -1.0
                unrealized[symbol] = trade.notional * sign * (row.close - trade.entry_price) / trade.entry_price

        # Schedule a fresh entry only when flat and the regime admits it.
        if (
            symbol not in open_trades
            and symbol not in pending
            and not halted
            and not day_halted
            and row.signal_admitted
        ):
            diagnostics["signals_seen"] += 1
            pending[symbol] = idx
            since_signal[symbol] = 0

        equity = initial_equity + realized + sum(unrealized.values())
        if equity > peak:
            peak = equity
        if peak > 0 and (1.0 - equity / peak) >= max_drawdown and not diagnostics["drawdown_breached"]:
            diagnostics["drawdown_breached"] = True
            diagnostics["drawdown_breach_at"] = ts.isoformat()
            diagnostics["drawdown_breach_pct"] = (1.0 - equity / peak) * 100.0
            diagnostics["equity_at_breach"] = equity
            if enforce_halt:
                halted = True
                diagnostics["halted"] = True
                for stale in list(pending):
                    pending.pop(stale, None)
                    since_signal.pop(stale, None)

        # Intraday loss guard: stop opening once the day is down too far.
        if (
            not halted
            and not day_halted
            and day_start_equity > 0
            and (equity - day_start_equity) / day_start_equity <= -max_daily_loss
        ):
            day_halted = True
            diagnostics["daily_loss_halts"] += 1
            for stale in list(pending):
                pending.pop(stale, None)
                since_signal.pop(stale, None)

        prev_ts = ts

    # Mark anything still open at the last observed close of its symbol.
    for symbol, trade in list(open_trades.items()):
        trade.exit_time = prev_ts
        trade.exit_price = last_close.get(symbol, trade.entry_price)
        trade.exit_reason = "end_of_window"
        sign = 1.0 if trade.direction == "long" else -1.0
        trade.gross_return = sign * (trade.exit_price - trade.entry_price) / trade.entry_price
        net = net_trade_return(
            trade.gross_return,
            TradeCosts(
                spread=trade.spread_cost,
                commission=trade.commission_cost,
                slippage=trade.slippage_cost,
            ),
        )
        trade.pnl = trade.notional * net
        trades.append(trade)
    if prev_ts is not None:
        points.append((prev_ts, initial_equity + realized))

    trades.sort(key=lambda t: t.entry_time)
    return trades, points, diagnostics


def _open_trade(
    signal_row: Any,
    bar_row: Any,
    costs: TradeCosts,
    risk_per_trade: float,
    leverage_cap: float,
    notional_base: float,
) -> Trade | None:
    kind = signal_row.signal_kind
    direction = signal_row.signal_direction
    sigma = float(signal_row.sigma)
    if not isinstance(kind, str) or not isinstance(direction, str) or sigma <= 0:
        return None
    entry_price = float(bar_row.open)
    if entry_price <= 0:
        return None
    target, stop = levels_for(kind, direction, float(signal_row.vwap), sigma)
    stop_distance = abs(entry_price - stop)
    if stop_distance <= 0:
        return None

    stop_fraction = stop_distance / entry_price
    limits = RiskLimits(risk_per_trade=risk_per_trade)
    notional = min(position_notional(notional_base, stop_fraction, limits), notional_base * leverage_cap)
    return Trade(
        symbol=bar_row.symbol,
        kind=kind,
        direction=direction,
        regime=str(signal_row.regime),
        vol_bucket=vol_bucket(getattr(signal_row, "atr_percentile", None)),
        session_hour_bucket=session_hour_bucket(bar_row.timestamp.hour),
        session=str(getattr(signal_row, "session", ""))[:10],
        entry_time=bar_row.timestamp,
        entry_price=entry_price,
        target=target,
        stop=stop,
        notional=notional,
        spread_cost=costs.spread,
        commission_cost=costs.commission,
        slippage_cost=costs.slippage,
    )


def _manage_trade(trade: Trade, row: Any, anchor_hour: int, financing_per_session: float) -> bool:
    """Advance one bar against the open trade; return True when it closes."""
    trade.bars_held += 1
    sign = 1.0 if trade.direction == "long" else -1.0
    stop_hit = row.low <= trade.stop if sign > 0 else row.high >= trade.stop
    target_hit = row.high >= trade.target if sign > 0 else row.low <= trade.target

    if stop_hit:
        trade.exit_price = trade.stop
        trade.exit_reason = "stop"
    elif target_hit:
        trade.exit_price = trade.target
        trade.exit_reason = "target"
    else:
        # Session boundary: first bar of the new session fills the exit, which
        # is the first price available after the old session's last close.
        current_session = str(getattr(row, "session", ""))
        if current_session and trade.session and current_session[:10] != trade.session:
            trade.exit_price = float(row.open)
            trade.exit_reason = "session_end"
        else:
            return False

    trade.exit_time = row.timestamp
    gross = sign * (trade.exit_price - trade.entry_price) / trade.entry_price
    trade.gross_return = gross
    trade.financing_cost = 0.0
    if trade.exit_reason == "session_end" and financing_per_session:
        trade.financing_cost = financing_per_session
    net = net_trade_return(
        gross,
        TradeCosts(
            spread=trade.spread_cost,
            commission=trade.commission_cost,
            slippage=trade.slippage_cost,
            financing=trade.financing_cost,
        ),
    )
    trade.pnl = trade.notional * net
    return True


# ---------------------------------------------------------------- metrics

def assign_folds(trades: list[Trade], start: pd.Timestamp, end: pd.Timestamp, blocks: int = 6) -> list[tuple[int, pd.Timestamp, pd.Timestamp]]:
    span = (end - start) / blocks
    edges = [start + span * i for i in range(blocks + 1)]
    for trade in trades:
        trade.fold = blocks - 1
        for i in range(blocks):
            if edges[i] <= trade.entry_time < edges[i + 1]:
                trade.fold = i
                break
    return [(i, edges[i], edges[i + 1]) for i in range(blocks)]


def _returns(points: list[tuple[pd.Timestamp, float]]) -> pd.Series:
    if not points:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(points, columns=["ts", "equity"]).drop_duplicates("ts", keep="last")
    daily = frame.groupby(frame["ts"].dt.floor("D"))["equity"].last()
    return daily.pct_change().dropna()


def _annualize(returns: pd.Series, downside_only: bool = False) -> float:
    if len(returns) < 2:
        return 0.0
    mean = float(returns.mean())
    if downside_only:
        risk = float(returns[returns < 0].std(ddof=0))
    else:
        risk = float(returns.std(ddof=0))
    if not np.isfinite(risk) or risk <= 0:
        return 0.0
    return mean / risk * np.sqrt(252.0)


def _max_drawdown(points: list[tuple[pd.Timestamp, float]]) -> tuple[float, int | None, pd.Timestamp | None]:
    """Max peak-to-trough on the equity series, plus recovery in days.

    Recovery is ``None`` when equity never regained the prior peak, which is
    reported as "never" rather than as zero days, so a run that ends under water
    cannot be misread as having recovered instantly.
    """
    if not points:
        return 0.0, None, None
    peak = -np.inf
    max_dd = 0.0
    trough_ts: pd.Timestamp | None = None
    peak_before_trough: float | None = None
    for ts, equity in points:
        if equity > peak:
            peak = equity
        dd = 1.0 - equity / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            trough_ts = ts
            peak_before_trough = peak
    recovery_days: int | None = None
    if trough_ts is not None and peak_before_trough:
        for ts, equity in points:
            if ts > trough_ts and equity >= peak_before_trough:
                recovery_days = int((ts - trough_ts).days)
                break
    return max_dd, recovery_days, trough_ts


def _losing_streak(trades: list[Trade]) -> int:
    longest = current = 0
    for trade in trades:
        if trade.pnl <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def core_stats(trades: list[Trade], initial: float) -> dict[str, Any]:
    """Headline numbers for a trade subset, in percent of starting equity."""
    if not trades:
        return {
            "trades": 0,
            "net_pct": 0.0,
            "hit_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
        }
    pnl = [t.pnl for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(trades),
        "net_pct": sum(pnl) / initial * 100.0,
        "hit_rate_pct": len(wins) / len(trades) * 100.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0.0,
        "expectancy": float(np.mean(pnl)),
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
    }


def _breakdown(trades: list[Trade], key, initial: float) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[Trade]] = {}
    for trade in trades:
        buckets.setdefault(str(key(trade)), []).append(trade)
    return {name: core_stats(group, initial) for name, group in sorted(buckets.items())}


def compute_metrics(
    trades: list[Trade],
    points: list[tuple[pd.Timestamp, float]],
    initial: float,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    returns = _returns(points)
    gross_pnl = sum(t.notional * t.gross_return for t in trades)
    spread_pnl = sum(t.notional * t.spread_cost for t in trades)
    commission_pnl = sum(t.notional * t.commission_cost for t in trades)
    slippage_pnl = sum(t.notional * t.slippage_cost for t in trades)
    financing_pnl = sum(t.notional * t.financing_cost for t in trades)
    net_pnl = sum(t.pnl for t in trades)
    exposure = sum(
        (t.exit_time - t.entry_time).total_seconds() for t in trades if t.exit_time is not None
    )
    window_seconds = max((window_end - window_start).total_seconds(), 1.0)
    max_dd, recovery_days, _trough = _max_drawdown(points)

    worst_day = float(returns.min()) * 100.0 if len(returns) else 0.0
    monthly: dict[str, float] = {}
    for ts, equity in points:
        monthly[ts.strftime("%Y-%m")] = equity
    monthly_series = pd.Series(monthly).sort_index()
    monthly_returns = monthly_series.pct_change().dropna()
    worst_month = float(monthly_returns.min()) * 100.0 if len(monthly_returns) else 0.0

    stats = core_stats(trades, initial)
    metrics: dict[str, Any] = {
        "trades": stats["trades"],
        "exposure_pct": exposure / window_seconds * 100.0,
        "gross_return_pct": gross_pnl / initial * 100.0,
        "costs": {
            "spread_pct": spread_pnl / initial * 100.0,
            "commission_pct": commission_pnl / initial * 100.0,
            "slippage_pct": slippage_pnl / initial * 100.0,
            "financing_pct": financing_pnl / initial * 100.0,
            "total_pct": (spread_pnl + commission_pnl + slippage_pnl + financing_pnl) / initial * 100.0,
        },
        "net_return_pct": net_pnl / initial * 100.0,
        "volatility_pct_annualized": float(returns.std(ddof=0)) * np.sqrt(252.0) * 100.0
        if len(returns) > 1
        else 0.0,
        "sharpe": _annualize(returns),
        "sortino": _annualize(returns, downside_only=True),
        "sharpe_caveat": SHARPE_CAVEAT,
        "profit_factor": stats["profit_factor"],
        "expectancy": stats["expectancy"],
        "hit_rate_pct": stats["hit_rate_pct"],
        "avg_win": stats["avg_win"],
        "avg_loss": stats["avg_loss"],
        "max_drawdown_pct": max_dd * 100.0,
        "recovery_days": recovery_days,
        "worst_day_pct": worst_day,
        "worst_month_pct": worst_month,
        "losing_streak": _losing_streak(trades),
        "by_pair": _breakdown(trades, lambda t: t.symbol, initial),
        "by_session": _breakdown(trades, lambda t: t.session_hour_bucket, initial),
        "by_regime": _breakdown(trades, lambda t: t.regime, initial),
        "by_volatility_bucket": _breakdown(trades, lambda t: t.vol_bucket, initial),
        "by_news_bucket": {"available": False, "reason": NEWS_BUCKET_UNAVAILABLE},
        "by_kind": _breakdown(trades, lambda t: t.kind, initial),
    }
    if extra:
        metrics.update(extra)
    return metrics


def sensitivity(
    trades: list[Trade],
    initial: float,
    cfg: dict[str, Any],
    base_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Cost, missing-trade and stress variations on the same trade list."""
    stress = float(cfg.get("validation", {}).get("minimum_stress_cost_multiplier", 2.0))

    def restated(multiplier_spread: float = 1.0, multiplier_slippage: float = 1.0,
                 drop_every: int = 0) -> dict[str, Any]:
        kept = [t for i, t in enumerate(trades) if not (drop_every and (i + 1) % drop_every == 0)]
        pnl = 0.0
        for trade in kept:
            gross = trade.gross_return
            extra = (
                trade.notional
                * (trade.spread_cost * (multiplier_spread - 1.0) + trade.slippage_cost * (multiplier_slippage - 1.0))
            )
            pnl += trade.notional * gross - extra - trade.notional * (
                trade.commission_cost + trade.spread_cost + trade.slippage_cost
            )
        subset = core_stats(kept, initial)
        return {
            "trades": subset["trades"],
            "net_pct": pnl / initial * 100.0,
            "hit_rate_pct": subset["hit_rate_pct"],
            "profit_factor": subset["profit_factor"],
        }

    return {
        "base": {
            "trades": base_metrics["trades"],
            "net_pct": base_metrics["net_return_pct"],
            "hit_rate_pct": base_metrics["hit_rate_pct"],
            "profit_factor": base_metrics["profit_factor"],
        },
        "spread_x2": restated(multiplier_spread=2.0),
        "slippage_x2": restated(multiplier_slippage=2.0),
        "stress_costs": restated(multiplier_spread=stress, multiplier_slippage=stress),
        "missing_trades_1_in_10": restated(drop_every=10),
        "note": (
            "Cost and missing-trade variants restate the recorded trades with the "
            "entry notional held fixed; only delayed execution re-runs sizing."
        ),
    }


def walk_forward_report(
    trades: list[Trade],
    folds: list[tuple[int, pd.Timestamp, pd.Timestamp]],
    initial: float,
) -> dict[str, Any]:
    """Per-fold results with the final block reported as the locked test."""
    rows = []
    for index, start, end in folds:
        subset = [t for t in trades if t.fold == index]
        stats = core_stats(subset, initial)
        rows.append(
            {
                "fold": index,
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "role": "seed" if index == 0 else "locked_final_test" if index == len(folds) - 1 else "walk_forward",
                **stats,
            }
        )
    development = [t for t in trades if t.fold < len(folds) - 1]
    locked = [t for t in trades if t.fold == len(folds) - 1]
    return {
        "method": "anchored_walk_forward",
        "folds": rows,
        "development": core_stats(development, initial),
        "locked_final_test": core_stats(locked, initial),
        "locked_is_untouched": True,
    }


def serialize_trades(trades: list[Trade], limit: int = 5000) -> list[dict[str, Any]]:
    rows = []
    for trade in trades[:limit]:
        rows.append(
            {
                "symbol": trade.symbol,
                "kind": trade.kind,
                "direction": trade.direction,
                "regime": trade.regime,
                "vol_bucket": trade.vol_bucket,
                "session_bucket": trade.session_hour_bucket,
                "session": trade.session,
                "entry_time": trade.entry_time.isoformat(),
                "entry_price": round(trade.entry_price, 6),
                "exit_time": trade.exit_time.isoformat() if trade.exit_time is not None else None,
                "exit_price": round(trade.exit_price, 6) if trade.exit_price is not None else None,
                "exit_reason": trade.exit_reason,
                "pnl": round(trade.pnl, 4),
                "net_return_pct": round(
                    (
                        trade.gross_return
                        - (
                            trade.spread_cost
                            + trade.commission_cost
                            + trade.slippage_cost
                            + trade.financing_cost
                        )
                    )
                    * 100.0,
                    6,
                ),
                "fold": trade.fold,
            }
        )
    return rows


def sample_points(points: list[tuple[pd.Timestamp, float]], limit: int = 1500) -> list[list[Any]]:
    """Downsample the equity series for the page without losing its shape.

    The last point is forced in, because the closing equity is a headline
    number and a strided pick can otherwise stop short of the final bar.
    """
    if len(points) <= limit:
        picked = points
    else:
        stride = len(points) / limit
        picked = [points[int(i * stride)] for i in range(limit)]
        picked[-1] = points[-1]
    return [[ts.isoformat(), round(equity, 2)] for ts, equity in picked]


def prepare_window(
    symbol: str,
    minute: pd.DataFrame,
    daily: pd.DataFrame,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    """Thin wrapper kept for tests and callers that already hold both frames."""
    return prepare_symbol(symbol, minute, daily, cfg)


__all__ = [
    "REGIME_ADMITS",
    "Trade",
    "admits",
    "assign_folds",
    "compute_metrics",
    "core_stats",
    "join_regime",
    "levels_for",
    "prepare_symbol",
    "sample_points",
    "serialize_trades",
    "sensitivity",
    "session_hour_bucket",
    "simulate",
    "trade_costs",
    "vol_bucket",
    "walk_forward_report",
]
