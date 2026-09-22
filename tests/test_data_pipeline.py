"""Unit tests for MarketDataDB and ForexDataFetcher."""

import datetime
from pathlib import Path
import pandas as pd
import pytest

from fx_strategy.data.database import MarketDataDB
from fx_strategy.data.fetcher import ForexDataFetcher


@pytest.fixture
def temp_db(tmp_path: Path) -> MarketDataDB:
    """Fixture providing a temporary SQLite database."""
    db_file = tmp_path / "test_market_data.db"
    return MarketDataDB(db_file)


def test_db_initialization(temp_db: MarketDataDB):
    """Test table and index are created upon initialization."""
    with temp_db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv'")
        assert cursor.fetchone() is not None

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ohlcv_lookup'")
        assert cursor.fetchone() is not None


def test_save_and_get_ohlcv(temp_db: MarketDataDB):
    """Test saving OHLCV records and retrieving them with correct columns and index."""
    dates = pd.date_range("2026-09-01", periods=5, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [1.10, 1.11, 1.12, 1.13, 1.14],
            "High": [1.12, 1.13, 1.14, 1.15, 1.16],
            "Low": [1.09, 1.10, 1.11, 1.12, 1.13],
            "Close": [1.11, 1.12, 1.13, 1.14, 1.15],
            "Volume": [1000, 1200, 1100, 1300, 1500],
        },
        index=dates,
    )

    count = temp_db.save_ohlcv(df, symbol="EURUSD", timeframe="1d", source="test")
    assert count == 5

    loaded_df = temp_db.get_ohlcv("EURUSD", "1d")
    assert len(loaded_df) == 5
    assert list(loaded_df.columns) == ["open", "high", "low", "close", "volume"]
    assert loaded_df["close"].iloc[0] == 1.11
    assert loaded_df["volume"].iloc[-1] == 1500.0


def test_idempotent_upsert(temp_db: MarketDataDB):
    """Test that saving the same records twice does not create duplicates."""
    dates = pd.date_range("2026-09-01", periods=3, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [2500.0, 2501.0, 2502.0],
            "High": [2502.0, 2503.0, 2504.0],
            "Low": [2499.0, 2500.0, 2501.0],
            "Close": [2501.0, 2502.0, 2503.0],
            "Volume": [50.0, 60.0, 70.0],
        },
        index=dates,
    )

    temp_db.save_ohlcv(df, symbol="XAUUSD", timeframe="1m")
    # Save same records again (e.g. updating with new close)
    df["Close"] = [2501.5, 2502.5, 2503.5]
    temp_db.save_ohlcv(df, symbol="XAUUSD", timeframe="1m")

    loaded_df = temp_db.get_ohlcv("XAUUSD", "1m")
    assert len(loaded_df) == 3
    assert loaded_df["close"].iloc[0] == 2501.5


def test_database_summary(temp_db: MarketDataDB):
    """Test summary statistics report."""
    dates = pd.date_range("2026-09-01", periods=2, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [0.0, 10.0],
        },
        index=dates,
    )
    temp_db.save_ohlcv(df, symbol="DXY", timeframe="1d", source="yfinance:DX-Y.NYB")

    summary = temp_db.get_summary()
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["symbol"] == "DXY"
    assert row["timeframe"] == "1d"
    assert row["total_bars"] == 2
    assert row["bars_with_volume"] == 1


def test_fetcher_ticker_resolution():
    """Test ticker symbol resolution in ForexDataFetcher."""
    fetcher = ForexDataFetcher()
    assert fetcher.get_ticker("EURUSD") == "EURUSD=X"
    assert fetcher.get_ticker("XAUUSD") == "GC=F"
    assert fetcher.get_ticker("DXY") == "DX-Y.NYB"
    assert fetcher.get_ticker("UNKNOWN") == "UNKNOWN"
