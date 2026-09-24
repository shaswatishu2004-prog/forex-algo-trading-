"""Multi-day Donchian breakout swing variant on daily bars.

Decision: docs/wayfinder/tickets/swing-variant.md. The one-minute VWAP engine
found no gross edge after costs on either pass, so this variant changes only the
horizon and keeps the validated machinery: the same four-state regime, the same
cost table, the same anchored walk-forward with a locked final test.

Signal and gate
---------------
A strict 20-day Donchian breakout: close above the highest high of the prior 20
daily bars reads long, below the lowest low reads short, the window excludes
the signal bar, and equality does not fire. ``swing_admits`` then gates the
breakout trend-only: uptrend admits longs, downtrend shorts, the two volatility
states admit nothing. That is a declared subset of the one-minute engine's
``REGIME_ADMITS`` continuation rows. The one-minute ``filters`` block does not
transfer; diagnostics say so instead of reusing it.

Exits
-----
The chandelier trail is 3 x ATR(14), recomputed at each bar close but
ratcheted so it never loosens, which keeps the stop aligned with the
risk-per-trade sizing instead of drifting away from it. The breach tests the
level known before the bar trades (no same-bar re-aim), and the fill is
gap-aware: ``min(trail, open)`` for longs, ``max(trail, open)`` for shorts. An
opposite-trend regime flip and the 20-session hold cap are decided at bar close
and fill at the next open. ``end_of_window`` closes at the last close and pays
financing, unlike the one-minute engine's omission.

Time convention
---------------
A daily bar stamped D covers the session [D - 3h, D + 21h] UTC and is knowable
at D + 21h, the same convention ``join_regime`` uses. Entries and exits filled
at an open are stamped at that open instant (fill bar stamp - 3h); intrabar
trail fills and ``end_of_window`` are stamped at the bar's close instant
(stamp + 21h) because the true intrabar instant is unknown on daily bars, which
overstates reported exposure by less than one session per trade. Every fill
lands on the 21:00 UTC roll, so the session-hour breakdown holds one bucket by
construction.

Costs
-----
Round-trip spread, commission and slippage come from ``trade_costs`` as in the
one-minute engine; financing is ``costs.financing_per_session`` once for every
daily bar held (unsigned, counted including the exit bar, so it is
conservative). The flat rate is a placeholder that dominates the about 1.3 bp
round-trip trading costs, so results also report 0x/1x/2x financing
sensitivity and an all-in stress that doubles all four cost lines.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fx_strategy.analysis.regime import compute_regime
from fx_strategy.backtest.engine import (
    Trade,
    session_hour_bucket,
    trade_costs,
    vol_bucket,
)
from fx_strategy.costs import TradeCosts, net_trade_return
from fx_strategy.risk import RiskLimits, position_notional

# Which directions each regime state admits for swing entries. A declared
# subset of engine.REGIME_ADMITS's continuation rows, enforced by a test.
SWING_ADMITS: dict[str, frozenset[str]] = {
    "uptrend": frozenset({"long"}),
    "downtrend": frozenset({"short"}),
}


# ---------------------------------------------------------------- signal

def swing_admits(regime: Any, direction: Any) -> bool:
    """Trend-only gate: uptrend lets longs through, downtrend shorts, nothing else."""
    if not isinstance(regime, str) or not isinstance(direction, str):
        return False
    rule = SWING_ADMITS.get(regime)
    return rule is not None and direction in rule


def swing_signals(daily: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Add Donchian channel, raw breakout and regime-gated direction columns.

    Point in time: the channel at bar t spans bars t-20..t-1 (shift first, so
    the signal bar's own extreme never feeds its own signal) and the regime
    label at t is trailing by construction.
    """
    params = (cfg or {}).get("swing", {})
    channel_days = int(params.get("entry_channel_days", 20))
    frame = daily.copy()
    prior_high = frame["high"].shift(1).rolling(channel_days, min_periods=channel_days).max()
    prior_low = frame["low"].shift(1).rolling(channel_days, min_periods=channel_days).min()

    long_break = (frame["close"] > prior_high).fillna(False)
    short_break = (frame["close"] < prior_low).fillna(False)

    breakout = pd.Series(pd.NA, index=frame.index, dtype="object")
    breakout[long_break] = "long"
    breakout[short_break] = "short"
    frame["swing_breakout"] = breakout

    regime = frame["regime"] if "regime" in frame.columns else pd.Series(np.nan, index=frame.index)
    admitted_long = long_break & regime.map(lambda label: swing_admits(label, "long"))
    admitted_short = short_break & regime.map(lambda label: swing_admits(label, "short"))
    direction = pd.Series(pd.NA, index=frame.index, dtype="object")
    direction[admitted_long] = "long"
    direction[admitted_short] = "short"
    frame["swing_direction"] = direction

    frame["channel_high"] = prior_high
    frame["channel_low"] = prior_low
    return frame


