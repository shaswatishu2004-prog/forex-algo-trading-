"""
FastAPI application — serves OHLCV data, signals, and coverage info to the dashboards.

Run:
    uvicorn api.main:app --reload --port 8000

Endpoints:
    GET /api/symbols           — list available symbols
    GET /api/ohlcv             — fetch OHLCV bars
    GET /api/coverage          — per-day bar counts for heatmap
    GET /api/summary           — DB-wide summary table
    GET /api/ingestion-log     — recent ingestion job history
    GET /api/signals           — latest signals from signals/ JSON files
    GET /api/verify            — quick data quality check
    GET /health                — liveness check
    GET /hub                   — strategy hub dashboard
"""

from __future__ import annotations

import json
import sys
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional

from data_layer.repository import MarketDataRepository

app = FastAPI(
    title="Quant Forex Hub & Backtest Simulator",
    description="High-performance quant trading engine, 2-year candlestick feed, and execution simulator.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Static mounts
app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")

# Shared repository instance
_repo: Optional[MarketDataRepository] = None


def get_repo() -> MarketDataRepository:
    global _repo
    if _repo is None:
        _repo = MarketDataRepository()
    return _repo


# ---------------------------------------------------------------------------
# Meta routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "service": "forex-data-api"}


# ---------------------------------------------------------------------------
# Page routes — Hyperlinked tabs served by FastAPI
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
@app.get("/hub", include_in_schema=False)
@app.get("/backtest", include_in_schema=False)
def serve_hub():
    """Serves the Candlestick Verification Menu & Backtest Simulator."""
    return FileResponse("dashboard/index.html")


@app.get("/hub.css", include_in_schema=False)
def hub_css():
    return FileResponse("dashboard/hub.css")


@app.get("/hub.js", include_in_schema=False)
def hub_js():
    return FileResponse("dashboard/hub.js")


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

@app.get("/api/symbols", tags=["data"])
def list_symbols():
    repo = get_repo()
    try:
        symbols = repo.get_symbols()
        # Ensure default targets are represented
        defaults = ["XAUUSD", "EURUSD", "USDJPY", "DXY"]
        merged = sorted(list(set(symbols + defaults)))
        return {"symbols": merged}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

@app.get("/api/ohlcv", tags=["data"])
def get_ohlcv(
    symbol: str = Query(..., description="e.g. XAUUSD, EURUSD"),
    timeframe: str = Query("1m", description="1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w"),
    lookback: Optional[str] = Query(None, description="Lookback window e.g. 1w, 1m, 3m, 6m, 1y, 2y, all"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: Optional[int] = Query(5000),
):
    repo = get_repo()
    sym = symbol.upper() if isinstance(symbol, str) else "EURUSD"
    tf = timeframe.lower() if isinstance(timeframe, str) else "1m"

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
    lb_days = lookback_map.get(lookback.lower()) if (lookback and isinstance(lookback, str)) else None

    try:
        df = repo.get_ohlcv(sym, tf, start=start, end=end, limit=limit, lookback_days=lb_days, allow_resample=True)
        # If requested 1m bars are missing or sparse, auto-fetch from Dukascopy
        if (df.empty or (lb_days and lb_days >= 7 and len(df) < 500 and tf == "1m")):
            try:
                from data_layer.ingestion.dukascopy_fetcher import DukascopyFetcher
                duka = DukascopyFetcher(repo=repo)
                duka.sync_symbol(sym, force_lookback_days=lb_days or 365)
                df = repo.get_ohlcv(sym, tf, start=start, end=end, limit=limit, lookback_days=lb_days, allow_resample=True)
            except Exception:
                pass
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    if df.empty:
        return {"symbol": sym, "timeframe": tf, "lookback": lookback, "bars": [], "count": 0}


    bars = [
        {
            "time": int(row.Index.timestamp()),
            "ts": row.Index.isoformat(),
            "open": round(row.open, 6),
            "high": round(row.high, 6),
            "low": round(row.low, 6),
            "close": round(row.close, 6),
            "volume": round(row.volume, 2),
        }
        for row in df.itertuples()
    ]
    return {
        "symbol": sym,
        "timeframe": tf,
        "lookback": lookback or "all",
        "count": len(bars),
        "bars": bars,
        "start": str(df.index.min())[:19] if not df.empty else None,
        "end": str(df.index.max())[:19] if not df.empty else None,
    }


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

@app.get("/api/coverage", tags=["data"])
def get_coverage(
    symbol: str = Query(...),
    timeframe: str = Query("1m"),
    start: Optional[str] = Query("2024-01-01"),
    end: Optional[str] = Query(None),
):
    repo = get_repo()
    try:
        df = repo.get_coverage(symbol.upper(), timeframe.lower(), start=start, end=end)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    if df.empty:
        return {"symbol": symbol, "timeframe": timeframe, "coverage": []}

    records = [
        {
            "date": str(row.date)[:10],
            "bar_count": int(row.bar_count),
            "expected_bars": int(row.expected_bars),
            "coverage_pct": float(row.coverage_pct),
        }
        for row in df.itertuples()
    ]
    return {"symbol": symbol.upper(), "timeframe": timeframe.lower(), "coverage": records}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@app.get("/api/summary", tags=["data"])
def get_summary():
    repo = get_repo()
    try:
        df = repo.get_summary()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")
    return {"summary": df.to_dict(orient="records")}


# ---------------------------------------------------------------------------
# Ingestion log
# ---------------------------------------------------------------------------

@app.get("/api/ingestion-log", tags=["meta"])
def get_ingestion_log(limit: int = Query(50)):
    repo = get_repo()
    try:
        df = repo.get_ingestion_log(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB error: {e}")

    records = []
    for row in df.itertuples(index=False):
        err = getattr(row, "error_msg", None)
        if pd.isna(err):
            err = None
        d_from = getattr(row, "date_from", None)
        if pd.isna(d_from):
            d_from = None
        d_to = getattr(row, "date_to", None)
        if pd.isna(d_to):
            d_to = None
        fin = getattr(row, "finished_at", None)
        if pd.isna(fin):
            fin = None

        records.append({
            "id": int(row.id),
            "symbol": str(row.symbol),
            "timeframe": str(row.timeframe),
            "source": str(row.source),
            "started_at": str(row.started_at),
            "finished_at": str(fin) if fin else None,
            "bars_fetched": int(row.bars_fetched) if pd.notna(row.bars_fetched) else 0,
            "bars_inserted": int(row.bars_inserted) if pd.notna(row.bars_inserted) else 0,
            "date_from": str(d_from) if d_from else None,
            "date_to": str(d_to) if d_to else None,
            "status": str(row.status),
            "error_msg": str(err) if err else None,
        })
    return {"log": records, "logs": records}


# ---------------------------------------------------------------------------
# Signals — read latest signal JSON files from signals/ directory
# ---------------------------------------------------------------------------

@app.get("/api/signals", tags=["strategy"])
def get_signals(
    symbol: Optional[str] = Query(None, description="Filter by symbol e.g. XAUUSD"),
    type: Optional[str] = Query(None, description="regime | vwap_position"),
    limit: int = Query(200),
):
    """Read the latest signal JSON files from signals/ and return records."""
    signals_dir = ROOT / "signals"
    if not signals_dir.exists():
        return {"signals": [], "files": [], "total": 0, "records": []}

    sym = symbol.strip().upper() if isinstance(symbol, str) and symbol.strip() else None
    sig_type = type.strip() if isinstance(type, str) and type.strip() else None
    max_limit = limit if isinstance(limit, int) else 200

    all_records = []
    files_loaded = []
    pattern = f"{sym}_*.json" if sym else "*.json"

    for path in sorted(signals_dir.glob(pattern), reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            files_loaded.append({
                "file": path.name,
                "symbol": doc.get("symbol"),
                "generated_at": doc.get("generated_at"),
                "record_counts": doc.get("record_counts", {}),
                "last_state": doc.get("last_state"),
                "minute_window": doc.get("minute_window"),
            })
            for rec in doc.get("records", []):
                if sig_type and rec.get("type") != sig_type:
                    continue
                all_records.append(rec)
        except Exception:
            continue

    all_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return {
        "files": files_loaded,
        "total": len(all_records),
        "records": all_records[:max_limit],
    }


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Data Verification endpoint — used by hub on load
# ---------------------------------------------------------------------------

@app.get("/api/verify", tags=["meta"])
def verify_data():
    """Quick per-symbol bar count and signal check for the hub dashboard."""
    repo = get_repo()
    backend = repo.backend
    expected = ["XAUUSD", "EURUSD", "USDJPY", "DXY"]
    results = []

    try:
        summary_df = repo.get_summary()
    except Exception as e:
        return {"db_ok": False, "backend": backend, "error": str(e), "symbols": [], "signal_files": []}

    for sym in expected:
        sym_df = summary_df[summary_df["symbol"] == sym] if not summary_df.empty else None
        if sym_df is not None and not sym_df.empty:
            total_bars = int(sym_df["total_bars"].sum())
            earliest = str(sym_df["earliest"].min())
            latest = str(sym_df["latest"].max())
            source = ", ".join(sym_df["source"].unique())

            results.append({
                "symbol": sym,
                "status": "ok",
                "total_bars": total_bars,
                "earliest": earliest,
                "latest": latest,
                "source": source,
            })
        else:
            results.append({
                "symbol": sym,
                "status": "pending",
                "total_bars": 0,
                "earliest": None,
                "latest": None,
                "source": None,
            })

    signals_dir = ROOT / "signals"
    sig_files = [p.name for p in sorted(signals_dir.glob("*.json"), reverse=True)[:10]] \
        if signals_dir.exists() else []

    return {
        "db_ok": any(r["status"] == "ok" for r in results),
        "backend": backend,
        "symbols": results,
        "signal_files": sig_files,
    }


# ---------------------------------------------------------------------------
# Data Sync endpoint — keeps dashboard always up to date
# ---------------------------------------------------------------------------

@app.get("/api/sync", tags=["data"])
@app.post("/api/sync", tags=["data"])
def sync_market_data(days_back: int = Query(7)):
    """Fetch recent bars to keep database and dashboard 100% up-to-date."""
    try:
        from data_layer.ingestion.pipeline import IngestionPipeline
        pipeline = IngestionPipeline()
        res = pipeline.sync_latest(days_back=days_back)
        return {"status": "ok", "synced": res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Bulk Data Download endpoint — pulls all future backtesting data at once via HTTP
# ---------------------------------------------------------------------------

@app.get("/api/download-all", tags=["data"])
@app.post("/api/download-all", tags=["data"])
def bulk_download_all_data():
    """Download full history for all symbols at once using repository data engine."""
    try:
        from scripts.download_all_data import download_all_historical_data
        res = download_all_historical_data(
            symbols=["XAUUSD", "EURUSD", "USDJPY", "DXY"],
            timeframes=["1d", "1h", "1m"],
            daily_bars=1000,
            intraday_bars=3000,
        )
        return {"status": "ok", "message": "Bulk download complete", "results": res}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# HistData 1-Minute Fetcher (philipperemy/FX-1-Minute-Data model)
# ---------------------------------------------------------------------------

@app.get("/api/fetch/histdata", tags=["data"])
@app.post("/api/fetch/histdata", tags=["data"])
def fetch_histdata_endpoint(
    symbol: str = Query("EURUSD", description="e.g. EURUSD, USDJPY, XAUUSD"),
    year: int = Query(2024, description="Trading year (e.g. 2024)"),
    month: Optional[int] = Query(None, description="Month (1-12) or None for full year"),
):
    """Fetch 1-minute historical data from HistData.com, store in DB, and log the request."""
    try:
        from data_layer.ingestion.histdata_fetcher import HistDataFetcher
        repo = get_repo()
        fetcher = HistDataFetcher(repo=repo)
        result = fetcher.ingest_symbol(symbol=symbol, year=year, month=month)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Backtest simulation endpoint — runs over stored repository market data
# ---------------------------------------------------------------------------

@app.get("/api/backtest/run", tags=["strategy"])
@app.post("/api/backtest/run", tags=["strategy"])
def run_backtest_endpoint(
    symbol: str = Query("EURUSD"),
    strategy: str = Query("vwap_mean_reversion"),
    timeframe: str = Query("1d"),
    lookback: Optional[str] = Query("1y", description="Lookback window: 1w, 1m, 3m, 6m, 1y, 2y, all"),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    initial_capital: float = Query(10000.0),
    risk_pct: float = Query(1.0),
    sl_multiplier: float = Query(1.5),
    tp_multiplier: float = Query(2.0),
    leverage: float = Query(100.0, description="Account leverage e.g. 100 for 1:100"),
    max_margin_pct: float = Query(50.0, description="Max allowed margin level e.g. 50%"),
    lot_size: Optional[float] = Query(0.10, description="Constant position size in lots applied to all trades"),
    lot_mode: str = Query("fixed", description="fixed | constant"),
    custom_lot_size: Optional[float] = Query(None, description="Custom fixed lot size alias"),
    commission_per_lot: float = Query(0.0, description="Custom commission per lot ($)"),
    slippage: float = Query(0.0, description="Custom slippage penalty in pips"),
):
    """Run strategy simulation over stored repository bars with constant position sizing across all trades."""
    from api.backtest_service import run_strategy_backtest
    repo = get_repo()
    return run_strategy_backtest(
        symbol=symbol,
        strategy=strategy,
        timeframe=timeframe,
        lookback=lookback,
        start=start,
        end=end,
        initial_capital=initial_capital,
        risk_pct=risk_pct,
        sl_multiplier=sl_multiplier,
        tp_multiplier=tp_multiplier,
        leverage=leverage,
        max_margin_pct=max_margin_pct,
        lot_mode=lot_mode,
        lot_size=lot_size,
        custom_lot_size=custom_lot_size,
        commission_per_lot=commission_per_lot,
        slippage=slippage,
        repo=repo,
    )


# ---------------------------------------------------------------------------
# Strategy Creation & Configuration endpoints
# ---------------------------------------------------------------------------

@app.get("/api/strategy/list", tags=["strategy"])
def list_strategies():
    path = ROOT / "config" / "strategies.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "strategies": [
            {
                "id": "strat_vwap_reversion",
                "name": "VWAP Band Mean Reversion",
                "symbol": "EURUSD",
                "timeframe": "1m",
                "entry_trigger": "Price Touches -2.0 Sigma Lower Band",
                "exit_rule": "Target VWAP / Stop-Loss 1.5x Sigma",
                "risk_pct": 1.0,
                "created_at": "2026-10-10",
                "status": "Active",
            },
            {
                "id": "strat_regime_breakout",
                "name": "COMEX Gold Momentum Breakout",
                "symbol": "XAUUSD",
                "timeframe": "1m",
                "entry_trigger": "Uptrend Regime + Band +2.0 Sigma Break",
                "exit_rule": "Trailing 2.0x Sigma Band TP",
                "risk_pct": 1.5,
                "created_at": "2026-10-10",
                "status": "Active",
            },
            {
                "id": "strat_usdjpy_pullback",
                "name": "USDJPY Trend Continuation",
                "symbol": "USDJPY",
                "timeframe": "1m",
                "entry_trigger": "Inside Band 1 + Regime Alignment",
                "exit_rule": "Take Profit 2.0x R:R",
                "risk_pct": 1.0,
                "created_at": "2026-10-10",
                "status": "Active",
            }
        ]
    }


@app.post("/api/strategy/save", tags=["strategy"])
def save_strategy(strategy: dict):
    path = ROOT / "config" / "strategies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = list_strategies()
    strats = data.get("strategies", [])
    if "id" not in strategy or not strategy["id"]:
        import time as _t
        strategy["id"] = f"strat_{int(_t.time())}"
    strats.append(strategy)
    path.write_text(json.dumps({"strategies": strats}, indent=2), encoding="utf-8")
    return {"status": "ok", "strategy": strategy}


# ---------------------------------------------------------------------------
# Market Data Sync / Self-Updating
# ---------------------------------------------------------------------------

@app.post("/api/market-data/sync", tags=["data"])
def sync_market_data(
    symbols: Optional[List[str]] = Query(None),
    lookback_days: Optional[int] = Query(None),
):
    """
    Trigger on-demand self-updating sync using Dukascopy (primary) with HistData backup.
    Fetches only missing 1-minute bars and appends them to the database.
    """
    from data_layer.ingestion.dukascopy_fetcher import DukascopyFetcher
    fetcher = DukascopyFetcher(repo=get_repo())
    targets = symbols or ["EURUSD", "XAUUSD", "USDJPY", "DXY"]
    results = fetcher.sync_all(symbols=targets, force_lookback_days=lookback_days)
    total_added = sum(r.get("bars_inserted", 0) for r in results.values())
    return {
        "status": "completed",
        "total_bars_inserted": total_added,
        "results": results,
    }

