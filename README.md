# Quant Forex Hub & Backtest Simulator

A quantitative foreign exchange research engine, 2-year verified historical candlestick data feed, and interactive backtesting simulator.

---

## ⚡ Architecture Overview

1. **Candlestick Verification Menu**
   - 2-year continuous historical market data from January 2024 to present.
   - Core currency pairs and commodities: **EURUSD**, **XAUUSD** (COMEX Gold), **USDJPY**, and **DXY** (US Dollar Index).
   - Multi-timeframe verification: `1d`, `1h`, `15m`, `5m`, and `1m`.
   - Real-time data sync powered by the Infoway HTTP API with local SQLite & TimescaleDB storage.

2. **Backtest Simulator**
   - High-performance quantitative execution engine.
   - Interactive TradingView candlestick execution overlays (BUY/SELL entries, TP/SL exit markers).
   - Leverage support: 1:100 leverage with strict 50% maximum margin utilization cap.
   - Position sizing: Dynamic risk-based lot sizing or custom fixed lot selection.
   - Comprehensive trade execution cards with exact timestamps, entry/exit prices, and lot sizes (strictly zero data tables).
   - SVG equity curve with total return, profit factor, max drawdown, and win rate metrics.

---

## 🚀 Quick Start

### 1. Launch with One Command
The repository includes an automated launcher that configures `.venv`, verifies dependencies, and starts the FastAPI server:

```powershell
python start.py
```

Then open your browser to:
- **Main Menu (Candlestick Verification)**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Backtest Simulator**: [http://127.0.0.1:8000/backtest](http://127.0.0.1:8000/backtest)
- **API Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### 2. Manual Setup (Alternative)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

---

## 📊 Data Management & CLI Tools

- **Download 2-Year Historical Bars**:
  ```powershell
  python scripts/download_all_data.py
  ```
- **Run CLI Backtest**:
  ```powershell
  python scripts/run_backtest.py --symbol EURUSD --timeframe 1d
  ```
- **Run Quant Analysis Engine**:
  ```powershell
  python scripts/run_analysis.py
  ```
- **Run Unit Tests**:
  ```powershell
  pytest
  ```

---

## 📁 Repository Structure

```text
├── api/                   # FastAPI backend & backtest simulation endpoints
├── config/                # Ingestion and strategy configuration YAML/JSON
├── dashboard/             # Pure vanilla frontend (index.html, hub.js, hub.css)
├── data_layer/            # Market data repository and Infoway API fetcher
├── scripts/               # Operational CLI scripts for backtesting and downloading
├── src/fx_strategy/       # Core quantitative algorithms, risk sizing, and cost math
├── tests/                 # Complete unit test suite
├── docker-compose.yml     # Optional TimescaleDB container setup
└── start.py               # One-click environment manager and server runner
```

---

## 🌐 Live GitHub Pages Deployment

The dashboard is automatically deployed to GitHub Pages via `.github/workflows/deploy-dashboard.yml` upon push to `main`.
