"""Tests for the multi-day swing variant (decision: swing-variant.md)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fx_strategy.analysis.config import load_config
from fx_strategy.backtest.engine import REGIME_ADMITS, Trade, admits, assign_folds
from fx_strategy.backtest.swing import (
    all_in_stress,
    financing_sensitivity,
    fold_stress,
    guardrails,
    prepare_daily,
    simulate_swing,
    swing_admits,
    swing_signals,
    update_trail,
)


# ---------------------------------------------------------------- fixtures

def cfg() -> dict:
    return load_config()


def _daily(stamps: list[str], ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [pd.Timestamp(stamp, tz="UTC") for stamp in stamps],
            "open": [row[0] for row in ohlc],
            "high": [row[1] for row in ohlc],
            "low": [row[2] for row in ohlc],
            "close": [row[3] for row in ohlc],
            "volume": [1.0] * len(ohlc),
        }
    )


def _swing_frame(
    stamps: list[str],
    ohlc: list[tuple[float, float, float, float]],
    *,
    regime: str | list[str] = "uptrend",
    atr: float = 0.01,
    directions: dict[int, str] | None = None,
    breakouts: dict[int, str] | None = None,
    symbol: str = "TEST",
) -> pd.DataFrame:
    frame = _daily(stamps, ohlc)
    count = len(frame)
    frame["regime"] = [regime] * count if isinstance(regime, str) else list(regime)
    frame["atr"] = atr
    frame["atr_percentile"] = 50.0
    frame["swing_breakout"] = pd.Series([pd.NA] * count, dtype="object")
    frame["swing_direction"] = pd.Series([pd.NA] * count, dtype="object")
    for index, direction in (directions or {}).items():
        frame.at[index, "swing_direction"] = direction
        frame.at[index, "swing_breakout"] = direction
    for index, breakout in (breakouts or {}).items():
        frame.at[index, "swing_breakout"] = breakout
    frame["symbol"] = symbol
    return frame


def _run(frame: pd.DataFrame, config: dict, initial: float = 100_000.0, delay: int = 1):
    return simulate_swing({"TEST": frame}, config, initial, entry_delay_bars=delay)


FLAT = (1.10, 1.10, 1.10, 1.10)


# ---------------------------------------------------------------- gate

def test_swing_admits_matrix_is_trend_only():
    assert swing_admits("uptrend", "long")
    assert not swing_admits("uptrend", "short")
    assert swing_admits("downtrend", "short")
    assert not swing_admits("downtrend", "long")
    assert not swing_admits("high_vol", "long")
    assert not swing_admits("high_vol", "short")
    assert not swing_admits("low_vol", "long")
    assert not swing_admits("low_vol", "short")
    assert not swing_admits(np.nan, "long")
    assert not swing_admits("uptrend", 5)


def test_swing_admits_is_subset_of_regime_admits_continuation():
    for regime in REGIME_ADMITS:
        for direction in ("long", "short"):
            if swing_admits(regime, direction):
                assert admits(regime, direction, "continuation")


def test_config_exposes_swing_block():
    swing = cfg()["swing"]
    assert int(swing["entry_channel_days"]) == 20
    assert float(swing["trail_atr_multiple"]) == 3.0
    assert int(swing["max_hold_sessions"]) == 20
    assert int(swing["delayed_entry_delay_bars"]) == 2


# ---------------------------------------------------------------- signals

def test_breakout_window_excludes_signal_bar_and_is_strict():
    stamps = [f"2026-03-{day:02d}" for day in range(1, 22)]
    # bars 0..18 flat, bar 19 sets the 100.00 prior high, bar 20 is the signal bar
    ohlc = [(85.0, 90.0, 80.0, 85.0)] * 19 + [(95.0, 100.0, 80.0, 95.0)]
    # equality does not fire
    equal = list(ohlc) + [(95.0, 120.0, 95.0, 100.0)]
    frame = _daily(stamps, equal)
    frame["regime"] = "uptrend"
    out = swing_signals(frame, cfg())
    assert pd.isna(out["swing_breakout"].iloc[20])

    # one tick above fires
    above = list(ohlc) + [(95.0, 120.0, 95.0, 100.5)]
    above_frame = _daily(stamps, above)
    above_frame["regime"] = "uptrend"
    out = swing_signals(above_frame, cfg())
    assert out["swing_breakout"].iloc[20] == "long"

    # the signal bar's own high enters the channel only for later bars:
    # bar 21's window now tops at 120, so a close of 100.8 cannot fire
    later = above + [(100.5, 101.0, 100.0, 100.8)]
    later_frame = _daily(stamps + ["2026-03-22"], later)
    later_frame["regime"] = "uptrend"
    out = swing_signals(later_frame, cfg())
    assert pd.isna(out["swing_breakout"].iloc[21])


def test_breakout_gated_off_in_volatility_states():
    stamps = [f"2026-03-{day:02d}" for day in range(1, 22)]
    ohlc = [(85.0, 90.0, 80.0, 85.0)] * 19 + [(95.0, 100.0, 80.0, 95.0)] + [(95.0, 120.0, 95.0, 100.5)]
    frame = _daily(stamps, ohlc)
    frame["regime"] = ["uptrend"] * 20 + ["low_vol"]
    out = swing_signals(frame, cfg())
    # raw breakout recorded but direction withheld
    assert out["swing_breakout"].iloc[20] == "long"
    assert pd.isna(out["swing_direction"].iloc[20])
    # same bar in an admitted regime passes the gate
    frame["regime"] = "uptrend"
    out = swing_signals(frame, cfg())
    assert out["swing_direction"].iloc[20] == "long"


def test_fresh_channel_reentry_after_pullback():
    stamps = [ts.strftime("%Y-%m-%d") for ts in pd.date_range("2026-04-01", periods=42, freq="D")]
    ohlc = (
        [(85.0, 90.0, 80.0, 85.0)] * 19          # 0..18 flat
        + [(95.0, 100.0, 80.0, 95.0)]            # 19 sets prior high 100
        + [(95.0, 120.0, 95.0, 100.5)]           # 20 breaks out (own high 120 enters channel)
        + [(100.0, 105.0, 99.0, 100.0)] * 19     # 21..39 pull back, close below the 120 top
        + [(105.0, 110.0, 99.0, 105.0)]          # 40 fresh high 110, old 120 rotated out
        + [(105.0, 111.0, 105.0, 110.5)]         # 41 closes above the fresh channel
    )
    frame = _daily(stamps, ohlc)
    frame["regime"] = "uptrend"
    out = swing_signals(frame, cfg())
    assert out["swing_direction"].iloc[20] == "long"
    # nothing re-fires while the close sits under the old extreme
    assert out["swing_direction"].iloc[25] is pd.NA or pd.isna(out["swing_direction"].iloc[25])
    assert pd.isna(out["swing_direction"].iloc[35])
    # a later bar closing above the rotated-in channel re-enters
    assert out["swing_direction"].iloc[41] == "long"


def test_prepare_daily_warms_up_without_signals():
    frame = _daily(
        [f"2026-03-{day:02d}" for day in range(1, 6)],
        [FLAT] * 5,
    )
    out = prepare_daily("EURUSD", frame, cfg())
    assert (out["symbol"] == "EURUSD").all()
    assert out["regime"].isna().all(), "regime needs warmup history"
    assert out["swing_direction"].isna().all()


# ---------------------------------------------------------------- trail

def test_update_trail_ratchets_and_never_loosens():
    assert update_trail(None, "long", 1.30, 0.01, 3.0) == pytest.approx(1.27)
    assert update_trail(1.10, "long", 1.30, 0.01, 3.0) == pytest.approx(1.27)
    assert update_trail(1.10, "long", 1.00, 0.01, 3.0) == 1.10, "never loosens long"
    assert update_trail(1.10, "short", 0.90, 0.01, 3.0) == pytest.approx(0.93)
    assert update_trail(1.10, "short", 1.20, 0.01, 3.0) == 1.10, "never loosens short"


# ---------------------------------------------------------------- fills

def test_entry_fills_next_open_at_signal_close_instant():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03"],
        [(1.10, 1.10, 1.10, 1.10), (1.10, 1.11, 1.09, 1.105), (1.11, 1.12, 1.10, 1.11)],
        directions={1: "long"},
    )
    trades, _, _ = _run(frame, cfg())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_price == 1.11, "fill is the next bar's open, not the signal close"
    assert trade.entry_time == pd.Timestamp("2026-03-02 21:00", tz="UTC")
    assert trade.kind == "donchian_breakout"
    assert trade.target == 0.0
    assert trade.exit_reason == "end_of_window"
    assert trade.entry_time < trade.exit_time


def test_end_of_window_exit_includes_financing():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03"],
        [(1.10, 1.10, 1.10, 1.10), (1.11, 1.11, 1.11, 1.11), (1.11, 1.12, 1.11, 1.115)],
        directions={0: "long"},
    )
    trades, points, _ = _run(frame, cfg())
    trade = trades[0]
    assert trade.exit_reason == "end_of_window"
    assert trade.exit_price == 1.115, "exits at the last close"
    assert trade.exit_time == pd.Timestamp("2026-03-03 21:00", tz="UTC")
    assert trade.bars_held == 2
    assert trade.financing_cost == pytest.approx(0.00025 * 2)
    assert trade.financing_cost > 0, "unlike the 1m engine, end_of_window pays financing"
    assert points[-1][1] == pytest.approx(100_000.0 + trade.pnl), "final point includes it"


def test_trail_gap_fills_at_open_not_the_level():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03"],
        [(1.10, 1.10, 1.10, 1.10), (1.11, 1.11, 1.10, 1.11), (1.00, 1.01, 0.99, 1.00)],
        directions={0: "long"},
    )
    trades, _, _ = _run(frame, cfg())
    trade = trades[0]
    assert trade.exit_reason == "trail"
    assert trade.exit_price == 1.00, "gap through the level: the open is the fill"
    assert trade.exit_time == pd.Timestamp("2026-03-03 21:00", tz="UTC"), "intrabar fill stamped at bar close, stamp + 21h"
    assert trade.bars_held == 2
    assert trade.financing_cost == pytest.approx(0.00025 * 2)


def test_trail_exit_fills_at_the_level_when_no_gap():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03"],
        [(1.10, 1.10, 1.10, 1.10), (1.11, 1.11, 1.10, 1.11), (1.09, 1.095, 1.07, 1.08)],
        directions={0: "long"},
    )
    trades, _, _ = _run(frame, cfg())
    trade = trades[0]
    assert trade.exit_reason == "trail"
    assert trade.exit_price == pytest.approx(1.08), "entry 1.11 minus 3 x 0.01"
    assert trade.exit_time == pd.Timestamp("2026-03-03 21:00", tz="UTC"), "intrabar fill stamped at bar close"


def test_trail_updates_at_close_and_tests_the_next_bar():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"],
        [
            (1.10, 1.10, 1.10, 1.10),   # signal
            (1.11, 1.11, 1.10, 1.11),   # fill, level 1.08, extreme 1.11
            (1.09, 1.50, 1.09, 1.45),   # low clears the old level, its own high raises it at close
            (1.40, 1.42, 1.30, 1.35),   # low breaks the raised 1.47, open gaps below it
        ],
        directions={0: "long"},
    )
    trades, _, _ = _run(frame, cfg())
    assert len(trades) == 1, "a bar never tests against a level its own extreme produced"
    trade = trades[0]
    assert trade.exit_price == 1.40, "level rose to 1.47 at the prior close, open gaps below it"
    assert trade.bars_held == 3
    assert trade.exit_reason == "trail"


def test_regime_flip_decided_at_close_fills_next_open():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"],
        [
            (1.10, 1.10, 1.10, 1.10),
            (1.11, 1.11, 1.10, 1.11),
            (1.11, 1.11, 1.10, 1.105),
            (1.112, 1.115, 1.11, 1.113),
        ],
        regime=["uptrend", "uptrend", "downtrend", "uptrend"],
        directions={0: "long"},
    )
    trades, _, _ = _run(frame, cfg())
    trade = trades[0]
    assert trade.exit_reason == "regime_flip"
    assert trade.exit_price == 1.112, "next bar's open, not the decision bar's close"
    assert trade.exit_time == pd.Timestamp("2026-03-03 21:00", tz="UTC")
    assert trade.bars_held == 3


def test_hold_cap_exits_next_open_after_max_sessions():
    config = cfg()
    config["swing"]["max_hold_sessions"] = 3
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"],
        [
            (1.10, 1.10, 1.10, 1.10),
            (1.11, 1.11, 1.11, 1.11),
            (1.11, 1.112, 1.109, 1.111),
            (1.111, 1.113, 1.11, 1.112),
            (1.114, 1.115, 1.113, 1.114),
        ],
        directions={0: "long"},
    )
    trades, _, _ = _run(frame, config)
    trade = trades[0]
    assert trade.exit_reason == "hold_cap"
    assert trade.exit_price == 1.114, "decision at close of session 3, fill at session 4's open"
    assert trade.bars_held == 4, "the exit bar is counted, matching the 1m engine"
    assert trade.financing_cost == pytest.approx(0.00025 * 4)


# ---------------------------------------------------------------- sizing

def test_sizing_uses_stop_fraction_and_caps_at_leverage():
    stamps = ["2026-03-01", "2026-03-02", "2026-03-03"]
    loose = _swing_frame(
        stamps,
        [(1.10, 1.10, 1.10, 1.10), (1.10, 1.11, 1.09, 1.105), (1.11, 1.11, 1.11, 1.11)],
        atr=0.01,
        directions={1: "long"},
    )
    tight = _swing_frame(
        stamps,
        [(1.10, 1.10, 1.10, 1.10), (1.10, 1.11, 1.09, 1.105), (1.11, 1.11, 1.11, 1.11)],
        atr=0.0001,
        directions={1: "long"},
    )
    loose_trade = _run(loose, cfg())[0][0]
    tight_trade = _run(tight, cfg())[0][0]
    # equity x 0.0025 / (3 x atr / entry), uncapped
    expected = 100_000.0 * 0.0025 / (3 * 0.01 / 1.11)
    assert loose_trade.notional == pytest.approx(expected, rel=1e-9)
    assert tight_trade.notional == 200_000.0, "capped at equity x max_gross_leverage"


def test_sizing_compounds_on_current_equity():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"],
        [
            (1.10, 1.10, 1.10, 1.10),
            (1.11, 1.11, 1.11, 1.11),
            (1.11, 1.20, 1.10, 1.15),
            (1.15, 1.16, 1.14, 1.15),
            (1.11, 1.11, 1.11, 1.11),
        ],
        directions={0: "long", 3: "long"},
    )
    trades, _, _ = _run(frame, cfg())
    assert len(trades) == 2
    assert trades[0].pnl > 0, "first trade wins at 1.15"
    assert trades[1].notional > trades[0].notional, "same stop fraction, larger equity"


# ---------------------------------------------------------------- halts and re-entry

def test_daily_loss_halt_drops_that_sessions_pending_signal():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"],
        [
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 1.0, 0.9995, 1.0),
            (0.994, 0.996, 0.993, 0.995),
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 1.0, 1.0, 1.0),
        ],
        atr=0.0004,
        directions={0: "long", 2: "long", 4: "long"},
    )
    trades, _, diagnostics = _run(frame, cfg())
    assert diagnostics["daily_loss_halts"] == 1, "session down more than max_daily_loss"
    assert diagnostics["signals_seen"] == 3
    assert len(trades) == 2, "the gap loss halted the session before its signal could fill"


def test_only_one_position_and_fresh_reentry_counts_signals():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"],
        [(1.11, 1.11, 1.11, 1.11)] * 6,
        regime=["uptrend", "uptrend", "uptrend", "downtrend", "uptrend", "uptrend"],
        directions={0: "long", 1: "long", 2: "long", 4: "long"},
    )
    trades, _, diagnostics = _run(frame, cfg())
    assert len(trades) == 2, "breakouts while a position is open are ignored"
    assert diagnostics["signals_seen"] == 2, "only the two signals taken from a flat book"


def test_diagnostics_reports_regime_rejections_and_filter_scope():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02"],
        [FLAT, FLAT],
        regime="low_vol",
        breakouts={0: "long"},
    )
    trades, _, diagnostics = _run(frame, cfg())
    assert trades == []
    assert diagnostics["signals_rejected_by_regime"] == 1
    assert diagnostics["signals_seen"] == 0
    assert diagnostics["entry_filters"]["swing_gate"] == "trend_only"
    assert "do not apply" in diagnostics["entry_filters"]["note"]


def test_points_are_chronological_and_folds_assign():
    frame = _swing_frame(
        ["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"],
        [
            (1.10, 1.10, 1.10, 1.10),
            (1.11, 1.11, 1.11, 1.11),
            (1.11, 1.20, 1.10, 1.15),
            (1.15, 1.16, 1.14, 1.15),
            (1.11, 1.11, 1.11, 1.11),
        ],
        directions={0: "long", 3: "long"},
    )
    trades, points, _ = _run(frame, cfg())
    for earlier, later in zip(points, points[1:]):
        assert earlier[0] <= later[0], "equity points never run backwards"

    start = pd.Timestamp("2026-03-01", tz="UTC")
    end = pd.Timestamp("2026-03-05", tz="UTC")
    folds = assign_folds(trades, start, end, 6)
    assert len(folds) == 6
    assert all(0 <= trade.fold < 6 for trade in trades)
    assert trades[0].entry_time >= start
    assert all(trade.exit_time <= end + pd.Timedelta(hours=21) for trade in trades)

    rows = fold_stress(trades, folds, 100_000.0)
    assert len(rows) == 6
    assert rows[-1]["fold"] == 5, "locked fold is the last block"
    assert {"fold", "net_pct_base", "net_pct_stressed", "trades"} <= set(rows[0])


# ---------------------------------------------------------------- guardrail math

def _metric_trade(fold: int, gross: float) -> Trade:
    """A directly constructed trade: 10k notional, 4.5 bp all-in costs.

    ``trade_costs`` returns zeros for an unknown symbol, so exact-value
    stress tests build Trade objects instead of running the simulator.
    """
    trade = Trade(
        symbol="TEST",
        kind="donchian_breakout",
        direction="long",
        regime="uptrend",
        vol_bucket="mid",
        session_hour_bucket="asia",
        session="2026-01-01",
        entry_time=pd.Timestamp("2026-01-01 21:00", tz="UTC"),
        entry_price=1.10,
        target=0.0,
        stop=1.09,
        notional=10_000.0,
        spread_cost=0.0001,
        commission_cost=0.0002,
        slippage_cost=0.0001,
        financing_cost=0.00005,
    )
    trade.gross_return = gross
    trade.pnl = trade.notional * (
        gross
        - (trade.spread_cost + trade.commission_cost + trade.slippage_cost + trade.financing_cost)
    )
    trade.fold = fold
    return trade


def _folds() -> list[tuple[int, pd.Timestamp, pd.Timestamp]]:
    start = pd.Timestamp("2026-01-01", tz="UTC")
    return [
        (index, start + pd.Timedelta(days=index), start + pd.Timedelta(days=index + 1))
        for index in range(6)
    ]


def _metrics(gross: float = 5.0, costs: float = 1.0, profit_factor: float = 2.0) -> dict:
    return {
        "gross_return_pct": gross,
        "costs": {"total_pct": costs},
        "profit_factor": profit_factor,
    }


def _failed(result: dict) -> list[str]:
    return [c["name"] for c in result["criteria"] if not c["passed"]]


GOOD_GROSS = [0.001, 0.002, 0.003, 0.005, 0.008, 0.04]


def test_all_in_stress_exact_values():
    trades = [_metric_trade(0, 0.01), _metric_trade(1, -0.005)]
    assert trades[0].pnl == pytest.approx(95.5)
    assert trades[1].pnl == pytest.approx(-54.5)

    stress = all_in_stress(trades, 100_000.0, 2.0)
    assert stress["multiplier"] == 2.0
    assert stress["trades"] == 2
    # 10k x (0.0100 - 2 x 0.00045) = 91.0; 10k x (-0.0050 - 2 x 0.00045) = -59.0
    assert stress["net_pct"] == pytest.approx((91.0 - 59.0) / 100_000.0 * 100.0)
    assert stress["net_pct"] == pytest.approx(0.032)
    assert stress["profit_factor"] == pytest.approx(91.0 / 59.0)
    assert stress["hit_rate_pct"] == pytest.approx(50.0)


def test_financing_sensitivity_is_linear_and_monotone():
    trades = [_metric_trade(0, 0.01), _metric_trade(1, -0.005)]
    sensitivity = financing_sensitivity(trades, 100_000.0)
    assert sensitivity["financing_0x"] == pytest.approx(0.042)
    assert sensitivity["financing_1x"] == pytest.approx(0.041)
    assert sensitivity["financing_2x"] == pytest.approx(0.040)
    assert (
        sensitivity["financing_0x"]
        > sensitivity["financing_1x"]
        > sensitivity["financing_2x"]
    ), "each extra financing turn costs the same equal step"


def test_guardrails_pass_when_all_five_criteria_hold():
    trades = [_metric_trade(index, gross) for index, gross in enumerate(GOOD_GROSS)]
    result = guardrails(trades, _metrics(), _folds(), 100_000.0, multiplier=2.0)
    assert result["all_passed"] is True
    assert all(c["passed"] is True for c in result["criteria"])
    assert [c["name"] for c in result["criteria"]] == [
        "gross_over_3x_costs",
        "profit_factor_over_1_3",
        "positive_skew",
        "all_folds_positive",
        "locked_green_at_stress",
    ]
    # pandas .skew() is the bias-corrected sample skewness:
    # biased g1 = 1.68, x sqrt(n(n-1))/(n-2) = 1.3693 -> 2.304
    assert result["skew"] == pytest.approx(2.30, abs=0.01)
    assert result["stress_multiplier"] == 2.0
    # locked fold restated: 10k x (0.04 - 2 x 0.00045) = 391 base-currency units
    locked = result["fold_stress"][-1]
    assert locked["fold"] == 5
    assert locked["trades"] == 1
    assert locked["net_pct_stressed"] == pytest.approx(391.0 / 100_000.0 * 100.0)


def test_guardrails_empty_fold_fails_only_all_folds_positive():
    grosses = [(0, 0.001), (1, 0.002), (2, 0.003), (4, 0.008), (5, 0.04)]
    trades = [_metric_trade(fold, gross) for fold, gross in grosses]
    result = guardrails(trades, _metrics(), _folds(), 100_000.0, multiplier=2.0)
    assert _failed(result) == ["all_folds_positive"]
    assert result["all_passed"] is False
    assert result["fold_stress"][3]["net_pct_base"] == 0.0, "an empty fold cannot agree"


def test_guardrails_low_profit_factor_fails_only_pf():
    trades = [_metric_trade(index, gross) for index, gross in enumerate(GOOD_GROSS)]
    result = guardrails(trades, _metrics(profit_factor=1.1), _folds(), 100_000.0, multiplier=2.0)
    assert _failed(result) == ["profit_factor_over_1_3"]


def test_guardrails_thin_gross_fails_only_gross_over_3x_costs():
    trades = [_metric_trade(index, gross) for index, gross in enumerate(GOOD_GROSS)]
    result = guardrails(trades, _metrics(gross=2.0), _folds(), 100_000.0, multiplier=2.0)
    assert _failed(result) == ["gross_over_3x_costs"]


def test_guardrails_negative_skew_fails_only_positive_skew():
    # one fat loss beside smaller wins makes the third moment negative while
    # every fold still nets positive at base costs (fold 1: -500 + 400 + 150)
    specs = (
        [(0, 25.0)]
        + [(1, -500.0), (1, 400.0), (1, 150.0)]
        + [(fold, 25.0) for fold in range(2, 6)]
    )
    trades = []
    for fold, pnl in specs:
        trade = _metric_trade(fold, pnl / 10_000.0 + 0.00045)
        assert trade.pnl == pytest.approx(pnl)
        trades.append(trade)

    result = guardrails(trades, _metrics(), _folds(), 100_000.0, multiplier=2.0)
    assert _failed(result) == ["positive_skew"]
    assert result["skew"] < 0.0
    assert result["fold_stress"][1]["net_pct_base"] == pytest.approx(50.0 / 100_000.0 * 100.0)
    # locked fold stressed: 10k x (0.00295 - 2 x 0.00045) = 20.5, still green
    assert result["fold_stress"][-1]["net_pct_stressed"] == pytest.approx(20.5 / 100_000.0 * 100.0)
