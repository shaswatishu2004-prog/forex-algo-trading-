"""Generate dashboard/findings.html from the staged SQLite database.

Run from the repository root:

    python scripts/generate_findings_page.py

The page is a generated artifact and is committed, so the Pages deployment
needs no database access. Every number and chart in the output comes from
data/forex_market_data.db at generation time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "forex_market_data.db"
OUT_PATH = ROOT / "dashboard" / "findings.html"

SYMBOLS = ("EURUSD", "XAUUSD", "DXY")
WEEKDAY_LETTERS = "MTWTFSS"
STATE_CLASS = {
    "uptrend": "ribbon-up",
    "downtrend": "ribbon-down",
    "high volatility": "ribbon-high",
    "low volatility": "ribbon-low",
}
REPO_URL = "https://github.com/shaswatishu2004-prog/forex-algo-trading-"

NAV = """<nav class="nav" aria-label="Primary">
    <div class="nav-inner">
      <a class="brand" href="index.html">FX ALGO <span>RESEARCH</span></a>
      <div class="nav-links">
        <a href="index.html#strategy">Strategy</a>
        <a href="index.html#data">Data</a>
        <a href="index.html#build">Build</a>
        <a href="index.html#tools">Tools</a>
        <a href="index.html#gates">Gates</a>
        <a href="backtest.html">Backtest</a>
        <a href="swing.html">Swing</a>
      </div>
      <div class="nav-actions">
        <a class="btn btn-primary" href="findings.html">Findings</a>
        <a class="btn btn-ghost" href="https://github.com/shaswatishu2004-prog/forex-algo-trading-" target="_blank" rel="noreferrer">Repository</a>
      </div>
    </div>
  </nav>"""


# ---------------------------------------------------------------- loading

def load_bars() -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    try:
        bars = pd.read_sql_query(
            "SELECT symbol, timeframe, timestamp, open, high, low, close, volume FROM ohlcv",
            con,
        )
    finally:
        con.close()
    bars["ts"] = pd.to_datetime(bars["timestamp"], utc=True)
    return bars


def series_stats(bars: pd.DataFrame) -> list[dict]:
    rows = []
    for symbol in SYMBOLS:
        for timeframe in ("1d", "1m"):
            part = bars[(bars["symbol"] == symbol) & (bars["timeframe"] == timeframe)]
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bars": int(len(part)),
                    "start": part["ts"].min(),
                    "end": part["ts"].max(),
                    "volume_share": float((part["volume"] > 0).mean()) * 100.0,
                    "volume_bars": int((part["volume"] > 0).sum()),
                }
            )
    return rows


def build_calendar(bars_1m: pd.DataFrame) -> tuple[list, dict]:
    first = bars_1m["ts"].min().floor("D")
    last = bars_1m["ts"].max().floor("D")
    dates = [d.date() for d in pd.date_range(first, last, freq="D")]
    cal: dict[str, dict] = {}
    for symbol in SYMBOLS:
        part = bars_1m[bars_1m["symbol"] == symbol]
        cal[symbol] = part.groupby(part["ts"].dt.date).size().to_dict()
    return dates, cal


def calendar_findings(dates: list, cal: dict) -> dict:
    weekday_medians = {}
    sunday_medians = {}
    for symbol in SYMBOLS:
        weekday = [
            cal[symbol].get(d, 0)
            for d in dates
            if d.weekday() < 5 and cal[symbol].get(d, 0) >= 0.5 * max(cal[symbol].values())
        ]
        sunday = [cal[symbol].get(d, 0) for d in dates if d.weekday() == 6]
        weekday_medians[symbol] = float(np.median(weekday)) if weekday else 0.0
        sunday_medians[symbol] = float(np.median(sunday)) if sunday else 0.0
    saturdays = [d for d in dates if d.weekday() == 5 and all(cal[s].get(d, 0) == 0 for s in SYMBOLS)]
    # A date only counts as "XAUUSD alone" when the other two symbols have bars,
    # so window boundary days do not show up here.
    xau_closed = [
        d for d in dates
        if d.weekday() < 5
        and cal["XAUUSD"].get(d, 0) == 0
        and cal["EURUSD"].get(d, 0) > 0
        and cal["DXY"].get(d, 0) > 0
    ]
    return {
        "weekday_medians": weekday_medians,
        "sunday_medians": sunday_medians,
        "saturdays": saturdays,
        "xau_closed": xau_closed,
        "first_date": dates[0],
        "first_counts": {s: cal[s].get(dates[0], 0) for s in SYMBOLS},
        "last_date": dates[-1],
        "last_counts": {s: cal[s].get(dates[-1], 0) for s in SYMBOLS},
    }


# ---------------------------------------------------------------- VWAP

def add_vwap(part: pd.DataFrame) -> pd.DataFrame:
    """Cumulative hlc3 VWAP with weighted sigma, reset at each UTC midnight."""
    out = part.sort_values("ts").reset_index(drop=True).copy()
    out["hlc3"] = (out["high"] + out["low"] + out["close"]) / 3.0
    syn = (out["high"] - out["low"]) + (out["close"] - out["close"].shift(1)).abs()
    syn = syn.fillna(0.0).clip(lower=0.0)
    real_share = float((out["volume"] > 0).mean())
    weight = out["volume"] if real_share > 0.5 else syn
    if float(weight.sum()) <= 0.0:
        weight = pd.Series(1.0, index=out.index)
    anchor = out["ts"].dt.floor("D")
    running = weight.groupby(anchor).cumsum()
    defined = running.where(running > 0)
    vwap = (weight * out["hlc3"]).groupby(anchor).cumsum() / defined
    vwap = vwap.groupby(anchor).ffill().fillna(out["hlc3"])
    dev = weight * (out["hlc3"] - vwap) ** 2
    sigma = np.sqrt(dev.groupby(anchor).cumsum() / defined)
    sigma = sigma.groupby(anchor).ffill().fillna(0.0)
    out["vwap"] = vwap
    out["sigma"] = sigma
    out["anchor"] = anchor
    out["utc_date"] = out["ts"].dt.date
    out["weight_kind"] = "reported volume" if real_share > 0.5 else "synthetic activity weight"
    return out


def pick_window(vw: pd.DataFrame, days: int = 3, min_bars: int = 900) -> pd.DataFrame:
    counts = vw.groupby("utc_date").size()
    full = [d for d, n in counts.items() if n >= min_bars]
    keep = full[-days:]
    return vw[vw["utc_date"].isin(keep)]


def vwap_stats(window: pd.DataFrame) -> dict:
    close, v, s = window["close"], window["vwap"], window["sigma"]
    return {
        "bars": int(len(window)),
        "inside1": float(((close >= v - s) & (close <= v + s)).mean()) * 100.0,
        "inside3": float(((close >= v - 3 * s) & (close <= v + 3 * s)).mean()) * 100.0,
        "resets": int(window["anchor"].nunique()),
        "start": window["ts"].min(),
        "end": window["ts"].max(),
    }


# ---------------------------------------------------------------- regime

def add_regime(part: pd.DataFrame) -> pd.DataFrame:
    """Four-state daily labels from ATR percentile, ADX with DI, EMA slope, ER."""
    out = part.sort_values("ts").reset_index(drop=True).copy()
    high, low, close = out["high"], out["low"], out["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=out.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=out.index
    )
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()
    atr_pct = atr.rolling(252).rank(pct=True) * 100.0
    ema50 = close.ewm(span=50, adjust=False).mean()
    slope = ema50 - ema50.shift(5)
    efficiency = (close - close.shift(10)).abs() / close.diff().abs().rolling(10).sum()

    high_vol = atr_pct >= 70
    trend_up = (adx >= 25) & (plus_di > minus_di) & (slope > 0)
    trend_down = (adx >= 25) & (minus_di > plus_di) & (slope < 0)
    er_up = (efficiency >= 0.35) & (slope > 0)
    er_down = (efficiency >= 0.35) & (slope < 0)
    label = np.select(
        [high_vol, trend_up, trend_down, er_up, er_down],
        ["high volatility", "uptrend", "downtrend", "uptrend", "downtrend"],
        default="low volatility",
    )
    out["atr_pct"] = atr_pct
    out["adx"] = adx
    out["label"] = label
    out["trend_evidence"] = trend_up | trend_down | er_up | er_down
    out["used_er"] = (~high_vol) & (~trend_up) & (~trend_down) & (er_up | er_down)
    return out


def regime_stats(window: pd.DataFrame) -> dict:
    dist = window["label"].value_counts(normalize=True) * 100.0
    trend_ev = int(window["trend_evidence"].sum())
    hidden = int((window["trend_evidence"] & (window["label"] == "high volatility")).sum())
    return {
        "bars": int(len(window)),
        "uptrend": float(dist.get("uptrend", 0.0)),
        "downtrend": float(dist.get("downtrend", 0.0)),
        "high": float(dist.get("high volatility", 0.0)),
        "low": float(dist.get("low volatility", 0.0)),
        "hidden": hidden,
        "hidden_share": (hidden / trend_ev * 100.0) if trend_ev else 0.0,
        "er_used": int(window["used_er"].sum()),
        "start": window["ts"].min(),
        "end": window["ts"].max(),
    }


# ---------------------------------------------------------------- charts

def path_from(xs, ys) -> str:
    return "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in zip(xs, ys))


def area_path(xs, upper, lower) -> str:
    head = path_from(xs, upper)
    tail = " L ".join(f"{x:.1f} {y:.1f}" for x, y in zip(reversed(xs), reversed(lower)))
    return f"{head} {tail} Z"


def wrap(svg: str, width: int, height: int, label: str) -> str:
    return (
        f'<svg class="chart" role="img" aria-label="{label}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">{svg}</svg>'
    )


def chart_bars(items: list[tuple[str, str, float, str]], scale_max: float, label: str) -> str:
    """Horizontal bars: (row label, css class, value, value label)."""
    width = 720
    x0, x1 = 132, 636
    row_h, gap = 22, 14
    height = len(items) * (row_h + gap) + 8
    parts = []
    for i, (text, cls, value, vlabel) in enumerate(items):
        y = 4 + i * (row_h + gap)
        w = max(0.0, min(1.0, value / scale_max)) * (x1 - x0)
        parts.append(f'<text class="rowlabel" x="{x0 - 12}" y="{y + 16}" text-anchor="end">{text}</text>')
        parts.append(f'<rect class="track" x="{x0}" y="{y}" width="{x1 - x0}" height="{row_h}" rx="4"/>')
        if w <= 0:
            parts.append(f'<rect class="bar-zero" x="{x0}" y="{y}" width="2" height="{row_h}" rx="1"/>')
        else:
            parts.append(
                f'<rect class="{cls}" x="{x0}" y="{y}" width="{w:.1f}" height="{row_h}" rx="4"/>'
            )
        parts.append(f'<text class="value" x="{x1 + 10}" y="{y + 16}">{vlabel}</text>')
    return wrap("".join(parts), width, height, label)


def cover_label(value: float) -> str:
    if value <= 0:
        return "0%"
    if value < 1:
        return f"{value:.2f}%"
    return f"{value:.1f}%"


def chart_calendar(dates: list, cal: dict, label: str) -> str:
    gutter, step, cw, ch = 96, 23, 20, 24
    top, row_gap = 48, 6
    width = gutter + len(dates) * step + 6
    height = top + len(SYMBOLS) * (ch + row_gap) + 4
    peak = max(cal[s].get(d, 0) for s in SYMBOLS for d in dates) or 1
    parts = []
    for i, d in enumerate(dates):
        cx = gutter + i * step + cw / 2
        parts.append(f'<text class="axis" x="{cx:.1f}" y="12" text-anchor="middle">{WEEKDAY_LETTERS[d.weekday()]}</text>')
        if d.day in (1, 8, 15, 22) or i in (0, len(dates) - 1):
            parts.append(f'<text class="axis-strong" x="{cx:.1f}" y="30" text-anchor="middle">{d.day:02d}</text>')
    for r, symbol in enumerate(SYMBOLS):
        y = top + r * (ch + row_gap)
        parts.append(f'<text class="rowlabel" x="{gutter - 12}" y="{y + 17}" text-anchor="end">{symbol}</text>')
        for i, d in enumerate(dates):
            x = gutter + i * step
            n = cal[symbol].get(d, 0)
            title = f"{symbol} {d.isoformat()}: {n} bars"
            if n <= 0:
                parts.append(
                    f'<rect class="cal-empty" x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4">'
                    f"<title>{title}</title></rect>"
                )
            else:
                opacity = 0.12 + 0.85 * (n / peak) ** 0.5
                parts.append(
                    f'<rect class="cal" style="fill: rgba(101, 169, 255, {opacity:.2f})" '
                    f'x="{x}" y="{y}" width="{cw}" height="{ch}" rx="4"><title>{title}</title></rect>'
                )
    return wrap("".join(parts), width, height, label)


def chart_vwap(window: pd.DataFrame, decimals: int, label: str) -> str:
    width, height = 720, 330
    ml, mr, mt, mb = 58, 14, 16, 42
    x0, x1 = ml, width - mr
    y0, y1 = mt, height - mb
    t0, t1 = window["ts"].iloc[0], window["ts"].iloc[-1]
    span = max((t1 - t0).total_seconds(), 1.0)

    def sx(ts) -> float:
        return x0 + (ts - t0).total_seconds() / span * (x1 - x0)

    lo = min(window["close"].min(), (window["vwap"] - 3 * window["sigma"]).min())
    hi = max(window["close"].max(), (window["vwap"] + 3 * window["sigma"]).max())
    pad = (hi - lo) * 0.04 or 1.0
    lo, hi = lo - pad, hi + pad

    def sy(price: float) -> float:
        return y1 - (price - lo) / (hi - lo) * (y1 - y0)

    parts = []
    for frac in np.linspace(0, 1, 5):
        price = lo + frac * (hi - lo)
        y = sy(price)
        parts.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="axis" x="{x0 - 8}" y="{y + 3.5:.1f}" text-anchor="end">'
            f"{price:.{decimals}f}</text>"
        )
    for _, group in window.groupby("anchor", sort=True):
        xa = sx(group["ts"].iloc[0])
        if xa - x0 < 3:
            continue
        parts.append(f'<line class="daysep" x1="{xa:.1f}" y1="{y0}" x2="{xa:.1f}" y2="{y1}"/>')
        day = pd.Timestamp(group["anchor"].iloc[0]).strftime("%d %b")
        parts.append(f'<text class="axis-strong" x="{xa + 5:.1f}" y="{y1 + 16}">{day} reset</text>')

    samp = window.iloc[::5]
    xs = [sx(t) for t in samp["ts"]]
    up1 = [sy(p) for p in samp["vwap"] + samp["sigma"]]
    lo1 = [sy(p) for p in samp["vwap"] - samp["sigma"]]
    up3 = [sy(p) for p in samp["vwap"] + 3 * samp["sigma"]]
    lo3 = [sy(p) for p in samp["vwap"] - 3 * samp["sigma"]]
    vwap_y = [sy(p) for p in samp["vwap"]]
    px_y = [sy(p) for p in samp["close"]]
    sig1_up = [sy(p) for p in samp["vwap"] + samp["sigma"]]
    sig1_lo = [sy(p) for p in samp["vwap"] - samp["sigma"]]
    sig3_up = [sy(p) for p in samp["vwap"] + 3 * samp["sigma"]]
    sig3_lo = [sy(p) for p in samp["vwap"] - 3 * samp["sigma"]]
    parts.append(f'<path class="area1" d="{area_path(xs, up1, lo1)}"/>')
    parts.append(f'<path class="px" d="{path_from(xs, px_y)}"/>')
    parts.append(f'<path class="sig3" d="{path_from(xs, sig3_up)}"/>')
    parts.append(f'<path class="sig3" d="{path_from(xs, sig3_lo)}"/>')
    parts.append(f'<path class="sig1" d="{path_from(xs, sig1_up)}"/>')
    parts.append(f'<path class="sig1" d="{path_from(xs, sig1_lo)}"/>')
    parts.append(f'<path class="vwap" d="{path_from(xs, vwap_y)}"/>')
    parts.append(f'<text class="axis" x="{x0}" y="{y1 + 34}">{t0.strftime("%d %b %Y")} start</text>')
    parts.append(
        f'<text class="axis" x="{x1}" y="{y1 + 34}" text-anchor="end">{t1.strftime("%d %b %Y")} end</text>'
    )
    return wrap("".join(parts), width, height, label)


def chart_regime(
    reg: pd.DataFrame,
    label: str,
    title: str = "EURUSD daily close",
    decimals: int = 4,
) -> str:
    width, height = 720, 470
    ml, mr = 58, 14
    x0, x1 = ml, width - mr
    a_top, a_bot = 16, 250
    r_top, r_bot = 258, 274
    b_top, b_bot = 306, 366
    c_top, c_bot = 396, 452
    n = len(reg)
    xs = list(np.linspace(x0, x1, n))
    step = (x1 - x0) / max(n - 1, 1)
    parts = [
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse">'
        '<path class="hatch-line" d="M0,6 L6,0"/></pattern></defs>'
    ]

    close = reg["close"].to_numpy()
    lo, hi = float(np.nanmin(close)), float(np.nanmax(close))
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad

    def sy_price(p: float) -> float:
        return a_bot - (p - lo) / (hi - lo) * (a_bot - a_top)

    for frac in np.linspace(0, 1, 4):
        price = lo + frac * (hi - lo)
        y = sy_price(price)
        parts.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{x0 - 8}" y="{y + 3.5:.1f}" text-anchor="end">{price:.{decimals}f}</text>')
    parts.append(f'<text class="axis-strong" x="{x0 + 6}" y="{a_top + 12}">{title}</text>')
    parts.append(f'<path class="px-daily" d="{path_from(xs, [sy_price(p) for p in close])}"/>')

    labels = reg["label"].tolist()
    start = 0
    for i in range(1, n + 1):
        if i == n or labels[i] != labels[start]:
            cls = STATE_CLASS[labels[start]]
            rx = xs[start]
            rw = (xs[i - 1] - xs[start] + step) if i < n else (xs[i - 1] - xs[start])
            rect = (
                f'<rect class="{cls}" x="{rx:.1f}" y="{r_top}" width="{rw:.1f}" '
                f'height="{r_bot - r_top}"/>'
            )
            if cls == "ribbon-down":
                rect += (
                    f'<rect class="ribbon-hatch" x="{rx:.1f}" y="{r_top}" width="{rw:.1f}" '
                    f'height="{r_bot - r_top}"/>'
                )
            parts.append(rect)
            start = i
    parts.append(f'<text class="axis" x="{x0 - 8}" y="{r_top + 12}" text-anchor="end">label</text>')

    atr = reg["atr_pct"].to_numpy()

    def sy_atr(v: float) -> float:
        return b_bot - v / 100.0 * (b_bot - b_top)

    for tick in (0, 30, 70, 100):
        y = sy_atr(tick)
        cls = "guide" if tick in (30, 70) else "grid"
        parts.append(f'<line class="{cls}" x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{x0 - 8}" y="{y + 3.5:.1f}" text-anchor="end">{tick}</text>')
    parts.append(f'<text class="axis-strong" x="{x0 + 6}" y="{b_top + 12}">ATR(14) percentile over 252 bars</text>')
    parts.append(f'<path class="ind" d="{path_from(xs, [sy_atr(v) for v in atr])}"/>')

    adx = reg["adx"].to_numpy()
    hi_adx = max(float(np.nanmax(adx)) * 1.1, 40.0)

    def sy_adx(v: float) -> float:
        return c_bot - v / hi_adx * (c_bot - c_top)

    parts.append(f'<line class="guide" x1="{x0}" y1="{sy_adx(25):.1f}" x2="{x1}" y2="{sy_adx(25):.1f}"/>')
    parts.append(f'<text class="axis" x="{x0 - 8}" y="{sy_adx(25) + 3.5:.1f}" text-anchor="end">25</text>')
    parts.append(f'<text class="axis" x="{x0 - 8}" y="{c_bot + 3.5:.1f}" text-anchor="end">0</text>')
    parts.append(f'<line class="grid" x1="{x0}" y1="{c_bot:.1f}" x2="{x1}" y2="{c_bot:.1f}"/>')
    parts.append(f'<text class="axis-strong" x="{x0 + 6}" y="{c_top + 12}">ADX(14)</text>')
    parts.append(f'<path class="ind" d="{path_from(xs, [sy_adx(v) for v in adx])}"/>')

    for j, i in enumerate(np.linspace(0, n - 1, 6).astype(int)):
        stamp = pd.Timestamp(reg["ts"].iloc[i])
        anchor = "start" if j == 0 else ("end" if j == 5 else "middle")
        parts.append(
            f'<text class="axis" x="{xs[i]:.1f}" y="{height - 6}" text-anchor="{anchor}">'
            f"{stamp.strftime('%b %y')}</text>"
        )
    return wrap("".join(parts), width, height, label)


def chart_depth(daily_rows: list[dict], minute_rows: list[dict], label: str) -> tuple[str, dict]:
    width, height = 720, 332
    ml, mr = 108, 16
    x0, x1 = ml, width - mr
    a_top = 34
    b_top, b_bot = 232, 300
    t_min = min(r["start"] for r in daily_rows)
    t_max = max(r["end"] for r in daily_rows + minute_rows)
    span_days = max((t_max - t_min).days, 1)

    def sxA(ts) -> float:
        return x0 + (ts - t_min).total_seconds() / (span_days * 86400) * (x1 - x0)

    parts = []
    years = sorted({t_min.year, t_max.year, *[y for y in (1980, 1990, 2000, 2010, 2020) if t_min.year < y < t_max.year]})
    a_bot = a_top + 6 * 24
    for year in years:
        x = sxA(pd.Timestamp(year=year, month=1, day=1, tz="UTC"))
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{a_top - 8}" x2="{x:.1f}" y2="{a_bot}"/>')
        parts.append(f'<text class="axis" x="{x:.1f}" y="{a_bot + 16}" text-anchor="middle">{year}</text>')
    parts.append(f'<text class="axis-strong" x="{x0}" y="{a_top - 16}">Daily bars, full history</text>')

    minute_top, minute_bottom = None, None
    for i, symbol in enumerate(SYMBOLS):
        daily = next(r for r in daily_rows if r["symbol"] == symbol)
        minute = next(r for r in minute_rows if r["symbol"] == symbol)
        y_day = a_top + (2 * i) * 24
        y_min = a_top + (2 * i + 1) * 24
        parts.append(f'<text class="rowlabel" x="{x0 - 12}" y="{y_day + 12}" text-anchor="end">{symbol} 1d</text>')
        parts.append(f'<text class="axis" x="{x0 - 12}" y="{y_min + 11}" text-anchor="end">{symbol} 1m</text>')
        dx0, dx1 = sxA(daily["start"]), sxA(daily["end"])
        parts.append(f'<rect class="depth-daily" x="{dx0:.1f}" y="{y_day}" width="{max(dx1 - dx0, 1):.1f}" height="14" rx="3"/>')
        mx0, mx1 = sxA(minute["start"]), sxA(minute["end"])
        parts.append(f'<rect class="depth-minute" x="{mx0:.1f}" y="{y_min}" width="{max(mx1 - mx0, 1.6):.1f}" height="10" rx="2"/>')
        if minute_top is None:
            minute_top = y_min - 4
        minute_bottom = y_min + 14

    m_min = min(r["start"] for r in minute_rows)
    m_max = max(r["end"] for r in minute_rows)
    m_days = max((m_max - m_min).days, 1)
    minute_px = (x1 - x0) * m_days / span_days

    def sxB(ts) -> float:
        return x0 + (ts - m_min).total_seconds() / (m_days * 86400) * (x1 - x0)

    src_x = sxA(m_min)
    parts.append(f'<line class="depth-zoom" x1="{src_x:.1f}" y1="{minute_top}" x2="{x0}" y2="{b_top - 10}"/>')
    parts.append(f'<line class="depth-zoom" x1="{src_x:.1f}" y1="{minute_bottom}" x2="{x1}" y2="{b_bot + 10}"/>')
    parts.append(
        f'<rect class="depth-zoom" x="{x0 - 4}" y="{b_top - 10}" width="{x1 - x0 + 8}" '
        f'height="{b_bot - b_top + 20}" rx="6"/>'
    )
    parts.append(f'<text class="axis-strong" x="{x0}" y="{b_top - 20}">Minute bars, same three symbols</text>')

    for i, symbol in enumerate(SYMBOLS):
        minute = next(r for r in minute_rows if r["symbol"] == symbol)
        y = b_top + 6 + i * 26
        parts.append(f'<text class="rowlabel" x="{x0 - 12}" y="{y + 12}" text-anchor="end">{symbol} 1m</text>')
        bx0, bx1 = sxB(minute["start"]), sxB(minute["end"])
        parts.append(f'<rect class="depth-minute" x="{bx0:.1f}" y="{y}" width="{max(bx1 - bx0, 2):.1f}" height="16" rx="3"/>')

    tick_days = list(pd.date_range(m_min.floor("D"), m_max.floor("D"), freq="4D"))
    for tick in tick_days:
        x = sxB(tick)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{b_top - 10}" x2="{x:.1f}" y2="{b_bot + 10}"/>')
        parts.append(f'<text class="axis" x="{x:.1f}" y="{b_bot + 26}" text-anchor="middle">{tick.strftime("%d %b")}</text>')

    stats = {"span_years": span_days / 365.25, "minute_px": minute_px, "m_days": m_days}
    return wrap("".join(parts), width, height, label), stats


def chart_offsets(rows: list[tuple[str, str, int, str]], label: str) -> str:
    width, height = 740, 214
    ml, mr = 150, 16
    x0, x1 = ml, width - mr
    h_lo, h_hi = -6.0, 30.0
    top, bot = 46, 180

    def sx(hours: float) -> float:
        return x0 + (hours - h_lo) / (h_hi - h_lo) * (x1 - x0)

    parts = []
    parts.append(f'<rect class="off-day" x="{sx(0):.1f}" y="{top}" width="{sx(24) - sx(0):.1f}" height="{bot - top}" rx="6"/>')
    for hours in range(-6, 31, 6):
        x = sx(hours)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bot}"/>')
        parts.append(f'<text class="axis" x="{x:.1f}" y="{top - 8}" text-anchor="middle">{hours}</text>')
    parts.append(f'<line class="off-utc" x1="{sx(0):.1f}" y1="{top - 4}" x2="{sx(0):.1f}" y2="{bot + 4}"/>')
    parts.append(f'<line class="off-utc" x1="{sx(24):.1f}" y1="{top - 4}" x2="{sx(24):.1f}" y2="{bot + 4}"/>')
    for i, (text, offset, shift, note) in enumerate(rows):
        y = 58 + i * 40
        start, end = -float(shift), 24.0 - shift
        parts.append(f'<text class="rowlabel" x="{x0 - 12}" y="{y + 17}" text-anchor="end">{text}</text>')
        parts.append(
            f'<rect class="off-bar" x="{sx(start):.1f}" y="{y}" width="{sx(end) - sx(start):.1f}" height="24" rx="4"/>'
        )
        parts.append(f'<text class="onbar mono" x="{sx(start) + 8:.1f}" y="{y + 16}">stored {offset}</text>')
        parts.append(f'<text class="onbar mono" x="{sx(end) - 8:.1f}" y="{y + 16}" text-anchor="end">{note}</text>')
    parts.append(f'<text class="axis" x="{x0}" y="{height - 6}">UTC hour of day, 0 and 24 are UTC midnight</text>')
    return wrap("".join(parts), width, height, label)


# ---------------------------------------------------------------- page

def finding(number: int, title: str, tag: str, body: str) -> str:
    return f'''
    <section class="panel" id="finding-{number}">
      <div class="panel-heading">
        <div><p class="eyebrow">FINDING {number:02d}</p><h2>{title}</h2></div>
        <span class="panel-tag">{tag}</span>
      </div>
      {body}
    </section>'''


def legend(items: list[tuple[str, str]]) -> str:
    chips = "".join(f'<span><i class="{cls}"></i>{text}</span>' for text, cls in items)
    return f'<div class="legend">{chips}</div>'


def short_list(dates: list) -> str:
    parts = [d.strftime("%b %d") for d in dates]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def build_page(bars: pd.DataFrame, stats: list[dict]) -> str:
    by_key = {(r["symbol"], r["timeframe"]): r for r in stats}
    total = int(len(bars))
    minute_bars_1m = [r for r in stats if r["timeframe"] == "1m"]
    daily_bars_1d = [r for r in stats if r["timeframe"] == "1d"]
    m_total = sum(r["bars"] for r in minute_bars_1m)
    d_total = sum(r["bars"] for r in daily_bars_1d)
    bars_1m = bars[bars["timeframe"] == "1m"]
    bars_1d = bars[bars["timeframe"] == "1d"]
    dates, cal = build_calendar(bars_1m)
    cf = calendar_findings(dates, cal)

    vw = {symbol: add_vwap(bars_1m[bars_1m["symbol"] == symbol]) for symbol in SYMBOLS}
    windows = {symbol: pick_window(vw[symbol]) for symbol in ("XAUUSD", "EURUSD")}
    vstats = {symbol: vwap_stats(window) for symbol, window in windows.items()}

    regime = add_regime(bars_1d[bars_1d["symbol"] == "EURUSD"])
    regime_window = regime.tail(780).dropna(subset=["atr_pct", "adx"]).reset_index(drop=True)
    rstats = regime_stats(regime.tail(780))

    # Minute rows end in +0100 or -0400, daily rows end in +00:00, so both
    # the offset and the format differ per series.
    offsets: dict[tuple[str, str], str] = {}
    raw_offsets: dict[tuple[str, str], str] = {}
    for symbol in SYMBOLS:
        for timeframe in ("1d", "1m"):
            part = bars[(bars["symbol"] == symbol) & (bars["timeframe"] == timeframe)]
            raw = part["timestamp"].str.extract(r"([+-]\d{2}:?\d{2})$")[0].fillna("+0000")
            raw_offsets[(symbol, timeframe)] = raw.mode().iloc[0]
            offsets[(symbol, timeframe)] = raw.str.replace(":", "", regex=False).mode().iloc[0]

    daily_rows = [by_key[(s, "1d")] for s in SYMBOLS]
    minute_rows = [by_key[(s, "1m")] for s in SYMBOLS]
    depth_svg, depth_stats = chart_depth(
        daily_rows, minute_rows, "Daily history against the minute window"
    )

    first_daily = min(r["start"] for r in daily_rows)
    last_any = max(r["end"] for r in stats)
    xau_1m = by_key[("XAUUSD", "1m")]
    dxy_1d = by_key[("DXY", "1d")]
    span_years = (last_any - first_daily).days / 365.25

    count_items = [
        (f'{r["symbol"]} {r["timeframe"]}', f'bar-{r["timeframe"]}', float(r["bars"]), f'{r["bars"]:,}')
        for r in stats
    ]
    cover_items = [
        (f'{r["symbol"]} {r["timeframe"]}', "bar-cover", r["volume_share"], cover_label(r["volume_share"]))
        for r in stats
    ]

    xw, ew = windows["XAUUSD"], windows["EURUSD"]
    xs_, es_ = vstats["XAUUSD"], vstats["EURUSD"]

    count_chart = chart_bars(count_items, max(r["bars"] for r in stats), "Bar count per series")
    cover_chart = chart_bars(cover_items, 100.0, "Share of bars carrying volume per series")
    cal_chart = chart_calendar(dates, cal, "Minute bars per UTC date and symbol")
    vwap_x_chart = chart_vwap(xw, 2, "XAUUSD one minute VWAP with one and three sigma bands")
    vwap_e_chart = chart_vwap(ew, 5, "EURUSD one minute VWAP with one and three sigma bands")
    regime_chart = chart_regime(regime_window, "EURUSD daily regime labels with ATR percentile and ADX")
    def utc_window(shift: int) -> str:
        start = -shift
        end = 24 - shift
        start_str = f"{start % 24:02d}:00"
        end_str = f"{(end - 1) % 24 + 1:02d}:00"
        return f"UTC {start_str} to {end_str}"

    offset_rows = []
    for label, key in (
        ("EURUSD 1m", ("EURUSD", "1m")),
        ("XAUUSD and DXY 1m", ("XAUUSD", "1m")),
        ("Daily bars", ("EURUSD", "1d")),
    ):
        shift = int(offsets[key][:3])
        offset_rows.append((label, raw_offsets[key], shift, utc_window(shift)))
    offset_chart = chart_offsets(offset_rows, "Where each stored day sits on the UTC clock")

    example = pd.Timestamp("2026-09-15 12:00", tz="UTC")
    offset_table_rows = [
        ("EURUSD 1m", ("EURUSD", "1m")),
        ("XAUUSD 1m and DXY 1m", ("XAUUSD", "1m")),
        ("All daily bars", ("EURUSD", "1d")),
    ]
    table_rows = ""
    for name, key in offset_table_rows:
        stored_offset = offsets[key]
        raw_form = raw_offsets[key]
        tz = stored_offset[:3] + ":" + stored_offset[3:]
        stored = example.tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S") + raw_form
        table_rows += (
            f'<div><strong>{name}</strong><span>{raw_form}</span>'
            f'<em class="mono">{stored}</em></div>'
        )

    sat_text = (
        "All three series are empty on every Saturday in the window: " + short_list(cf["saturdays"]) + "."
        if cf["saturdays"]
        else ""
    )
    xau_text = (
        "XAUUSD alone is empty on " + short_list(cf["xau_closed"]) + " while the other two symbols keep trading."
        if cf["xau_closed"]
        else ""
    )
    sun_text = (
        "Sundays hold only the market open: "
        + ", ".join(f"{int(cf['sunday_medians'][s])} bars on {s}" for s in SYMBOLS)
        + " as a typical value."
    )
    weekday_text = ", ".join(
        f"{s} about {int(cf['weekday_medians'][s]):,}" for s in SYMBOLS
    )

    sections = []

    sections.append(finding(
        1,
        "Bar counts by series",
        f"{total:,} bars",
        f'''
      <div class="prose">
        <p>The ohlcv table holds <strong>{total:,} bars</strong> in six series. Minute rows dominate the
        file: the three 1m series add up to {m_total:,} bars against {d_total:,} daily bars, because one
        full day of minute data is about 1,400 rows while a full year of daily data is 252.</p>
        <p>DXY carries the deepest daily history at {by_key[("DXY", "1d")]["bars"]:,} bars back to
        {by_key[("DXY", "1d")]["start"].strftime("%Y-%m-%d")}. EURUSD stops at
        {by_key[("EURUSD", "1d")]["bars"]:,} bars from {by_key[("EURUSD", "1d")]["start"].strftime("%Y-%m-%d")},
        which is where its Yahoo series begins.</p>
      </div>
      {count_chart}
      {legend([("1d bars", "bar-1d"), ("1m bars", "bar-1m")])}
      <p class="caption">Each bar is a count grouped by symbol and timeframe.</p>'''))

    sections.append(finding(
        2,
        "Volume exists on one series",
        f'{xau_1m["volume_share"]:.1f}% covered',
        f'''
      <div class="prose">
        <p>Volume above zero appears on <strong>{xau_1m["volume_share"]:.3f}% of XAUUSD minute bars</strong>
        ({xau_1m["volume_bars"]:,} of {xau_1m["bars"]:,}) and on
        {by_key[("XAUUSD", "1d")]["volume_share"]:.1f}% of its daily bars. EURUSD and DXY read zero at both
        timeframes, and DXY daily shows volume on {dxy_1d["volume_bars"]} stray bars only.</p>
        <p>The VWAP weight for the two spot series therefore comes from the synthetic formula
        <span class="formula">(H-L) + |C_t - C_(t-1)|</span>. That substitution is a fact about the data,
        not a preference, and the two weight schemes will not produce identical bands.</p>
      </div>
      {cover_chart}
      {legend([("Coverage", "bar-cover"), ("No coverage", "empty")])}
      <p class="caption">Coverage counts bars where volume is greater than zero.</p>'''))

    sections.append(finding(
        3,
        "Minute gaps follow the trading week",
        f"{len(dates)} UTC dates",
        f'''
      <div class="prose">
        <p>Grouped by UTC date the minute series are nearly complete on weekdays: {weekday_text} bars on a
        typical weekday. {sat_text} {sun_text} {xau_text}</p>
        <p>The first and last columns are window boundaries rather than session gaps. On\n        {cf["first_date"].isoformat()} only EURUSD holds bars ({cf["first_counts"]["EURUSD"]}). On\n        {cf["last_date"].isoformat()}, XAUUSD holds {cf["last_counts"]["XAUUSD"]} bars and DXY\n        {cf["last_counts"]["DXY"]} while EURUSD holds none.</p>
      </div>
      {cal_chart}
      {legend([("Many bars", "lvl-hi"), ("Few bars", "lvl-lo"), ("No bars", "empty")])}
      <p class="caption">Hover a cell for its exact bar count. Column numbers are day of month, letters are weekday.</p>'''))

    sections.append(finding(
        4,
        "VWAP bands on gold futures",
        "real COMEX volume",
        f'''
      <div class="prose">
        <p>Bands rebuilt from <strong>{xs_["bars"]:,} one-minute bars</strong> over
        {xs_["start"].strftime("%Y-%m-%d")} to {xs_["end"].strftime("%Y-%m-%d")} with reported volume as the
        weight. The close stayed inside the 1 sigma band on {xs_["inside1"]:.1f}% of bars and inside the
        3 sigma band on {xs_["inside3"]:.1f}%.</p>
        <p>The anchor resets {xs_["resets"]} times in the window, at UTC midnight, so every day starts with
        the bands collapsed on the first bar and widens them as volume accumulates.</p>
      </div>
      {vwap_x_chart}
      {legend([
          ("Close", "ln-thin"),
          ("VWAP", "ln"),
          ("1 sigma band", "dash"),
          ("3 sigma band", "dot"),
          ("1 sigma envelope", "env"),
      ])}
      <div class="finding-note">Open decision: the anchor shown resets at UTC midnight. The research
      recommends 21:00 UTC, the 17:00 New York session reset, and the anchor time conflict ticket decides
      which one ships.</div>'''))

    sections.append(finding(
        5,
        "VWAP bands on EURUSD",
        "synthetic weight",
        f'''
      <div class="prose">
        <p>Same window and same formula, weight <span class="formula">(H-L) + |C_t - C_(t-1)|</span> because
        spot FX publishes no volume. The close stayed inside the 1 sigma band on
        {es_["inside1"]:.1f}% of bars and inside the 3 sigma band on {es_["inside3"]:.1f}%.</p>
        <p>The synthetic weight reacts to range and price change instead of traded size, so EURUSD bands
        track price shape more closely than the volume weighted gold bands do. Both percentages describe one
        three day sample, not a proven hit rate.</p>
      </div>
      {vwap_e_chart}
      {legend([
          ("Close", "ln-thin"),
          ("VWAP", "ln"),
          ("1 sigma band", "dash"),
          ("3 sigma band", "dot"),
          ("1 sigma envelope", "env"),
      ])}'''))

    sections.append(finding(
        6,
        "Regime labels on EURUSD daily bars",
        f'{rstats["bars"]:,} bars charted',
        f'''
      <div class="prose">
        <p>Labels computed over the full daily history and charted across the last {rstats["bars"]} bars
        from {rstats["start"].strftime("%Y-%m-%d")}. Over that window the four states split
        <strong>{rstats["uptrend"]:.0f}% uptrend, {rstats["downtrend"]:.0f}% downtrend,
        {rstats["high"]:.0f}% high volatility, {rstats["low"]:.0f}% low volatility</strong>.</p>
        <p>Priority shapes the result: {rstats["hidden"]} bars ({rstats["hidden_share"]:.0f}% of the window)
        carry trend evidence yet print high volatility, because volatility beats trend by design. The
        efficiency ratio fallback supplies {rstats["er_used"]} labels when ADX reads below 25.</p>
      </div>
      {regime_chart}
      {legend([
          ("high volatility", "ribbon-high"),
          ("uptrend", "ribbon-up"),
          ("low volatility", "ribbon-low"),
          ("downtrend", "hatch"),
      ])}
      <div class="finding-note">Every bar carries exactly one label, so a strong trend inside high
      volatility reads as high volatility rather than as uptrend. Two independent axes would be a different
      design.</div>'''))

    sections.append(finding(
        7,
        "Daily history dwarfs minute history",
        f'{len(dates)} dates of 1m',
        f'''
      <div class="prose">
        <p>Drawn to one scale, daily bars reach {first_daily.strftime("%Y-%m-%d")}, a span of
        {span_years:.0f} years, while minute bars cover {len(dates)} UTC dates. On that scale the minute
        window is <strong>{depth_stats["minute_px"]:.1f} pixels wide</strong>, which is why the second panel
        zooms into it.</p>
        <p>The zoomed panel is the real constraint: a minute level backtest runs on
        {depth_stats["m_days"]} days of data today, and the deeper HistData download has to land before any
        minute level claim is credible.</p>
      </div>
      {depth_svg}
      {legend([("Daily range", "b-35"), ("Minute range", "b-90")])}'''))

    sections.append(finding(
        8,
        "Timestamps store three offsets",
        "mixed timezone",
        f'''
      <div class="prose">
        <p>The timestamp column keeps the source offset and the source format:
        {raw_offsets[("EURUSD", "1m")]} on EURUSD minute bars,
        {raw_offsets[("XAUUSD", "1m")]} on XAUUSD and DXY minute bars, and
        {raw_offsets[("EURUSD", "1d")]} on daily bars. One UTC instant therefore writes three different
        local strings, and minute rows carry no colon while daily rows do.</p>
        <p>A SQL range filter on the raw text sorts those strings wrongly, so every timestamp has to be
        parsed to UTC before any comparison. The chart shows where each stored 24 hour window sits against
        the UTC clock.</p>
      </div>
      {offset_chart}
      <div class="timeframe-table offsets">
        <div class="tf-head"><span>Series</span><span>Stored offset</span><span>String for 12:00 UTC</span></div>
        {table_rows}
      </div>
      <div class="finding-note">Input to the cleaning rules ticket: parse to UTC on read and never compare
      raw timestamp text.</div>'''))

    kpis = f'''
    <section class="kpi-grid" aria-label="Key numbers">
      <article class="kpi" id="kpi-bars"><span>Bars staged</span><strong>{total:,}</strong><small>Six series in one ohlcv table</small></article>
      <article class="kpi" id="kpi-dates"><span>Minute depth</span><strong>{len(dates)} dates</strong><small>{dates[0].isoformat()} to {dates[-1].isoformat()} in UTC</small></article>
      <article class="kpi" id="kpi-years"><span>Daily depth</span><strong>{span_years:.0f} years</strong><small>{first_daily.strftime("%Y-%m-%d")} to {last_any.strftime("%Y-%m-%d")}</small></article>
      <article class="kpi" id="kpi-volume"><span>Minute bars with volume</span><strong>{xau_1m["volume_share"]:.1f}%</strong><small>XAUUSD only, the other five series read zero</small></article>
    </section>'''

    summary = f'''
    <section class="panel" id="findings-summary">
      <div class="panel-heading"><div><p class="eyebrow">CONSEQUENCES</p><h2>What these findings change</h2></div></div>
      <ul class="gate-list">
        <li><span class="gate-dot blue"></span><div><b>Synthetic weight is mandatory</b><small>EURUSD and DXY publish no volume, so their VWAP weight comes from range plus price change.</small></div></li>
        <li><span class="gate-dot blue"></span><div><b>Timestamps need UTC normalization</b><small>Three stored offsets mean raw string filters misorder rows.</small></div></li>
        <li><span class="gate-dot blue"></span><div><b>Minute history is {len(dates)} dates deep</b><small>Minute level claims wait on the deeper HistData download.</small></div></li>
        <li><span class="gate-dot blue"></span><div><b>The anchor stays open</b><small>UTC midnight against the recommended 21:00 UTC session reset.</small></div></li>
        <li><span class="gate-dot blue"></span><div><b>Regime priority hides trends</b><small>{rstats["hidden"]} charted bars carry trend evidence and still print high volatility.</small></div></li>
      </ul>
      <p class="caption">Formulas and sources live in
      <a href="{REPO_URL}/blob/main/docs/vwap-regime-research.md">vwap-regime-research.md</a> and the staged
      data record in <a href="{REPO_URL}/blob/main/docs/wayfinder/tickets/stage-raw-data.md">stage-raw-data.md</a>.</p>
    </section>'''

    body = "\n".join(sections)

    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Eight computed findings from the staged SQLite data behind the VWAP and regime strategy.">
  <title>Data findings | Quantitative FX Research Dashboard</title>
  <link rel="stylesheet" href="styles.css">
  <link rel="stylesheet" href="findings.css">
</head>
<body>
  {NAV}
  <header class="topbar">
    <div>
      <p class="eyebrow">DATA FINDINGS</p>
      <h1>What the staged data shows</h1>
      <p class="subtitle">Eight charts computed from the local SQLite database behind the VWAP and regime engine, with the numbers that decide the next build steps.</p>
    </div>
    <span class="status-pill">{total:,} BARS</span>
  </header>

  <main>
    <section class="notice" aria-label="Generation note">
      <strong>Generated view.</strong> Every number and line here is computed from data/forex_market_data.db by scripts/generate_findings_page.py. It is research evidence, not a live performance report.
    </section>

    {kpis}

{body}

    {summary}
  </main>

  <footer>Quantitative FX Research Engine · Generated from the staged database · <a href="index.html">Dashboard</a> · <a href="{REPO_URL}" target="_blank" rel="noreferrer">View repository</a></footer>
  <script src="app.js" defer></script>
</body>
</html>
'''


def main() -> None:
    bars = load_bars()
    stats = series_stats(bars)
    html = build_page(bars, stats)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
