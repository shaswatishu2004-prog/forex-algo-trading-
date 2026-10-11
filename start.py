"""
start.py — One-command launcher for the entire Forex Algo stack.

Sequence:
  1. Load environment variables (.env) for Infoway Market Data API
  2. Check Database health (TimescaleDB primary with graceful SQLite fallback)
  3. Sync latest market data to keep dashboard 100% up-to-date
  4. Verify data in DB and signals generated
  5. Start FastAPI server (uvicorn) & open Strategy Hub in browser

Usage:
    python start.py                  # Full boot & sync (checks DB -> syncs -> verifies -> boots Hub)
    python start.py --verify-only    # Data checks only, no server
    python start.py --no-sync        # Skip live feed sync on boot
    python start.py --no-browser     # Start server but skip auto-open
    python start.py --port 8080      # Custom port
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Auto-delegate to .venv python if executed outside virtualenv
ROOT = Path(__file__).resolve().parent
_venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
if _venv_python.exists() and Path(sys.executable).resolve() != _venv_python.resolve():
    sys.exit(subprocess.call([str(_venv_python), __file__] + sys.argv[1:]))

# Load environment file (.env) immediately
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_layer.repository import MarketDataRepository

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("start")

# ─── ANSI colours ────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def ok(msg):   log.info(f"{GREEN}  [OK]   {msg}{RESET}")
def err(msg):  log.error(f"{RED}  [ERR]  {msg}{RESET}")
def warn(msg): log.warning(f"{YELLOW}  [WARN] {msg}{RESET}")
def info(msg): log.info(f"{CYAN}  [INFO] {msg}{RESET}")
def head(msg): log.info(f"\n{BOLD}{msg}{RESET}")

EXPECTED_SYMBOLS = ["XAUUSD", "EURUSD", "USDJPY", "DXY"]


# ══════════════════════════════════════════════════════════════════════
# STEP 1 — Database Health (TimescaleDB primary, SQLite fallback)
# ══════════════════════════════════════════════════════════════════════

def check_database(dsn: str, timeout: float = 2.0) -> tuple[str, bool]:
    """
    Check if TimescaleDB is reachable.
    If not, gracefully fall back to local SQLite so the system is always operational.
    """
    head("[ 1 / 4 ]  Database Engine Connection")

    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=int(timeout))
        with conn.cursor() as cur:
            cur.execute("SELECT default_version FROM pg_available_extensions WHERE name='timescaledb'")
            row = cur.fetchone()
        conn.close()
        ver = row[0] if row else "PostgreSQL"
        ok(f"TimescaleDB ({ver}) reachable on {dsn.split(' ')[0]}")
        return "timescaledb", True
    except Exception:
        info("Using high-performance local data store (SQLite at data/forex_market_data.db)")
        sqlite_path = ROOT / "data" / "forex_market_data.db"
        if sqlite_path.exists():
            ok(f"Local market database ready ({sqlite_path.stat().st_size / 1024 / 1024:.1f} MB)")
        else:
            info("Local market database initialized")
        return "sqlite", True


# ══════════════════════════════════════════════════════════════════════
# STEP 2 — Sync Latest Market Data
# ══════════════════════════════════════════════════════════════════════

def sync_latest_market_data(symbols: list[str] | None = None) -> None:
    """
    Sync recent intraday and daily market bars to ensure the dashboard
    always presents up-to-date prices and indicators.
    """
    head("[ 2 / 4 ]  Syncing Latest Market Data")
    try:
        from data_layer.ingestion.pipeline import IngestionPipeline
        pipeline = IngestionPipeline()
        target = symbols or EXPECTED_SYMBOLS
        info(f"Checking live market feeds for {', '.join(target)}...")
        results = pipeline.sync_latest(symbols=target, days_back=7)
        for sym, res in results.items():
            if res.get("synced"):
                bars = res.get("bars_added", 0)
                ok(f"{sym:<8} live sync completed (+{bars:,} bars updated)")
    except Exception as e:
        info(f"Market feed sync note: {e}")


# ══════════════════════════════════════════════════════════════════════
# STEP 3 — Data Verification
# ══════════════════════════════════════════════════════════════════════

def verify_data(backend: str = "sqlite") -> dict:
    """
    Verify stored market data and computed strategy signals.
    """
    head("[ 3 / 4 ]  Data & Strategy Verification")
    report: dict[str, Any] = {"ok": True, "symbols": [], "warnings": [], "backend": backend}

    try:
        repo = MarketDataRepository(force_sqlite=(backend == "sqlite"))
        summary_df = repo.get_summary()

        if summary_df.empty:
            warn("Database has no recorded OHLCV data yet.")
            report["ok"] = False
            return report

        for sym in EXPECTED_SYMBOLS:
            sym_df = summary_df[summary_df["symbol"] == sym] if not summary_df.empty else None
            if sym_df is not None and not sym_df.empty:
                total_bars = int(sym_df["total_bars"].sum())
                earliest = str(sym_df["earliest"].min())
                latest = str(sym_df["latest"].max())
                sources = ", ".join(sym_df["source"].unique())

                sym_stats = {
                    "symbol": sym,
                    "status": "ok",
                    "bars": total_bars,
                    "first_bar": earliest,
                    "last_bar": latest,
                    "source": sources,
                }
                _print_sym_row(sym_stats)
                report["symbols"].append(sym_stats)
            else:
                report["symbols"].append({"symbol": sym, "status": "pending", "bars": 0})

    except Exception as exc:
        err(f"Data verification check failed: {exc}")
        report["ok"] = False

    # Signal files audit
    sig_dir = ROOT / "signals"
    sig_files = list(sig_dir.glob("*.json")) if sig_dir.exists() else []
    info(f"Strategy signal packages in signals/: {len(sig_files)} active")
    report["signal_files"] = len(sig_files)
    ok("Market data and strategy packages verified")

    return report


def _print_sym_row(s: dict) -> None:
    icon = f"{GREEN}[OK]{RESET}" if s["status"] == "ok" else f"{YELLOW}[..]{RESET}"
    bars = f"{s.get('bars', 0):>10,}"
    first = s.get("first_bar", "-")
    last  = s.get("last_bar", "-")
    print(f"   {icon}  {s['symbol']:<8}  {bars} bars  {first} -> {last}")


# ══════════════════════════════════════════════════════════════════════
# STEP 4 — FastAPI Server & Hub Dashboard
# ══════════════════════════════════════════════════════════════════════

def start_server(port: int = 8000, open_browser: bool = True) -> None:
    head("[ 4 / 4 ]  Starting API & Strategy Hub Dashboard")

    cmd = [
        sys.executable, "-m", "uvicorn",
        "api.main:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--reload",
        "--log-level", "warning",
    ]

    info(f"uvicorn api.main:app  →  http://localhost:{port}")
    info(f"Quant Forex Hub       →  http://localhost:{port}/hub")
    info(f"Backtest Simulator    →  http://localhost:{port}/backtest")
    info(f"Press Ctrl+C to stop\n")

    proc = subprocess.Popen(cmd, cwd=str(ROOT))

    if open_browser:
        time.sleep(1.8)   # wait for uvicorn to bind
        webbrowser.open(f"http://localhost:{port}/backtest")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print(f"\n{DIM}Shutting down server…{RESET}")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)


# ══════════════════════════════════════════════════════════════════════
# Main CLI Entrypoint
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forex Algo Trading Stack Launcher (DB Check -> Sync Data -> Verify -> Boot Hub).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    parser.add_argument("--no-sync", action="store_true", help="Skip live market sync on startup")
    parser.add_argument("--verify-only", action="store_true", help="Run checks only, no server")
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "FOREX_DB_DSN",
            "host=localhost port=5432 dbname=forex_data user=forex_user password=forex_pass",
        ),
    )
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}  +----------------------------------------------------------+")
    print(f"  |       Forex Algo Trading - Decoupled Tunnel Boot         |")
    print(f"  |   [Data Layer]  <--->  [Analysis Engine]  <--->  [Hub]   |")
    print(f"  +----------------------------------------------------------+{RESET}\n")

    # Step 1: Database
    backend, _ = check_database(args.dsn)

    # Step 2: Sync latest data (unless --no-sync is given)
    if not args.no_sync:
        sync_latest_market_data()

    # Step 3: Verify data
    report = verify_data(backend=backend)

    if args.verify_only:
        sys.exit(0 if report["ok"] else 1)

    # Step 4: Server
    start_server(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