def prepare_daily(symbol: str, daily: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Label the full daily frame and attach swing signal columns for one symbol.

    Callers pass the whole history so the regime percentile and the 20-day
    channel are warm before the window starts, then clip to the window.
    """
    labeled = compute_regime(daily, cfg)
    frame = swing_signals(labeled, cfg)
    frame["symbol"] = symbol
    return frame


# ---------------------------------------------------------------- trail

def update_trail(
    trail: float | None,
    direction: str,
    extreme: float,
    atr: float,
    multiple: float,
) -> float:
    """One ratcheted chandelier step, applied at bar close.

    The level is the running extreme since entry offset by ``multiple`` x ATR.
    ``max`` for longs and ``min`` for shorts mean the trail can only move toward
    profit and never loosens, even when ATR expands.
    """
    if direction == "long":
        candidate = extreme - multiple * atr
        return candidate if trail is None else max(trail, candidate)
    candidate = extreme + multiple * atr
    return candidate if trail is None else min(trail, candidate)


# ---------------------------------------------------------------- simulation

def _open_swing_trade(
    signal_row: Any,
    bar_row: Any,
    costs: TradeCosts,
    risk_per_trade: float,
    leverage_cap: float,
    notional_base: float,
    trail_multiple: float,
    open_gap: pd.Timedelta,
) -> Trade | None:
    direction = signal_row.swing_direction
    atr = float(getattr(signal_row, "atr", np.nan))
    entry_price = float(bar_row.open)
    # The bar stamped D opens at D - open_gap; the fill is stamped at that
    # open instant, which is also the signal bar's close instant.
    fill_instant = bar_row.timestamp - open_gap
    if not isinstance(direction, str) or entry_price <= 0:
        return None
    if not np.isfinite(atr) or atr <= 0:
        return None

    stop = entry_price - trail_multiple * atr if direction == "long" else entry_price + trail_multiple * atr
    stop_fraction = abs(entry_price - stop) / entry_price
    if stop_fraction <= 0:
        return None
    limits = RiskLimits(risk_per_trade=risk_per_trade)
    notional = min(
        position_notional(notional_base, stop_fraction, limits),
        notional_base * leverage_cap,
    )
    return Trade(
        symbol=bar_row.symbol,
        kind="donchian_breakout",
        direction=direction,
        regime=str(getattr(signal_row, "regime", "")),
        vol_bucket=vol_bucket(getattr(signal_row, "atr_percentile", None)),
        session_hour_bucket=session_hour_bucket(fill_instant.hour),
        session=str(signal_row.timestamp.date()),
        entry_time=fill_instant,
        entry_price=entry_price,
        target=0.0,
        stop=stop,
        notional=notional,
        spread_cost=costs.spread,
        commission_cost=costs.commission,
        slippage_cost=costs.slippage,
    )


def _close_swing_trade(trade: Trade, financing_per_session: float) -> None:
    """Gross, financing for every bar held, and net pnl on the recorded costs."""
    sign = 1.0 if trade.direction == "long" else -1.0
    trade.gross_return = sign * (trade.exit_price - trade.entry_price) / trade.entry_price
    trade.financing_cost = financing_per_session * max(trade.bars_held, 0)
    net = net_trade_return(
        trade.gross_return,
        TradeCosts(
            spread=trade.spread_cost,
            commission=trade.commission_cost,
            slippage=trade.slippage_cost,
            financing=trade.financing_cost,
        ),
    )
    trade.pnl = trade.notional * net


def simulate_swing(
    prepared: dict[str, pd.DataFrame],
    cfg: dict[str, Any],
    initial_equity: float,
    entry_delay_bars: int = 1,
) -> tuple[list[Trade], list[tuple[pd.Timestamp, float]], dict[str, Any]]:
    """Walk the merged daily stream: fill on gated breaks, manage, close.

    Returns the trade list, an equity point series and a diagnostics block.
    Halt semantics mirror the one-minute engine: ``max_daily_loss`` halts
    entries for the session (one daily bar is one session here) and the
    portfolio drawdown records a breach without stopping unless configured.
    """
    swing_cfg = cfg.get("swing", {})
    trail_multiple = float(swing_cfg.get("trail_atr_multiple", 3.0))
    max_hold = int(swing_cfg.get("max_hold_sessions", 20))
    risk_cfg = cfg.get("risk", {})
    risk_per_trade = float(risk_cfg.get("max_risk_per_trade", 0.0025))
    leverage_cap = float(risk_cfg.get("max_gross_leverage", 2.0))
    max_daily_loss = float(risk_cfg.get("max_daily_loss", 0.01))
    max_drawdown = float(risk_cfg.get("max_portfolio_drawdown", 0.15))
    financing_per_session = float(cfg.get("costs", {}).get("financing_per_session", 0.0))
    anchor = int(cfg.get("vwap", {}).get("anchor_hour_utc", 21))
    enforce_halt = bool(cfg.get("backtest", {}).get("enforce_portfolio_halt", False))
    open_gap = pd.Timedelta(hours=24 - anchor)
    close_gap = pd.Timedelta(hours=anchor)

    frames = [frame for frame in prepared.values() if not frame.empty]
    if not frames:
        return [], [(pd.Timestamp.now(tz="UTC"), initial_equity)], {"halted": False}
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("timestamp", kind="stable").reset_index(drop=True)

    costs_by_symbol = {symbol: trade_costs(symbol, cfg) for symbol in prepared}
    rejected = int(
        (combined["swing_breakout"].notna() & combined["swing_direction"].isna()).sum()
    )
    diagnostics: dict[str, Any] = {
        "signals_seen": 0,
        "signals_rejected_by_regime": rejected,
        "entries_skipped_no_atr": 0,
        "entry_filters": {
            "note": (
                "The one-minute filters (skip_sessions, min_target_cost_multiple) "
                "do not apply to swing entries; the gate is swing_admits."
            ),
            "swing_gate": "trend_only",
        },
        "daily_loss_halts": 0,
        "enforce_portfolio_halt": enforce_halt,
        "drawdown_breached": False,
        "drawdown_breach_at": None,
        "drawdown_breach_pct": None,
        "equity_at_breach": None,
        "halted": False,
    }

    open_trades: dict[str, Trade] = {}
    mgmt: dict[str, dict[str, Any]] = {}
    unrealized: dict[str, float] = {}
    pending: dict[str, int] = {}
    since_signal: dict[str, int] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    points: list[tuple[pd.Timestamp, float]] = []
    realized = 0.0
    peak = initial_equity

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
            points.append((ts + close_gap, initial_equity))
        elif this_day != day:
            if prev_ts is not None:
                points.append((prev_ts + close_gap, initial_equity + realized + sum(unrealized.values())))
            day = this_day
            day_start_equity = initial_equity + realized
            day_halted = False

        # A pending signal fills at this bar's open, entry_delay_bars later.
        if symbol in pending:
            since_signal[symbol] = since_signal.get(symbol, 0) + 1
            if since_signal[symbol] >= entry_delay_bars:
                signal_idx = pending.pop(symbol)
                since_signal.pop(symbol, None)
                if symbol not in open_trades and not halted and not day_halted:
                    signal_row = combined.iloc[signal_idx]
                    equity_now = initial_equity + realized + sum(unrealized.values())
                    trade = _open_swing_trade(
                        signal_row,
                        row,
                        costs_by_symbol[symbol],
                        risk_per_trade,
                        leverage_cap,
                        equity_now,
                        trail_multiple,
                        open_gap,
                    )
                    if trade is None:
                        diagnostics["entries_skipped_no_atr"] += 1
                    else:
                        open_trades[symbol] = trade
                        unrealized[symbol] = 0.0
                        mgmt[symbol] = {
                            "trail": trade.stop,
                            "extreme": trade.entry_price,
                            "pending_exit": None,
                        }

        trade = open_trades.get(symbol)
        if trade is not None:
            state = mgmt[symbol]
            trade.bars_held += 1
            sign = 1.0 if trade.direction == "long" else -1.0
            closed = False
            if state["pending_exit"] is not None:
                # Decided at the previous close, filled at this open.
                trade.exit_price = float(row.open)
                trade.exit_reason = state["pending_exit"]
                trade.exit_time = ts - open_gap
                closed = True
            else:
                trail = float(state["trail"])
                breached = row.low <= trail if sign > 0 else row.high >= trail
                if breached:
                    # Gap-aware: through the level, the open is the fill.
                    trade.exit_price = min(trail, float(row.open)) if sign > 0 else max(trail, float(row.open))
                    trade.exit_reason = "trail"
                    trade.exit_time = ts + close_gap
                    closed = True
                else:
                    # Update the level at close, then decide at close. The bar
                    # never tests against a level its own extreme produced.
                    state["extreme"] = (
                        max(float(state["extreme"]), float(row.high))
                        if sign > 0
                        else min(float(state["extreme"]), float(row.low))
                    )
                    atr = float(row.atr)
                    if np.isfinite(atr) and atr > 0:
                        state["trail"] = update_trail(
                            trail, trade.direction, float(state["extreme"]), atr, trail_multiple
                        )
                    regime = row.regime
                    flipped = (sign > 0 and regime == "downtrend") or (
                        sign < 0 and regime == "uptrend"
                    )
                    if flipped:
                        state["pending_exit"] = "regime_flip"
                    elif trade.bars_held >= max_hold:
                        state["pending_exit"] = "hold_cap"

            if closed:
                _close_swing_trade(trade, financing_per_session)
                realized += trade.pnl
                unrealized.pop(symbol, None)
                open_trades.pop(symbol, None)
                mgmt.pop(symbol, None)
                trades.append(trade)
                points.append((trade.exit_time, initial_equity + realized + sum(unrealized.values())))
            else:
                unrealized[symbol] = (
                    trade.notional * sign * (row.close - trade.entry_price) / trade.entry_price
                )

        # Schedule a fresh entry only when flat, with the gate already applied.
        if (
            symbol not in open_trades
            and symbol not in pending
            and not halted
            and not day_halted
            and pd.notna(row.swing_direction)
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

        # Session loss guard: one daily bar is one session here, so a session
        # down too far clears anything scheduled at its close.
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

    # Mark anything still open at the window's last close, financing included.
    for symbol, trade in list(open_trades.items()):
        trade.exit_time = prev_ts + close_gap
        trade.exit_price = last_close.get(symbol, trade.entry_price)
        trade.exit_reason = "end_of_window"
        _close_swing_trade(trade, financing_per_session)
        realized += trade.pnl
        unrealized.pop(symbol, None)
        trades.append(trade)
    if prev_ts is not None:
        points.append((prev_ts + close_gap, initial_equity + realized))

    trades.sort(key=lambda t: t.entry_time)
    return trades, points, diagnostics


# ---------------------------------------------------------------- guardrails

def _stressed_pnl(trade: Trade, multiplier: float) -> float:
    """Pnl restated with every cost line multiplied, entry notional held fixed."""
    return trade.notional * (
        trade.gross_return
        - multiplier
        * (trade.spread_cost + trade.commission_cost + trade.slippage_cost + trade.financing_cost)
    )


def all_in_stress(trades: list[Trade], initial: float, multiplier: float = 2.0) -> dict[str, Any]:
    """Restate the recorded trades with all four cost lines multiplied."""
    pnls = [_stressed_pnl(trade, multiplier) for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "multiplier": multiplier,
        "trades": len(pnls),
        "net_pct": sum(pnls) / initial * 100.0 if initial else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0.0,
        "hit_rate_pct": len(wins) / len(pnls) * 100.0 if pnls else 0.0,
    }


def fold_stress(
    trades: list[Trade],
    folds: list[tuple[int, pd.Timestamp, pd.Timestamp]],
    initial: float,
    multiplier: float = 2.0,
) -> list[dict[str, Any]]:
    """Base and all-in stressed net per fold, in fold order."""
    rows = []
    for index, start, end in folds:
        subset = [t for t in trades if t.fold == index]
        stressed = [_stressed_pnl(trade, multiplier) for trade in subset]
        wins = [pnl for pnl in stressed if pnl > 0]
        losses = [pnl for pnl in stressed if pnl < 0]
        gross_loss = abs(sum(losses))
        rows.append(
            {
                "fold": index,
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "trades": len(subset),
                "net_pct_base": sum(t.pnl for t in subset) / initial * 100.0 if initial else 0.0,
                "net_pct_stressed": sum(stressed) / initial * 100.0 if initial else 0.0,
                "profit_factor_stressed": sum(wins) / gross_loss if gross_loss > 0 else 0.0,
            }
        )
    return rows


def financing_sensitivity(trades: list[Trade], initial: float) -> dict[str, float]:
    """Net return with financing at 0x, 1x and 2x, everything else held."""
    out: dict[str, float] = {}
    for label, multiplier in (("financing_0x", 0.0), ("financing_1x", 1.0), ("financing_2x", 2.0)):
        pnl = sum(
            trade.notional
            * (
                trade.gross_return
                - (
                    trade.spread_cost
                    + trade.commission_cost
                    + trade.slippage_cost
                    + trade.financing_cost * multiplier
                )
            )
            for trade in trades
        )
        out[label] = pnl / initial * 100.0 if initial else 0.0
    return out


def guardrails(
    trades: list[Trade],
    metrics: dict[str, Any],
    folds: list[tuple[int, pd.Timestamp, pd.Timestamp]],
    initial: float,
    multiplier: float = 2.0,
) -> dict[str, Any]:
    """Evaluate the five success criteria registered in swing-variant.md.

    Fixed before the first run: gross above three times costs, profit factor
    above 1.30, positive skew, every fold net positive at base costs (an empty
    fold cannot agree), and the locked final test green under the all-in
    stress. All five must pass.
    """
    pnls = pd.Series([t.pnl for t in trades], dtype=float)
    skew = float(pnls.skew()) if len(pnls) >= 3 else 0.0
    stressed = all_in_stress(trades, initial, multiplier)
    stressed_folds = fold_stress(trades, folds, initial, multiplier)

    costs_pct = float(metrics["costs"]["total_pct"])
    gross_pct = float(metrics["gross_return_pct"])
    profit_factor = float(metrics["profit_factor"])
    fold_nets = [row["net_pct_base"] for row in stressed_folds]
    locked = stressed_folds[-1]["net_pct_stressed"] if stressed_folds else 0.0

    criteria = [
        {
            "name": "gross_over_3x_costs",
            "value": gross_pct,
            "threshold": 3.0 * costs_pct,
            "passed": bool(costs_pct > 0 and gross_pct > 3.0 * costs_pct),
        },
        {
            "name": "profit_factor_over_1_3",
            "value": profit_factor,
            "threshold": 1.3,
            "passed": bool(profit_factor > 1.3),
        },
        {
            "name": "positive_skew",
            "value": skew,
            "threshold": 0.0,
            "passed": bool(skew > 0.0),
        },
        {
            "name": "all_folds_positive",
            "value": min(fold_nets) if fold_nets else 0.0,
            "threshold": 0.0,
            "passed": bool(fold_nets) and all(net > 0 for net in fold_nets),
        },
        {
            "name": "locked_green_at_stress",
            "value": locked,
            "threshold": 0.0,
            "passed": bool(locked > 0.0),
        },
    ]
    return {
        "criteria": criteria,
        "all_passed": all(criterion["passed"] for criterion in criteria),
        "stress_multiplier": multiplier,
        "skew": skew,
        "all_in_stress": stressed,
        "fold_stress": stressed_folds,
        "note": (
            "Criteria registered in docs/wayfinder/tickets/swing-variant.md "
            "before any run; the stress row doubles spread, commission, "
            "slippage and financing together."
        ),
    }


__all__ = [
    "SWING_ADMITS",
    "all_in_stress",
    "financing_sensitivity",
    "fold_stress",
    "guardrails",
    "prepare_daily",
    "simulate_swing",
    "swing_admits",
    "swing_signals",
    "update_trail",
]
