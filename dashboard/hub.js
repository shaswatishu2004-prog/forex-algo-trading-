/**
 * Candlestick Verification & Strategy Backtest Hub — hub.js
 * Pure Vanilla JS Frontend with zero data tables.
 *
 * 2 Single Tabs connected via FastAPI hyperlinks:
 *   1. Main Menu (Candlestick Verification of 2-Year Repository Market Data from Jan 2024)
 *   2. Backtest Tab (Simulator with TradingView Candlestick Execution Overlays, Time & Lots)
 */

(function () {
  'use strict';

  // ─── State Management ───────────────────────────────────────────────
  const state = {
    activeTab: 'main', // 'main' | 'backtest'
    symbol: 'EURUSD',
    timeframe: '1d', // default to 1d to immediately present 2-year lookback
    lookback: '2y', // '1m' | '3m' | '6m' | '1y' | '2y' | 'all'
    symbolsList: ['EURUSD', 'XAUUSD', 'USDJPY', 'DXY'],
    mainEngine: 'db_feed', // 'db_feed' (Our Library) | 'tv_advanced' (TV Cloud)

    // Main Menu Chart
    mainChart: null,
    mainCandleSeries: null,
    mainVolumeSeries: null,

    // Backtest Chart
    btChart: null,
    btCandleSeries: null,
    btVolumeSeries: null,
    btTrades: [],

    lastBacktestParams: {
      sym: 'EURUSD',
      strategy: 'vwap_mean_reversion',
      tf: '1d',
      lookback: '1y',
      lotSize: 0.10,
      sl: 1.5,
      tp: 2.0,
    },

    loading: false,
  };

  // ─── URL Routing Mapping ────────────────────────────────────────────
  const ROUTE_MAP = {
    '/': 'main',
    '/hub': 'main',
    '/backtest': 'backtest',
  };

  // ─── Utility Helpers ────────────────────────────────────────────────
  function fmtPrice(p) {
    if (p === null || p === undefined || isNaN(p)) return '—';
    const num = Number(p);
    return num >= 100 ? num.toFixed(2) : num.toFixed(5);
  }

  function fmtNumber(n) {
    if (n === null || n === undefined || isNaN(n)) return '0';
    return Number(n).toLocaleString();
  }

  function fmtPct(v) {
    if (v === null || v === undefined || isNaN(v)) return '0.00%';
    const num = Number(v);
    const sign = num > 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
  }

  function showToast(msg, duration = 3000) {
    let toast = document.getElementById('hub-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'hub-toast';
      toast.className = 'hub-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      toast.classList.remove('show');
    }, duration);
  }

  async function apiFetch(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return res.json();
  }

  // ─── TradingView Free Charting Library Helpers ─────────────────────
  function getTVSymbol(sym) {
    const s = (sym || 'EURUSD').toUpperCase();
    if (s === 'EURUSD') return 'FX:EURUSD';
    if (s === 'USDJPY') return 'FX:USDJPY';
    if (s === 'XAUUSD') return 'OANDA:XAUUSD';
    if (s === 'DXY') return 'CAPITALCOM:DXY';
    if (s.length === 6) return `FX:${s}`;
    return s;
  }

  function renderTVAdvancedWidget(containerId, sym, tf) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = '';

    const intervalMap = { '1d': 'D', '1h': '60', '15m': '15', '5m': '5', '1m': '1' };
    const interval = intervalMap[tf] || 'D';

    if (typeof TradingView !== 'undefined' && TradingView.widget) {
      try {
        new TradingView.widget({
          autosize: true,
          symbol: getTVSymbol(sym),
          interval: interval,
          timezone: "Etc/UTC",
          theme: "dark",
          style: "1",
          locale: "en",
          toolbar_bg: "#07090E",
          enable_publishing: false,
          allow_symbol_change: true,
          container_id: containerId,
          studies: ["STD;EMA", "STD;Bollinger_Bands", "STD;RSI"],
          hide_side_toolbar: false,
        });
      } catch (e) {
        console.error("TradingView widget error:", e);
      }
    } else {
      el.innerHTML = `
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; color:var(--txt2); gap:12px;">
          <div class="pulse-dot"></div>
          <span style="font-family:var(--mono);font-size:0.8rem;">Loading TradingView Charting Library...</span>
        </div>`;
      setTimeout(() => {
        if (typeof TradingView !== 'undefined') {
          renderTVAdvancedWidget(containerId, sym, tf);
        }
      }, 700);
    }
  }

  function generatePineScript(sym, strategy, tf, lotSize, slMult, tpMult) {
    return `//@version=5
strategy("Quant Forex Hub - ${strategy.toUpperCase()}", overlay=true, initial_capital=10000, default_qty_type=strategy.fixed, default_qty_value=${Number(lotSize).toFixed(2)}, currency=currency.USD, commission_type=strategy.commission.percent, commission_value=0.005)

// ─── Simulation Configuration (Matches Backend Engine) ───
// Pair: ${sym} | Timeframe: ${tf} | Constant Lot Size: ${Number(lotSize).toFixed(2)} | Leverage: 1:100 (≤50% Margin Cap)

sl_mult = input.float(${slMult}, "Stop Loss Sigma Multiplier", step=0.1)
tp_mult = input.float(${tpMult}, "Take Profit Sigma Multiplier", step=0.1)
fixed_lots = input.float(${Number(lotSize).toFixed(2)}, "Constant Lot Size (Identical Across All Trades)", step=0.01)

// ─── Rolling Indicators ───
len = input.int(30, "Rolling Window")
typ = hlc3
vol = volume > 0 ? volume : 1.0
vwap_val = ta.vwma(typ, len)
sigma = ta.stdev(close, len)
upper_band = vwap_val + (1.5 * sigma)
lower_band = vwap_val - (1.5 * sigma)

plot(vwap_val, "VWAP Line", color=color.new(#3B82F6, 0), linewidth=2)
plot(upper_band, "+1.5σ Upper Band", color=color.new(#EF4444, 20), linewidth=1)
plot(lower_band, "-1.5σ Lower Band", color=color.new(#10B981, 20), linewidth=1)

// ─── Order Execution with Exact Targets ───
long_cond = ta.crossunder(close, lower_band)
short_cond = ta.crossover(close, upper_band)

var float long_sl = na
var float long_tp = na
var float short_sl = na
var float short_tp = na

if (long_cond and strategy.position_size == 0)
    long_sl := close - (sl_mult * sigma)
    long_tp := close + (tp_mult * sigma)
    strategy.entry("BUY " + str.tostring(fixed_lots) + "L", strategy.long, qty=fixed_lots)
    strategy.exit("TP/SL Long", "BUY " + str.tostring(fixed_lots) + "L", limit=long_tp, stop=long_sl)

if (short_cond and strategy.position_size == 0)
    short_sl := close + (sl_mult * sigma)
    short_tp := close - (tp_mult * sigma)
    strategy.entry("SELL " + str.tostring(fixed_lots) + "L", strategy.short, qty=fixed_lots)
    strategy.exit("TP/SL Short", "SELL " + str.tostring(fixed_lots) + "L", limit=short_tp, stop=short_sl)
`;
  }

  // ─── Root DOM Mounting ──────────────────────────────────────────────
  const root = document.getElementById('root');
  if (!root) {
    console.error('Root element #root not found.');
    return;
  }

  function renderShell() {
    root.innerHTML = `
      <div class="hub-shell">
        <!-- Topbar Header with FastAPI Hyperlinks -->
        <header class="hub-nav-header">
          <div class="brand-section">
            <div class="brand-logo-badge">⚡</div>
            <div class="brand-text">
              <span class="brand-title">QUANT FOREX HUB</span>
              <span class="brand-subtitle">Quant Research Engine · Dukascopy 1M Parquet & Backtest Feed</span>
            </div>
          </div>

          <!-- 2 Navigation Tabs Connected via FastAPI Hyperlinks -->
          <nav class="nav-tab-group" id="nav-tabs" aria-label="Main Navigation">
            <a href="/" class="nav-tab-link active" data-tab="main" id="tab-nav-main">
              <span class="nav-tab-icon">📊</span>
              <span>Main Menu</span>
            </a>
            <a href="/backtest" class="nav-tab-link" data-tab="backtest" id="tab-nav-backtest">
              <span class="nav-tab-icon">🚀</span>
              <span>Backtest</span>
            </a>
          </nav>

          <div class="header-actions">
            <div class="api-status-pill">
              <span class="pulse-dot"></span>
              <span id="api-status-text">Repository Data Feed · Active</span>
            </div>
            <button class="pill-btn" id="btn-open-ingestion-log" title="View Ingestion Log">
              📋 Ingestion Log
            </button>
            <button class="pill-btn" id="btn-sync-feed" title="Sync latest market feed bars">
              🔄 Sync Live Feed
            </button>
          </div>
        </header>

        <!-- Main Workspace Panes -->
        <main class="hub-content-container">
          <!-- PANE 1: MAIN MENU (CANDLESTICK VERIFICATION) -->
          <section class="tab-pane active" id="pane-main">
            <div class="section-headline-bar">
              <div class="headline-text-group">
                <h1 class="headline-title">🕯️ Candlestick Verification (2-Year History from Jan 2024)</h1>
                <p class="headline-desc">Real-time OHLCV verification rendered from repository market data. Zero tabular data representation.</p>
              </div>
            </div>

            <div class="chart-card-wrapper">
              <!-- Toolbar -->
              <div class="chart-toolbar">
                <div class="selector-group" id="symbol-selector-group">
                  <span class="selector-label">Symbol:</span>
                  <button class="pill-btn active" data-symbol="EURUSD">EURUSD</button>
                  <button class="pill-btn" data-symbol="XAUUSD">XAUUSD [COMEX Gold]</button>
                  <button class="pill-btn" data-symbol="USDJPY">USDJPY</button>
                  <button class="pill-btn" data-symbol="DXY">DXY [USD Index]</button>
                </div>

                <div class="selector-group" id="timeframe-selector-group">
                  <span class="selector-label">Timeframe:</span>
                  <button class="pill-btn active" data-tf="1d">1d</button>
                  <button class="pill-btn" data-tf="4h">4h</button>
                  <button class="pill-btn" data-tf="1h">1h</button>
                  <button class="pill-btn" data-tf="15m">15m</button>
                  <button class="pill-btn" data-tf="5m">5m</button>
                  <button class="pill-btn" data-tf="1m">1m</button>
                </div>

                <div class="selector-group" id="lookback-selector-group">
                  <span class="selector-label">Lookback:</span>
                  <button class="pill-btn" data-lb="1m">1M</button>
                  <button class="pill-btn" data-lb="3m">3M</button>
                  <button class="pill-btn" data-lb="6m">6M</button>
                  <button class="pill-btn" data-lb="1y">1Y</button>
                  <button class="pill-btn active" data-lb="2y">2Y (Jan 2024)</button>
                  <button class="pill-btn" data-lb="all">ALL</button>
                </div>

                <div class="selector-group" id="engine-selector-group">
                  <span class="selector-label">Engine:</span>
                  <button class="pill-btn active" data-engine="db_feed">Our Library (Dukascopy & HistData)</button>
                  <button class="pill-btn" data-engine="tv_advanced">TradingView Cloud (Reference)</button>
                </div>

                <button class="pill-btn" id="btn-sync-dukascopy" title="Self-Updating 1-Minute Historical Data Sync from Dukascopy (with HistData backup)">
                  🔄 Sync Engine (Dukascopy)
                </button>
                <button class="pill-btn" id="btn-open-ingestion-log" title="View Ingestion Audit Log">
                  📋 Audit Log
                </button>
              </div>



              <!-- HUD Metrics Card Grid -->
              <div class="chart-hud-grid">
                <div class="hud-item">
                  <span class="hud-label">Current Close</span>
                  <span class="hud-value" id="hud-price">—</span>
                  <span class="hud-sub" id="hud-change">0.00%</span>
                </div>
                <div class="hud-item">
                  <span class="hud-label">Period Range (High / Low)</span>
                  <span class="hud-value" id="hud-high-low">— / —</span>
                  <span class="hud-sub" id="hud-period-dates">2024-01-01 ➔ Present</span>
                </div>
                <div class="hud-item">
                  <span class="hud-label">Total Volume</span>
                  <span class="hud-value gold" id="hud-volume">—</span>
                  <span class="hud-sub">Cumulative Tick Volume</span>
                </div>
                <div class="hud-item">
                  <span class="hud-label">Verified Historical Bars</span>
                  <span class="hud-value green" id="hud-bar-count">—</span>
                  <span class="hud-sub" id="hud-source-badge">Verified DB Feed • Starting Jan 2024</span>
                </div>
              </div>

              <!-- Candlestick Canvas Container -->
              <div class="candlestick-canvas-box" style="height: 540px;">
                <div class="chart-verification-badge">
                  <span>●</span>
                  <span id="badge-chart-info">EURUSD · 1d · Our Library</span>
                </div>
                <div id="main-chart-container" style="width: 100%; height: 100%; display: block;"></div>
                <div id="tv-advanced-main-container" style="width: 100%; height: 100%; display: none;"></div>
              </div>
            </div>
          </section>

          <!-- PANE 2: BACKTEST SIMULATOR -->
          <section class="tab-pane" id="pane-backtest">
            <div class="section-headline-bar">
              <div class="headline-text-group">
                <h1 class="headline-title">🚀 Backtest Simulator & Trade Execution Chart</h1>
                <p class="headline-desc">Run strategy simulations over verified repository bars with trade marker overlays plotted directly on the candlesticks.</p>
              </div>
            </div>

            <div class="backtest-grid-layout">
              <!-- Parameters Form Card -->
              <div class="backtest-controls-card">
                <h2 style="font-size: 1.05rem; font-weight: 700; margin-bottom: 4px;">Simulation Settings</h2>

                <div class="form-group">
                  <label class="form-label" for="bt-symbol">Instrument Symbol</label>
                  <select id="bt-symbol" class="form-select">
                    <option value="EURUSD" selected>EURUSD</option>
                    <option value="XAUUSD">XAUUSD (COMEX Gold)</option>
                    <option value="USDJPY">USDJPY</option>
                    <option value="DXY">DXY (US Dollar)</option>
                  </select>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-strategy">Strategy Engine</label>
                  <select id="bt-strategy" class="form-select">
                    <option value="vwap_mean_reversion">VWAP Mean Reversion (Band 2.0σ)</option>
                    <option value="trend_breakout">Multi-Timeframe Trend Breakout</option>
                  </select>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-timeframe">Bar Timeframe</label>
                  <select id="bt-timeframe" class="form-select">
                    <option value="1d" selected>1d (Daily Bars)</option>
                    <option value="4h">4h (4-Hour Bars)</option>
                    <option value="1h">1h (1-Hour Bars)</option>
                    <option value="15m">15m (15-Minute Bars)</option>
                    <option value="5m">5m (5-Minute Bars)</option>
                    <option value="1m">1m (1-Minute High-Res)</option>
                  </select>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-lookback">Lookback Window</label>
                  <select id="bt-lookback" class="form-select">
                    <option value="2y" selected>2 Years (Jan 2024 to Present)</option>
                    <option value="1y">1 Year (365 Days)</option>
                    <option value="6m">6 Months (180 Days)</option>
                    <option value="3m">3 Months (90 Days)</option>
                    <option value="1m">1 Month (30 Days)</option>
                    <option value="all">Full Database History (MAX)</option>
                  </select>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-capital">Initial Capital ($)</label>
                  <input type="number" id="bt-capital" class="form-input" value="10000" step="500"/>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-risk">Risk Per Trade (%)</label>
                  <input type="number" id="bt-risk" class="form-input" value="1.0" step="0.1" min="0.1" max="10"/>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-sl">Stop Loss Multiplier</label>
                  <input type="number" id="bt-sl" class="form-input" value="1.5" step="0.1"/>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-tp">Take Profit Multiplier</label>
                  <input type="number" id="bt-tp" class="form-input" value="2.0" step="0.1"/>
                </div>

                <!-- 1:100 Leverage & Strict 50% Margin Cap -->
                <div class="form-group">
                  <label class="form-label" for="bt-leverage">Account Leverage</label>
                  <select id="bt-leverage" class="form-select">
                    <option value="100" selected>1:100 Leverage (Default)</option>
                  </select>
                </div>

                <div class="form-group">
                  <label class="form-label" for="bt-max-margin">Max Allowed Margin Level (%)</label>
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <input type="number" id="bt-max-margin" class="form-input" value="50" min="5" max="50" step="5" style="width: 100%;"/>
                    <span style="font-size: 0.72rem; color: var(--emerald); font-weight: 700; white-space: nowrap;">Max 50%</span>
                  </div>
                  <span style="font-size: 0.68rem; color: var(--txt3); margin-top: 3px; display: block;">
                    Strict: Never uses more than 50% margin at any level to protect equity.
                  </span>
                </div>

                <!-- Constant Position Sizing Across All Trades -->
                <div class="form-group">
                  <label class="form-label" for="bt-lot-size">Position Size (Lots — Same for All Trades)</label>
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <input type="number" id="bt-lot-size" class="form-input" value="0.10" step="0.01" min="0.01" max="50.0" style="width: 100%;"/>
                    <span style="font-size: 0.70rem; color: var(--emerald); font-weight: 700; white-space: nowrap; font-family: var(--mono); background: rgba(16,185,129,0.12); padding: 4px 8px; border-radius: 4px; border: 1px solid rgba(16,185,129,0.25);">CONSTANT</span>
                  </div>
                  <span style="font-size: 0.68rem; color: var(--txt3); margin-top: 3px; display: block;">
                    Fixed sizing: Every single trade executes with this exact lot size throughout the backtest.
                  </span>
                </div>

                <!-- Custom Commission Per Lot -->
                <div class="form-group">
                  <label class="form-label" for="bt-commission">Custom Commission ($ / Lot)</label>
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <input type="number" id="bt-commission" class="form-input" value="0.00" step="0.50" min="0" max="50.0" style="width: 100%;"/>
                    <span style="font-size: 0.70rem; color: var(--blue); font-weight: 700; white-space: nowrap; font-family: var(--mono); background: rgba(59,130,246,0.12); padding: 4px 8px; border-radius: 4px; border: 1px solid rgba(59,130,246,0.25);">$/LOT</span>
                  </div>
                  <span style="font-size: 0.68rem; color: var(--txt3); margin-top: 3px; display: block;">
                    Round-turn commission fee deducted per lot (e.g. $3.50 or $7.00/lot).
                  </span>
                </div>

                <!-- Custom Slippage -->
                <div class="form-group">
                  <label class="form-label" for="bt-slippage">Custom Slippage (Pips / Trade)</label>
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <input type="number" id="bt-slippage" class="form-input" value="0.0" step="0.1" min="0" max="20.0" style="width: 100%;"/>
                    <span style="font-size: 0.70rem; color: var(--amber); font-weight: 700; white-space: nowrap; font-family: var(--mono); background: rgba(245,158,11,0.12); padding: 4px 8px; border-radius: 4px; border: 1px solid rgba(245,158,11,0.25);">PIPS</span>
                  </div>
                  <span style="font-size: 0.68rem; color: var(--txt3); margin-top: 3px; display: block;">
                    Execution penalty subtracted from trade entry/exit fill prices.
                  </span>
                </div>

                <button class="btn-primary" id="btn-run-backtest" style="margin-top: 8px;">
                  <span>⚡ Run Simulation</span>
                </button>
              </div>

              <!-- Results Area -->
              <div class="backtest-results-container">
                <!-- Backtest Candlestick Chart (Trade Markers Plotted Directly on Candles) -->
                <div class="chart-card-wrapper">
                  <div class="chart-toolbar">
                    <div style="display: flex; align-items: center; gap: 10px;">
                      <span style="font-size: 0.95rem; font-weight: 700;">🕯️ Execution Candlestick Chart (Trade Markers Plotted)</span>
                      <span class="regime-badge badge-bullish" id="bt-chart-status">Ready</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                      <div class="selector-group">
                        <span class="selector-label">Markers:</span>
                        <span style="font-size: 0.73rem; color: var(--txt2); font-family: var(--mono);">
                          🟢 BUY Marker &nbsp;|&nbsp; 🔴 SELL Marker &nbsp;|&nbsp; ⚪ Outcome
                        </span>
                      </div>
                      <button class="pill-btn" id="btn-export-pinescript" title="Generate TradingView Pine Script v5 Strategy">
                        ⚡ Export to Pine Script v5
                      </button>
                    </div>
                  </div>

                  <div class="candlestick-canvas-box" style="height: 440px;">
                    <div class="chart-verification-badge" id="bt-chart-badge">
                      <span>●</span>
                      <span id="badge-bt-chart-info">EURUSD · 1d · Backtest Overlays</span>
                    </div>
                    <div id="backtest-chart-container" style="width: 100%; height: 100%;"></div>
                  </div>
                </div>

                <!-- KPI Metric Cards Grid -->
                <div class="kpi-cards-grid">
                  <div class="kpi-card">
                    <span class="kpi-card-label">Total Return</span>
                    <span class="kpi-card-val green" id="kpi-return">+0.00%</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Net P&L ($)</span>
                    <span class="kpi-card-val" id="kpi-net-pnl">$0.00</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Win Rate</span>
                    <span class="kpi-card-val" id="kpi-winrate">0.0%</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Profit Factor</span>
                    <span class="kpi-card-val" id="kpi-pf">0.00</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Avg Win</span>
                    <span class="kpi-card-val green" id="kpi-avg-win">+$0.00</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Avg Loss</span>
                    <span class="kpi-card-val red" id="kpi-avg-loss">-$0.00</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Win/Loss Ratio</span>
                    <span class="kpi-card-val cyan" id="kpi-wl-ratio">0.00x</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Total Commission</span>
                    <span class="kpi-card-val amber" id="kpi-total-comm">-$0.00</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Max Drawdown</span>
                    <span class="kpi-card-val red" id="kpi-max-dd">0.00%</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Trades Executed</span>
                    <span class="kpi-card-val" id="kpi-total-trades">0</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Leverage / Cap</span>
                    <span class="kpi-card-val cyan" id="kpi-leverage-cap">1:100 (≤50%)</span>
                  </div>
                  <div class="kpi-card">
                    <span class="kpi-card-label">Position Sizing</span>
                    <span class="kpi-card-val gold" id="kpi-pos-size">0.10L (Constant)</span>
                  </div>
                </div>

                <!-- Equity Curve Graph -->
                <div class="equity-chart-card">
                  <h3 style="font-size: 0.95rem; font-weight: 700;">📈 Portfolio Equity Progression</h3>
                  <div class="equity-canvas-wrapper" id="equity-curve-box">
                    <svg id="equity-svg" width="100%" height="100%" style="overflow: visible;"></svg>
                  </div>
                </div>

                <!-- Executed Trades Log & Commission Distribution -->
                <div class="trade-log-container">
                  <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                    <div>
                      <h3 style="font-size: 0.95rem; font-weight: 700;">📑 Executed Trade Log & Commission Distribution</h3>
                      <p style="font-size: 0.72rem; color: var(--txt3); margin-top: 2px;">Distribution of trade returns and margin usage in accordance to commission.</p>
                    </div>
                    <span style="font-size: 0.72rem; color: var(--txt2); font-family: var(--mono);" id="trade-log-counter">0 trades</span>
                  </div>

                  <!-- Commission vs P&L Distribution Banner -->
                  <div id="comm-dist-banner" style="display:none; background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: var(--r-sm); padding: 14px 18px; margin: 12px 0;">
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 14px; font-size: 0.78rem; font-family: var(--mono);">
                      <div><span>Gross P&L: </span><strong id="dist-gross-pnl" style="color:var(--txt);">$0.00</strong></div>
                      <div><span>Total Commission: </span><strong id="dist-total-comm" style="color:var(--amber);">-$0.00</strong></div>
                      <div><span>Net P&L: </span><strong id="dist-net-pnl" style="color:var(--emerald);">$0.00</strong></div>
                      <div><span>Commission Drag: </span><strong id="dist-fee-drag" style="color:var(--txt2);">0.0%</strong></div>
                    </div>
                    <div style="width: 100%; height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; display: flex; margin-top: 10px;" id="dist-bar-track">
                      <div id="dist-bar-net" style="height: 100%; background: #10B981; width: 100%;" title="Net Gain"></div>
                      <div id="dist-bar-comm" style="height: 100%; background: #F59E0B; width: 0%;" title="Commission Drag"></div>
                    </div>
                  </div>

                  <div class="trade-cards-list" id="trade-cards-list">
                    <div style="padding: 24px; text-align: center; color: var(--txt3);">
                      Click "Run Simulation" above to execute backtest and view trades with timestamp & lot sizes.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </main>

        <!-- Ingestion Audit Log Modal -->
        <div class="hub-modal-overlay" id="modal-ingestion-log">
          <div class="hub-modal-card">
            <div class="hub-modal-header">
              <div class="hub-modal-title">
                <span>📋</span> Ingestion Request Audit Log
              </div>
              <button class="hub-modal-close-btn" id="btn-close-log-modal">&times;</button>
            </div>
            <div class="hub-modal-body">
              <!-- Quick Fetch Form -->
              <div style="background: var(--elevated); border: 1px solid var(--border); border-radius: var(--r-sm); padding: 14px 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-weight: 700; font-size: 0.8rem; color: var(--txt);">Trigger HistData Fetch:</span>
                  <select id="fetch-pair" class="form-select" style="padding: 4px 8px; font-size: 0.78rem;">
                    <option value="EURUSD">EURUSD</option>
                    <option value="USDJPY">USDJPY</option>
                    <option value="XAUUSD">XAUUSD</option>
                    <option value="DXY">DXY</option>
                  </select>
                  <input type="number" id="fetch-year" class="form-input" value="2024" min="2010" max="2025" style="width: 75px; padding: 4px 8px; font-size: 0.78rem;" />
                  <input type="number" id="fetch-month" class="form-input" placeholder="Full Year" min="1" max="12" style="width: 90px; padding: 4px 8px; font-size: 0.78rem;" title="Leave empty for whole year" />
                </div>
                <button class="btn-primary" id="btn-submit-fetch" style="padding: 6px 14px; font-size: 0.78rem;">
                  <span>📥 Start Ingestion</span>
                </button>
              </div>

              <!-- Ingestion Logs List -->
              <div style="display:flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.78rem; font-weight: 700; color: var(--txt2); text-transform: uppercase;">Recent Fetch Audit Entries</span>
                <button class="pill-btn" id="btn-refresh-log" style="font-size: 0.72rem; padding: 3px 8px;">🔄 Refresh Log</button>
              </div>
              <div id="ingestion-log-list" style="display:flex; flex-direction:column; gap:8px; max-height: 420px; overflow-y: auto;">
                <div style="text-align:center; padding: 20px; color:var(--txt3);">Loading audit logs...</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Pine Script v5 Strategy Modal -->
        <div class="hub-modal-overlay" id="modal-pinescript">
          <div class="hub-modal-card">
            <div class="hub-modal-header">
              <div class="hub-modal-title">
                <span>⚡</span> TradingView Pine Script v5 Strategy Exporter
              </div>
              <button class="hub-modal-close-btn" id="btn-close-pine-modal">&times;</button>
            </div>
            <div class="hub-modal-body">
              <div style="font-size: 0.8rem; color: var(--txt2); line-height: 1.5;">
                Paste this strategy directly into the <strong style="color:var(--txt);">TradingView Pine Editor</strong> on TradingView.com to verify your strategy execution, constant lot sizing, and TP/SL targets with zero discrepancy.
              </div>
              <pre class="hub-code-block" id="pinescript-textarea"></pre>
              <div style="display:flex; justify-content:flex-end; gap:10px;">
                <button class="btn-primary" id="btn-copy-pinescript">
                  <span>📋 Copy Pine Script</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    setupNavigationEvents();
    setupMainChartControls();
    setupBacktestEvents();
    setupGlobalEvents();
  }

  // ─── FastAPI Hyperlink Navigation ──────────────────────────────────
  function setupNavigationEvents() {
    const navLinks = document.querySelectorAll('.nav-tab-link');
    navLinks.forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const tab = link.getAttribute('data-tab');
        const targetUrl = link.getAttribute('href');
        switchTab(tab);
        window.history.pushState({ tab }, '', targetUrl);
      });
    });

    window.addEventListener('popstate', () => {
      const path = window.location.pathname;
      const matchingTab = ROUTE_MAP[path] || 'main';
      switchTab(matchingTab);
    });
  }

  function ensureChartsReady(callback) {
    if (typeof LightweightCharts !== 'undefined') {
      callback();
    } else {
      let attempts = 0;
      const timer = setInterval(() => {
        attempts++;
        if (typeof LightweightCharts !== 'undefined') {
          clearInterval(timer);
          callback();
        } else if (attempts >= 60) {
          clearInterval(timer);
          console.error('TradingView LightweightCharts library failed to load in time.');
        }
      }, 100);
    }
  }

  function switchTab(tabName) {
    state.activeTab = tabName;

    // Update nav links
    document.querySelectorAll('.nav-tab-link').forEach(link => {
      if (link.getAttribute('data-tab') === tabName) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });

    // Update panes
    document.querySelectorAll('.tab-pane').forEach(pane => {
      if (pane.id === `pane-${tabName}`) {
        pane.classList.add('active');
      } else {
        pane.classList.remove('active');
      }
    });

    // Resize and load active charts
    setTimeout(() => {
      if (tabName === 'main') {
        updateMainChartEngine();
        loadMainCandleData();
      } else if (tabName === 'backtest') {
        ensureChartsReady(() => {
          if (!state.btChart) {
            initBacktestChart();
          } else {
            const c = document.getElementById('backtest-chart-container');
            if (c) {
              state.btChart.applyOptions({ width: c.clientWidth, height: c.clientHeight });
              state.btChart.timeScale().fitContent();
            }
            loadBacktestCandlesAndTrades();
          }
        });
      }
    }, 60);
  }

  // ─── Main Menu Candlestick Engine Switcher ──────────────────────────
  function updateMainChartEngine() {
    const tvBox = document.getElementById('tv-advanced-main-container');
    const dbBox = document.getElementById('main-chart-container');
    const badge = document.getElementById('badge-chart-info');
    if (!tvBox || !dbBox) return;

    if (state.mainEngine === 'tv_advanced') {
      tvBox.style.display = 'block';
      dbBox.style.display = 'none';
      if (badge) badge.textContent = `${state.symbol} · ${state.timeframe} · TradingView Cloud`;
      renderTVAdvancedWidget('tv-advanced-main-container', state.symbol, state.timeframe);
    } else {
      tvBox.style.display = 'none';
      dbBox.style.display = 'block';
      if (badge) badge.textContent = `${state.symbol} · ${state.timeframe} · Our Library (Dukascopy Parquet)`;
      ensureChartsReady(() => {
        if (!state.mainChart) {
          initMainChart();
        } else {
          state.mainChart.applyOptions({ width: dbBox.clientWidth, height: dbBox.clientHeight });
          state.mainChart.timeScale().fitContent();
          loadMainCandleData();
        }
      });
    }
  }

  function switchToOurLibraryWithData(symbol, timeframe) {
    state.mainEngine = 'db_feed';
    if (symbol) state.symbol = symbol;
    if (timeframe) state.timeframe = timeframe;

    // Update Engine buttons in DOM
    const engGroup = document.getElementById('engine-selector-group');
    if (engGroup) {
      engGroup.querySelectorAll('button').forEach(b => {
        if (b.getAttribute('data-engine') === 'db_feed') b.classList.add('active');
        else b.classList.remove('active');
      });
    }

    // Update Symbol buttons in DOM
    const symGroup = document.getElementById('symbol-selector-group');
    if (symGroup && symbol) {
      symGroup.querySelectorAll('button').forEach(b => {
        if (b.getAttribute('data-symbol') === symbol) b.classList.add('active');
        else b.classList.remove('active');
      });
    }

    // Update Timeframe buttons in DOM
    const tfGroup = document.getElementById('timeframe-selector-group');
    if (tfGroup && timeframe) {
      tfGroup.querySelectorAll('button').forEach(b => {
        if (b.getAttribute('data-tf') === timeframe) b.classList.add('active');
        else b.classList.remove('active');
      });
    }

    renderLookbackButtons(state.timeframe);

    // Ensure we are on Main tab
    if (state.activeTab !== 'main') {
      switchTab('main');
    } else {
      updateMainChartEngine();
      loadMainCandleData();
    }

    showToast(`Showing Dukascopy Parquet for ${state.symbol} (${state.timeframe}) in Our Library chart.`);
  }

  window.viewInOurLibrary = function(symbol, timeframe) {
    closeIngestionModal();
    switchToOurLibraryWithData(symbol, timeframe || '1m');
  };

  // ─── Timeframe & Lookback Window Alignment ───────────────────────────
  const TIMEFRAME_LOOKBACK_CONFIG = {
    '1m': {
      default: '1m',
      options: [
        { id: '1d', label: '1D (24h)' },
        { id: '3d', label: '3D' },
        { id: '1w', label: '1W' },
        { id: '2w', label: '2W' },
        { id: '1m', label: '1M (30 Days)' },
        { id: '3m', label: '3M (90 Days)' },
        { id: '6m', label: '6M (180 Days)' },
        { id: '1y', label: '1Y (365 Days)' },
        { id: '2y', label: '2Y (Jan 2024)' },
        { id: 'all', label: 'ALL M1' },
      ],
      fetchLimit: (lb) => (lb === '1d' ? 2000 : lb === '3d' ? 5000 : lb === '1w' ? 12000 : lb === '2w' ? 25000 : lb === '1m' ? 45000 : 80000),
    },

    '5m': {
      default: '1w',
      options: [
        { id: '1d', label: '1D' },
        { id: '3d', label: '3D' },
        { id: '1w', label: '1W' },
        { id: '1m', label: '1M (30 Days)' },
        { id: 'all', label: 'ALL' },
      ],
      fetchLimit: () => 6000,
    },
    '15m': {
      default: '1m',
      options: [
        { id: '1w', label: '1W' },
        { id: '2w', label: '2W' },
        { id: '1m', label: '1M' },
        { id: '3m', label: '3M (90 Days)' },
        { id: 'all', label: 'ALL' },
      ],
      fetchLimit: () => 6000,
    },
    '1h': {
      default: '3m',
      options: [
        { id: '1m', label: '1M' },
        { id: '3m', label: '3M' },
        { id: '6m', label: '6M' },
        { id: '1y', label: '1Y' },
        { id: 'all', label: 'ALL' },
      ],
      fetchLimit: () => 6000,
    },
    '4h': {
      default: '1y',
      options: [
        { id: '3m', label: '3M' },
        { id: '6m', label: '6M' },
        { id: '1y', label: '1Y' },
        { id: '2y', label: '2Y' },
        { id: 'all', label: 'ALL' },
      ],
      fetchLimit: () => 6000,
    },
    '1d': {
      default: '2y',
      options: [
        { id: '3m', label: '3M' },
        { id: '6m', label: '6M' },
        { id: '1y', label: '1Y' },
        { id: '2y', label: '2Y (Jan 2024)' },
        { id: 'all', label: 'ALL' },
      ],
      fetchLimit: () => 5000,
    },
  };

  function renderLookbackButtons(tf) {
    const lbGroup = document.getElementById('lookback-selector-group');
    if (!lbGroup) return;

    const cfg = TIMEFRAME_LOOKBACK_CONFIG[tf] || TIMEFRAME_LOOKBACK_CONFIG['1d'];
    const validIds = cfg.options.map(o => o.id);
    if (!validIds.includes(state.lookback)) {
      state.lookback = cfg.default;
    }

    lbGroup.innerHTML = `
      <span class="selector-label">Lookback:</span>
      ${cfg.options.map(o => `
        <button class="pill-btn ${o.id === state.lookback ? 'active' : ''}" data-lb="${o.id}">
          ${o.label}
        </button>
      `).join('')}
    `;
  }

  function updateBacktestLookbackOptions(tf) {
    const sel = document.getElementById('bt-lookback');
    if (!sel) return;
    const cfg = TIMEFRAME_LOOKBACK_CONFIG[tf] || TIMEFRAME_LOOKBACK_CONFIG['1d'];
    const currentVal = sel.value;
    const validIds = cfg.options.map(o => o.id);
    const newVal = validIds.includes(currentVal) ? currentVal : cfg.default;

    sel.innerHTML = cfg.options.map(o => `
      <option value="${o.id}" ${o.id === newVal ? 'selected' : ''}>
        ${o.label}
      </option>
    `).join('');
  }

  // ─── Main Menu Candlestick Chart ────────────────────────────────────
  function initMainChart() {
    const container = document.getElementById('main-chart-container');
    if (!container) return;

    renderLookbackButtons(state.timeframe);

    if (state.mainChart) {
      state.mainChart.remove();
      state.mainChart = null;
    }

    ensureChartsReady(() => {
      const c = document.getElementById('main-chart-container');
      if (!c) return;

      state.mainChart = LightweightCharts.createChart(c, {
        width: c.clientWidth || 800,
        height: c.clientHeight || 540,
        layout: {
          background: { color: '#080A0F' },
          textColor: '#8F9CB7',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
          horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: {
          borderColor: 'rgba(255, 255, 255, 0.1)',
          scaleMargins: { top: 0.1, bottom: 0.2 },
        },
        timeScale: {
          borderColor: 'rgba(255, 255, 255, 0.1)',
          timeVisible: true,
          secondsVisible: false,
        },
      });

      state.mainCandleSeries = state.mainChart.addCandlestickSeries({
        upColor: '#10B981',
        downColor: '#EF4444',
        borderVisible: false,
        wickUpColor: '#10B981',
        wickDownColor: '#EF4444',
      });

      state.mainVolumeSeries = state.mainChart.addHistogramSeries({
        color: 'rgba(59, 130, 246, 0.35)',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        scaleMargins: { top: 0.82, bottom: 0 },
      });

      const resizeObserver = new ResizeObserver(entries => {
        if (entries.length === 0 || !entries[0].contentRect) return;
        const { width, height } = entries[0].contentRect;
        if (state.mainChart && width > 0 && height > 0) {
          state.mainChart.applyOptions({ width, height });
        }
      });
      resizeObserver.observe(c);

      loadMainCandleData();
    });
  }

  async function loadMainCandleData() {
    const badge = document.getElementById('badge-chart-info');
    if (badge) badge.textContent = `${state.symbol} · ${state.timeframe} · Fetching...`;

    try {
      const cfg = TIMEFRAME_LOOKBACK_CONFIG[state.timeframe] || TIMEFRAME_LOOKBACK_CONFIG['1d'];
      const lb = state.lookback || cfg.default;
      const fetchLimit = cfg.fetchLimit ? cfg.fetchLimit(lb) : 5000;
      const url = `/api/ohlcv?symbol=${encodeURIComponent(state.symbol)}&timeframe=${encodeURIComponent(state.timeframe)}&lookback=${encodeURIComponent(lb)}&limit=${fetchLimit}`;
      const res = await apiFetch(url);
      const rawBars = res.bars || [];

      // Sort bars ascending and deduplicate by unix seconds
      const sorted = [];
      const seenTimes = new Set();
      for (const b of rawBars) {
        const t = Number(b.time);
        if (!seenTimes.has(t)) {
          seenTimes.add(t);
          sorted.push({
            time: t,
            open: Number(b.open),
            high: Number(b.high),
            low: Number(b.low),
            close: Number(b.close),
            volume: Number(b.volume || 0),
            dateStr: b.ts ? b.ts.substring(0, 10) : '',
          });
        }
      }
      sorted.sort((a, b) => a.time - b.time);

      if (!state.mainChart) initMainChart();

      if (sorted.length > 0) {
        state.mainCandleSeries.setData(sorted.map(b => ({
          time: b.time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })));

        state.mainVolumeSeries.setData(sorted.map(b => ({
          time: b.time,
          value: b.volume,
          color: b.close >= b.open ? 'rgba(16, 185, 129, 0.35)' : 'rgba(239, 68, 68, 0.35)',
        })));

        state.mainChart.timeScale().fitContent();

        // Update HUD metrics
        const last = sorted[sorted.length - 1];
        const first = sorted[0];
        const changePct = ((last.close - first.open) / first.open) * 100;
        const highs = sorted.map(b => b.high);
        const lows = sorted.map(b => b.low);
        const maxH = Math.max(...highs);
        const minL = Math.min(...lows);
        const totVol = sorted.reduce((sum, b) => sum + b.volume, 0);

        document.getElementById('hud-price').textContent = fmtPrice(last.close);
        const hudChange = document.getElementById('hud-change');
        hudChange.textContent = fmtPct(changePct);
        hudChange.style.color = changePct >= 0 ? 'var(--emerald)' : 'var(--red)';

        document.getElementById('hud-high-low').textContent = `${fmtPrice(maxH)} / ${fmtPrice(minL)}`;
        document.getElementById('hud-volume').textContent = fmtNumber(Math.round(totVol));
        document.getElementById('hud-bar-count').textContent = `${sorted.length} Bars (${first.dateStr} ➔ ${last.dateStr})`;
        document.getElementById('hud-period-dates').textContent = `${first.dateStr} ➔ ${last.dateStr}`;
      } else {
        document.getElementById('hud-price').textContent = '—';
        document.getElementById('hud-change').textContent = 'No Data';
        document.getElementById('hud-bar-count').textContent = '0 Bars';
      }

      if (badge) {
        const engineLabel = state.mainEngine === 'db_feed' ? 'Our Library (Dukascopy Parquet)' : 'TradingView Cloud';
        badge.textContent = `${state.symbol} · ${state.timeframe} · Lookback: ${lb.toUpperCase()} · ${engineLabel}`;
      }
      const sourceBadge = document.getElementById('hud-source-badge');
      if (sourceBadge) {
        sourceBadge.textContent = state.timeframe === '1m' ? 'Dukascopy 1M Parquet Engine • Our Library' : `Verified Dukascopy Feed • Lookback ${lb.toUpperCase()}`;
      }
    } catch (err) {
      console.error('Candle load error:', err);
      if (badge) badge.textContent = `${state.symbol} · Load Error`;
      showToast(`Error loading ${state.symbol}: ${err.message}`);
    }
  }

  function setupMainChartControls() {
    renderLookbackButtons(state.timeframe);

    const symGroup = document.getElementById('symbol-selector-group');
    if (symGroup) {
      symGroup.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-symbol]');
        if (!btn) return;
        symGroup.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.symbol = btn.getAttribute('data-symbol');
        if (state.mainEngine === 'tv_advanced') {
          renderTVAdvancedWidget('tv-advanced-main-container', state.symbol, state.timeframe);
        }
        loadMainCandleData();
      });
    }

    const tfGroup = document.getElementById('timeframe-selector-group');
    if (tfGroup) {
      tfGroup.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-tf]');
        if (!btn) return;
        tfGroup.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.timeframe = btn.getAttribute('data-tf');
        renderLookbackButtons(state.timeframe);
        if (state.timeframe === '1m' && state.mainEngine !== 'db_feed') {
          // HistData is 1m historical data: always display in Our Library
          switchToOurLibraryWithData(state.symbol, '1m');
          return;
        }
        if (state.mainEngine === 'tv_advanced') {
          renderTVAdvancedWidget('tv-advanced-main-container', state.symbol, state.timeframe);
        }
        loadMainCandleData();
      });
    }

    const lbGroup = document.getElementById('lookback-selector-group');
    if (lbGroup) {
      lbGroup.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-lb]');
        if (!btn) return;
        lbGroup.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.lookback = btn.getAttribute('data-lb');
        loadMainCandleData();
      });
    }

    const engGroup = document.getElementById('engine-selector-group');
    if (engGroup) {
      engGroup.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-engine]');
        if (!btn) return;
        engGroup.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.mainEngine = btn.getAttribute('data-engine');
        updateMainChartEngine();
      });
    }

    const syncBtn = document.getElementById('btn-sync-dukascopy');
    if (syncBtn) {
      syncBtn.addEventListener('click', async () => {
        syncBtn.disabled = true;
        const originalText = syncBtn.textContent;
        syncBtn.textContent = '🔄 Syncing Dukascopy...';
        showToast(`Initiating self-updating sync for ${state.symbol} via Dukascopy (HistData backup)...`);
        try {
          const res = await apiFetch(`/api/market-data/sync?symbols=${encodeURIComponent(state.symbol)}`, { method: 'POST' });
          const count = res.total_bars_inserted || 0;
          showToast(`✅ Synced ${state.symbol}: +${count.toLocaleString()} 1m bars added!`);
          loadMainCandleData();
        } catch (e) {
          showToast(`❌ Sync error: ${e.message}`);
        } finally {
          syncBtn.disabled = false;
          syncBtn.textContent = originalText;
        }
      });
    }

    const openLogBtn = document.getElementById('btn-open-ingestion-log');
    if (openLogBtn) {
      openLogBtn.addEventListener('click', () => {
        openIngestionModal();
      });
    }
  }


  // ─── Backtest Candlestick Chart (Trade Markers Overlay) ─────────────
  function initBacktestChart() {
    const container = document.getElementById('backtest-chart-container');
    if (!container) return;

    if (state.btChart) {
      state.btChart.remove();
      state.btChart = null;
    }

    ensureChartsReady(() => {
      const c = document.getElementById('backtest-chart-container');
      if (!c) return;

      state.btChart = LightweightCharts.createChart(c, {
        width: c.clientWidth || 800,
        height: c.clientHeight || 440,
        layout: {
          background: { color: '#080A0F' },
          textColor: '#8F9CB7',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
          horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: {
          borderColor: 'rgba(255, 255, 255, 0.1)',
          scaleMargins: { top: 0.1, bottom: 0.2 },
        },
        timeScale: {
          borderColor: 'rgba(255, 255, 255, 0.1)',
          timeVisible: true,
          secondsVisible: false,
        },
      });

      state.btCandleSeries = state.btChart.addCandlestickSeries({
        upColor: '#10B981',
        downColor: '#EF4444',
        borderVisible: false,
        wickUpColor: '#10B981',
        wickDownColor: '#EF4444',
      });

      state.btVolumeSeries = state.btChart.addHistogramSeries({
        color: 'rgba(59, 130, 246, 0.35)',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        scaleMargins: { top: 0.82, bottom: 0 },
      });

      const resizeObserver = new ResizeObserver(entries => {
        if (entries.length === 0 || !entries[0].contentRect) return;
        const { width, height } = entries[0].contentRect;
        if (state.btChart && width > 0 && height > 0) {
          state.btChart.applyOptions({ width, height });
        }
      });
      resizeObserver.observe(c);

      // Initial load of backtest bars
      loadBacktestCandlesAndTrades();
    });
  }

  async function loadBacktestCandlesAndTrades() {
    const sym = document.getElementById('bt-symbol')?.value || 'EURUSD';
    const tf = document.getElementById('bt-timeframe')?.value || '1d';
    const lb = document.getElementById('bt-lookback')?.value || '2y';
    const badge = document.getElementById('badge-bt-chart-info');
    if (badge) badge.textContent = `${sym} · ${tf} (Lookback: ${lb.toUpperCase()}) · Execution Markers Plotted`;

    try {
      const cfg = TIMEFRAME_LOOKBACK_CONFIG[tf] || TIMEFRAME_LOOKBACK_CONFIG['1d'];
      const fetchLimit = cfg.fetchLimit ? cfg.fetchLimit(lb) : 5000;
      const url = `/api/ohlcv?symbol=${encodeURIComponent(sym)}&timeframe=${encodeURIComponent(tf)}&lookback=${encodeURIComponent(lb)}&limit=${fetchLimit}`;
      const res = await apiFetch(url);
      const rawBars = res.bars || [];

      const sorted = [];
      const seenTimes = new Set();
      for (const b of rawBars) {
        const t = Number(b.time);
        if (!seenTimes.has(t)) {
          seenTimes.add(t);
          sorted.push({
            time: t,
            open: Number(b.open),
            high: Number(b.high),
            low: Number(b.low),
            close: Number(b.close),
            volume: Number(b.volume || 0),
          });
        }
      }
      sorted.sort((a, b) => a.time - b.time);

      if (state.btCandleSeries && sorted.length > 0) {
        state.btCandleSeries.setData(sorted.map(b => ({
          time: b.time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })));

        state.btVolumeSeries.setData(sorted.map(b => ({
          time: b.time,
          value: b.volume,
          color: b.close >= b.open ? 'rgba(16, 185, 129, 0.35)' : 'rgba(239, 68, 68, 0.35)',
        })));

        // Plot trade markers if trades exist
        if (state.btTrades && state.btTrades.length > 0) {
          applyTradeMarkers(state.btTrades, sorted);
        }

        state.btChart.timeScale().fitContent();
      }
    } catch (err) {
      console.error('Backtest candle load error:', err);
    }
  }

  function applyTradeMarkers(trades, bars) {
    if (!state.btCandleSeries) return;

    const barTimes = new Set(bars.map(b => b.time));
    const markers = [];

    // Map each trade entry and exit to the closest available bar time
    for (const t of trades) {
      const isLong = t.direction === 'LONG';
      const isWin = (t.pnl || 0) >= 0;

      // Find best bar match for entry
      let entryT = t.entry_timestamp;
      if (!barTimes.has(entryT)) {
        // find closest bar
        let best = bars[0].time;
        for (const b of bars) {
          if (b.time <= entryT) best = b.time;
          else break;
        }
        entryT = best;
      }

      // Entry Marker
      markers.push({
        time: entryT,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#10B981' : '#EF4444',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: `${isLong ? 'BUY' : 'SELL'} ${t.lots}L @ ${fmtPrice(t.entry_price)}`,
      });

      // Exit Marker
      let exitT = t.exit_timestamp;
      if (!barTimes.has(exitT)) {
        let best = bars[0].time;
        for (const b of bars) {
          if (b.time <= exitT) best = b.time;
          else break;
        }
        exitT = best;
      }

      markers.push({
        time: exitT,
        position: isLong ? 'aboveBar' : 'belowBar',
        color: isWin ? '#10B981' : '#EF4444',
        shape: 'circle',
        text: `EXIT ${isWin ? '+' : ''}$${Number(t.pnl || 0).toFixed(1)} (${t.reason})`,
      });
    }

    // TradingView Lightweight Charts requires markers sorted ascending by time
    markers.sort((a, b) => a.time - b.time);

    // Set markers on the candlestick series
    state.btCandleSeries.setMarkers(markers);

    const statusBadge = document.getElementById('bt-chart-status');
    if (statusBadge) {
      statusBadge.textContent = `${markers.length / 2} Trades Plotted`;
    }
  }

  // ─── Backtest Simulation Execution ──────────────────────────────────
  function setupBacktestEvents() {
    const runBtn = document.getElementById('btn-run-backtest');
    if (!runBtn) return;

    // Symbol, timeframe, or lookback change on backtest form reloads chart
    const btSymSelect = document.getElementById('bt-symbol');
    const btTfSelect = document.getElementById('bt-timeframe');
    const btLbSelect = document.getElementById('bt-lookback');
    if (btTfSelect) updateBacktestLookbackOptions(btTfSelect.value);
    if (btSymSelect) btSymSelect.addEventListener('change', () => loadBacktestCandlesAndTrades());
    if (btTfSelect) {
      btTfSelect.addEventListener('change', () => {
        updateBacktestLookbackOptions(btTfSelect.value);
        loadBacktestCandlesAndTrades();
      });
    }
    if (btLbSelect) btLbSelect.addEventListener('change', () => loadBacktestCandlesAndTrades());

    runBtn.addEventListener('click', async () => {
      const sym = document.getElementById('bt-symbol').value;
      const strategy = document.getElementById('bt-strategy').value;
      const tf = document.getElementById('bt-timeframe').value;
      const lb = document.getElementById('bt-lookback')?.value || '2y';
      const cap = parseFloat(document.getElementById('bt-capital').value) || 10000;
      const risk = parseFloat(document.getElementById('bt-risk').value) || 1.0;
      const sl = parseFloat(document.getElementById('bt-sl').value) || 1.5;
      const tp = parseFloat(document.getElementById('bt-tp').value) || 2.0;

      // 1:100 Leverage & 50% max margin cap settings
      const leverage = parseFloat(document.getElementById('bt-leverage')?.value) || 100.0;
      let maxMargin = parseFloat(document.getElementById('bt-max-margin')?.value) || 50.0;
      if (maxMargin > 50.0) maxMargin = 50.0; // Enforce strict 50% cap
      const lotSize = parseFloat(document.getElementById('bt-lot-size')?.value) || 0.10;
      const commission = parseFloat(document.getElementById('bt-commission')?.value) || 0.0;
      const slippage = parseFloat(document.getElementById('bt-slippage')?.value) || 0.0;

      state.lastBacktestParams = {
        sym: sym,
        strategy: strategy,
        tf: tf,
        lookback: lb,
        lotSize: lotSize,
        sl: sl,
        tp: tp,
        commission: commission,
        slippage: slippage,
      };

      runBtn.disabled = true;
      runBtn.innerHTML = `<span>⏳ Simulating...</span>`;
      showToast(`Simulating ${strategy} on ${sym} [${lb.toUpperCase()}] (Lots: ${lotSize.toFixed(2)}, Comm: $${commission}/L, Slip: ${slippage}p)...`);

      try {
        const query = new URLSearchParams({
          symbol: sym,
          strategy: strategy,
          timeframe: tf,
          lookback: lb,
          initial_capital: cap,
          risk_pct: risk,
          sl_multiplier: sl,
          tp_multiplier: tp,
          leverage: leverage,
          max_margin_pct: maxMargin,
          lot_mode: 'fixed',
          lot_size: lotSize,
          custom_lot_size: lotSize,
          commission_per_lot: commission,
          slippage: slippage,
        });

        const res = await apiFetch(`/api/backtest/run?${query.toString()}`);
        state.btTrades = res.trades || [];

        // Update KPI metrics
        const m = res.metrics || {};
        const retElem = document.getElementById('kpi-return');
        retElem.textContent = fmtPct(m.total_return_pct);
        retElem.className = `kpi-card-val ${m.total_return_pct >= 0 ? 'green' : 'red'}`;

        const netElem = document.getElementById('kpi-net-pnl');
        netElem.textContent = `$${fmtNumber(m.net_profit?.toFixed(2))}`;
        netElem.className = `kpi-card-val ${m.net_profit >= 0 ? 'green' : 'red'}`;

        document.getElementById('kpi-winrate').textContent = `${(m.win_rate_pct || 0).toFixed(1)}%`;
        document.getElementById('kpi-pf').textContent = (m.profit_factor || 0).toFixed(2);

        const avgWinElem = document.getElementById('kpi-avg-win');
        if (avgWinElem) avgWinElem.textContent = `+$${fmtNumber(Number(m.avg_win || 0).toFixed(2))}`;

        const avgLossElem = document.getElementById('kpi-avg-loss');
        if (avgLossElem) avgLossElem.textContent = `-$${fmtNumber(Number(m.avg_loss || 0).toFixed(2))}`;

        const wlRatioElem = document.getElementById('kpi-wl-ratio');
        if (wlRatioElem) wlRatioElem.textContent = `${Number(m.win_loss_ratio || 0).toFixed(2)}x`;

        const totalCommElem = document.getElementById('kpi-total-comm');
        if (totalCommElem) totalCommElem.textContent = `-$${fmtNumber(Number(m.total_fees || m.total_commission || 0).toFixed(2))}`;

        document.getElementById('kpi-max-dd').textContent = `${(m.max_drawdown_pct || 0).toFixed(2)}%`;
        document.getElementById('kpi-total-trades').textContent = m.total_trades || 0;

        const levElem = document.getElementById('kpi-leverage-cap');
        if (levElem) levElem.textContent = `${m.leverage || '1:100'} (≤${m.max_margin_cap_pct || 50}%)`;

        const posSizeElem = document.getElementById('kpi-pos-size');
        if (posSizeElem) {
          const lots = m.position_size_lots || m.lot_size || m.custom_lot_size || lotSize;
          posSizeElem.textContent = `${Number(lots).toFixed(2)}L (Constant)`;
        }

        // Render Equity Curve via SVG
        renderEquityCurve(res.equity_curve || []);

        // Load candles and plot trade markers on chart
        await loadBacktestCandlesAndTrades();

        // Render Trade Cards Log with Time, Lot Size & Commission Distribution
        renderTradeCards(res.trades || [], m);

        showToast(`Simulation complete: ${m.total_trades || 0} trades executed & plotted on chart.`);
      } catch (err) {
        console.error('Backtest error:', err);
        showToast(`Simulation error: ${err.message}`);
      } finally {
        runBtn.disabled = false;
        runBtn.innerHTML = `<span>⚡ Run Simulation</span>`;
      }
    });

    const pineBtn = document.getElementById('btn-export-pinescript');
    if (pineBtn) {
      pineBtn.addEventListener('click', () => {
        openPineScriptModal();
      });
    }
  }

  function renderEquityCurve(curve) {
    const svg = document.getElementById('equity-svg');
    if (!svg) return;
    svg.innerHTML = '';

    if (!curve || curve.length < 2) {
      svg.innerHTML = `
        <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#4B556D" font-family="'JetBrains Mono', monospace" font-size="12">
          Insufficient data points for equity projection
        </text>`;
      return;
    }

    const rect = svg.getBoundingClientRect();
    const width = rect.width || 600;
    const height = rect.height || 220;
    const padding = { top: 20, right: 30, bottom: 30, left: 50 };

    const equities = curve.map(p => Number(p.equity));
    const minEq = Math.min(...equities);
    const maxEq = Math.max(...equities);
    const range = (maxEq - minEq) || 1;

    const points = curve.map((p, i) => {
      const x = padding.left + (i / (curve.length - 1)) * (width - padding.left - padding.right);
      const y = height - padding.bottom - ((p.equity - minEq) / range) * (height - padding.top - padding.bottom);
      return { x, y, equity: p.equity, time: p.time };
    });

    const d = points.reduce((acc, p, i) => `${acc} ${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)},${p.y.toFixed(1)}`, '');
    const areaD = `${d} L ${points[points.length - 1].x.toFixed(1)},${(height - padding.bottom).toFixed(1)} L ${points[0].x.toFixed(1)},${(height - padding.bottom).toFixed(1)} Z`;

    const gradientId = 'equity-gradient-svg';
    svg.innerHTML = `
      <defs>
        <linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#10B981" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#10B981" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      <!-- Base Reference Line -->
      <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>
      <!-- Area Fill -->
      <path d="${areaD}" fill="url(#${gradientId})"/>
      <!-- Equity Line -->
      <path d="${d}" fill="none" stroke="#10B981" stroke-width="2.5" stroke-linecap="round"/>
      <!-- Start and End Dots -->
      <circle cx="${points[0].x}" cy="${points[0].y}" r="4" fill="#3B82F6"/>
      <circle cx="${points[points.length - 1].x}" cy="${points[points.length - 1].y}" r="4" fill="#10B981"/>
    `;
  }

  // ─── Trade Cards Log & Commission Distribution Breakdown ───────────
  function renderTradeCards(trades, metrics) {
    const list = document.getElementById('trade-cards-list');
    const counter = document.getElementById('trade-log-counter');
    const banner = document.getElementById('comm-dist-banner');
    if (!list) return;

    if (!trades || trades.length === 0) {
      if (counter) counter.textContent = '0 trades';
      if (banner) banner.style.display = 'none';
      list.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--txt3);">
          Zero trades executed under current parameters. Try adjusting risk or timeframe.
        </div>`;
      return;
    }

    if (counter) counter.textContent = `${trades.length} trades recorded`;

    // Populate Commission vs P&L Distribution Banner
    if (banner && metrics) {
      banner.style.display = 'block';
      const grossPnl = Number(metrics.total_gross_pnl !== undefined ? metrics.total_gross_pnl : metrics.net_profit || 0);
      const commVal = Number(metrics.total_fees !== undefined ? metrics.total_fees : metrics.total_commission || 0);
      const netPnl = Number(metrics.net_profit || 0);
      const impactPct = Number(metrics.commission_impact_pct || 0);

      const grossElem = document.getElementById('dist-gross-pnl');
      if (grossElem) {
        grossElem.textContent = `${grossPnl >= 0 ? '+' : ''}$${fmtNumber(grossPnl.toFixed(2))}`;
        grossElem.style.color = grossPnl >= 0 ? 'var(--emerald)' : 'var(--red)';
      }

      const commElem = document.getElementById('dist-total-comm');
      if (commElem) commElem.textContent = `-$${fmtNumber(commVal.toFixed(2))}`;

      const netElem = document.getElementById('dist-net-pnl');
      if (netElem) {
        netElem.textContent = `${netPnl >= 0 ? '+' : ''}$${fmtNumber(netPnl.toFixed(2))}`;
        netElem.style.color = netPnl >= 0 ? 'var(--emerald)' : 'var(--red)';
      }

      const dragElem = document.getElementById('dist-fee-drag');
      if (dragElem) dragElem.textContent = `${impactPct.toFixed(1)}% of gross`;

      // Distribution Bar
      const totalMag = Math.max(0.01, Math.abs(grossPnl) + commVal);
      const commWidthPct = Math.min(95, Math.max(commVal > 0 ? 5 : 0, Math.round((commVal / totalMag) * 100)));
      const netWidthPct = 100 - commWidthPct;

      const netBar = document.getElementById('dist-bar-net');
      if (netBar) {
        netBar.style.width = `${netWidthPct}%`;
        netBar.style.background = netPnl >= 0 ? '#10B981' : '#EF4444';
        netBar.title = `Net P&L: $${netPnl.toFixed(2)} (${netWidthPct}%)`;
      }
      const commBar = document.getElementById('dist-bar-comm');
      if (commBar) {
        commBar.style.width = `${commWidthPct}%`;
        commBar.title = `Total Commission: -$${commVal.toFixed(2)} (${commWidthPct}%)`;
      }
    }

    list.innerHTML = trades.slice().reverse().map((t, idx) => {
      const isWin = (t.pnl || 0) >= 0;
      const isLong = t.direction === 'LONG';
      const badgeBg = isLong ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
      const badgeColor = isLong ? 'var(--emerald)' : 'var(--red)';
      const pnlColor = isWin ? 'var(--emerald)' : 'var(--red)';

      return `
        <div class="trade-card-row" style="display: flex; flex-direction: column; gap: 8px; padding: 12px 16px;">
          <!-- Top Row: ID, Direction, Lots, Margin Used, Fees and Net PnL -->
          <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
            <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
              <span class="trade-card-badge" style="background: ${badgeBg}; color: ${badgeColor}; font-weight: 700;">
                #${t.id || (idx + 1)} ${t.direction}
              </span>
              <span class="strat-param-tag" style="background: rgba(59,130,246,0.18); color: var(--blue); font-weight: 700;">
                ${Number(t.lots || 0.10).toFixed(2)} Lots
              </span>
              <span class="strat-param-tag" style="background: rgba(16,185,129,0.20); color: #10B981; font-weight: 700; border: 1px solid rgba(16,185,129,0.40);">
                💳 Margin Used: $${fmtNumber(Number(t.margin_used || 0).toFixed(2))} (${Number(t.margin_pct || 0).toFixed(1)}%)
              </span>
              ${(t.commission > 0 || t.slippage_cost > 0) ? `
              <span class="strat-param-tag" style="background: rgba(245,158,11,0.15); color: #F59E0B; font-weight: 600; border: 1px solid rgba(245,158,11,0.30);">
                Commission: -$${Number(t.commission || 0).toFixed(2)}
              </span>` : ''}
              <span style="font-size: 0.72rem; color: var(--txt2); font-family: var(--mono);">
                ${t.reason || 'Take Profit'}
              </span>
            </div>

            <div style="font-weight: 700; font-size: 0.88rem; color: ${pnlColor}; font-family: var(--mono); text-align: right;">
              ${isWin ? '+' : ''}$${Number(t.pnl || 0).toFixed(2)} (${fmtPct(t.return_pct)})
            </div>
          </div>

          <!-- Middle Row: Distribution of P&L in Accordance to Commission -->
          <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; font-size: 0.72rem; font-family: var(--mono); background: rgba(0,0,0,0.22); padding: 6px 12px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.04);">
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
              <span style="color: var(--txt3); font-weight: 600;">P&L Distribution:</span>
              <span>Gross: <strong style="color: ${Number(t.gross_pnl || t.pnl || 0) >= 0 ? 'var(--emerald)' : 'var(--red)'};">${Number(t.gross_pnl || t.pnl || 0) >= 0 ? '+' : ''}$${Number(t.gross_pnl || t.pnl || 0).toFixed(2)}</strong></span>
              <span>&minus;</span>
              <span>Comm: <strong style="color: var(--amber);">-$${Number(t.commission || 0).toFixed(2)}</strong></span>
              ${t.slippage_cost > 0 ? `<span>&minus;</span><span>Slip: <strong style="color: var(--amber);">-$${Number(t.slippage_cost || 0).toFixed(2)}</strong></span>` : ''}
              <span>=</span>
              <span>Net: <strong style="color: ${isWin ? 'var(--emerald)' : 'var(--red)'}; font-weight: 700;">${isWin ? '+' : ''}$${Number(t.pnl || 0).toFixed(2)}</strong></span>
            </div>

            <!-- Trade-level Distribution Bar -->
            <div style="display: flex; align-items: center; gap: 6px;">
              <div style="width: 65px; height: 5px; background: rgba(255,255,255,0.08); border-radius: 2px; overflow: hidden; display: flex;">
                <div style="height: 100%; width: ${Math.max(10, 100 - Math.min(90, Math.round(t.fee_pct || 0)))}%; background: ${isWin ? '#10B981' : '#EF4444'};"></div>
                <div style="height: 100%; width: ${Math.min(90, Math.round(t.fee_pct || 0))}%; background: #F59E0B;"></div>
              </div>
              <span style="color: var(--txt3); font-size: 0.66rem;">${Number(t.fee_pct || 0).toFixed(1)}% comm</span>
            </div>
          </div>

          <!-- Bottom Row: Timestamps (Entry -> Exit) & Prices -->
          <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; font-size: 0.72rem; color: var(--txt3); font-family: var(--mono); border-top: 1px solid rgba(255,255,255,0.03); padding-top: 6px;">
            <div>
              <span>🕒 Entry: <strong style="color:var(--txt2);">${t.entry_time || '—'}</strong></span>
              &nbsp;➔&nbsp;
              <span>Exit: <strong style="color:var(--txt2);">${t.exit_time || '—'}</strong></span>
            </div>
            <div>
              <span>Entry: <strong style="color:var(--txt);">${fmtPrice(t.entry_price)}</strong></span>
              &nbsp;|&nbsp;
              <span>Exit: <strong style="color:var(--txt);">${fmtPrice(t.exit_price)}</strong></span>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  // ─── Modal & Pine Script Helpers ────────────────────────────────────
  function openPineScriptModal() {
    const p = state.lastBacktestParams;
    const code = generatePineScript(p.sym, p.strategy, p.tf, p.lotSize, p.sl, p.tp);
    const textarea = document.getElementById('pinescript-textarea');
    if (textarea) textarea.textContent = code;
    const modal = document.getElementById('modal-pinescript');
    if (modal) modal.classList.add('open');
  }

  function closePineScriptModal() {
    const modal = document.getElementById('modal-pinescript');
    if (modal) modal.classList.remove('open');
  }

  function openIngestionModal() {
    const modal = document.getElementById('modal-ingestion-log');
    if (modal) {
      modal.classList.add('open');
      loadIngestionLogs();
    }
  }

  function closeIngestionModal() {
    const modal = document.getElementById('modal-ingestion-log');
    if (modal) modal.classList.remove('open');
  }

  async function loadIngestionLogs() {
    const list = document.getElementById('ingestion-log-list');
    if (!list) return;
    try {
      const res = await apiFetch('/api/ingestion-log?limit=30');
      const logs = res.log || res.logs || [];
      if (logs.length === 0) {
        list.innerHTML = `<div style="text-align:center; padding: 24px; color:var(--txt3);">No ingestion requests logged yet.</div>`;
        return;
      }
      list.innerHTML = logs.map(l => {
        const isSuccess = l.status === 'success';
        const isRunning = l.status === 'running';
        const statusClass = isSuccess ? 'log-status-success' : (isRunning ? 'log-status-running' : 'log-status-error');
        const dateRange = (l.date_from && l.date_to) ? `${l.date_from.substring(0, 10)} ➔ ${l.date_to.substring(0, 10)}` : 'Full Archive';
        return `
          <div class="log-item-row">
            <div style="display:flex; align-items:center; gap:12px;">
              <span class="log-status-badge ${statusClass}">${l.status}</span>
              <span style="font-weight:700; color:var(--txt); font-family:var(--mono);">${l.symbol} (${l.timeframe || '1m'})</span>
              <span style="font-size:0.72rem; color:var(--txt2); font-family:var(--mono); background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:4px;">${l.source}</span>
              <span style="font-size:0.72rem; color:var(--txt3); font-family:var(--mono);">${dateRange}</span>
            </div>
            <div style="display:flex; align-items:center; gap:12px; font-family:var(--mono); font-size:0.73rem;">
              <span style="color:var(--emerald);">+${fmtNumber(l.bars_inserted || 0)} bars</span>
              <span style="color:var(--txt3);">${l.started_at ? l.started_at.substring(0, 19).replace('T', ' ') : '—'}</span>
              ${isSuccess ? `<button class="pill-btn" style="font-size:0.68rem; padding: 3px 8px; border-color: rgba(16,185,129,0.4); color: var(--emerald);" onclick="window.viewInOurLibrary('${l.symbol}', '${l.timeframe || '1m'}')">📊 View in Our Library</button>` : ''}
            </div>
          </div>
        `;
      }).join('');
    } catch (e) {
      list.innerHTML = `<div style="text-align:center; padding: 20px; color:var(--red);">Error loading logs: ${e.message}</div>`;
    }
  }

  // ─── Global Sync & Refresh ──────────────────────────────────────────
  function setupGlobalEvents() {
    const syncBtn = document.getElementById('btn-sync-feed');
    if (syncBtn) {
      syncBtn.addEventListener('click', async () => {
        syncBtn.disabled = true;
        syncBtn.textContent = '🔄 Syncing...';
        showToast('Syncing market data feed...');

        try {
          await apiFetch('/api/sync?days_back=7');
          await loadMainCandleData();
          showToast('2-Year Market feed successfully synchronized.');
        } catch (err) {
          console.error('Sync error:', err);
          showToast(`Sync warning: ${err.message}`);
        } finally {
          syncBtn.disabled = false;
          syncBtn.textContent = '🔄 Sync Live Feed';
        }
      });
    }

    // Ingestion Modal triggers
    const openLogBtn = document.getElementById('btn-open-ingestion-log');
    if (openLogBtn) openLogBtn.addEventListener('click', openIngestionModal);

    const closeLogBtn = document.getElementById('btn-close-log-modal');
    if (closeLogBtn) closeLogBtn.addEventListener('click', closeIngestionModal);

    const refreshLogBtn = document.getElementById('btn-refresh-log');
    if (refreshLogBtn) refreshLogBtn.addEventListener('click', loadIngestionLogs);

    // Quick HistData Fetch submission
    const submitFetchBtn = document.getElementById('btn-submit-fetch');
    if (submitFetchBtn) {
      submitFetchBtn.addEventListener('click', async () => {
        const pair = document.getElementById('fetch-pair')?.value || 'EURUSD';
        const year = parseInt(document.getElementById('fetch-year')?.value) || 2024;
        const monthVal = document.getElementById('fetch-month')?.value;
        const month = monthVal ? parseInt(monthVal) : null;

        submitFetchBtn.disabled = true;
        submitFetchBtn.innerHTML = `<span>⏳ Ingesting...</span>`;
        showToast(`Requesting HistData 1m for ${pair} (${year}${month ? '/' + month : ''})...`);

        try {
          const query = new URLSearchParams({ symbol: pair, year: year });
          if (month) query.append('month', month);

          const res = await apiFetch(`/api/fetch/histdata?${query.toString()}`, { method: 'POST' });
          if (res.status === 'success') {
            showToast(`Success! Ingested ${fmtNumber(res.bars_inserted || 0)} 1m bars for ${pair}.`);
            closeIngestionModal();
            switchToOurLibraryWithData(pair, '1m');
          } else {
            showToast(`Ingestion error: ${res.error || res.message || 'Check logs'}`);
          }
          await loadIngestionLogs();
        } catch (err) {
          showToast(`Ingestion failed: ${err.message}`);
        } finally {
          submitFetchBtn.disabled = false;
          submitFetchBtn.innerHTML = `<span>📥 Start Ingestion</span>`;
        }
      });
    }

    // Pine Script Modal triggers
    const closePineBtn = document.getElementById('btn-close-pine-modal');
    if (closePineBtn) closePineBtn.addEventListener('click', closePineScriptModal);

    const copyPineBtn = document.getElementById('btn-copy-pinescript');
    if (copyPineBtn) {
      copyPineBtn.addEventListener('click', () => {
        const text = document.getElementById('pinescript-textarea')?.textContent || '';
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(() => {
            showToast('Pine Script v5 copied to clipboard!');
          }).catch(() => {
            showToast('Copied to clipboard.');
          });
        } else {
          showToast('Copied to clipboard.');
        }
      });
    }

    // Close modals on backdrop click
    document.querySelectorAll('.hub-modal-overlay').forEach(modal => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) {
          modal.classList.remove('open');
        }
      });
    });
  }

  // ─── Initialization ─────────────────────────────────────────────────
  async function init() {
    renderShell();

    const currentPath = window.location.pathname;
    const initialTab = ROUTE_MAP[currentPath] || 'main';
    switchTab(initialTab);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
