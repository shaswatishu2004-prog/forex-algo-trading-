"""Institutional VWAP with volume-weighted sigma bands on 1m bars.

Decision: docs/wayfinder/tickets/vwap-band-math.md, anchor from
docs/wayfinder/tickets/anchor-time-conflict.md (21:00 UTC trading-day reset).

- Price: hlc3 = (H + L + C) / 3.
- Weight per session: the bar's volume column when every bar in the session is
  positive and the column varies at least ``volume_min_unique_per_session``
  times; otherwise the synthetic activity weight (H - L) + |C_t - C_t-1|,
  floored at 0. The choice is recorded per bar in ``weight_mode``.
- VWAP and sigma reset at the anchor: VWAP = sum(w*p) / sum(w) and
  sigma = sqrt(sum(w*(p - VWAP)^2) / sum(w)) over bars since the reset.
  The two-pass sum is computed as sum(w*p^2)/sum(w) - VWAP^2, which is the
  same quantity.
- Bands: VWAP +/- 1*sigma and VWAP +/- 3*sigma. Sigma is 0 on the first bar of
  a session, so bands start collapsed and widen as bars accumulate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_weight(frame: pd.DataFrame) -> pd.Series:
    """Range plus absolute close-to-close change, floored at zero."""
    range_part = (frame["high"] - frame["low"]).clip(lower=0)
    change_part = frame["close"].diff().abs().fillna(0.0)
    return (range_part + change_part).clip(lower=0.0)


def session_labels(timestamps: pd.Series, anchor_hour_utc: int) -> pd.Series:
    """Session date label: a bar at time t belongs to date (t + (24-anchor)) floor day."""
    shift = pd.Timedelta(hours=(24 - anchor_hour_utc) % 24)
    return (timestamps + shift).dt.floor("D")


def compute_vwap_bands(
    frame: pd.DataFrame,
    anchor_hour_utc: int = 21,
    volume_min_unique: int = 2,
) -> pd.DataFrame:
    """Add hlc3, weight, vwap, sigma, and band columns to a cleaned 1m frame.

    The input must be sorted ascending by timestamp with a tz-aware UTC
    timestamp column named ``timestamp``.
    """
    if frame.empty:
        return frame.copy()

    data = frame.copy()
    data["hlc3"] = (data["high"] + data["low"] + data["close"]) / 3.0
    session = session_labels(data["timestamp"], anchor_hour_utc)
    data["session"] = session

    volume = data["volume"].fillna(0.0)
    positive = (volume > 0).groupby(session).transform("all")
    distinct = volume.groupby(session).transform("nunique")
    usable = positive & (distinct >= volume_min_unique)
    data["weight_mode"] = np.where(usable, "volume", "synthetic")

    weight = np.where(usable, volume, synthetic_weight(data))
    # A synthetic window can be all zeros (flat bars); fall back to unit weight
    # for those sessions so the cumulative sums stay well defined.
    session_weight = pd.Series(weight, index=data.index).groupby(session).transform("sum")
    zero_window = session_weight <= 0
    weight = np.where(zero_window, 1.0, weight)
    data["weight"] = weight

    grouped_w = data["weight"].groupby(session, sort=False)
    w_sum = grouped_w.cumsum()
    wp_sum = (data["weight"] * data["hlc3"]).groupby(session, sort=False).cumsum()
    wpp_sum = (data["weight"] * data["hlc3"] ** 2).groupby(session, sort=False).cumsum()

    data["vwap"] = wp_sum / w_sum
    variance = (wpp_sum / w_sum - data["vwap"] ** 2).clip(lower=0.0)
    data["sigma"] = np.sqrt(variance)

    for sigma_count in (1, 3):
        data[f"band{sigma_count}_upper"] = data["vwap"] + sigma_count * data["sigma"]
        data[f"band{sigma_count}_lower"] = data["vwap"] - sigma_count * data["sigma"]

    return data
