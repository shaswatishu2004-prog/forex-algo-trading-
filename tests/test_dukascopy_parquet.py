"""
Tests for Dukascopy Parquet MarketDataRepository and dynamic resampling.
"""

import pytest
from data_layer.repository import MarketDataRepository, resample_ohlcv


def test_dukascopy_repository_symbols():
    repo = MarketDataRepository()
    symbols = repo.get_symbols()
    assert "EURUSD" in symbols
    assert "USDJPY" in symbols
    assert "XAUUSD" in symbols
    assert "DXY" in symbols


def test_dukascopy_ohlcv_1m_data():
    repo = MarketDataRepository()
    df = repo.get_ohlcv("EURUSD", timeframe="1m", lookback_days=7)
    assert not df.empty
    assert len(df) > 500
    for col in ["open", "high", "low", "close", "volume"]:
        assert col in df.columns


def test_dukascopy_resample_daily():
    repo = MarketDataRepository()
    df_1d = repo.get_ohlcv("EURUSD", timeframe="1d", lookback_days=365)
    assert not df_1d.empty
    assert len(df_1d) >= 200
    # High should be >= Low
    assert (df_1d["high"] >= df_1d["low"]).all()


def test_dukascopy_summary():
    repo = MarketDataRepository()
    summary = repo.get_summary()
    assert not summary.empty
    assert len(summary) >= 4
    for sym in ["EURUSD", "USDJPY", "XAUUSD", "DXY"]:
        row = summary[summary["symbol"] == sym]
        assert not row.empty
        assert row["total_bars"].iloc[0] > 500000


def test_dukascopy_coverage():
    repo = MarketDataRepository()
    cov = repo.get_coverage("EURUSD", timeframe="1m")
    assert not cov.empty
    assert "date" in cov.columns
    assert "coverage_pct" in cov.columns
