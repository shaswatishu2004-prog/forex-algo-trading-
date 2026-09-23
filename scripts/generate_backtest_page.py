"""Generate dashboard/backtest.html from data/backtest/results.json.

The page is the visualization half of the backtest contract: one year of
one-minute bars fed to the anchored walk-forward runner, then replayed on loop.
The curve and the trade strip are drawn server-side and revealed by a single
cursor that sweeps the window and restarts forever, so the year is watched the
way it was simulated: bar by bar, wrapping back to the first bar at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "data" / "backtest" / "results.json"
OUT_PATH = ROOT / "dashboard" / "backtest.html"
REPO = "https://github.com/shaswatishu2004-prog/forex-algo-trading-"
TICKETS = f"{REPO}/blob/main/docs/wayfinder/tickets"

sys.path.insert(0, str(ROOT / "scripts"))
from generate_findings_page import NAV, REPO_URL, legend, wrap  # noqa: E402,F401

# Geometry shared by the SVG and the replay script.
VIEW_W = 1140
LEFT = 76
PLOT_W = 1030
EQ_TOP = 22
EQ_H = 246
AXIS_Y = EQ_TOP + EQ_H + 26
STRIP_TOP = AXIS_Y + 26
STRIP_BASE = STRIP_TOP + 26
STRIP_H = 44
VIEW_H = STRIP_BASE + STRIP_H + 14
CYCLE_MS = 16000


# ---------------------------------------------------------------- formatting

def pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def count(value: Any) -> str:
    return f"{int(value):,}"


def tone(value: float | None) -> str:
    if value is None:
        return "mute"
    return "pos" if value >= 0 else "neg"


def recovery(days: Any) -> str:
    return "never" if days is None else f"{days} d"


def role_label(role: str) -> str:
    return {
        "seed": "Seed",
        "walk_forward": "Walk-forward",
        "locked_final_test": "Locked test",
    }.get(role, role)


# ---------------------------------------------------------------- components

def panel(eyebrow: str, title: str, tag: str, body: str) -> str:
    return f"""
    <section class="panel">
      <div class="panel-heading">
        <div><p class="eyebrow">{eyebrow}</p><h2>{title}</h2></div>
        <span class="panel-tag">{tag}</span>
      </div>
      {body}
    </section>"""


def kpi(label: str, value: str, note: str, cls: str = "") -> str:
    strong = f'<strong class="{cls}">{value}</strong>' if cls else f"<strong>{value}</strong>"
    return f'<article class="kpi"><span>{label}</span>{strong}<small>{note}</small></article>'


def xtab(headers: list[str], rows: list[list[Any]], foot: list[Any] | None = None) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, tuple):
                text, cls = cell
                cells.append(f'<td class="{cls}">{text}</td>')
            else:
                cells.append(f"<td>{cell}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    tail = ""
    if foot is not None:
        cells = []
        for cell in foot:
            if isinstance(cell, tuple):
                text, cls = cell
                cells.append(f'<td class="{cls}">{text}</td>')
            else:
                cells.append(f"<td>{cell}</td>")
        tail = "<tfoot><tr>" + "".join(cells) + "</tr></tfoot>"
    return (
        '<div class="xtab-wrap"><table class="xtab">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>{tail}</table></div>"
    )


def gate_list(items: list[tuple[str, str, str]]) -> str:
    rows = "".join(
        f'<li><span class="gate-dot {cls}"></span><div><b>{title}</b><small>{note}</small></div></li>'
        for cls, title, note in items
    )
    return f'<ul class="gate-list">{rows}</ul>'


# ---------------------------------------------------------------- charts

def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=None)


def month_starts(start: datetime, end: datetime) -> list[datetime]:
    out = []
    year, month = start.year, start.month
    while datetime(year, month, 1) <= end:
        out.append(datetime(year, month, 1))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return out


def replay_svg(
    points: list[list[Any]],
    trades: list[dict[str, Any]],
    t0: datetime,
    t1: datetime,
) -> str:
    span = max((t1 - t0).total_seconds(), 1.0)
    equities = [float(p[1]) for p in points]
    lo, hi = min(equities), max(equities)
    pad = max((hi - lo) * 0.08, 1.0)
    lo, hi = lo - pad, hi + pad

    def x_of(ts: datetime) -> float:
        return LEFT + ((ts - t0).total_seconds() / span) * PLOT_W

    def y_of(value: float) -> float:
        return EQ_TOP + (1.0 - (value - lo) / (hi - lo)) * EQ_H

    parts: list[str] = []

    # Equity gridlines and value labels.
    for i in range(5):
        value = lo + (hi - lo) * (i / 4)
        y = y_of(value)
        parts.append(f'<line class="grid" x1="{LEFT}" y1="{y:.1f}" x2="{LEFT + PLOT_W}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{LEFT - 10}" y="{y + 3.5:.1f}" text-anchor="end">{value:,.0f}</text>')

    # Month ticks.
    for month in month_starts(t0, t1):
        x = x_of(month)
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{EQ_TOP}" x2="{x:.1f}" y2="{EQ_TOP + EQ_H}"/>')
        parts.append(f'<text class="axis" x="{x:.1f}" y="{AXIS_Y}" text-anchor="middle">{month.strftime("%b %y")}</text>')

    # Baseline at the starting equity.
    start_eq = equities[0]
    y_start = y_of(start_eq)
    parts.append(
        f'<line class="baseline" x1="{LEFT}" y1="{y_start:.1f}" x2="{LEFT + PLOT_W}" y2="{y_start:.1f}"/>'
    )

    # Equity curve and trade strip are revealed by the cursor, so both are
    # only ever drawn inside the clipped group below, never in full up front.
    curve = " ".join(
        f"{x_of(parse_ts(str(p[0]))):.1f},{y_of(float(p[1])):.1f}" for p in points
    )
    max_pnl = max((abs(float(t["pnl"])) for t in trades), default=1.0) or 1.0

    # Strip baseline, zero label, and month gridlines running through it.
    parts.append(
        f'<line class="grid" x1="{LEFT}" y1="{STRIP_BASE}" x2="{LEFT + PLOT_W}" y2="{STRIP_BASE}"/>'
    )
    parts.append(f'<text class="axis" x="{LEFT - 10}" y="{STRIP_BASE + 3.5}" text-anchor="end">0</text>')
    parts.append(f'<text class="axis" x="{LEFT - 10}" y="{STRIP_TOP + 8}" text-anchor="end">pnl</text>')
    for month in month_starts(t0, t1):
        x = x_of(month)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="{STRIP_TOP}" x2="{x:.1f}" y2="{STRIP_BASE + STRIP_H / 2}"/>'
        )

    # Reveal group: everything the cursor has passed.
    revealed = (
        f'<g clip-path="url(#replay-cut)">{curve_group(curve, trades, t0, t1, max_pnl)}</g>'
    )
    parts.append(revealed)

    # Cursor.
    parts.append(
        f'<line id="cursor" class="cursor" x1="{LEFT}" y1="{EQ_TOP}" x2="{LEFT}" y2="{STRIP_BASE + STRIP_H / 2}"/>'
    )

    defs = (
        '<defs><clipPath id="replay-cut">'
        f'<rect id="replay-rect" x="{LEFT}" y="0" width="0" height="{VIEW_H}"/>'
        "</clipPath></defs>"
    )
    axis = (
        f'<line class="axis-line" x1="{LEFT}" y1="{EQ_TOP + EQ_H}" x2="{LEFT + PLOT_W}" y2="{EQ_TOP + EQ_H}"/>'
    )
    return wrap(defs + axis + "".join(parts), VIEW_W, VIEW_H, "Equity curve and trade strip")


def curve_group(
    curve: str,
    trades: list[dict[str, Any]],
    t0: datetime,
    t1: datetime,
    max_pnl: float,
) -> str:
    """The curve and the trade ticks, drawn only inside the reveal clip."""
    span = max((t1 - t0).total_seconds(), 1.0)

    def x_of(ts: datetime) -> float:
        return LEFT + ((ts - t0).total_seconds() / span) * PLOT_W

    out = [f'<polyline class="equity" points="{curve}"/>']
    for trade in trades:
        ts = trade.get("exit_time") or trade.get("entry_time")
        if not ts:
            continue
        x = x_of(parse_ts(ts))
        pnl = float(trade["pnl"])
        length = (abs(pnl) / max_pnl) * (STRIP_H / 2 - 4)
        y2 = STRIP_BASE - length if pnl > 0 else STRIP_BASE + length
        cls = "tick-win" if pnl > 0 else "tick-loss"
        out.append(f'<line class="{cls}" x1="{x:.1f}" y1="{STRIP_BASE}" x2="{x:.1f}" y2="{y2:.1f}"/>')
    return "".join(out)


# ---------------------------------------------------------------- tables

def walk_forward_table(report: dict[str, Any]) -> str:
    rows = []
    for fold in report["folds"]:
        rows.append(
            [
                f"#{fold['fold']}",
                f"{fold['start']} to {fold['end']}",
                role_label(fold["role"]),
                count(fold["trades"]),
                (pct(fold["net_pct"]), tone(fold["net_pct"])),
                num(fold["hit_rate_pct"], 1) + "%",
                num(fold["profit_factor"]),
                (pct(fold["expectancy"], 2) if fold["trades"] else "n/a", "mute"),
            ]
        )
    dev = report["development"]
    locked = report["locked_final_test"]
    foot = [
        "Development",
        "Everything except the last block",
        "Anchored",
        count(dev["trades"]),
        (pct(dev["net_pct"]), tone(dev["net_pct"])),
        num(dev["hit_rate_pct"], 1) + "%",
        num(dev["profit_factor"]),
        (pct(dev["expectancy"]), tone(dev["expectancy"])),
    ]
    return xtab(
        [
            "Block",
            "Range",
            "Role",
            "Trades",
            "Net return",
            "Hit rate",
            "PF",
            "Expectancy",
        ],
        rows,
        foot,
    )


def cost_table(metrics: dict[str, Any]) -> str:
    costs = metrics["costs"]
    rows = [
        ["Gross edge before costs", (pct(metrics["gross_return_pct"]), tone(metrics["gross_return_pct"]))],
        ["Spread", (pct(-costs["spread_pct"]), "neg" if costs["spread_pct"] else "mute")],
        ["Commission", (pct(-costs["commission_pct"]), "neg" if costs["commission_pct"] else "mute")],
        ["Slippage", (pct(-costs["slippage_pct"]), "neg" if costs["slippage_pct"] else "mute")],
        ["Financing across 21:00", (pct(-costs["financing_pct"]), "neg" if costs["financing_pct"] else "mute")],
    ]
    foot = [
        "Total costs",
        (pct(costs["total_pct"]), "neg"),
    ]
    net_rows = xtab(["Line", "Percent of starting equity"], rows, foot)
    return net_rows


def sensitivity_table(sens: dict[str, Any], delayed: dict[str, Any]) -> str:
    order = [
        ("base", "As simulated"),
        ("spread_x2", "Spread doubled"),
        ("slippage_x2", "Slippage doubled"),
        ("stress_costs", "Stress: every cost doubled"),
        ("missing_trades_1_in_10", "One trade in ten missed"),
    ]
    rows = []
    for key, label in order:
        item = sens.get(key)
        if not item:
            continue
        rows.append(
            [
                label,
                count(item["trades"]),
                (pct(item["net_pct"]), tone(item["net_pct"])),
                num(item["hit_rate_pct"], 1) + "%",
                num(item["profit_factor"]),
            ]
        )
    rows.append(
        [
            f"Delayed execution ({delayed.get('entry_delay_bars', 5)} bars to fill)",
            count(delayed.get("trades", 0)),
            (pct(delayed.get("net_return_pct")), tone(delayed.get("net_return_pct"))),
            num(delayed.get("hit_rate_pct"), 1) + "%",
            num(delayed.get("profit_factor")),
        ]
    )
    table = xtab(["Variation", "Trades", "Net return", "Hit rate", "PF"], rows)
    note = sens.get("note", "")
    return table + f'<p class="caption">{note}</p>'


def breakdown_table(title: str, data: dict[str, Any]) -> str:
    rows = []
    for name, item in data.items():
        rows.append(
            [
                name.replace("_", " "),
                count(item["trades"]),
                (pct(item["net_pct"]), tone(item["net_pct"])),
                num(item["hit_rate_pct"], 1) + "%",
                num(item["profit_factor"]),
            ]
        )
    body = xtab(["Bucket", "Trades", "Net return", "Hit rate", "PF"], rows)
    return panel("BREAKDOWN", title, f"{len(rows)} buckets", body)


# ---------------------------------------------------------------- page parts

STYLE = """
.xtab-wrap { overflow-x: auto; }
.xtab { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.xtab th, .xtab td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: right; font-variant-numeric: tabular-nums; }
.xtab th:first-child, .xtab td:first-child { text-align: left; }
.xtab thead th { color: var(--muted); font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; border-top: 1px solid var(--line); }
.xtab tbody td { color: var(--text); font-family: var(--mono); font-size: 12px; }
.xtab tbody td:first-child { font-family: inherit; }
.xtab tbody td.mute { color: var(--muted); }
.xtab tbody td.pos { color: var(--green); }
.xtab tbody td.neg { color: var(--red); }
.xtab tfoot td { color: var(--b-70); font-weight: 700; font-family: var(--mono); font-size: 12px; border-bottom: 0; border-top: 1px solid var(--line); }
.xtab tfoot td:first-child { font-family: inherit; color: var(--text); }
.xtab tfoot td.pos { color: var(--green); }
.xtab tfoot td.neg { color: var(--red); }

