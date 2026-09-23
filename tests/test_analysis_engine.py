"""Tests for the analysis engine: cleaning, VWAP bands, regime, signals JSON."""

from pathlib import Path

import pandas as pd
import pytest

from fx_strategy.analysis.cleaning import (
    aggregate_daily,
    merge_frames,
    read_histdata_minute_csv,
    read_user_minute_csv,
)
from fx_strategy.analysis.config import load_config
from fx_strategy.analysis.regime import compute_regime
from fx_strategy.analysis.signals import build_symbol_signals, write_symbol_signals
from fx_strategy.analysis.vwap import compute_vwap_bands, session_labels, synthetic_weight


@pytest.fixture
def cfg() -> dict:
    return load_config()


def _minute_frame(start: str, periods: int, base_price: float = 1.10, volume: float = 100.0) -> pd.DataFrame:
    stamps = pd.date_range(start, periods=periods, freq="1min", tz="UTC")
    drift = [0.0001 * i for i in range(periods)]
    close = [base_price + d for d in drift]
    high = [c + 0.0002 for c in close]
    low = [c - 0.0002 for c in close]
    open_ = [c - 0.0001 for c in close]
    return pd.DataFrame(
        {
            "timestamp": stamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": [volume] * periods,
        }
    )


def test_load_config_returns_engine_settings(cfg):
    assert cfg["timeframes"]["regime"] == "1d"
    assert cfg["timeframes"]["signal"] == "1m"
    assert cfg["vwap"]["anchor_hour_utc"] == 21
    assert cfg["vwap"]["bands_sigmas"] == [1, 3]
    assert cfg["refresh"]["window_days"] == 365
    assert cfg["refresh"]["loop_days"] == 365
    assert cfg["signals"]["directory"] == "signals"
    assert cfg["sources"]["histdata_utc_offset_hours"]["DXY"] == "us_eastern_dst"
    assert cfg["sources"]["histdata_utc_offset_hours"]["EURUSD"] == "us_eastern_dst"
    assert cfg["sources"]["histdata_utc_offset_hours"]["XAUUSD"] == "us_eastern_dst"
    assert cfg["sources"]["histdata_valid_from"]["DXY"] == "2018-12-16"


def test_session_labels_use_21_utc_boundary():
    stamps = pd.Series(
        pd.to_datetime(
            ["2026-09-22 20:59:00", "2026-09-22 21:00:00", "2026-09-23 20:59:00"]
        ).tz_localize("UTC")
    )
    labels = session_labels(stamps, anchor_hour_utc=21)
    # Session D covers [D-1 21:00, D 21:00): the standard FX trading day.
    assert labels.iloc[0] == pd.Timestamp("2026-09-22", tz="UTC")
    assert labels.iloc[1] == pd.Timestamp("2026-09-23", tz="UTC")
    assert labels.iloc[2] == pd.Timestamp("2026-09-23", tz="UTC")


def test_session_reset_restarts_cumulative_vwap():
    # 20:00-20:59 close one session; 21:00 opens a fresh one.
    frame = _minute_frame("2026-09-22 20:00:00", periods=120)
    banded = compute_vwap_bands(frame, anchor_hour_utc=21)
    first_of_new = banded[banded["timestamp"] == pd.Timestamp("2026-09-22 21:00:00", tz="UTC")]
    assert len(first_of_new) == 1
    row = first_of_new.iloc[0]
    # First bar of a fresh session: VWAP equals that bar's hlc3, sigma is 0.
    hlc3 = (row["high"] + row["low"] + row["close"]) / 3
    assert row["vwap"] == pytest.approx(hlc3)
    assert row["sigma"] == pytest.approx(0.0)
    assert row["band1_upper"] == pytest.approx(row["vwap"])


def test_vwap_equals_price_when_weight_constant():
    frame = _minute_frame("2026-09-22 10:00:00", periods=10, volume=50.0)
    banded = compute_vwap_bands(frame, anchor_hour_utc=21, volume_min_unique=1)
    # Constant positive volume with min_unique=1 uses the volume column; with
    # equal weights over a linearly rising price the mean sits mid-range.
    assert banded["vwap"].iloc[-1] == pytest.approx(banded["hlc3"].mean(), rel=1e-9)
    assert banded["sigma"].iloc[-1] > 0


