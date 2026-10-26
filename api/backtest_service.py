"""
Backtest service — executes strategy simulations over repository historical OHLCV data.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from data_layer.repository import MarketDataRepository


def run_strategy_backtest(
    symbol: str = "EURUSD",
    timeframe: str = "1m",
    strategy: str = "vwap_mean_reversion",
    initial_capital: float = 10000.0,
    risk_pct: float = 1.0,
    sl_multiplier: float = 1.5,
    tp_multiplier: float = 2.0,
    leverage: float = 100.0,
    max_margin_pct: float = 50.0,
    lot_mode: str = "fixed",
    lot_size: Optional[float] = 0.10,
    custom_lot_size: Optional[float] = None,
    commission_per_lot: float = 0.0,
    slippage: float = 0.0,
    lookback: Optional[str] = "1y",
    start: Optional[str] = None,
    end: Optional[str] = None,
    repo: MarketDataRepository | None = None,
) -> Dict[str, Any]:
    """
    Run backtest simulation on stored repository bars with constant position sizing
    across all trades, 1:100 leverage, lookback windowing, and a strict maximum 50% margin cap.
    """
    if repo is None:
        repo = MarketDataRepository()

    lookback_map = {
        "1d": 1,
        "3d": 3,
        "1w": 7,
        "2w": 14,
        "1m": 30,
        "3m": 90,
        "6m": 180,
        "1y": 365,
        "2y": 730,
        "all": None,
        "max": None,
    }
    lookback_days = lookback_map.get(str(lookback).lower()) if lookback else None

    df = repo.get_ohlcv(
        symbol.upper(),
        timeframe.lower(),
        start=start,
        end=end,
        limit=None,
        lookback_days=lookback_days,
        allow_resample=True,
    )

    # If data is missing or lookback is sparse, auto-fetch from Dukascopy
    if df.empty or len(df) < 30 or (lookback_days and lookback_days >= 7 and len(df) < 500 and timeframe.lower() == "1m"):
        try:
            from data_layer.ingestion.dukascopy_fetcher import DukascopyFetcher
            duka = DukascopyFetcher(repo=repo)
            duka.sync_symbol(symbol.upper(), force_lookback_days=lookback_days or 365)
            df = repo.get_ohlcv(
                symbol.upper(),
                timeframe.lower(),
                start=start,
                end=end,
                limit=None,
                lookback_days=lookback_days,
                allow_resample=True,
            )
        except Exception:
            pass

    if df.empty or len(df) < 30:

        return {
            "symbol": symbol,
            "total_trades": 0,
            "metrics": {
                "initial_capital": initial_capital,
                "final_equity": initial_capital,
                "net_profit": 0.0,
                "total_return_pct": 0.0,
                "win_rate": 0.0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "win_loss_ratio": 0.0,
                "total_gross_pnl": 0.0,
                "total_commission": 0.0,
                "total_slippage_cost": 0.0,
                "total_fees": 0.0,
                "commission_impact_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "leverage": f"1:{int(leverage)}",
                "max_margin_cap_pct": max_margin_pct,
                "peak_margin_used_pct": 0.0,
                "lot_mode": "constant",
                "position_size_lots": 0.10,
                "lookback": lookback or "all",
                "start_date": None,
                "end_date": None,
                "bars_evaluated": 0,
            },
            "equity_curve": [{"time": "Start", "equity": initial_capital}],
            "trades": [],
        }

    # Feature calculations
    df = df.copy()
    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    low = df["low"].values
    times = [t.isoformat() for t in df.index]

    n = len(df)
    # Rolling VWAP & StdDev (window = 30)
    window = min(30, n // 2)
    typical = (high + low + close) / 3.0
    vol_safe = np.where(volume > 0, volume, 1.0)

    # Compute VWAP
    vwap = np.zeros(n)
    sigma = np.zeros(n)
    for i in range(window, n):
        w_typ = typical[i - window : i]
        w_vol = vol_safe[i - window : i]
        vwap[i] = np.sum(w_typ * w_vol) / np.sum(w_vol)
        sigma[i] = np.std(close[i - window : i])

    # Default sigma if flat
    sigma = np.where(sigma == 0, 0.001, sigma)

    # ─── Constant Position Sizing (Identical Throughout All Trades) ───
    sym_upper = symbol.upper()
    raw_lot = custom_lot_size if (custom_lot_size is not None and float(custom_lot_size) > 0) else lot_size
    if raw_lot is not None and float(raw_lot) > 0:
        fixed_lots = round(float(raw_lot), 2)
    else:
        # Standard default: 0.10 lots per $10,000 initial equity
        fixed_lots = max(0.01, round(initial_capital * 0.00001, 2))

    # Pre-check margin against initial bar price at 1:100 leverage and 50% max margin
    first_p = float(close[0]) if len(close) > 0 else 1.0
    if "XAU" in sym_upper:
        margin_per_lot_init = first_p
    elif "JPY" in sym_upper:
        margin_per_lot_init = 1000.0
    elif "DXY" in sym_upper:
        margin_per_lot_init = first_p * 10.0
    else:
        margin_per_lot_init = 1000.0 * first_p

    max_allowed_margin = initial_capital * (max_margin_pct / 100.0)
    max_safe_lots = max(0.01, round(max_allowed_margin / (margin_per_lot_init + 1e-6), 2))
    was_margin_capped = False
    if fixed_lots > max_safe_lots:
        fixed_lots = max_safe_lots
        was_margin_capped = True

    # Simulation state
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    peak_margin_pct = 0.0
    position = 0  # 1 for Long, -1 for Short
    entry_price = 0.0
    entry_time = ""
    entry_ts = 0
    trade_lots = fixed_lots
    trade_margin = 0.0
    trade_margin_pct = 0.0
    target_price = 0.0
    stop_price = 0.0

    trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = [{"time": times[0][:16], "equity": round(capital, 2)}]
    daily_returns: List[float] = []

    trade_id = 1

    for i in range(window + 1, n):
        cur_p = close[i]
        cur_t = times[i]
        cur_vwap = vwap[i]
        cur_sig = sigma[i]

        # Check existing position exit
        if position != 0:
            pnl_val = 0.0
            exit_trade = False
            exit_reason = ""

            if position == 1:
                # Long position
                if cur_p >= target_price:
                    exit_trade = True
                    exit_reason = "Take Profit"
                elif cur_p <= stop_price:
                    exit_trade = True
                    exit_reason = "Stop Loss"
                elif strategy == "vwap_mean_reversion" and cur_p >= cur_vwap:
                    exit_trade = True
                    exit_reason = "VWAP Target"

            elif position == -1:
                # Short position
                if cur_p <= target_price:
                    exit_trade = True
                    exit_reason = "Take Profit"
                elif cur_p >= stop_price:
                    exit_trade = True
                    exit_reason = "Stop Loss"
                elif strategy == "vwap_mean_reversion" and cur_p <= cur_vwap:
                    exit_trade = True
                    exit_reason = "VWAP Target"

            if exit_trade:
                price_diff = (cur_p - entry_price) * position
                if "XAU" in sym_upper:
                    # Gold: 1 lot = 100 troy ounces
                    pnl_gross = price_diff * (trade_lots * 100.0)
                    pip_val_usd = 10.0
                elif "DXY" in sym_upper:
                    pnl_gross = price_diff * (trade_lots * 1000.0)
                    pip_val_usd = 10.0
                else:
                    # Forex: 1 lot = 100,000 units
                    pnl_gross = price_diff * (trade_lots * 100000.0)
                    if "JPY" in sym_upper and cur_p > 0:
                        pnl_gross /= cur_p
                        pip_val_usd = 1000.0 / cur_p
                    else:
                        pip_val_usd = 10.0

                comm_cost = float(commission_per_lot) * trade_lots
                slip_cost = float(slippage) * pip_val_usd * trade_lots
                total_trade_fee = comm_cost + slip_cost
                pnl_val = pnl_gross - total_trade_fee

                ret_pct = round(float((cur_p - entry_price) / entry_price * position) * 100, 2)
                capital += pnl_val
                exit_ts = int(pd.Timestamp(cur_t).timestamp())
                fee_share_pct = round(float(total_trade_fee / abs(pnl_gross) * 100.0), 1) if abs(pnl_gross) > 0 else 0.0

                trades.append({
                    "id": trade_id,
                    "entry_time": entry_time[:19].replace("T", " "),
                    "exit_time": cur_t[:19].replace("T", " "),
                    "entry_timestamp": entry_ts,
                    "exit_timestamp": exit_ts,
                    "lots": round(float(trade_lots), 2),
                    "margin_used": round(float(trade_margin), 2),
                    "margin_pct": round(float(trade_margin_pct), 1),
                    "commission": round(float(comm_cost), 2),
                    "slippage_cost": round(float(slip_cost), 2),
                    "fee_pct": fee_share_pct,
                    "direction": "LONG" if position == 1 else "SHORT",
                    "entry_price": round(float(entry_price), 5),
                    "exit_price": round(float(cur_p), 5),
                    "pnl": round(float(pnl_val), 2),
                    "gross_pnl": round(float(pnl_gross), 2),
                    "return_pct": ret_pct,
                    "status": "WIN" if pnl_val > 0 else "LOSS",
                    "reason": exit_reason + (" (Margin 50% Capped)" if was_margin_capped else ""),
                })
                trade_id += 1
                position = 0
                daily_returns.append(pnl_val / capital if capital > 0 else 0.0)

        # Check entry signal if flat
        if position == 0 and capital > 0:
            lower_band = cur_vwap - (1.5 * cur_sig)
            upper_band = cur_vwap + (1.5 * cur_sig)
            entry_triggered = False
            new_pos = 0

            if cur_p <= lower_band:
                entry_triggered = True
                new_pos = 1
                stop_price = cur_p - (sl_multiplier * cur_sig)
                target_price = cur_p + (tp_multiplier * cur_sig)
            elif cur_p >= upper_band:
                entry_triggered = True
                new_pos = -1
                stop_price = cur_p + (sl_multiplier * cur_sig)
                target_price = cur_p - (tp_multiplier * cur_sig)

            if entry_triggered:
                # ─── Margin & Constant Lot Sizing (1:100 leverage, ≤50% margin) ───
                if "XAU" in sym_upper:
                    margin_per_lot = float(cur_p)
                elif "JPY" in sym_upper:
                    margin_per_lot = 1000.0
                elif "DXY" in sym_upper:
                    margin_per_lot = float(cur_p) * 10.0
                else:
                    margin_per_lot = 1000.0 * float(cur_p)

                # Keep position sizing identical across all trades
                selected_lots = fixed_lots
                trade_margin = selected_lots * margin_per_lot
                trade_margin_pct = (trade_margin / capital) * 100.0 if capital > 0 else 0.0
                peak_margin_pct = max(peak_margin_pct, trade_margin_pct)

                # Record entry
                position = new_pos
                entry_price = cur_p
                entry_time = cur_t
                entry_ts = int(pd.Timestamp(cur_t).timestamp())
                trade_lots = selected_lots

        # Update peak & drawdown
        peak = max(peak, capital)
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

        if i % 10 == 0 or i == n - 1:
            equity_curve.append({"time": cur_t[:16], "equity": round(capital, 2)})

    # Summary metrics
    wins = [t for t in trades if t["status"] == "WIN"]
    losses = [t for t in trades if t["status"] == "LOSS"]
    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0.0

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # Strategy Details: Average Win and Average Loss
    avg_win = round(float(np.mean([t["pnl"] for t in wins])), 2) if wins else 0.0
    avg_loss = round(float(abs(np.mean([t["pnl"] for t in losses]))), 2) if losses else 0.0
    win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (99.0 if avg_win > 0 else 0.0)

    # Commission vs P&L distribution
    total_commission = round(float(sum(t.get("commission", 0.0) for t in trades)), 2)
    total_slippage = round(float(sum(t.get("slippage_cost", 0.0) for t in trades)), 2)
    total_fees = round(total_commission + total_slippage, 2)
    total_gross_pnl = round(float(sum(t.get("gross_pnl", t["pnl"]) for t in trades)), 2)
    comm_impact_pct = round((total_fees / gross_profit * 100.0), 1) if gross_profit > 0 else 0.0

    total_return_pct = round((capital - initial_capital) / initial_capital * 100, 2)

    sharpe = 0.0
    if len(daily_returns) > 1 and np.std(daily_returns) > 0:
        sharpe = round(float(np.mean(daily_returns) / np.std(daily_returns) * math.sqrt(252)), 2)

    return {
        "symbol": sym_upper,
        "strategy": strategy,
        "total_trades": len(trades),
        "metrics": {
            "initial_capital": round(float(initial_capital), 2),
            "final_equity": round(float(capital), 2),
            "net_profit": round(float(capital - initial_capital), 2),
            "total_return_pct": round(float(total_return_pct), 2),
            "win_rate": float(win_rate),
            "win_rate_pct": float(win_rate),
            "profit_factor": round(float(profit_factor), 2),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "win_loss_ratio": float(win_loss_ratio),
            "total_gross_pnl": float(total_gross_pnl),
            "total_commission": float(total_commission),
            "total_slippage_cost": float(total_slippage),
            "total_fees": float(total_fees),
            "commission_impact_pct": float(comm_impact_pct),
            "max_drawdown_pct": round(float(max_dd), 2),
            "sharpe_ratio": float(sharpe),
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "leverage": f"1:{int(leverage)}",
            "max_margin_cap_pct": float(max_margin_pct),
            "peak_margin_used_pct": round(float(peak_margin_pct), 1),
            "lot_mode": "constant",
            "position_size_lots": float(fixed_lots),
            "lot_size": float(fixed_lots),
            "custom_lot_size": float(fixed_lots),
            "commission_per_lot": float(commission_per_lot),
            "slippage_pips": float(slippage),
            "avg_margin_used": round(float(np.mean([t["margin_used"] for t in trades])), 2) if trades else 0.0,
            "lookback": lookback or "all",
            "start_date": times[0][:10] if times else None,
            "end_date": times[-1][:10] if times else None,
            "bars_evaluated": int(n),
        },
        "equity_curve": equity_curve,
        "trades": trades[-200:],
    }
