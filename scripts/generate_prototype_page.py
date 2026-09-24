"""Generate dashboard/prototype.html: the trailing year of one-minute history.

Run from the repository root:

    python scripts/generate_prototype_page.py

The page is a generated artifact and is committed, so the Pages deployment
needs no backend. Every chart is recomputed from data/processed over the
trailing refresh.window_days (365) of one-minute bars with the same
compute_vwap_bands and compute_regime functions the analysis engine runs.
The window carries no entries, exits, or fills: this is the observation view
that precedes the signal combination rule and the backtest design.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "dashboard" / "prototype.html"

src_dir = ROOT / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fx_strategy.analysis.cleaning import PROCESSED_DIR  # noqa: E402
from fx_strategy.analysis.config import load_config  # noqa: E402
from fx_strategy.analysis.regime import compute_regime  # noqa: E402
from fx_strategy.analysis.signals import _band_position  # noqa: E402
from fx_strategy.analysis.vwap import compute_vwap_bands  # noqa: E402

from generate_findings_page import (  # noqa: E402
    NAV,
    REPO_URL,
    STATE_CLASS,
    chart_bars,
    chart_regime,
    chart_vwap,
    legend,
    wrap,
)

SYMBOLS = ("EURUSD", "XAUUSD", "DXY")
DECIMALS = {"EURUSD": 5, "XAUUSD": 2, "DXY": 2}
SESSIONS_CHARTED = 4
REGIME_BARS = 260

TICKET_URL = f"{REPO_URL}/blob/main/docs/wayfinder/tickets"

REGIME_DISPLAY = {
    "uptrend": "uptrend",
    "downtrend": "downtrend",
    "high_vol": "high volatility",
    "low_vol": "low volatility",
}
REGIME_COLUMNS = ["uptrend", "downtrend", "high volatility", "low volatility"]
# Row order reads from the top band down through the middle to the bottom band.
POSITIONS = [
    ("above_band3", "above 3 sigma", "ribbon-high"),
    ("between_1_and_3_upper", "1-3 sigma up", "bar-1m"),
    ("inside_band1", "inside 1 sigma", "bar-cover"),
    ("between_1_and_3_lower", "1-3 sigma down", "ribbon-low"),
    ("below_band3", "below 3 sigma", "ribbon-down"),
]
BAND_LEGEND = [
    ("Close", "ln-thin"),
    ("VWAP", "ln"),
    ("1 sigma band", "dash"),
    ("3 sigma band", "dot"),
    ("1 sigma envelope", "env"),
]
REGIME_LEGEND = [
    ("high volatility", "ribbon-high"),
    ("uptrend", "ribbon-up"),
    ("low volatility", "ribbon-low"),
    ("downtrend", "hatch"),
]

STYLE = """
.switcher { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 0 0 20px; }
.switch-label { color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; margin-right: 2px; }
[hidden] { display: none !important; }
.legend i.ribbon-down { background: var(--b-15); }
.xtab { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 12.5px; }
.xtab th, .xtab td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: right; font-variant-numeric: tabular-nums; }
.xtab th:first-child, .xtab td:first-child { text-align: left; }
.xtab thead th { color: var(--muted); font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; border-top: 1px solid var(--line); }
.xtab tbody td { color: var(--text); font-family: var(--mono); font-size: 12px; }
.xtab tbody td:first-child { font-family: inherit; }
.xtab td small { color: var(--muted); font-size: 11px; margin-left: 4px; }
.xtab tfoot td { color: var(--b-70); font-family: var(--mono); font-size: 12px; border-bottom: 0; }
.xtab tfoot td:first-child { font-family: inherit; }
"""

SCRIPT = """
(function () {
  var symbols = ["EURUSD", "XAUUSD", "DXY"];
  var pick = new URLSearchParams(window.location.search).get("symbol");
  if (symbols.indexOf(pick) < 0) pick = "EURUSD";
  symbols.forEach(function (s) {
    var section = document.getElementById("sym-" + s);
    var button = document.getElementById("pick-" + s);
    if (section) section.hidden = s !== pick;
    if (button) button.className = "btn " + (s === pick ? "btn-primary" : "btn-ghost");
  });
})();
"""


# ---------------------------------------------------------------- loading

def read_processed(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    if not frame["timestamp"].is_monotonic_increasing:
        frame = frame.sort_values("timestamp")
    return frame.reset_index(drop=True)


def load_window(symbol: str, cutoff: pd.Timestamp | None) -> pd.DataFrame:
    frame = read_processed(PROCESSED_DIR / f"{symbol}_1m.csv")
    if frame.empty or cutoff is None:
        return frame
    return frame[frame["timestamp"] >= cutoff].reset_index(drop=True)


def band_frame(minute: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    anchor = int(cfg["vwap"]["anchor_hour_utc"])
    volume_min = int(cfg["vwap"].get("volume_min_unique_per_session", 2))
    banded = compute_vwap_bands(minute, anchor_hour_utc=anchor, volume_min_unique=volume_min)
    banded["ts"] = banded["timestamp"]
    # Session start: label minus the hours the anchor sits before midnight.
    banded["anchor"] = banded["session"] - pd.Timedelta(hours=(24 - anchor) % 24)
    return banded


def add_positions(banded: pd.DataFrame) -> pd.DataFrame:
    frame = banded.dropna(subset=["vwap", "sigma"]).reset_index(drop=True)
    frame["position"] = _band_position(
        frame["close"],
        frame["band1_upper"],
        frame["band3_upper"],
        frame["band1_lower"],
        frame["band3_lower"],
        frame["sigma"],
    )
    frame["date"] = frame["timestamp"].dt.floor("D")
    return frame


def load_regime(symbol: str, cfg: dict[str, Any]) -> pd.DataFrame:
    daily = read_processed(PROCESSED_DIR / f"{symbol}_1d.csv")
    if daily.empty:
        return daily
    labeled = compute_regime(daily, cfg)
    labeled = labeled.dropna(subset=["regime", "atr_percentile", "adx"]).reset_index(drop=True)
    labeled["label"] = labeled["regime"].map(REGIME_DISPLAY)
    labeled = labeled.dropna(subset=["label"]).reset_index(drop=True)
    labeled["atr_pct"] = labeled["atr_percentile"]
    labeled["ts"] = labeled["timestamp"]
    labeled["date"] = labeled["timestamp"].dt.floor("D")
    return labeled


# ---------------------------------------------------------------- selection

def pick_chart_sessions(
    tail: pd.DataFrame, anchor: int, now: pd.Timestamp
) -> tuple[pd.DataFrame, int]:
    """The last few closed sessions whose bars reach the session end."""
    grouped = tail.groupby("session")
    first = grouped["timestamp"].min()
    last = grouped["timestamp"].max()
    shift_hours = (24 - anchor) % 24
    # A session bucket runs label - shift to label + (24 - shift); the bars of
    # a finished session must reach its final ten minutes.
    end_after_label = pd.Timedelta(hours=24 - shift_hours)
    labels = pd.Series(first.index, index=first.index)
    complete = last >= labels + end_after_label - pd.Timedelta(minutes=10)

    current = (now + pd.Timedelta(hours=shift_hours)).floor("D")
    table = pd.DataFrame({"complete": complete})
    table = table[table.index != current]
    chosen = table[table["complete"]].tail(SESSIONS_CHARTED)
    if len(chosen) < 2:
        chosen = table.tail(SESSIONS_CHARTED)
    if chosen.empty:
        return tail, 0
    frame = tail[tail["session"].isin(chosen.index)]
    return frame, int(chosen.index.nunique())


def window_stats(tail: pd.DataFrame) -> dict[str, Any]:
    position = tail["position"]
    classified = tail[position.ne("indeterminate")]
    n = len(classified)
    close, vwap, sigma = classified["close"], classified["vwap"], classified["sigma"]
    inside1 = float(((close >= vwap - sigma) & (close <= vwap + sigma)).mean()) * 100.0 if n else 0.0
    inside3 = float(((close >= vwap - 3 * sigma) & (close <= vwap + 3 * sigma)).mean()) * 100.0 if n else 0.0

    previous = position.shift(1)
    # The engine counts every state change, including the move into and out of
    # a collapsed band at each session reset. Flips ignore those resets.
    records = int(position.ne(previous).sum())
    flips = int(
        (
            position.ne(previous)
            & previous.notna()
            & position.ne("indeterminate")
            & previous.ne("indeterminate")
        ).sum()
    )

    counts = position.value_counts()
    weights = tail.groupby("weight_mode")["session"].nunique()
    return {
        "bars": int(len(tail)),
        "classified": n,
        "inside1": inside1,
        "inside3": inside3,
        "records": records,
        "flips": flips,
        "indeterminate": int(counts.get("indeterminate", 0)),
        "sessions": int(tail["session"].nunique()),
        "dates": int(tail["date"].nunique()),
        "first": tail["timestamp"].min(),
        "last": tail["timestamp"].max(),
        "volume_sessions": int(weights.get("volume", 0)),
        "synthetic_sessions": int(weights.get("synthetic", 0)),
        "dist": {key: int(counts.get(key, 0)) for key, _, _ in POSITIONS},
    }


# ---------------------------------------------------------------- charts

def chart_tape(
    counts: pd.DataFrame,
    ribbon: pd.Series,
    days: pd.DatetimeIndex,
    label: str,
) -> str:
    """One cell per date and band position, plus the regime ribbon above."""
    width = 990
    gutter, right = 120, 16
    x0, x1 = gutter, width - right
    n = len(days)
    step = (x1 - x0) / max(n, 1)
    cell_w = max(step - 0.35, 0.9)
    ribbon_y, ribbon_h = 22, 10
    row_top, row_h, row_gap = 42, 22, 4
    bottom = row_top + len(POSITIONS) * (row_h + row_gap) - row_gap
    height = bottom + 26
    peak = float(counts.to_numpy().max()) if n else 0.0
    peak = peak or 1.0

    parts = []
    for i, day in enumerate(days):
        x = x0 + i * step
        if day.day == 1 and 4 < i < n - 4:
            parts.append(
                f'<text class="axis-strong" x="{x + cell_w / 2:.1f}" y="12" text-anchor="middle">'
                f'{day.strftime("%b")}</text>'
            )
        elif i == 0:
            parts.append(f'<text class="axis-strong" x="{x:.1f}" y="12">{day.strftime("%d %b %y")}</text>')
        elif i == n - 1:
            parts.append(
                f'<text class="axis-strong" x="{x + cell_w:.1f}" y="12" text-anchor="end">'
                f'{day.strftime("%d %b %y")}</text>'
            )

    parts.append(f'<text class="axis" x="{gutter - 10}" y="{ribbon_y + 8}" text-anchor="end">regime</text>')
    for i, day in enumerate(days):
        name = ribbon.iloc[i]
        if not isinstance(name, str):
            continue
        cls = STATE_CLASS.get(name)
        if cls is None:
            continue
        x = x0 + i * step
        parts.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{ribbon_y}" width="{cell_w:.2f}" height="{ribbon_h}">'
            f'<title>{day.strftime("%Y-%m-%d")} &#183; {name}</title></rect>'
        )

    for r, (_, display, _cls) in enumerate(POSITIONS):
        y = row_top + r * (row_h + row_gap)
        parts.append(
            f'<text class="rowlabel" x="{gutter - 10}" y="{y + row_h / 2 + 4:.1f}" text-anchor="end">'
            f"{display}</text>"
        )
        parts.append(f'<rect class="track" x="{x0}" y="{y}" width="{x1 - x0}" height="{row_h}" rx="3"/>')
        if display not in counts.columns:
            continue
        series = counts[display]
        for i, day in enumerate(days):
            value = int(series.iloc[i])
            if value <= 0:
                continue
            opacity = 0.12 + 0.85 * (value / peak) ** 0.5
            x = x0 + i * step
            parts.append(
                f'<rect style="fill: rgba(101, 169, 255, {opacity:.2f})" x="{x:.1f}" y="{y + 1}" '
                f'width="{cell_w:.2f}" height="{row_h - 2}" rx="1">'
                f'<title>{day.strftime("%Y-%m-%d")} &#183; {display} &#183; {value:,} bars</title></rect>'
            )
    return wrap("".join(parts), width, height, label)


def build_xtab(matrix: pd.DataFrame) -> str:
    total = int(matrix.to_numpy().sum())

    def share(value: int) -> str:
        pct = value / total * 100.0 if total else 0.0
        return f" <small>({pct:.1f}%)</small>"

    head = "".join(f"<th>{col}</th>" for col in matrix.columns) + "<th>Total</th>"
    rows = ""
    for _, display, _cls in POSITIONS:
        cells = "".join(
            f"<td>{int(matrix.loc[display, col]):,}{share(int(matrix.loc[display, col]))}</td>"
            for col in matrix.columns
        )
        row_total = int(matrix.loc[display].sum())
        rows += f"<tr><td>{display}</td>{cells}<td>{row_total:,}</td></tr>"
    foot = "".join(f"<td>{int(matrix[col].sum()):,}</td>" for col in matrix.columns)
    foot += f"<td>{total:,}</td>"
    return (
        '<table class="xtab"><thead><tr><th>Band position</th>'
        + head
        + "</tr></thead><tbody>"
        + rows
        + f'</tbody><tfoot><tr><td>All positions</td>{foot}</tr></tfoot></table>'
    )


def panel(eyebrow: str, title: str, tag: str, body: str) -> str:
    return f"""
    <section class="panel">
      <div class="panel-heading">
        <div><p class="eyebrow">{eyebrow}</p><h2>{title}</h2></div>
        <span class="panel-tag">{tag}</span>
      </div>
      {body}
    </section>"""


# ---------------------------------------------------------------- sections

def weight_sentence(symbol: str, stats: dict[str, Any]) -> str:
    volume_sessions = stats["volume_sessions"]
    synthetic_sessions = stats["synthetic_sessions"]
    if volume_sessions and synthetic_sessions:
        return (
            f"{volume_sessions:,} sessions weight reported volume and {synthetic_sessions:,} sessions "
            "fall back to the synthetic activity weight."
        )
    if synthetic_sessions:
        return (
            f"All {synthetic_sessions:,} sessions use the synthetic activity weight, because {symbol} "
            "publishes no volume."
        )
    return f"All {volume_sessions:,} sessions weight reported volume."


def build_section(
    symbol: str,
    cfg: dict[str, Any],
    cutoff: pd.Timestamp | None,
    now: pd.Timestamp,
    window_days: int,
) -> str:
    minute = load_window(symbol, cutoff)
    if minute.empty:
        return panel(
            "NO DATA",
            f"{symbol} holds no minute bars in the window",
            "empty",
            '<div class="prose"><p>data/processed carries no one-minute rows for this symbol inside the '
            "trailing window. Re-run the cleaning step before generating the page again.</p></div>",
        )

    anchor = int(cfg["vwap"]["anchor_hour_utc"])
    tail = add_positions(band_frame(minute, cfg))
    stats = window_stats(tail)
    labeled = load_regime(symbol, cfg)

    # Position distribution over the whole window.
    pos_items = []
    for key, display, cls in POSITIONS:
        value = stats["dist"].get(key, 0)
        pct = value / stats["classified"] * 100.0 if stats["classified"] else 0.0
        pos_items.append((display, cls, pct, f"{pct:.1f}%"))
    pos_scale = max(item[2] for item in pos_items) or 1.0
    pos_chart = chart_bars(
        pos_items, pos_scale, f"Share of classified bars in each band position on {symbol}"
    )

    first_d = stats["first"].strftime("%Y-%m-%d")
    last_d = stats["last"].strftime("%Y-%m-%d")
    summary_body = f"""
      <div class="prose">
        <p>The trailing {window_days} days hold <strong>{stats['bars']:,} one-minute bars</strong> across
        {stats['sessions']} sessions and {stats['dates']} dates with bars, from {first_d} to {last_d} in
        UTC. Every session resets VWAP and both bands at {anchor}:00 UTC, so sigma starts at zero on the
        first bar and widens as bars arrive. {weight_sentence(symbol, stats)}</p>
        <p>The close held inside the 1 sigma band on <strong>{stats['inside1']:.1f}%</strong> of the
        {stats['classified']:,} classified bars, and inside the 3 sigma band on {stats['inside3']:.1f}%.
        The band position changed state {stats['flips']:,} times between classified states. The engine
        writes {stats['records']:,} position records for this window, counting every move into or out of a
        collapsed band as one.</p>
      </div>
      {pos_chart}
      {legend([(display, cls) for _, display, cls in POSITIONS])}
      <p class="caption">Share of classified bars over the trailing year. The
      {stats['indeterminate']:,} bars with collapsed bands sit outside every count here.</p>"""

    # Last sessions on the one-minute chart.
    session_frame, session_count = pick_chart_sessions(tail, anchor, now)
    if session_frame.empty:
        session_frame = tail
        session_count = int(session_frame["session"].nunique())
    vwap_svg = chart_vwap(
        session_frame,
        DECIMALS[symbol],
        f"{symbol} one minute VWAP with one and three sigma bands",
    )
    chart_start = session_frame["ts"].min().strftime("%Y-%m-%d %H:%M")
    chart_end = session_frame["ts"].max().strftime("%Y-%m-%d %H:%M")
    bands_body = f"""
      <div class="prose">
        <p>The chart plots the last {session_count} sessions inside the window, from {chart_start} to
        {chart_end} UTC. Each dashed separator marks the {anchor}:00 UTC reset and carries the date its
        session starts on, so the bands behind it begin collapsed on that date's first bar.</p>
        <p>Every line comes from the function the engine runs:
        <span class="formula">hlc3 = (H + L + C) / 3</span>, cumulative weight since the reset, then VWAP
        and weighted sigma. The chart samples every fifth bar to stay light; the engine computes every
        minute.</p>
      </div>
      {vwap_svg}
      {legend(BAND_LEGEND)}
      <p class="caption">Observation only: no entry, exit, or fill is drawn on this chart.</p>"""

    # Four-state regime channel over the last 260 labeled bars.
    if labeled.empty:
        regime_body = (
            '<div class="prose"><p>No labeled daily bars exist for this symbol, so the regime channel '
            "cannot be drawn.</p></div>"
        )
        regime_tag = "no labels"
    else:
        regime_frame = labeled.tail(REGIME_BARS).reset_index(drop=True)
        regime_svg = chart_regime(
            regime_frame,
            f"{symbol} daily regime labels with ATR percentile and ADX",
            title=f"{symbol} daily close",
            decimals=DECIMALS[symbol],
        )
        n_reg = len(regime_frame)
        dist = regime_frame["label"].value_counts()
        share_up = float(dist.get("uptrend", 0)) / n_reg * 100.0
        share_dn = float(dist.get("downtrend", 0)) / n_reg * 100.0
        share_hi = float(dist.get("high volatility", 0)) / n_reg * 100.0
        share_lo = float(dist.get("low volatility", 0)) / n_reg * 100.0
        reg_start = regime_frame["ts"].min().strftime("%Y-%m-%d")
        regime_body = f"""
      <div class="prose">
        <p>Labels come from the full daily history, then chart across the last {n_reg} bars from
        {reg_start}. Over that stretch the four states split <strong>{share_up:.0f}% uptrend,
        {share_dn:.0f}% downtrend, {share_hi:.0f}% high volatility, and {share_lo:.0f}%
        low volatility</strong>.</p>
        <p>Each bar carries one label, and volatility outranks trend by design: a strong trend inside a
        high ATR percentile still prints high volatility. Every input is trailing, so a label at bar t
        uses only bars at or before t.</p>
      </div>
      {regime_svg}
      {legend(REGIME_LEGEND)}
      <p class="caption">These labels join to the minute window on UTC date for the tape below.</p>"""
        regime_tag = f"{n_reg} bars"

    # Signal tape and the position by regime cross-tab.
    days = pd.date_range(tail["date"].min(), tail["date"].max(), freq="D")
    n_days = len(days)
    counts = pd.crosstab(tail["date"], tail["position"])
    counts = counts.reindex(index=days, columns=[key for key, _, _ in POSITIONS], fill_value=0)
    counts = counts.fillna(0).astype(int)
    counts.columns = [display for _, display, _ in POSITIONS]

    reg_by_date = labeled.drop_duplicates("date").set_index("date")["label"] if not labeled.empty else pd.Series(dtype=object)
    ribbon = reg_by_date.reindex(days)
    ribbon_missing = int(ribbon.isna().sum())
    tape_svg = chart_tape(
        counts, ribbon, days, f"{symbol} band position per date with the daily regime ribbon"
    )
    tape_body = f"""
      <div class="prose">
        <p>One column per UTC date in the window, {n_days} dates in all, and one row per band position of
        the close. Cell opacity grows with the square root of the bar count, so a solid cell means most of
        that day sat in this state. The ribbon above carries the daily regime for the same date, and
        {ribbon_missing} dates hold no daily bar and show no ribbon cell.</p>
        <p>Empty track means no bars that day, which covers weekends and holidays. Hover any cell for its
        exact count.</p>
      </div>
      {tape_svg}
      {legend(REGIME_LEGEND)}
      <p class="caption">Ribbon colors match the daily regime chart above.</p>"""

    position = tail["position"]
    minute_regime = tail["date"].map(reg_by_date)
    eligible = minute_regime.notna() & position.ne("indeterminate")
    joined = int(eligible.sum())
    classified = int(position.ne("indeterminate").sum())
    excluded = classified - joined
    if joined:
        matrix = pd.crosstab(minute_regime[eligible], position[eligible]).T
        matrix = matrix.reindex(
            index=[key for key, _, _ in POSITIONS], columns=REGIME_COLUMNS, fill_value=0
        )
        matrix = matrix.fillna(0).astype(int)
        matrix.index = [display for _, display, _ in POSITIONS]
        xtab = build_xtab(matrix)
    else:
        xtab = '<p class="caption">No classified minute in the window joins to a daily regime label.</p>'
    tab_body = f"""
      <div class="prose">
        <p>Every classified minute joins to the daily bar with the same UTC date and lands in one cell:
        band position on the row, regime on the column. The table holds {joined:,} minutes, while
        {excluded:,} classified minutes sit on dates without a labeled daily bar and stay outside it.</p>
        <p>Read the table as co-occurrence, not as trades. The window carries no entries, exits, or costs,
        and what a 1 sigma touch or a 3 sigma touch should mean is still the open
        <a href="{TICKET_URL}/signal-combination-rule.md">signal combination rule</a> decision.</p>
      </div>
      {xtab}
      <div class="finding-note">Containment and counts describe where price sat, not what a rule would
      have earned. The <a href="{TICKET_URL}/backtest-design.md">backtest design</a> ticket fixes the
      split, the costs, and the report contents before any equity curve exists.</div>"""

    kpis = f"""
    <section class="kpi-grid" aria-label="{symbol} window summary">
      <article class="kpi"><span>Window</span><strong>{window_days} days</strong>
        <small>{first_d} to {last_d}, UTC</small></article>
      <article class="kpi"><span>Minute bars</span><strong>{stats['bars']:,}</strong>
        <small>{stats['sessions']} sessions across {stats['dates']} dates with bars</small></article>
      <article class="kpi"><span>Close inside 1 sigma</span><strong>{stats['inside1']:.1f}%</strong>
        <small>3 sigma holds {stats['inside3']:.1f}% of {stats['classified']:,} bars</small></article>
      <article class="kpi"><span>Position changes</span><strong>{stats['flips']:,}</strong>
        <small>classified crossings, {stats['records']:,} records with resets</small></article>
    </section>"""

    hidden = "" if symbol == SYMBOLS[0] else " hidden"
    return f"""
  <div class="sym" id="sym-{symbol}"{hidden}>
    {kpis}
    {panel("WINDOW", "Band positions over the trailing year", f"{symbol} &#183; {window_days} days", summary_body)}
    {panel("1 MINUTE", "VWAP and bands on the last sessions", f"1m &#183; {session_count} sessions", bands_body)}
    {panel("1 DAY", "Four-state regime channel", regime_tag, regime_body)}
    {panel("SIGNAL TAPE", "Band position across the window", f"{n_days} dates", tape_body)}
    {panel("CROSS TAB", "Band position against daily regime", f"{joined:,} minutes", tab_body)}
  </div>"""


# ---------------------------------------------------------------- page

def build_page(window_days: int, sections: str) -> str:
    notice = (
        "<strong>Observation view.</strong> This page recomputes the analysis engine over the trailing "
        f"{window_days} days of one-minute history and charts where the close sat against VWAP and its "
        "bands, next to the daily regime for the same dates. Containment is not a win rate, and the "
        "window holds no entries, exits, or costs. Regenerate with "
        '<span class="formula">python scripts/generate_prototype_page.py</span>.'
    )
    switcher = (
        '<section class="switcher" aria-label="Symbol"><span class="switch-label">Symbol</span>'
        + "".join(
            f'<a id="pick-{symbol}" class="btn'
            + (" btn-primary" if i == 0 else " btn-ghost")
            + f'" href="prototype.html?symbol={symbol}">{symbol}</a>'
            for i, symbol in enumerate(SYMBOLS)
        )
        + "</section>"
    )
    summary = f"""
    <section class="panel" id="prototype-summary">
      <div class="panel-heading"><div><p class="eyebrow">READ THIS FIRST</p><h2>What the view is and is not</h2></div><span class="panel-tag">Observation mode</span></div>
      <ul class="gate-list">
        <li><span class="gate-dot blue"></span><div><b>Every record observes</b><small>The engine writes action observe on every band-position and regime record. Nothing here enters, exits, or fills a trade.</small></div></li>
        <li><span class="gate-dot blue"></span><div><b>Containment is not a win rate</b><small>The share of bars inside a band measures the dispersion of this sample, not the hit rate of a rule that pays spread and slippage.</small></div></li>
        <li><span class="gate-dot amber"></span><div><b>Entry rules are still open</b><small>The signal combination rule decides what a 1 sigma touch and a 3 sigma touch mean before any equity curve exists.</small></div></li>
        <li><span class="gate-dot blue"></span><div><b>The page regenerates on demand</b><small><span class="formula">python scripts/generate_prototype_page.py</span> rebuilds every number from data/processed over the trailing {window_days} days.</small></div></li>
      </ul>
      <p class="caption">Tickets behind this view:
        <a href="{TICKET_URL}/visual-prototype.md">visual prototype</a>,
        <a href="{TICKET_URL}/signal-combination-rule.md">signal combination rule</a>, and
        <a href="{TICKET_URL}/backtest-design.md">backtest design</a> in
        <a href="{REPO_URL}/blob/main/docs/wayfinder/map.md">the wayfinder map</a>.</p>
    </section>"""

    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="One year of one-minute VWAP bands and four-state daily regime for EURUSD, XAUUSD, and DXY, generated from the cleaned data.">
  <title>Backtest prototype | Quantitative FX Research Dashboard</title>
  <link rel="stylesheet" href="styles.css">
  <link rel="stylesheet" href="findings.css">
  <style>{STYLE}</style>
</head>
<body>
  {NAV}
  <header class="topbar">
    <div>
      <p class="eyebrow">BACKTEST PROTOTYPE</p>
      <h1>One year of one-minute history</h1>
      <p class="subtitle">The trailing {window_days} days of VWAP bands and four-state daily regime for EURUSD, XAUUSD, and DXY, recomputed from data/processed. No trade is taken anywhere in this view.</p>
    </div>
    <span class="status-pill">{window_days} DAYS &#183; 3 SYMBOLS</span>
  </header>

  <main>
    <section class="notice" aria-label="Observation note">
      {notice}
    </section>

    {switcher}

{sections}

    {summary}
  </main>

  <footer>Quantitative FX Research Engine &#183; Generated from data/processed &#183; <a href="index.html">Dashboard</a> &#183; <a href="findings.html">Findings</a> &#183; <a href="{REPO_URL}" target="_blank" rel="noreferrer">View repository</a></footer>
  <script>{SCRIPT}</script>
  <script src="app.js" defer></script>
</body>
</html>
'''


# ---------------------------------------------------------------- run

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate dashboard/prototype.html from data/processed over the trailing window."
    )
    parser.add_argument("--config", default=None, help="Path to config file (default: config/default.yaml)")
    parser.add_argument(
        "--window-days",
        type=int,
        default=None,
        help="Trailing window in days (default: config refresh.window_days, 0 = full history)",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    window_days = args.window_days
    if window_days is None:
        window_days = int(cfg.get("refresh", {}).get("window_days", 365))

    now = pd.Timestamp.now(tz="UTC")
    cutoff = now - pd.Timedelta(days=window_days) if window_days > 0 else None
    shown_days = window_days if window_days > 0 else None

    sections = []
    for symbol in SYMBOLS:
        section = build_section(symbol, cfg, cutoff, now, window_days if window_days > 0 else 0)
        sections.append(section)
    page_days = window_days if window_days > 0 else 365

    html = build_page(page_days, "\n".join(sections))
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({len(html):,} bytes)")
    del shown_days


if __name__ == "__main__":
    main()