def test_volume_weight_mode_falls_back_to_synthetic():
    frame = _minute_frame("2026-09-22 10:00:00", periods=10, volume=1.0)
    banded = compute_vwap_bands(frame, anchor_hour_utc=21, volume_min_unique=2)
    assert set(banded["weight_mode"]) == {"synthetic"}
    weight = synthetic_weight(frame)
    assert (weight >= 0).all()


def test_vwap_bands_widen_within_session():
    frame = _minute_frame("2026-09-22 10:00:00", periods=60)
    banded = compute_vwap_bands(frame, anchor_hour_utc=21, volume_min_unique=1)
    widths = (banded["band3_upper"] - banded["band3_lower"]).iloc[1:]
    assert widths.iloc[-1] > widths.iloc[0]


def test_aggregate_daily_boundary():
    frame = _minute_frame("2026-09-22 20:58:00", periods=4)
    daily = aggregate_daily(frame, anchor_hour_utc=21)
    # Session D covers [D-1 21:00, D 21:00), so 20:58/20:59 belong to the
    # session ending 2026-09-22 21:00 (labeled 09-22) and 21:00/21:01 open the
    # session labeled 09-23. Labels are midnight UTC of the session end date.
    assert len(daily) == 2
    assert daily["timestamp"].iloc[0] == pd.Timestamp("2026-09-22", tz="UTC")
    assert daily["timestamp"].iloc[1] == pd.Timestamp("2026-09-23", tz="UTC")
    assert daily["open"].iloc[1] == pytest.approx(frame["open"].iloc[2])


def test_read_user_minute_csv_applies_offset_and_rejects_bad_bars(tmp_path: Path):
    csv = tmp_path / "sample.csv"
    rows = [
        "2026-09-22 10:00\t1.10\t1.11\t1.09\t1.105\t30",
        "2026-09-22 10:01\t1.10\t1.09\t1.11\t1.105\t30",  # high < low: rejected
        "2026-09-22 10:01\t1.10\t1.12\t1.09\t1.106\t40",  # duplicate stamp: last wins
        "2026-09-22 10:03\t1.10\t1.11\t1.09\t-1.10\t30",  # negative close: rejected
    ]
    csv.write_text("\n".join(rows), encoding="utf-8")
    frame, drops = read_user_minute_csv(csv, offset_hours=2)
    assert len(frame) == 2
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2026-09-22 12:00:00", tz="UTC")
    assert drops["invalid_range"] == 1
    assert drops["non_positive_price"] == 1
    assert frame["close"].iloc[-1] == pytest.approx(1.106)


def test_read_histdata_offset_and_valid_from(tmp_path: Path):
    csv = tmp_path / "udx.csv"
    csv.write_text(
        "20181214 165900;24061.0;24064.0;24051.0;24052.0;0\n"  # DJIA era: drop
        "20181216 200000;96.90;96.95;96.88;96.911;0\n"
        "20181217 100000;96.92;96.97;96.90;96.94;0\n",
        encoding="utf-8",
    )
    frame, drops = read_histdata_minute_csv(csv, offset_hours=5, valid_from="2018-12-16")
    assert len(frame) == 2
    assert drops["outside_valid_range"] == 1
    # Integer offset path: the file clock is fixed EST, so +5h puts the
    # first kept bar at 2018-12-17 01:00 UTC (the DST sentinel is separate).
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2018-12-17 01:00:00", tz="UTC")
    assert frame["timestamp"].is_monotonic_increasing


def _hist_row(stamp: str) -> str:
    return f"{stamp};1.1000;1.1010;1.0990;1.1005;0"


def test_dst_sentinel_winter_and_summer_offsets(tmp_path: Path):
    csv = tmp_path / "dst.csv"
    csv.write_text(_hist_row("20230116 120000") + "\n" + _hist_row("20230716 120000"), encoding="utf-8")
    frame, _ = read_histdata_minute_csv(csv, offset_hours="us_eastern_dst")
    # New York wall clock: winter rows +5 (EST), summer rows +4 (EDT).
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2023-01-16 17:00:00", tz="UTC")
    assert frame["timestamp"].iloc[1] == pd.Timestamp("2023-07-16 16:00:00", tz="UTC")