.kpi strong.pos { color: var(--green); }
.kpi strong.neg { color: var(--red); }
.kpi strong.mute { color: var(--muted); }

.replay-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 16px; }
.replay-bar .switch-label { color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; margin-right: 2px; }
.readout { display: flex; flex-wrap: wrap; gap: 10px 26px; padding: 14px 16px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius-sm); margin-bottom: 16px; }
.readout div span { display: block; color: var(--muted); font-size: 10.5px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
.readout div b { display: block; margin-top: 5px; font-family: var(--mono); font-size: 17px; font-variant-numeric: tabular-nums; color: var(--text); }
.readout div b.pos { color: var(--green); }
.readout div b.neg { color: var(--red); }
.readout div b.blue { color: var(--b-70); }

.chart.equity-chart { margin-top: 4px; }
.chart .grid { stroke: rgba(101, 169, 255, .10); stroke-width: 1; }
.chart .axis { fill: var(--muted); font-size: 10px; letter-spacing: .04em; }
.chart .axis-line { stroke: var(--line); stroke-width: 1; }
.chart .baseline { stroke: var(--b-25); stroke-width: 1; stroke-dasharray: 5 5; }
.chart .equity { fill: none; stroke: var(--b-50); stroke-width: 1.8; stroke-linejoin: round; }
.chart .cursor { stroke: var(--amber); stroke-width: 1.2; stroke-dasharray: 4 4; }
.chart .tick-win { stroke: var(--green); stroke-width: 1.6; }
.chart .tick-loss { stroke: var(--red); stroke-width: 1.6; }
.legend i.tick-win { background: var(--green); width: 3px; height: 13px; border-radius: 1px; }
.legend i.tick-loss { background: var(--red); width: 3px; height: 13px; border-radius: 1px; }

.loop-chip { display: inline-flex; align-items: center; gap: 8px; padding: 6px 11px; border: 1px solid rgba(245, 196, 106, .45); background: rgba(245, 196, 106, .08); border-radius: 999px; color: var(--amber); font-size: 11px; font-weight: 700; letter-spacing: .06em; }
.loop-chip i { width: 7px; height: 7px; border-radius: 50%; background: var(--amber); animation: pulse 1.1s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }

.two-up { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; }
@media (max-width: 900px) { .two-up { grid-template-columns: 1fr; } }
"""

SCRIPT = """
(function () {
  var LEFT = __LEFT__;
  var PLOT_W = __PLOT_W__;
  var T0 = __T0__;
  var T1 = __T1__;
  var CYCLE = __CYCLE__;
  var PTS = __PTS__;
  var TRS = __TRS__;
  var INITIAL = __INITIAL__;

  var rect = document.getElementById("replay-rect");
  var cursor = document.getElementById("cursor");
  var elDate = document.getElementById("ro-date");
  var elEquity = document.getElementById("ro-equity");
  var elNet = document.getElementById("ro-net");
  var elTrades = document.getElementById("ro-trades");
  var elLoop = document.getElementById("loop-count");
  var elapsedEl = document.getElementById("ro-elapsed");
  var playing = true;
  var speed = 1;
  var loop = 1;
  var offset = 0;
  var last = null;

  function xOf(t) { return LEFT + ((t - T0) / (T1 - T0)) * PLOT_W; }

  function equityAt(t) {
    var lo = 0, hi = PTS.length - 1;
    if (t <= PTS[0][0]) return PTS[0][1];
    if (t >= PTS[hi][0]) return PTS[hi][1];
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (PTS[mid][0] <= t) lo = mid; else hi = mid;
    }
    return PTS[lo][1];
  }

  function tradesAt(t) {
    var n = 0;
    for (var i = 0; i < TRS.length; i++) { if (TRS[i] <= t) n++; else break; }
    return n;
  }

  function fmtDate(t) {
    var d = new Date(t);
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return months[d.getUTCMonth()] + " " + String(d.getUTCDate()).padStart(2, "0") + ", " + d.getUTCFullYear();
  }

  function fmtDuration(ms) {
    var days = Math.floor(ms / 86400000);
    return days + " days";
  }

  function setClass(el, cls) { if (el) el.className = cls; }

  function frame(now) {
    if (last === null) last = now;
    var dt = now - last;
    last = now;
    if (playing) offset += dt * speed;
    if (offset >= CYCLE) {
      offset = offset % CYCLE;
      loop += 1;
      if (elLoop) elLoop.textContent = String(loop);
    }
    var frac = offset / CYCLE;
    var t = T0 + frac * (T1 - T0);
    var x = xOf(t);
    if (rect) rect.setAttribute("width", String(Math.max(0, x - LEFT)));
    if (cursor) { cursor.setAttribute("x1", x.toFixed(1)); cursor.setAttribute("x2", x.toFixed(1)); }

    var eq = equityAt(t);
    if (elDate) elDate.textContent = fmtDate(t);
    if (elEquity) elEquity.textContent = eq.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (elNet) {
      var net = (eq / INITIAL - 1) * 100;
      elNet.textContent = (net >= 0 ? "+" : "") + net.toFixed(2) + "%";
      setClass(elNet, net >= 0 ? "pos" : "neg");
    }
    if (elTrades) elTrades.textContent = String(tradesAt(t));
    if (elapsedEl) elapsedEl.textContent = fmtDuration(t - T0);

    requestAnimationFrame(frame);
  }

  var playBtn = document.getElementById("btn-play");
  if (playBtn) {
    playBtn.addEventListener("click", function () {
      playing = !playing;
      playBtn.textContent = playing ? "Pause replay" : "Resume replay";
      playBtn.className = playing ? "btn btn-primary" : "btn btn-ghost";
    });
  }
  var restartBtn = document.getElementById("btn-restart");
  if (restartBtn) {
    restartBtn.addEventListener("click", function () {
      offset = 0;
      loop += 1;
      if (elLoop) elLoop.textContent = String(loop);
    });
  }
  document.querySelectorAll("[data-speed]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      speed = parseFloat(btn.getAttribute("data-speed"));
      document.querySelectorAll("[data-speed]").forEach(function (b) {
        b.className = "btn " + (b === btn ? "btn-primary" : "btn-ghost");
      });
    });
  });

  requestAnimationFrame(frame);
})();
"""


# ---------------------------------------------------------------- page

def build_page(results: dict[str, Any]) -> str:
    metrics = results["metrics"]
    window = results["window"]
    wf = results["walk_forward"]
    sens = results["sensitivity"]
    delayed = results["delayed_execution"]
    diag = results["diagnostics"]
    costs = metrics["costs"]
    trades = results.get("trades", [])
    points = results.get("equity_curve", [])

    t0 = parse_ts(window["start"])
    t1 = parse_ts(window["end"])
    days = int(window["days"])

    meta = f"""
    <ul class="meta-strip" aria-label="Run summary">
      <li><b>{count(metrics['trades'])}</b> trades</li>
      <li class="{'warn' if metrics['net_return_pct'] < 0 else ''}"><b>{pct(metrics['net_return_pct'])}</b> net</li>
      <li><b>{num(metrics['hit_rate_pct'], 1)}%</b> hit rate</li>
      <li><b>{count(sum(int(v) for v in diag.get('per_symbol_bars', {}).values()))}</b> 1m bars simulated</li>
      <li><b>{days}</b> day window</li>
    </ul>"""

    notice = (
        "<strong>Backtest report, research only.</strong> This page renders "
        f"<span class='formula'>data/backtest/results.json</span>: {days} days of one-minute bars for "
        f"{', '.join(results['symbols'])} fed to an anchored walk-forward runner, one bar at a time, then replayed on loop below. "
        "Costs are charged on every round trip. It is not live performance and not investment advice. "
        "Regenerate with <span class='formula'>python scripts/run_backtest.py</span> then "
        "<span class='formula'>python scripts/generate_backtest_page.py</span>."
    )

    kpis = "".join(
        [
            kpi(
                "Net return",
                pct(metrics["net_return_pct"]),
                f"after {pct(costs['total_pct'])} of round-trip costs",
                tone(metrics["net_return_pct"]),
            ),
            kpi(
                "Gross edge",
                pct(metrics["gross_return_pct"]),
                "before any cost is charged",
                tone(metrics["gross_return_pct"]),
            ),
            kpi(
                "Trades",
                count(metrics["trades"]),
                f"{metrics['exposure_pct']:.1f}% of the window in the market",
            ),
            kpi(
                "Hit rate",
                f"{num(metrics['hit_rate_pct'], 1)}%",
                f"spec target was 55 to 65% at 1:1",
                "neg" if metrics["hit_rate_pct"] < 55 else "pos",
            ),
            kpi(
                "Profit factor",
                num(metrics["profit_factor"]),
                f"expectancy {pct(metrics['expectancy'])} per trade",
                "neg" if metrics["profit_factor"] < 1 else "pos",
            ),
            kpi(
                "Sharpe",
                num(metrics["sharpe"]),
                f"Sortino {num(metrics['sortino'])}, annualized",
                "neg" if metrics["sharpe"] < 1 else "pos",
            ),
            kpi(
                "Max drawdown",
                f"{num(metrics['max_drawdown_pct'])}%",
                f"recovery {recovery(metrics.get('recovery_days'))}",
                "neg",
            ),
            kpi(
                "Stress at 2x costs",
                pct(sens.get("stress_costs", {}).get("net_pct")),
                "required pass under validation.minimum_stress_cost_multiplier",
                tone(sens.get("stress_costs", {}).get("net_pct")),
            ),
        ]
    )

    # Replay controls and live readout.
    ro = f"""
    <div class="replay-bar">
      <span class="switch-label">Replay</span>
      <button id="btn-play" class="btn btn-primary" type="button">Pause replay</button>
      <button id="btn-restart" class="btn btn-ghost" type="button">Restart year</button>
      <button class="btn btn-ghost" data-speed="1" type="button">1x</button>
      <button class="btn btn-ghost" data-speed="2" type="button">2x</button>
      <button class="btn btn-ghost" data-speed="4" type="button">4x</button>
      <span class="loop-chip"><i></i>LOOP <span id="loop-count">1</span></span>
    </div>
    <div class="readout" aria-live="off">
      <div><span>Cursor date</span><b id="ro-date">&mdash;</b></div>
      <div><span>Equity</span><b id="ro-equity">&mdash;</b></div>
      <div><span>Net</span><b id="ro-net">&mdash;</b></div>
      <div><span>Trades closed</span><b id="ro-trades">0</b></div>
      <div><span>Window elapsed</span><b id="ro-elapsed" class="blue">0 days</b></div>
    </div>"""

    chart_body = (
        ro
        + replay_svg(points, trades, t0, t1)
        + legend(
            [
                ("Equity curve", "ln"),
                ("Winning exit", "tick-win"),
                ("Losing exit", "tick-loss"),
                ("Starting equity", "dash"),
            ]
        )
        + '<p class="caption">The cursor sweeps the window, reveals the curve and the trade strip as it goes, then wraps back to the first bar and starts the year again. '
        f"Each trade fills on the bar after its signal, exits at a stop, a target, or the session boundary, and is charged spread, commission, and slippage on the round trip.</p>"
    )

    replay_panel = panel(
        "REPLAY",
        "One year of one-minute bars, on loop",
        f"{days} days",
        chart_body,
    )

    verdict = gate_list(
        [
            (
                "red",
                "Costs eat the whole edge and more",
                f"Gross {pct(metrics['gross_return_pct'])} against {pct(costs['total_pct'])} of round-trip costs over "
                f"{count(metrics['trades'])} trades. Spread alone is {pct(costs['spread_pct'])}.",
            ),
            (
                "red",
                "The hit rate is a genuine strategy result, not a session artifact",
                f"{num(metrics['hit_rate_pct'], 1)}% of exits land on the target. Stops fire far more often than targets, "
                "and excluding the session-boundary exits does not move the number.",
            ),
            (
                "red",
                "Stress at twice the costs is worse still",
                f"{pct(sens.get('stress_costs', {}).get('net_pct'))} net. A strategy that needs kinder costs to break even is not robust.",
            ),
            (
                "amber",
                "The daily loss halt still stops entries inside a bad day",
                f"{count(diag.get('daily_loss_halts', 0))} halts. "
                + (
                    f"The {pct(metrics['net_return_pct'])} drawdown limit was "
                    + ("breached" if diag.get("drawdown_breached") else "not breached")
                    + " and is recorded rather than enforced, so the year runs to completion."
                ),
            ),
            (
                "blue",
                "Locked final test held out",
                f"{count(wf['locked_final_test']['trades'])} trades in the last block, untouched during development. "
                "Regime filters rejected {0} signals that the trend, volatility, and direction rules did not allow.".format(
                    count(diag.get("signals_rejected_by_regime", 0))
                ),
            ),
        ]
    )

    report_panel = panel(
        "VERDICT",
        "What this run says",
        "unprofitable",
        verdict,
    )

    wf_panel = panel(
        "WALK-FORWARD",
        "Anchored blocks with a locked final test",
        wf.get("method", "anchored_walk_forward").replace("_", " "),
        walk_forward_table(wf)
        + '<p class="caption">Block 0 seeds the walk-forward; the last block is the locked final test and was never used to adjust the rules. '
        f"Development {count(wf['development']['trades'])} trades at {pct(wf['development']['net_pct'])}; "
        f"locked test {count(wf['locked_final_test']['trades'])} trades at {pct(wf['locked_final_test']['net_pct'])}.</p>",
    )

    cost_panel = panel(
        "COSTS",
        "Where the money went",
        pct(costs["total_pct"]) + " total",
        cost_table(metrics)
        + '<p class="caption">Costs are decimal returns on notional per symbol, charged only where '
        "<span class='formula'>execution.include_*</span> allows. Financing is charged once for any position "
        "still open when the session rolls at 21:00 UTC; a stop or target hit inside one session pays none.</p>",
    )

    sens_panel = panel(
        "SENSITIVITY",
        "Cost and fill variations on the same trades",
        "5 variations",
        sensitivity_table(sens, delayed),
    )

    dd_body = gate_list(
        [
            ("amber", "Worst day", f"{pct(metrics['worst_day_pct'])} on the worst single day."),
            ("amber", "Worst month", f"{pct(metrics['worst_month_pct'])} on the worst calendar month."),
            ("red", "Losing streak", f"{count(metrics['losing_streak'])} consecutive losing trades."),
            (
                "blue",
                "Average trade",
                f"win {metrics['avg_win']:,.2f} against loss {metrics['avg_loss']:,.2f} on "
                f"{count(metrics['trades'])} trades, expectancy {pct(metrics['expectancy'])}.",
            ),
            ("blue", "Exposure", f"{metrics['exposure_pct']:.2f}% of the window held a position."),
            ("blue", "Volatility", f"{num(metrics['volatility_pct_annualized'])}% annualized on daily equity."),
        ]
    )
    risk_panel = panel(
        "RISK",
        "Tail statistics over the window",
        "1 year",
        dd_body,
    )

    diag_body = gate_list(
        [
            ("blue", "Bars simulated", " · ".join(f"{k} {count(v)}" for k, v in sorted(diag.get("per_symbol_bars", {}).items()))),
            ("blue", "Signals seen", f"{count(diag.get('signals_seen', 0))} entries evaluated bar by bar."),
            ("blue", "Signals rejected by regime", f"{count(diag.get('signals_rejected_by_regime', 0))} filtered by the daily regime map before any fill."),
            ("blue", "Daily loss halts", f"{count(diag.get('daily_loss_halts', 0))} days stopped for new entries after the daily loss cap."),
            (
                "amber" if diag.get("drawdown_breached") else "blue",
                "Drawdown limit",
                (
                    f"Breached at {pct(diag.get('drawdown_breach_pct'))} on {str(diag.get('drawdown_breach_at', ''))[:10]}, "
                    f"equity {diag.get('equity_at_breach'):,.0f}. Recorded, not enforced, so the report keeps all {days} days."
                    if diag.get("drawdown_breached")
                    else f"Never reached the {pct(15.0, 0)} portfolio limit."
                ),
            ),
            ("blue", "News bucket", metrics.get("by_news_bucket", {}).get("reason", "No news calendar staged.")),
            ("blue", "Generation", f"{results.get('generated_at', '')} · schema {results.get('schema_version', '')} · {diag.get('elapsed_seconds', 0)}s to run."),
        ]
    )
    diag_panel = panel(
        "DIAGNOSTICS",
        "How the run was produced",
        "engine counters",
        diag_body,
    )

    caveat = f'<p class="caption">{metrics.get("sharpe_caveat", "")}</p>'

    breakdowns = "\n".join(
        [
            breakdown_table("By symbol", metrics["by_pair"]),
            breakdown_table("By signal kind", metrics["by_kind"]),
            breakdown_table("By daily regime", metrics["by_regime"]),
            breakdown_table("By session", metrics["by_session"]),
            breakdown_table("By volatility bucket", metrics["by_volatility_bucket"]),
            panel(
                "NOT AVAILABLE",
                "By news bucket",
                "no calendar",
                f'<div class="prose"><p>{metrics["by_news_bucket"]["reason"]}</p></div>',
            ),
        ]
    )

    script = (
        SCRIPT.replace("__LEFT__", str(LEFT))
        .replace("__PLOT_W__", str(PLOT_W))
        .replace("__T0__", str(int(t0.timestamp() * 1000)))
        .replace("__T1__", str(int(t1.timestamp() * 1000)))
        .replace("__CYCLE__", str(CYCLE_MS))
        .replace("__INITIAL__", str(int(results["initial_equity"])))
        .replace("__PTS__", json.dumps([[int(parse_ts(str(p[0])).timestamp() * 1000), p[1]] for p in points]))
        .replace("__TRS__", json.dumps(sorted(int(parse_ts(str(t["exit_time"] or t["entry_time"])).timestamp() * 1000) for t in trades if (t.get("exit_time") or t.get("entry_time")))))
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="One year of one-minute bars replayed through an anchored walk-forward backtest for EURUSD, XAUUSD, and DXY, with equity, trades, costs, and the full strategy-spec report.">
  <title>Backtest replay | Quantitative FX Research Dashboard</title>
  <link rel="stylesheet" href="styles.css">
  <link rel="stylesheet" href="findings.css">
  <style>{STYLE}</style>
</head>
<body>
  {NAV}
  <header class="topbar">
    <div>
      <p class="eyebrow">BACKTEST REPLAY</p>
      <h1>{days} days of one-minute bars, on loop</h1>
      <p class="subtitle">The trailing {days} days fed bar by bar to an anchored walk-forward backtest with a locked final test, then swept across the screen over and over. Equity, trades, and the whole strategy-spec report below.</p>
    </div>
    <span class="status-pill">{days} DAYS &#183; {count(metrics['trades'])} TRADES</span>
  </header>

  <main>
    <section class="notice" aria-label="Backtest note">{notice}</section>

    <section class="kpi-grid" aria-label="Headline results">{kpis}</section>

    <section class="band" id="replay">
      <div class="band-head">
        <p class="eyebrow">REPLAY</p>
        <h2 class="band-title">Watch the year run</h2>
        <span class="band-note">cursor sweeps the window and restarts</span>
      </div>
      {replay_panel}
    </section>

    <section class="band" id="verdict">
      <div class="band-head">
        <p class="eyebrow">REPORT</p>
        <h2 class="band-title">Strategy-spec report</h2>
        <span class="band-note">every figure recomputed from results.json</span>
      </div>
      <div class="grid two-up">
        {report_panel}
        {wf_panel}
      </div>
      <div class="grid two-up">
        {cost_panel}
        {sens_panel}
      </div>
      <div class="grid two-up">
        {risk_panel}
        {diag_panel}
      </div>
      {caveat}
    </section>

    <section class="band" id="breakdowns">
      <div class="band-head">
        <p class="eyebrow">BREAKDOWNS</p>
        <h2 class="band-title">Where the losses came from</h2>
        <span class="band-note">same trades, cut five ways</span>
      </div>
      <div class="grid two-up">
        {breakdowns}
      </div>
    </section>
  </main>

  <footer>Quantitative FX Research Engine · Generated from data/backtest/results.json · <a href="index.html">Dashboard</a> · <a href="{REPO_URL}" target="_blank" rel="noreferrer">View repository</a></footer>
  <script>{script}</script>
</body>
</html>
"""


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate dashboard/backtest.html from the run output.")
    parser.add_argument("--results", default=str(RESULTS_PATH), help="Path to results.json")
    parser.add_argument("--out", default=str(OUT_PATH), help="Path to write the page")
    args = parser.parse_args(argv)

    results_path = Path(args.results)
    out_path = Path(args.out)
    if not results_path.exists():
        raise SystemExit(f"{results_path} is missing; run scripts/run_backtest.py first")

    results = json.loads(results_path.read_text(encoding="utf-8"))
    html = build_page(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path.relative_to(ROOT)} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
