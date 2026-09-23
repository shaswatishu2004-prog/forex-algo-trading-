"""Four-state daily regime: uptrend, downtrend, high_vol, low_vol.

Decision: docs/wayfinder/tickets/regime-definitions.md.

- Volatility: ATR(14) percentile over a trailing 252-bar window (30 bars
  minimum before the percentile exists). Percentile >= 70 reads high_vol.
- Trend: ADX(14) >= 25 with +DI > -DI and positive EMA(50) slope over 5 bars
  reads uptrend; the mirror reads downtrend. When ADX is below the threshold,
  ER(10) >= 0.35 with the same EMA slope sign substitutes.
- Priority: high_vol, then the trend states, then low_vol. Every bar gets at
  most one label; bars before all inputs exist stay unlabeled.
- Point in time: every input is trailing, so the label at bar t uses only bars
  at or before t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def compute_regime(daily_frame: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Add regime indicator columns and the single ``regime`` label per bar.

    Expects a cleaned daily frame sorted ascending with a tz-aware UTC
    ``timestamp`` column. Bars before every input exists get a null label.
    """
    params = (cfg or {}).get("regime", {})
    atr_period = int(params.get("atr_period", 14))
    pct_window = int(params.get("atr_percentile_window", 252))
    pct_min = int(params.get("atr_percentile_min_bars", 30))
    high_pct = float(params.get("high_vol_percentile", 70))
    adx_period = int(params.get("adx_period", 14))
    adx_threshold = float(params.get("adx_threshold", 25))
    ema_period = int(params.get("ema_period", 50))
    slope_lookback = int(params.get("ema_slope_lookback", 5))
    er_period = int(params.get("efficiency_period", 10))
    er_threshold = float(params.get("efficiency_threshold", 0.35))

    if daily_frame.empty:
        return daily_frame.copy()

    data = daily_frame.copy()
    prev_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr"] = _wilder(true_range, atr_period)
    data["atr_percentile"] = (
        data["atr"].rolling(pct_window, min_periods=pct_min).rank(pct=True) * 100.0
    )

    up_move = data["high"] - data["high"].shift(1)
    down_move = data["low"].shift(1) - data["low"]
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    smooth_plus = _wilder(plus_dm, adx_period)
    smooth_minus = _wilder(minus_dm, adx_period)
    smooth_tr = _wilder(true_range, adx_period)

    data["plus_di"] = 100.0 * smooth_plus / smooth_tr
    data["minus_di"] = 100.0 * smooth_minus / smooth_tr
    di_sum = data["plus_di"] + data["minus_di"]
    di_diff = (data["plus_di"] - data["minus_di"]).abs()
    dx = (100.0 * di_diff / di_sum.replace(0.0, np.nan)).fillna(0.0)
    data["adx"] = _wilder(dx, adx_period)

    data["ema"] = data["close"].ewm(span=ema_period, adjust=False, min_periods=ema_period).mean()
    data["ema_slope"] = data["ema"] - data["ema"].shift(slope_lookback)

    direction_sum = data["close"].diff().abs().rolling(er_period, min_periods=er_period).sum()
    data["efficiency_ratio"] = (data["close"] - data["close"].shift(er_period)).abs() / direction_sum.replace(
        0.0, np.nan
    )

    inputs_ready = (
        data["atr"].notna()
        & data["atr_percentile"].notna()
        & data["adx"].notna()
        & data["ema_slope"].notna()
        & data["efficiency_ratio"].notna()
    )
    high_vol = data["atr_percentile"] >= high_pct
    adx_trend_up = (
        (data["adx"] >= adx_threshold)
        & (data["plus_di"] > data["minus_di"])
        & (data["ema_slope"] > 0)
    )
    adx_trend_down = (
        (data["adx"] >= adx_threshold)
        & (data["minus_di"] > data["plus_di"])
        & (data["ema_slope"] < 0)
    )
    er_fallback_up = (data["adx"] < adx_threshold) & (data["efficiency_ratio"] >= er_threshold) & (
        data["ema_slope"] > 0
    )
    er_fallback_down = (data["adx"] < adx_threshold) & (data["efficiency_ratio"] >= er_threshold) & (
        data["ema_slope"] < 0
    )
    uptrend = adx_trend_up | er_fallback_up
    downtrend = adx_trend_down | er_fallback_down

    labels = np.select(
        [~inputs_ready, high_vol, uptrend, downtrend],
        ["", "high_vol", "uptrend", "downtrend"],
        default="low_vol",
    )
    data["regime"] = pd.Series(labels, index=data.index, dtype="object").replace("", pd.NA)
    return data