def test_dst_us_schedule_before_2019(tmp_path: Path):
    csv = tmp_path / "dst_us.csv"
    csv.write_text(
        "\n".join(
            [
                _hist_row("20180309 120000"),  # Fri before 2nd Sun Mar: winter
                _hist_row("20180312 120000"),  # Mon after: summer
                _hist_row("20181102 120000"),  # Fri before 1st Sun Nov: summer
                _hist_row("20181105 120000"),  # Mon after: winter
            ]
        ),
        encoding="utf-8",
    )
    frame, _ = read_histdata_minute_csv(csv, offset_hours="us_eastern_dst")
    # +5 stamps land at 17:00 UTC, +4 stamps at 16:00 UTC.
    assert [stamp.hour for stamp in frame["timestamp"]] == [17, 16, 16, 17]


def test_dst_eu_schedule_from_2019(tmp_path: Path):
    csv = tmp_path / "dst_eu.csv"
    csv.write_text(
        "\n".join(
            [
                _hist_row("20190329 120000"),  # Fri before last Sun Mar: winter
                _hist_row("20190331 185900"),  # spring Sunday: 19:00 hour skipped
                _hist_row("20190331 200000"),
                _hist_row("20190401 120000"),  # Mon after: summer
                _hist_row("20191025 120000"),  # Fri before last Sun Oct: summer
                _hist_row("20191027 195800"),  # first 19:xx block: still summer
                _hist_row("20191027 195900"),
                _hist_row("20191027 195800"),  # repeated hour after the step: winter
                _hist_row("20191027 195900"),
                _hist_row("20191027 200000"),
                _hist_row("20191028 120000"),  # Mon after: winter
            ]
        ),
        encoding="utf-8",
    )
    frame, _ = read_histdata_minute_csv(csv, offset_hours="us_eastern_dst")
    # Raw duplicated fall stamps convert to distinct UTC minutes, so the
    # row-order split keeps both halves instead of deduping one away.
    assert list(frame["timestamp"]) == [
        pd.Timestamp("2019-03-29 17:00:00", tz="UTC"),  # +5
        pd.Timestamp("2019-03-31 23:59:00", tz="UTC"),  # 18:59 +5
        pd.Timestamp("2019-04-01 00:00:00", tz="UTC"),  # 20:00 +4
        pd.Timestamp("2019-04-01 16:00:00", tz="UTC"),  # +4
        pd.Timestamp("2019-10-25 16:00:00", tz="UTC"),  # +4
        pd.Timestamp("2019-10-27 23:58:00", tz="UTC"),  # first block +4
        pd.Timestamp("2019-10-27 23:59:00", tz="UTC"),
        pd.Timestamp("2019-10-28 00:58:00", tz="UTC"),  # second block +5
        pd.Timestamp("2019-10-28 00:59:00", tz="UTC"),
        pd.Timestamp("2019-10-28 01:00:00", tz="UTC"),
        pd.Timestamp("2019-10-28 17:00:00", tz="UTC"),  # +5
    ]


def test_dst_missing_fall_step_uses_us_schedule(tmp_path: Path):
    """EURUSD 2024: the EU fall Sunday leaves no repeated stamp to split."""
    csv = tmp_path / "dst_nostep.csv"
    csv.write_text(
        "\n".join(
            [
                _hist_row("20240331 185900"),  # spring gap present
                _hist_row("20240331 200000"),
                _hist_row("20241027 195800"),  # fall Sunday runs straight through
                _hist_row("20241027 195900"),
                _hist_row("20241027 200000"),
                _hist_row("20241030 120000"),  # still summer until 1st Sun Nov
                _hist_row("20241104 120000"),  # Mon after: winter
            ]
        ),
        encoding="utf-8",
    )
    frame, _ = read_histdata_minute_csv(csv, offset_hours="us_eastern_dst")
    assert frame["timestamp"].iloc[-2] == pd.Timestamp("2024-10-30 16:00:00", tz="UTC")
    assert frame["timestamp"].iloc[-1] == pd.Timestamp("2024-11-04 17:00:00", tz="UTC")


