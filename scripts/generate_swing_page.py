"""Generate dashboard/swing.html from data/backtest/swing_results.json.

The swing page reuses the backtest generator's tables, replay machinery and
styles, and adds the pre-registered criteria panel plus the fold bars. Every
figure is read back out of the committed JSON, so the page cannot drift from
the run it reports.
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "data" / "backtest" / "swing_results.json"
OUT_PATH = ROOT / "dashboard" / "swing.html"

sys.path.insert(0, str(ROOT / "scripts"))

from generate_backtest_page import (  # noqa: E402
    CYCLE_MS,
    LEFT,
    PLOT_W,
    SCRIPT,
    STYLE,
    VIEW_W,
    breakdown_table,
    cost_table,
    count,
    gate_list,
    kpi,
    num,
    panel,
    parse_ts,
    pct,
    replay_svg,
    role_label,
    tone,
    xtab,
)
from generate_findings_page import NAV, REPO_URL, legend, wrap  # noqa: E402

EXIT_LABELS = {
    "trail": "on the trail",
    "hold_cap": "at the hold cap",
    "regime_flip": "on a regime flip",
    "end_of_window": "at the window edge",
}

CRITERION_TITLES = {
    "gross_over_3x_costs": "Gross over three times costs",
    "profit_factor_over_1_3": "Profit factor over 1.30",
    "positive_skew": "Positive skew",
    "all_folds_positive": "Every fold nets positive",
    "locked_green_at_stress": "Locked test green at 2x costs",
}


def criterion_note(
    criterion: dict[str, Any],
    metrics: dict[str, Any],
    neg_folds: int,
    total_folds: int,
) -> str:
    """One plain-English sentence per pre-registered criterion."""
    name = criterion["name"]
    value = float(criterion["value"])
    threshold = float(criterion["threshold"])
    if name == "gross_over_3x_costs":
        return (
            f"Gross {pct(value)} against the {pct(threshold)} required — three times the "
            f"{pct(metrics['costs']['total_pct'])} of round-trip costs."
        )
    if name == "profit_factor_over_1_3":
        return f"Profit factor {num(value)} against a floor of {num(threshold, 1)}."
    if name == "positive_skew":
        return f"Trade pnl skew {num(value)} against a floor of {num(threshold)}."
    if name == "all_folds_positive":
        return (
            f"Worst fold {pct(value)} against a floor of {pct(threshold)}; "
            f"{neg_folds} of {total_folds} folds net negative."
        )
    if name == "locked_green_at_stress":
        return (
            f"Locked block {pct(value)} with spread, commission, slippage and financing all "
            f"doubled, against a floor of {pct(threshold)}."
        )
    return f"Value {value:g} against a threshold of {threshold:g}."


def fold_table(report: dict[str, Any]) -> str:
    """Walk-forward folds with the n/a rule for a fold that has no loser."""
    rows = []
    for fold in report["folds"]:
        profit_factor = fold.get("profit_factor")
        pf_cell = (
            "n/a"
            if float(fold["avg_loss"]) >= 0 or profit_factor is None
            else num(profit_factor)
        )
        rows.append(
            [
                f"#{fold['fold']}",
                f"{fold['start']} to {fold['end']}",
                role_label(fold["role"]),
                count(fold["trades"]),
                (pct(fold["net_pct"]), tone(fold["net_pct"])),
                num(fold["hit_rate_pct"], 1) + "%",
                pf_cell,
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
    ]
    return xtab(
        ["Block", "Range", "Role", "Trades", "Net return", "Hit rate", "PF"],
        rows,
        foot,
    )


def fold_bars_svg(
    folds: list[dict[str, Any]],
    stress: dict[int, dict[str, Any]],
) -> str:
    """One bar per fold, with an amber mark where the fold lands at 2x costs."""
    left, plot_w = 132, 970
    row_h, gap, top = 30, 14, 24
    axis_y = top + len(folds) * (row_h + gap) + 4
    height = axis_y + 34
    base = [float(f["net_pct"]) for f in folds]
    marked = [
        float(stress.get(f["fold"], {}).get("net_pct_stressed", f["net_pct"]))
        for f in folds
    ]
    lo = min([0.0] + base + marked)
    hi = max([0.0] + base + marked)
    pad = max((hi - lo) * 0.14, 0.05)
    lo, hi = lo - pad, hi + pad

    def x_of(value: float) -> float:
        return left + (value - lo) / (hi - lo) * plot_w

    parts: list[str] = []
    step = 0.2 if (hi - lo) > 0.9 else 0.1
    tick = math.ceil(lo / step) * step
    while tick <= hi:
        x = x_of(tick)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" y2="{axis_y}"/>'
        )
        label = "0%" if abs(tick) < 1e-9 else f"{tick:+.1f}%"
        parts.append(
            f'<text class="axis" x="{x:.1f}" y="{axis_y + 18}" text-anchor="middle">{label}</text>'
        )
        tick += step
    x0 = x_of(0.0)
    parts.append(
        f'<line class="baseline" x1="{x0:.1f}" y1="{top - 10}" x2="{x0:.1f}" y2="{axis_y}"/>'
    )

    for i, fold in enumerate(folds):
        y = top + i * (row_h + gap)
        value = float(fold["net_pct"])
        marked_value = float(
            stress.get(fold["fold"], {}).get("net_pct_stressed", value)
        )
        row_label = f"Fold {fold['fold']}"
        if fold["role"] == "seed":
            row_label += " · seed"
        elif fold["role"] == "locked_final_test":
            row_label += " · locked"
        mid = y + row_h / 2
        parts.append(
            f'<text class="axis" x="{left - 12}" y="{mid + 3.5:.1f}" text-anchor="end">{row_label}</text>'
        )
        x_bar = x_of(value)
        x_a, x_b = sorted((x0, x_bar))
        cls = "pos" if value >= 0 else "neg"
        parts.append(
            f'<rect class="fold-bar {cls}" x="{x_a:.1f}" y="{y}" width="{max(x_b - x_a, 1.5):.1f}" '
            f'height="{row_h}" rx="3"/>'
        )
        x_mark = x_of(marked_value)
        parts.append(
            f'<rect class="fold-mark" x="{x_mark - 1.5:.1f}" y="{y - 4}" width="3" '
            f'height="{row_h + 8}" rx="1.5"/>'
        )
        end_x = x_bar + (7 if value >= 0 else -7)
        anchor = "start" if value >= 0 else "end"
        parts.append(
            f'<text class="axis" x="{end_x:.1f}" y="{mid + 3.5:.1f}" text-anchor="{anchor}">{value:+.2f}%</text>'
        )
    parts.append(
        f'<line class="axis-line" x1="{left}" y1="{axis_y}" x2="{left + plot_w}" y2="{axis_y}"/>'
    )
    return wrap(
        "".join(parts), VIEW_W, height, "Fold net returns at base and stressed costs"
    )


SWING_STYLE = """
.chart .fold-bar { fill-opacity: .8; }
.chart .fold-bar.pos { fill: var(--green); }
.chart .fold-bar.neg { fill: var(--red); }
.chart .fold-mark { fill: var(--amber); }
.legend i.fold-pos { background: var(--green); width: 12px; height: 8px; border-radius: 2px; }
.legend i.fold-neg { background: var(--red); width: 12px; height: 8px; border-radius: 2px; }
.legend i.fold-mark { background: var(--amber); width: 3px; height: 13px; border-radius: 1px; }
"""

# ---------------------------------------------------------------- page


def build_page(results: dict[str, Any]) -> str:
    metrics = results["metrics"]
    window = results["window"]
    wf = results["walk_forward"]
    guard = results["guardrails"]
    sens = results["sensitivity"]
    delayed = results["delayed_execution"]
    financing = results["financing_sensitivity"]
    diag = results["diagnostics"]
    costs = metrics["costs"]
    trades = results.get("trades", [])
    points = results.get("equity_curve", [])

    t0 = parse_ts(window["start"])
    t1 = parse_ts(window["end"])
    days = int(window["days"])
    n_trades = int(metrics["trades"])
    symbols = ", ".join(results["symbols"])

    bars = diag.get("per_symbol_bars", {})
    total_bars = sum(int(v) for v in bars.values())
    criteria = guard["criteria"]
    passed = sum(1 for c in criteria if c["passed"])
    total_criteria = len(criteria)
    folds = wf["folds"]
    neg_folds = sum(1 for f in folds if f["net_pct"] < 0)
    stress = guard["all_in_stress"]
    stress_by_fold = {int(s["fold"]): s for s in guard.get("fold_stress", [])}
    exits: dict[str, int] = {}
    for trade in trades:
        exits[trade["exit_reason"]] = exits.get(trade["exit_reason"], 0) + 1
    trading_costs = (
        costs["spread_pct"] + costs["commission_pct"] + costs["slippage_pct"]
    )
    financing_share = (
        costs["financing_pct"] / costs["total_pct"] * 100.0 if costs["total_pct"] else 0.0
    )
    fin_ratio = costs["financing_pct"] / trading_costs if trading_costs else 0.0
    dev = wf["development"]
    locked = wf["locked_final_test"]
    last_fold = folds[-1]["fold"]

    meta = f"""
    <ul class="meta-strip" aria-label="Run summary">
      <li><b>{count(n_trades)}</b> trades</li>
      <li class="{'warn' if metrics['net_return_pct'] < 0 else ''}"><b>{pct(metrics['net_return_pct'])}</b> net</li>
      <li><b>{num(metrics['hit_rate_pct'], 1)}%</b> hit rate</li>
      <li><b>{count(total_bars)}</b> daily bars simulated</li>
      <li><b>{days}</b> day window</li>
    </ul>"""

    notice = (
        "<strong>Swing backtest report, research only.</strong> This page renders "
        f"<span class='formula'>data/backtest/swing_results.json</span>: {days} days of daily bars for "
        f"{symbols} run bar by bar through the daily swing variant and replayed on loop below. "
        "Spread, commission and slippage are charged on every round trip, plus the flat financing "
        "rate once for each session a position is held. It is not live performance and not "
        "investment advice. Regenerate with <span class='formula'>python scripts/run_swing_backtest.py</span> then "
        "<span class='formula'>python scripts/generate_swing_page.py</span>."
    )

    kpis = "".join(
        [
            kpi(
                "Net return",
                pct(metrics["net_return_pct"]),
                f"after {num(costs['total_pct'])}% of costs, {num(costs['financing_pct'])}% of it financing",
                "warn",
            ),
            kpi(
                "Gross edge",
                pct(metrics["gross_return_pct"]),
                "before any cost is charged",
                "pos",
            ),
            kpi(
                "Total costs",
                pct(-costs["total_pct"]),
                f"{financing_share:.0f}% of it is the financing placeholder",
                "neg",
            ),
            kpi(
                "Trades",
                count(n_trades),
                f"{metrics['exposure_pct']:.1f}% of the window in the market, summed across symbols",
            ),
            kpi(
                "Hit rate",
                num(metrics["hit_rate_pct"], 1) + "%",
                "spec target was 55 to 65% at 1:1",
                "neg" if metrics["hit_rate_pct"] < 55 else "pos",
            ),
            kpi(
                "Profit factor",
                num(metrics["profit_factor"]),
                f"expectancy {metrics['expectancy']:+,.2f} per trade",
                "pos" if metrics["profit_factor"] >= 1 else "neg",
            ),
            kpi(
                "Max drawdown",
                num(metrics["max_drawdown_pct"]) + "%",
                (
                    "recovery never: the window ended below the pre-trough peak"
                    if metrics.get("recovery_days") is None
                    else f"recovery {metrics['recovery_days']} days"
                ),
                "warn",
            ),
            kpi(
                "Guardrails",
                f"{passed} / {total_criteria}",
                "criteria registered before any run; see the criteria band",
                "neg" if passed < total_criteria else "pos",
            ),
        ]
    )

    # Replay controls and live readout.
    ro = f"""
    <div class="replay-bar">
      <span class="switch-label">Replay</span>
      <button id="btn-play" class="btn btn-primary" type="button">Pause replay</button>
      <button id="btn-restart" class="btn btn-ghost" type="button">Restart window</button>
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

    reason_bits = ", ".join(
        f"{n} {EXIT_LABELS.get(reason, reason.replace('_', ' '))}"
        for reason, n in sorted(exits.items(), key=lambda kv: -kv[1])
    )
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
        + '<p class="caption">The cursor sweeps the window, reveals the curve and the exit ticks as it '
        "goes, then wraps to the first point and starts again. Signals decide at a daily bar close "
        "and fill at the next daily open (the 21:00 UTC roll), so a Friday signal fills at Sunday's "
        "roll. "
        + f"Of the {n_trades} exits, {reason_bits}. Each fill pays spread, commission, slippage and "
        "one financing session for every daily bar held.</p>"
    )
    replay_panel = panel(
        "REPLAY",
        "One year of daily bars, on loop",
        f"{days} days",
        chart_body,
    )

    # ---------------------------------------------------------------- criteria
    crit_rows: list[tuple[str, str, str]] = []
    for criterion in criteria:
        crit_rows.append(
            (
                "green" if criterion["passed"] else "red",
                CRITERION_TITLES.get(
                    criterion["name"], criterion["name"].replace("_", " ")
                ),
                criterion_note(criterion, metrics, neg_folds, len(folds)),
            )
        )
    crit_rows.append(
        (
            "red" if stress["net_pct"] < 0 else "green",
            "All-in stress at 2x costs",
            f"Spread, commission, slippage and financing doubled together: {pct(stress['net_pct'])} net, "
            f"PF {num(stress['profit_factor'])}, hit {num(stress['hit_rate_pct'], 1)}%.",
        )
    )
    crit_rows.append(
        (
            "amber" if passed < total_criteria else "green",
            f"{passed} of {total_criteria} criteria pass",
            guard["note"],
        )
    )
    guardrails_panel = panel(
        "CRITERIA",
        "Five criteria, registered before any run",
        f"{passed} / {total_criteria} pass",
        gate_list(crit_rows),
    )

    wf_panel = panel(
        "WALK-FORWARD",
        "Anchored blocks with a locked final test",
        wf.get("method", "anchored_walk_forward").replace("_", " "),
        fold_table(wf)
        + '<p class="caption">Block 0 seeds the walk-forward; the last block is the locked final test '
        "and was never used to adjust the rules. "
        f"Development {count(dev['trades'])} trades at {pct(dev['net_pct'])} with PF {num(dev['profit_factor'])}; "
        f"locked test {count(locked['trades'])} trades at {pct(locked['net_pct'])} with PF {num(locked['profit_factor'])}. "
        "Profit factor reads n/a on a fold with no losing trade and 0.00 on a fold with no winning trade.</p>",
    )

    positive = [f for f in folds if f["net_pct"] > 0]
    stressed_positive = [
        stress_by_fold[f["fold"]]["net_pct_stressed"]
        for f in positive
        if f["fold"] in stress_by_fold
    ]
    worst = min(folds, key=lambda f: f["net_pct"])
    fold_caption = (
        f"{len(positive)} of {len(folds)} folds net positive at base costs; the worst is "
        f"fold {worst['fold']} at {pct(worst['net_pct'])}. The amber marks restate each fold with "
        "spread, commission, slippage and financing doubled together: the positive folds shrink to "
        f"as little as {pct(min(stressed_positive))} and every negative fold deepens."
    )
    fold_panel = panel(
        "BLOCKS",
        "Net return by walk-forward block",
        f"{len(folds)} folds",
        fold_bars_svg(folds, stress_by_fold)
        + legend(
            [
                ("Net fold return", "fold-pos"),
                ("Negative fold", "fold-neg"),
                ("Same fold at 2x costs", "fold-mark"),
            ]
        )
        + f'<p class="caption">{fold_caption}</p>',
    )

    # ---------------------------------------------------------------- report
    cost_panel = panel(
        "COSTS",
        "Where the money went",
        pct(costs["total_pct"]) + " total",
        cost_table(metrics)
        + "<p class=\"caption\">Costs are decimal returns on notional per symbol, charged on every "
        "round trip, plus <span class='formula'>costs.financing_per_session</span> once for every "
        f"daily bar a position is held. Financing is {financing_share:.0f}% of the "
        f"{pct(costs['total_pct'])} total; spread, commission and slippage together are "
        f"{pct(trading_costs)}.</p>",
    )

    financing_rows = [
        ["Financing removed (0x)", (pct(financing["financing_0x"]), tone(financing["financing_0x"]))],
        ["As charged (1x)", (pct(financing["financing_1x"]), tone(financing["financing_1x"]))],
        ["Financing doubled (2x)", (pct(financing["financing_2x"]), tone(financing["financing_2x"]))],
    ]
    financing_panel = panel(
        "FINANCING",
        "The placeholder that decides the sign",
        "0x / 1x / 2x",
        xtab(["Financing rate", "Net return"], financing_rows)
        + "<p class=\"caption\">The flat 0.00025 per-session placeholder is charged once per daily "
        f"bar held: {num(costs['financing_pct'])} of the {num(costs['total_pct'])} points of cost, "
        f"{fin_ratio:.0f}x the {num(trading_costs)} points of spread, commission and slippage "
        f"combined. At 2x the net flips to {pct(financing['financing_2x'])}, so signed per-symbol "
        "broker swap rates are required before live.</p>",
    )

    sens_order = [
        ("base", "As simulated, financing included"),
        ("spread_x2", "Spread doubled, financing excluded"),
        ("slippage_x2", "Slippage doubled, financing excluded"),
        ("stress_costs", "Spread and slippage doubled, financing excluded"),
        ("missing_trades_1_in_10", "One trade in ten missed, financing excluded"),
    ]
    sens_rows = []
    for key, label in sens_order:
        item = sens.get(key)
        if not item:
            continue
        sens_rows.append(
            [
                label,
                count(item["trades"]),
                (pct(item["net_pct"]), tone(item["net_pct"])),
                num(item["hit_rate_pct"], 1) + "%",
                num(item["profit_factor"]),
            ]
        )
    sens_rows.append(
        [
            f"Delayed execution ({delayed.get('entry_delay_bars')} bars to fill)",
            count(delayed.get("trades", 0)),
            (pct(delayed.get("net_return_pct")), tone(delayed.get("net_return_pct"))),
            num(delayed.get("hit_rate_pct"), 1) + "%",
            num(delayed.get("profit_factor")),
        ]
    )
    sens_panel = panel(
        "SENSITIVITY",
        "Cost and fill variations on the same trades",
        "5 variations",
        xtab(["Variation", "Trades", "Net return", "Hit rate", "PF"], sens_rows)
        + f"<p class=\"caption\">The base and delayed rows are full nets including financing; the four "
        "restated rows charge spread, commission and slippage only — financing is excluded (the "
        "one-minute parity convention) — which is why they read above base. The all-in stress that "
        f"doubles all four cost lines together lands at {pct(stress['net_pct'])} in the criteria "
        "panel. Variants hold entry notional fixed; only delayed execution re-runs sizing.</p>",
    )

    risk_panel = panel(
        "RISK",
        "Tail statistics over the window",
        f"{days} days",
        gate_list(
            [
                ("amber", "Worst day", f"{pct(metrics['worst_day_pct'])} on the worst single day."),
                (
                    "amber",
                    "Worst month",
                    f"{pct(metrics['worst_month_pct'])} on the worst calendar month.",
                ),
                (
                    "amber",
                    "Losing streak",
                    f"{count(metrics['losing_streak'])} consecutive losing trades.",
                ),
                (
                    "blue",
                    "Average trade",
                    f"win {metrics['avg_win']:,.2f} against loss {metrics['avg_loss']:,.2f} on "
                    f"{count(n_trades)} trades, expectancy {metrics['expectancy']:+,.2f} per trade.",
                ),
                (
                    "blue",
                    "Exposure",
                    f"{metrics['exposure_pct']:.2f}% of the window held a position, summed across the "
                    "three symbols, which can overlap.",
                ),
                (
                    "blue",
                    "Volatility",
                    f"{num(metrics['volatility_pct_annualized'])}% annualized on daily equity, "
                    f"Sharpe {num(metrics['sharpe'])} and Sortino {num(metrics['sortino'])}.",
                ),
            ]
        ),
    )

    diag_panel = panel(
        "DIAGNOSTICS",
        "How the run was produced",
        "engine counters",
        gate_list(
            [
                (
                    "blue",
                    "Daily bars simulated",
                    " · ".join(f"{k} {count(v)}" for k, v in sorted(bars.items()))
                    + f" ({count(total_bars)} total).",
                ),
                (
                    "blue",
                    "Signals seen",
                    f"{count(diag.get('signals_seen', 0))} breakout fills evaluated; "
                    f"{count(diag.get('signals_rejected_by_regime', 0))} rejected by the regime gate "
                    "before any fill.",
                ),
                (
                    "blue",
                    "Entry gate",
                    f"swing_admits is {diag.get('entry_filters', {}).get('swing_gate', 'trend_only')}: "
                    "uptrend lets longs through, downtrend shorts, nothing else.",
                ),
                (
                    "blue",
                    "One-minute filters",
                    diag.get("entry_filters", {}).get("note", ""),
                ),
                (
                    "blue",
                    "Daily loss halts",
                    f"{count(diag.get('daily_loss_halts', 0))} days stopped for new entries after the "
                    "daily loss cap.",
                ),
                (
                    "amber" if diag.get("drawdown_breached") else "blue",
                    "Drawdown limit",
                    (
                        f"Breached at {pct(diag.get('drawdown_breach_pct'))} on "
                        f"{str(diag.get('drawdown_breach_at', ''))[:10]}. Recorded, not enforced, so "
                        f"the report keeps all {days} days."
                        if diag.get("drawdown_breached")
                        else f"Never reached the {pct(0.15 * 100, 0)} portfolio limit; recorded "
                        "rather than enforced, so the report keeps every day."
                    ),
                ),
                (
                    "blue",
                    "News bucket",
                    metrics.get("by_news_bucket", {}).get("reason", "No news calendar staged."),
                ),
                (
                    "blue",
                    "Generation",
                    f"{results.get('generated_at', '')} · schema {results.get('schema_version', '')} · "
                    f"{diag.get('elapsed_seconds', 0)}s to run.",
                ),
            ]
        ),
    )

    scope_panel = panel(
        "SCOPE",
        "What this variant trades",
        str(results.get("strategy", "swing")),
        gate_list(
            [
                (
                    "blue",
                    "Signal",
                    f"{results.get('strategy', 'donchian_20d')}: a 20-day Donchian channel measured "
                    "over bars t-20 to t-1, so the window excludes the signal bar itself.",
                ),
                (
                    "blue",
                    "Entries",
                    "Decided at the daily bar close, filled at the next daily open — the 21:00 UTC "
                    "roll; a weekend signal fills at Sunday's roll.",
                ),
                (
                    "blue",
                    "Exits",
                    "A ratcheted chandelier trail that never loosens, an opposite-regime flip filled "
                    "at the next open, a 20-session hold cap, and a forced close at the window edge.",
                ),
                (
                    "blue",
                    "Sessions",
                    f"Every fill books on the roll, so all {count(n_trades)} trades land in the asia "
                    "session bucket; the flat spread model understates roll spreads and the 2x stress "
                    "is the partial mitigation.",
                ),
                (
                    "blue",
                    "Fill delay",
                    f"Entries fill {count(results.get('entry_delay_bars', 1))} bar after the signal; "
                    "the sensitivity panel shows the two-bar variant.",
                ),
            ]
        ),
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

    trade_rows = []
    for i, trade in enumerate(trades):
        trade_rows.append(
            [
                str(i + 1),
                trade["symbol"],
                trade["direction"],
                trade["regime"],
                str(trade["session"]),
                str(trade["entry_time"])[:10],
                str(trade["exit_time"])[:10],
                str(trade["exit_reason"]).replace("_", " "),
                (f"{float(trade['pnl']):+,.2f}", tone(float(trade["pnl"]))),
                (f"{float(trade['net_return_pct']):+.2f}%", tone(float(trade["net_return_pct"]))),
                f"#{trade['fold']}",
            ]
        )
    trades_panel = panel(
        "EVERY FILL",
        f"All {count(n_trades)} trades",
        f"fold #{last_fold} locked",
        xtab(
            [
                "#",
                "Symbol",
                "Dir",
                "Regime",
                "Session",
                "Entry",
                "Exit",
                "Reason",
                "PnL",
                "Net %",
                "Fold",
            ],
            trade_rows,
        )
        + "<p class=\"caption\">Session is the signal session; entries fill at the next daily open "
        "(the 21:00 UTC roll — a Friday signal fills at Sunday's roll) and exits fill at the trail "
        "touch or the roll after a hold-cap, regime-flip or window decision. PnL is in account "
        "currency on the trade's own notional, and Net % is that trade's return, not on account "
        f"equity. Fold #{last_fold} is the locked final test.</p>",
    )

    script = (
        SCRIPT.replace("__LEFT__", str(LEFT))
        .replace("__PLOT_W__", str(PLOT_W))
        .replace("__T0__", str(int(t0.timestamp() * 1000)))
        .replace("__T1__", str(int(t1.timestamp() * 1000)))
        .replace("__CYCLE__", str(CYCLE_MS))
        .replace("__INITIAL__", str(int(results["initial_equity"])))
        .replace(
            "__PTS__",
            json.dumps(
                [[int(parse_ts(str(p[0])).timestamp() * 1000), p[1]] for p in points]
            ),
        )
        .replace(
            "__TRS__",
            json.dumps(
                sorted(
                    int(parse_ts(str(t["exit_time"] or t["entry_time"])).timestamp() * 1000)
                    for t in trades
                    if (t.get("exit_time") or t.get("entry_time"))
                )
            ),
        )
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{days} days of daily bars replayed through the pre-registered swing variant backtest for {symbols}, with equity, folds, costs, and the five-criterion verdict.">
  <title>Swing backtest replay | Quantitative FX Research Dashboard</title>
  <link rel="stylesheet" href="styles.css">
  <link rel="stylesheet" href="findings.css">
  <style>{STYLE}{SWING_STYLE}</style>
</head>
<body>
  {NAV}
  <header class="topbar">
    <div>
      <p class="eyebrow">SWING BACKTEST</p>
      <h1>{days} days of daily bars, on loop</h1>
      <p class="subtitle">The trailing {days} days of {symbols} daily bars run through the swing variant — 20-day Donchian breakouts gated by the trend-only regime, a ratcheted chandelier trail, five criteria registered before any run — then swept across the screen over and over.</p>
    </div>
    <span class="status-pill">{days} DAYS &#183; {count(n_trades)} TRADES</span>
  </header>

  <main>
    <section class="notice" aria-label="Swing backtest note">{notice}</section>

    {meta}

    <section class="kpi-grid" aria-label="Headline results">{kpis}</section>

    <section class="band" id="replay">
      <div class="band-head">
        <p class="eyebrow">REPLAY</p>
        <h2 class="band-title">Watch the window run</h2>
        <span class="band-note">cursor sweeps the window and restarts</span>
      </div>
      {replay_panel}
    </section>

    <section class="band" id="criteria">
      <div class="band-head">
        <p class="eyebrow">CRITERIA</p>
        <h2 class="band-title">The pre-registered verdict</h2>
        <span class="band-note">{total_criteria} criteria, registered before any run</span>
      </div>
      <div class="grid two-up">
        {guardrails_panel}
        {wf_panel}
      </div>
      {fold_panel}
    </section>

    <section class="band" id="report">
      <div class="band-head">
        <p class="eyebrow">REPORT</p>
        <h2 class="band-title">Strategy-spec report</h2>
        <span class="band-note">every figure recomputed from swing_results.json</span>
      </div>
      <div class="grid two-up">
        {cost_panel}
        {financing_panel}
      </div>
      <div class="grid two-up">
        {sens_panel}
        {risk_panel}
      </div>
      <div class="grid two-up">
        {diag_panel}
        {scope_panel}
      </div>
      {caveat}
    </section>

    <section class="band" id="breakdowns">
      <div class="band-head">
        <p class="eyebrow">BREAKDOWNS</p>
        <h2 class="band-title">Where the return came from</h2>
        <span class="band-note">same trades, cut five ways</span>
      </div>
      <div class="grid two-up">
        {breakdowns}
      </div>
    </section>

    <section class="band" id="trades">
      <div class="band-head">
        <p class="eyebrow">TRADES</p>
        <h2 class="band-title">Every fill in the window</h2>
        <span class="band-note">{n_trades} rows, fold #{last_fold} is locked</span>
      </div>
      {trades_panel}
    </section>
  </main>

  <footer>Quantitative FX Research Engine · Generated from data/backtest/swing_results.json · <a href="index.html">Dashboard</a> · <a href="{REPO_URL}" target="_blank" rel="noreferrer">View repository</a></footer>
  <script>{script}</script>
  <script src="app.js" defer></script>
</body>
</html>
"""


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate dashboard/swing.html from the swing run output."
    )
    parser.add_argument(
        "--results", default=str(RESULTS_PATH), help="Path to swing_results.json"
    )
    parser.add_argument("--out", default=str(OUT_PATH), help="Path to write the page")
    args = parser.parse_args(argv)

    results_path = Path(args.results)
    out_path = Path(args.out)
    if not results_path.exists():
        raise SystemExit(f"{results_path} is missing; run scripts/run_swing_backtest.py first")

    results = json.loads(results_path.read_text(encoding="utf-8"))
    html = build_page(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path.relative_to(ROOT)} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()

