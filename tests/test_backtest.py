"""Tests for the backtest engine: regime join, signal rules, fills, metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fx_strategy.analysis.config import load_config
from fx_strategy.backtest.engine import (
    REGIME_ADMITS,
    Trade,
    admits,
    assign_folds,
    core_stats,
    join_regime,
    levels_for,
    sample_points,
    serialize_trades,
    simulate,
    trade_costs,
    vol_bucket,
    walk_forward_report,
)


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cfg() -> dict:
    return load_config()


def _minute(stamps: list[str], prices: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    rows = []
    for stamp, (open_, high, low, close) in zip(stamps, prices, strict=True):
        rows.append(
            {
                "timestamp": pd.Timestamp(stamp, tz="UTC"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100.0,
            }
        )
    return pd.DataFrame(rows)


def _regime_frame(labels: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [pd.Timestamp(stamp, tz="UTC") for stamp in labels],
            "regime": list(labels.values()),
            "atr_percentile": [50.0] * len(labels),
        }
    )


def _prepared(
    stamps: list[str],
    ohlc: list[tuple[float, float, float, float]],
    *,
    admitted: list[bool],
    sessions: list[str],
    signal_index: int = 0,
    kind: str = "mean_reversion",
    direction: str = "long",
    vwap: float = 1.10,
    sigma: float = 0.001,
    regime: str = "uptrend",
) -> pd.DataFrame:
    frame = _minute(stamps, ohlc)
    frame["vwap"] = vwap
    frame["sigma"] = sigma
    frame["session"] = sessions
    frame["regime"] = regime
    frame["atr_percentile"] = 50.0
    frame["signal_kind"] = pd.NA
    frame["signal_direction"] = pd.NA
    frame["signal_admitted"] = admitted
    frame.loc[signal_index, "signal_kind"] = kind
    frame.loc[signal_index, "signal_direction"] = direction
    frame["symbol"] = "TEST"
    return frame


# ---------------------------------------------------------------- regime join

def test_regime_join_never_reads_a_daily_bar_before_it_prints():
    """A daily bar dated D only exists at D 21:00, so minutes before that see D-1."""
    daily = _regime_frame({"2026-01-01": "uptrend", "2026-01-02": "downtrend"})
    minute = _minute(
        [
            "2026-01-01 12:00",
            "2026-01-01 21:30",
            "2026-01-02 12:00",
            "2026-01-02 22:00",
        ],
        [(1.0, 1.0, 1.0, 1.0)] * 4,
    )

    joined = join_regime(minute, daily, anchor_hour=21)
    labels = list(joined["regime"])

    assert pd.isna(labels[0]), "before the first daily bar prints there is no label"
    assert labels[1] == "uptrend"
    assert labels[2] == "uptrend", "a minute inside session D must not read daily D"
    assert labels[3] == "downtrend", "daily D is available once D 21:00 passes"


def test_regime_join_tolerates_a_missing_daily_bar():
    daily = _regime_frame({"2026-01-01": "uptrend"})
    minute = _minute(["2026-01-05 12:00"], [(1.0, 1.0, 1.0, 1.0)])
    joined = join_regime(minute, daily, anchor_hour=21)
    assert list(joined["regime"]) == ["uptrend"], "gaps fall back to the last printed label"


def test_regime_join_with_no_labels_leaves_the_column_null():
    daily = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2026-01-01", tz="UTC")],
            "regime": [np.nan],
            "atr_percentile": [np.nan],
        }
    )
    minute = _minute(["2026-01-02 12:00"], [(1.0, 1.0, 1.0, 1.0)])
    joined = join_regime(minute, daily, anchor_hour=21)
    assert joined["regime"].isna().all()
    assert "atr_percentile" in joined.columns


# ---------------------------------------------------------------- regime filter

def test_every_regime_state_is_covered_by_the_filter_table():
    assert set(REGIME_ADMITS) == {"uptrend", "downtrend", "low_vol", "high_vol"}


@pytest.mark.parametrize(
    ("regime", "direction", "kind", "expected"),
    [
        ("uptrend", "long", "mean_reversion", True),
        ("uptrend", "long", "continuation", True),
        ("uptrend", "short", "mean_reversion", False),
        ("downtrend", "short", "continuation", True),
        ("downtrend", "long", "continuation", False),
        ("low_vol", "long", "mean_reversion", True),
        ("low_vol", "short", "mean_reversion", True),
        ("low_vol", "long", "continuation", False),
        ("high_vol", "short", "continuation", True),
        ("high_vol", "short", "mean_reversion", False),
        (None, "long", "mean_reversion", False),
        ("uptrend", None, "mean_reversion", False),
        ("uptrend", "long", None, False),
        ("sideways", "long", "mean_reversion", False),
    ],
)
def test_regime_filter_admits_exactly_what_the_ticket_says(regime, direction, kind, expected):
    assert admits(regime, direction, kind) is expected


# ---------------------------------------------------------------- exit levels

@pytest.mark.parametrize(
    ("kind", "direction", "target", "stop"),
    [
        ("mean_reversion", "long", 1.10, 1.08),
        ("mean_reversion", "short", 1.10, 1.12),
        ("continuation", "long", 1.15, 1.11),
        ("continuation", "short", 1.05, 1.09),
    ],
)
def test_exit_levels_are_one_to_one_before_costs(kind, direction, target, stop):
    got_target, got_stop = levels_for(kind, direction, vwap=1.10, sigma=0.01)
    assert got_target == pytest.approx(target)
    assert got_stop == pytest.approx(stop)
    # Reward and risk match only where the entry is implied by the rule:
    # mean reversion touches one sigma out, continuation breaks a third.
    if kind == "mean_reversion":
        entry = 1.10 - 0.01 if direction == "long" else 1.10 + 0.01
    else:
        entry = 1.10 + 0.03 if direction == "long" else 1.10 - 0.03
    assert abs(got_target - entry) == pytest.approx(abs(entry - got_stop))


def test_mean_reversion_reward_matches_risk():
    target, stop = levels_for("mean_reversion", "long", vwap=1.10, sigma=0.01)
    entry = 1.10 - 0.01  # one sigma below VWAP, where the touch fired
    assert target - entry == pytest.approx(entry - stop)


def test_continuation_reward_matches_risk():
    target, stop = levels_for("continuation", "long", vwap=1.10, sigma=0.01)
    entry = 1.10 + 0.03  # three sigma above VWAP
    assert target - entry == pytest.approx(entry - stop)


# ---------------------------------------------------------------- costs

def test_costs_are_charged_only_for_enabled_components(cfg):
    costs = trade_costs("EURUSD", cfg)
    assert costs.spread == pytest.approx(cfg["costs"]["EURUSD"]["spread"])
    assert costs.commission == pytest.approx(cfg["costs"]["EURUSD"]["commission"])

    cfg["execution"]["include_spread"] = False
    assert trade_costs("EURUSD", cfg).spread == 0.0

    cfg["execution"]["include_commission"] = False
    assert trade_costs("EURUSD", cfg).commission == 0.0


def test_unknown_symbol_pays_no_costs(cfg):
    assert trade_costs("NOSUCH", cfg).total == 0.0


# --------------------------------------------------------------- entry filters

def _costly_cfg(cfg: dict) -> dict:
    """Give the synthetic TEST symbol an EURUSD-sized round-trip cost."""
    cfg["costs"]["TEST"] = {
        "spread": 0.000055,
        "commission": 0.000060,
        "slippage": 0.000018,
    }
    return cfg


def _three_bars(**kwargs) -> pd.DataFrame:
    return _prepared(
        stamps=["2026-01-02 10:00", "2026-01-02 10:01", "2026-01-02 10:02"],
        ohlc=[
            (1.0995, 1.0998, 1.0992, 1.0994),
            (1.0990, 1.0995, 1.0988, 1.0993),
            (1.0993, 1.1002, 1.0990, 1.1001),
        ],
        admitted=[True, False, False],
        sessions=["2026-01-02"] * 3,
        **kwargs,
    )


def test_session_filter_drops_a_signal_from_a_skipped_session(cfg):
    """Asia runs 21:00-07:00 UTC; a signal there is counted, not taken."""
    frame = _three_bars()
    frame["timestamp"] = pd.to_datetime(
        ["2026-01-02 23:00", "2026-01-02 23:01", "2026-01-02 23:02"]
    ).tz_localize("UTC")

    trades, _, diag = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert trades == []
    assert diag["signals_seen"] == 1, "the signal was evaluated before being skipped"
    assert diag["signals_skipped_by_session"] == 1
    assert diag["signals_skipped_by_cost_floor"] == 0


def test_session_filter_can_be_turned_off(cfg):
    cfg["filters"]["skip_sessions"] = []
    frame = _three_bars()
    frame["timestamp"] = pd.to_datetime(
        ["2026-01-02 23:00", "2026-01-02 23:01", "2026-01-02 23:02"]
    ).tz_localize("UTC")

    trades, _, diag = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    assert diag["signals_skipped_by_session"] == 0


def test_a_config_without_a_filters_block_takes_every_signal(cfg):
    """The empty default must reproduce the unfiltered baseline exactly."""
    cfg.pop("filters", None)
    frame = _three_bars()
    frame["timestamp"] = pd.to_datetime(
        ["2026-01-02 23:00", "2026-01-02 23:01", "2026-01-02 23:02"]
    ).tz_localize("UTC")

    trades, _, diag = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    assert diag["signals_skipped_by_session"] == 0
    assert diag["signals_skipped_by_cost_floor"] == 0


def test_cost_floor_drops_a_target_worth_less_than_the_round_trip(cfg):
    """VWAP 1.0991 against a 1.0990 entry pays 0.0001 for a 0.00044 floor."""
    _costly_cfg(cfg)
    frame = _three_bars(vwap=1.0991)

    trades, _, diag = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert trades == []
    assert diag["signals_skipped_by_cost_floor"] == 1
    assert diag["signals_skipped_by_session"] == 0, "10:01 UTC is London, not Asia"


def test_cost_floor_can_be_turned_off(cfg):
    _costly_cfg(cfg)
    cfg["filters"]["min_target_cost_multiple"] = 0

    trades, _, diag = simulate({"TEST": _three_bars(vwap=1.0991)}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    assert diag["signals_skipped_by_cost_floor"] == 0


def test_a_target_worth_several_round_trips_is_taken(cfg):
    """VWAP 1.10 from a 1.0990 entry pays 0.0010, near 7x the round trip."""
    _costly_cfg(cfg)

    trades, _, diag = simulate({"TEST": _three_bars()}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    assert diag["signals_skipped_by_cost_floor"] == 0


# ---------------------------------------------------------------- buckets

@pytest.mark.parametrize(
    ("value", "expected"),
    [(90.0, "high"), (70.0, "high"), (50.0, "mid"), (30.0, "low"), (10.0, "low")],
)
def test_volatility_bucket_thresholds(value, expected):
    assert vol_bucket(value) == expected


@pytest.mark.parametrize("value", [None, np.nan, pd.NA, "n/a"])
def test_volatility_bucket_survives_missing_input(value):
    assert vol_bucket(value) == "unknown"


# ---------------------------------------------------------------- folds

def test_folds_cover_the_window_and_flag_the_locked_block():
    start = pd.Timestamp("2026-01-01", tz="UTC")
    end = pd.Timestamp("2026-07-01", tz="UTC")
    trades = [
        _trade(start + pd.Timedelta(days=1)),
        _trade(start + pd.Timedelta(days=100)),
        _trade(end - pd.Timedelta(days=1)),
    ]
    folds = assign_folds(trades, start, end, blocks=6)

    assert len(folds) == 6
    assert folds[0][0] == 0 and folds[-1][0] == 5
    assert trades[0].fold == 0
    assert trades[-1].fold == 5, "the final block is the locked test"

    report = walk_forward_report(trades, folds, initial=100_000.0)
    assert report["method"] == "anchored_walk_forward"
    assert report["locked_is_untouched"] is True
    assert report["folds"][-1]["role"] == "locked_final_test"
    assert report["folds"][0]["role"] == "seed"
    assert report["folds"][1]["role"] == "walk_forward"
    assert report["locked_final_test"]["trades"] == 1


def _trade(entry: pd.Timestamp, pnl: float = 10.0) -> Trade:
    return Trade(
        symbol="EURUSD",
        kind="mean_reversion",
        direction="long",
        regime="uptrend",
        vol_bucket="mid",
        session_hour_bucket="london",
        session=entry.date().isoformat(),
        entry_time=entry,
        entry_price=1.10,
        target=1.11,
        stop=1.09,
        notional=10_000.0,
        pnl=pnl,
        exit_time=entry + pd.Timedelta(minutes=5),
        exit_price=1.11,
        exit_reason="target",
        gross_return=0.01,
    )


# ---------------------------------------------------------------- stats

def test_core_stats_reports_hit_rate_and_profit_factor():
    trades = [
        _trade(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i), pnl)
        for i, pnl in enumerate([100.0, -50.0, 200.0, -50.0])
    ]
    stats = core_stats(trades, initial=10_000.0)
    assert stats["trades"] == 4
    assert stats["hit_rate_pct"] == pytest.approx(50.0)
    assert stats["profit_factor"] == pytest.approx(300.0 / 100.0)
    assert stats["net_pct"] == pytest.approx(2.0)
    assert stats["avg_win"] == pytest.approx(150.0)
    assert stats["avg_loss"] == pytest.approx(-50.0)


def test_core_stats_on_no_trades_is_zeroed():
    stats = core_stats([], initial=10_000.0)
    assert stats["trades"] == 0
    assert stats["net_pct"] == 0.0
    assert stats["profit_factor"] == 0.0


# ---------------------------------------------------------------- simulation

def test_fill_happens_at_the_next_bar_open_and_target_closes_the_trade(cfg):
    frame = _prepared(
        stamps=[
            "2026-01-02 10:00",
            "2026-01-02 10:01",
            "2026-01-02 10:02",
        ],
        ohlc=[
            (1.0995, 1.0998, 1.0992, 1.0994),
            (1.0990, 1.0995, 1.0988, 1.0993),
            (1.0993, 1.1002, 1.0990, 1.1001),
        ],
        admitted=[True, False, False],
        sessions=["2026-01-02"] * 3,
    )

    trades, points, diagnostics = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_price == pytest.approx(1.0990), "entry is the next bar's open"
    assert trade.exit_reason == "target"
    assert trade.exit_price == pytest.approx(1.10)
    assert trade.pnl > 0
    assert trade.regime == "uptrend"
    assert trade.kind == "mean_reversion"
    assert trade.direction == "long"
    assert diagnostics["halted"] is False
    assert points, "the run must leave an equity series"
    assert points[0][1] == pytest.approx(100_000.0)
    assert points[-1][1] > 100_000.0, "a winning trade lifts closing equity"


def test_open_position_closes_on_the_first_bar_of_the_next_session(cfg):
    frame = _prepared(
        stamps=[
            "2026-01-02 10:00",
            "2026-01-02 10:01",
            "2026-01-02 21:00",
        ],
        ohlc=[
            (1.0995, 1.0998, 1.0992, 1.0994),
            (1.0990, 1.0995, 1.0992, 1.0993),
            (1.0996, 1.0999, 1.0994, 1.0997),
        ],
        admitted=[True, False, False],
        sessions=["2026-01-02", "2026-01-02", "2026-01-03"],
    )

    trades, _, _ = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "session_end"
    assert trade.exit_price == pytest.approx(1.0996), "exits at the new session's open"
    assert trade.financing_cost == pytest.approx(
        cfg["costs"]["financing_per_session"]
    ), "an exit that crosses 21:00 pays exactly one session of financing"


def test_stop_loss_is_hit_before_the_target_when_one_bar_spans_both(cfg):
    frame = _prepared(
        stamps=["2026-01-02 10:00", "2026-01-02 10:01", "2026-01-02 10:02"],
        ohlc=[
            (1.0995, 1.0998, 1.0992, 1.0994),
            (1.0990, 1.0995, 1.0988, 1.0993),
            (1.0992, 1.1050, 1.0950, 1.0970),  # spans both stop and target
        ],
        admitted=[True, False, False],
        sessions=["2026-01-02"] * 3,
    )

    trades, _, _ = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert len(trades) == 1
    assert trades[0].exit_reason == "stop", "an ambiguous bar resolves against the trader"
    assert trades[0].pnl < 0


def test_a_signal_the_regime_rejects_never_opens_a_position(cfg):
    frame = _prepared(
        stamps=["2026-01-02 10:00", "2026-01-02 10:01"],
        ohlc=[(1.0995, 1.0998, 1.0992, 1.0994), (1.0990, 1.0995, 1.0988, 1.0993)],
        admitted=[False, False],
        sessions=["2026-01-02"] * 2,
    )

    trades, _, diagnostics = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert trades == []
    assert diagnostics["signals_seen"] == 0
    assert diagnostics["halted"] is False


def test_rejected_signals_are_counted_separately_from_taken_ones(cfg):
    frame = _prepared(
        stamps=["2026-01-02 10:00", "2026-01-02 10:01"],
        ohlc=[(1.0995, 1.0998, 1.0992, 1.0994), (1.0990, 1.0995, 1.0988, 1.0993)],
        admitted=[False, False],
        sessions=["2026-01-02"] * 2,
    )
    frame["signal_kind"] = "mean_reversion"
    frame["signal_direction"] = "long"

    _, _, diagnostics = simulate({"TEST": frame}, cfg, initial_equity=100_000.0)

    assert diagnostics["signals_rejected_by_regime"] == 2
    assert diagnostics["signals_seen"] == 0


def test_an_empty_window_returns_no_trades(cfg):
    trades, points, diagnostics = simulate({}, cfg, initial_equity=100_000.0)
    assert trades == []
    assert diagnostics["halted"] is False
    assert points


# ---------------------------------------------------------------- serialising

def test_serialised_trades_carry_the_fields_the_report_needs():
    trade = _trade(pd.Timestamp("2026-01-01 10:00", tz="UTC"), pnl=123.4567)
    trade.spread_cost = 0.00005
    rows = serialize_trades([trade])
    row = rows[0]
    for field in (
        "symbol",
        "kind",
        "direction",
        "regime",
        "vol_bucket",
        "session_bucket",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "pnl",
        "net_return_pct",
        "fold",
    ):
        assert field in row
    assert row["pnl"] == pytest.approx(123.4567)
    assert row["net_return_pct"] == pytest.approx((0.01 - 0.00005) * 100)


def test_serialisation_respects_the_row_limit():
    trades = [_trade(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(minutes=i)) for i in range(10)]
    assert len(serialize_trades(trades, limit=3)) == 3


def test_sample_points_downscales_without_losing_the_endpoints():
    start = pd.Timestamp("2026-01-01", tz="UTC")
    points = [(start + pd.Timedelta(minutes=i), 100_000.0 + i) for i in range(5000)]
    sampled = sample_points(points, limit=200)
    assert len(sampled) <= 200
    assert sampled[0][0] == start.isoformat()
    assert sampled[-1][1] == pytest.approx(100_000.0 + 4999)