def test_histdata_rejects_unknown_offset_mode(tmp_path: Path):
    csv = tmp_path / "bad.csv"
    csv.write_text(_hist_row("20260922 210000"), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown histdata offset mode"):
        read_histdata_minute_csv(csv, offset_hours="est")


def test_read_histdata_minute_csv_seven_columns(tmp_path: Path):
    csv = tmp_path / "hist.csv"
    csv.write_text(
        "20260922 205900;1.1000;1.1010;1.0990;1.1005;0\n"
        "20260922 210000;1.1005;1.1015;1.0995;1.1010;0\n",
        encoding="utf-8",
    )
    frame, _ = read_histdata_minute_csv(csv)
    assert len(frame) == 2
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2026-09-22 20:59:00", tz="UTC")
    assert frame["timestamp"].iloc[1] == pd.Timestamp("2026-09-22 21:00:00", tz="UTC")
    assert frame["high"].iloc[0] >= frame["low"].iloc[0]


def test_merge_frames_first_source_wins():
    base = _minute_frame("2026-09-22 10:00:00", periods=5)
    overlay = base.copy()
    overlay["close"] = 9.99
    merged, _ = merge_frames([("user", overlay), ("histdata", base)])
    assert len(merged) == 5
    assert (merged["close"] == 9.99).all()


def _daily_uptrend(periods: int = 300) -> pd.DataFrame:
    """Steady climb with moderate ranges: ADX trend with roomy percentile."""
    stamps = pd.date_range("2025-01-01", periods=periods, freq="1D", tz="UTC")
    close = [100.0 + 0.5 * i for i in range(periods)]
    high = [c + 0.8 for c in close]
    low = [c - 0.8 for c in close]
    open_ = [c - 0.2 for c in close]
    return pd.DataFrame(
        {"timestamp": stamps, "open": open_, "high": high, "low": low, "close": close, "volume": 0.0}
    )


def test_regime_uptrend_label(cfg):
    regime = compute_regime(_daily_uptrend(), cfg)
    labeled = regime.dropna(subset=["regime"])
    assert not labeled.empty
    # First labels appear only after ATR percentile, ADX, EMA and ER warm up.
    first_label_position = regime["regime"].first_valid_index()
    assert first_label_position >= 49
    assert set(labeled["regime"]) <= {"uptrend", "high_vol", "low_vol"}
    assert labeled["regime"].iloc[-1] in {"uptrend", "high_vol"}


def test_regime_priority_high_vol_beats_trend(cfg):
    frame = _daily_uptrend(periods=280)
    # Final bar: enormous range spikes ATR percentile while the climb continues.
    frame.loc[frame.index[-1], "high"] = frame["close"].iloc[-1] + 40.0
    frame.loc[frame.index[-1], "low"] = frame["close"].iloc[-1] - 40.0
    regime = compute_regime(frame, cfg)
    assert regime["regime"].iloc[-1] == "high_vol"


def test_regime_labeled_values_match_contract(cfg):
    regime = compute_regime(_daily_uptrend(), cfg)
    values = set(regime["regime"].dropna().unique())
    assert values <= {"uptrend", "downtrend", "high_vol", "low_vol"}


def test_build_and_write_signals_roundtrip(cfg, tmp_path: Path):
    frame = _minute_frame("2026-09-22 10:00:00", periods=30)
    banded = compute_vwap_bands(frame, anchor_hour_utc=21, volume_min_unique=1)
    regime = compute_regime(_daily_uptrend(), cfg)
    document = build_symbol_signals("EURUSD", banded, regime, cfg)

    assert document["schema_version"] == "1.0"
    assert document["symbol"] == "EURUSD"
    assert document["anchor_hour_utc"] == 21
    # Position-change-only emission: bar 0 is indeterminate (sigma 0), bar 1
    # lands at exactly +1 sigma of the linear ramp, and stays between the 1
    # and 3 sigma upper bands for the rest of the session.
    assert document["record_counts"]["vwap_position"] == 2
    assert document["last_state"]["position"] == "between_1_and_3_upper"
    assert document["last_state"]["session"] == "2026-09-22"
    assert document["record_counts"]["regime"] == len(regime.dropna(subset=["regime"]))
    positions = {record["position"] for record in document["records"] if record["type"] == "vwap_position"}
    assert positions <= {"indeterminate", "inside_band1", "between_1_and_3_upper",
                         "between_1_and_3_lower", "above_band3", "below_band3"}
    assert all(record["action"] == "observe" for record in document["records"])

    cfg = {**cfg, "signals": {**cfg["signals"], "directory": str(tmp_path)}}
    target = write_symbol_signals(document, cfg)
    assert target.exists()
    assert target.name.startswith("EURUSD_")
    import json

    reloaded = json.loads(target.read_text(encoding="utf-8"))
    assert reloaded["record_counts"] == document["record_counts"]
