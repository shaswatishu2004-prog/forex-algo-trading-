/**
 * Candlestick Verification & Strategy Backtest Hub — hub.js
 * Pure Vanilla JS Frontend with zero data tables.
 *
 * 2 Single Tabs connected via FastAPI hyperlinks:
 *   1. Main Menu (Candlestick Verification of 2-Year Infoway Market Data from Jan 2024)
 *   2. Backtest Tab (Simulator with TradingView Candlestick Execution Overlays, Time & Lots)
 */

(function () {
  'use strict';

  // ─── State Management ───────────────────────────────────────────────
  const state = {
    activeTab: 'main', // 'main' | 'backtest'
    symbol: 'EURUSD',
    timeframe: '1d', // default to 1d to immediately present 2-year lookback
    symbolsList: ['EURUSD', 'XAUUSD', 'USDJPY', 'DXY'],

    // Main Menu Chart
    mainChart: null,
    mainCandleSeries: null,
    mainVolumeSeries: null,

    // Backtest Chart
    btChart: null,
    btCandleSeries: null,
    btVolumeSeries: null,
    btTrades: [],

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
              <span class="brand-subtitle">Infoway 2-Year Engine · Jan 2024 to Present</span>
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
              <span id="api-status-text">Infoway 2-Year Feed · Active</span>
            </div>
            <button class="pill-btn" id="btn-sync-feed" title="Sync latest Infoway bars">
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
                <p class="headline-desc">Real-time OHLCV verification rendered from Infoway API. Zero tabular data representation.</p>
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
                  <button class="pill-btn active" data-tf="1d">1d (2-Year History)</button>
                  <button class="pill-btn" data-tf="1h">1h</button>
                  <button class="pill-btn" data-tf="15m">15m</button>
                  <button class="pill-btn" data-tf="5m">5m</button>
                  <button class="pill-btn" data-tf="1m">1m</button>
                </div>
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
                  <span class="hud-sub">Infoway Cumulative Tick Volume</span>
                </div>
                <div class="hud-item">
                  <span class="hud-label">Verified Historical Bars</span>
                  <span class="hud-value green" id="hud-bar-count">—</span>
                  <span class="hud-sub" id="hud-source-badge">Infoway API • Starting Jan 2024</span>
                </div>
              </div>

              <!-- Candlestick Canvas Container -->
              <div class="candlestick-canvas-box" style="height: 540px;">
                <div class="chart-verification-badge">
                  <span>●</span>
                  <span id="badge-chart-info">EURUSD · 1d · LIVE</span>
                </div>
                <div id="main-chart-container" style="width: 100%; height: 100%;"></div>
              </div>
            </div>
          </section>

          <!-- PANE 2: BACKTEST SIMULATOR -->
          <section class="tab-pane" id="pane-backtest">
            <div class="section-headline-bar">
              <div class="headline-text-group">
                <h1 class="headline-title">🚀 Backtest Simulator & Trade Execution Chart</h1>
                <p class="headline-desc">Run strategy simulations over verified Infoway bars with trade marker overlays plotted directly on the candlesticks.</p>
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
                    <option value="1d" selected>1d (2-Year Lookback from Jan 2024)</option>
                    <option value="1m">1m Intraday</option>
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

                <!-- Lot Sizing Mode: Dynamic vs Custom -->
                <div class="form-group">
                  <label class="form-label" for="bt-lot-mode">Lot Sizing Mode</label>
                  <select id="bt-lot-mode" class="form-select">
                    <option value="dynamic" selected>Dynamic (Auto-sized from Risk & 50% Margin Cap)</option>
                    <option value="custom">Custom Choose Lot Size</option>
                  </select>
                </div>

                <div class="form-group" id="bt-custom-lot-group" style="display: none;">
                  <label class="form-label" for="bt-custom-lot">Custom Lot Size (Lots)</label>
                  <input type="number" id="bt-custom-lot" class="form-input" value="0.10" step="0.01" min="0.01" max="50.0"/>
                  <span style="font-size: 0.68rem; color: var(--amber); margin-top: 3px; display: block;">
                    Clamped automatically if required margin exceeds the 50% account cap.
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
                    <div class="selector-group">
                      <span class="selector-label">Markers:</span>
                      <span style="font-size: 0.73rem; color: var(--txt2); font-family: var(--mono);">
                        🟢 BUY Marker &nbsp;|&nbsp; 🔴 SELL Marker &nbsp;|&nbsp; ⚪ Outcome
                      </span>
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
                    <span class="kpi-card-label">Peak Margin Used</span>
                    <span class="kpi-card-val gold" id="kpi-peak-margin">0.0%</span>
                  </div>
                </div>

                <!-- Equity Curve Graph -->
                <div class="equity-chart-card">
                  <h3 style="font-size: 0.95rem; font-weight: 700;">📈 Portfolio Equity Progression</h3>
                  <div class="equity-canvas-wrapper" id="equity-curve-box">
                    <svg id="equity-svg" width="100%" height="100%" style="overflow: visible;"></svg>
                  </div>
                </div>

                <!-- Executed Trades Log (Visual Cards with Time & Lot Size, Zero Tables) -->
                <div class="trade-log-container">
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">📑 Executed Trade Log (Time & Lot Size)</h3>
                    <span style="font-size: 0.72rem; color: var(--txt2); font-family: var(--mono);" id="trade-log-counter">0 trades</span>
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
        ensureChartsReady(() => {
          if (!state.mainChart) {
            initMainChart();
          } else {
            const c = document.getElementById('main-chart-container');
            if (c) {
              state.mainChart.applyOptions({ width: c.clientWidth, height: c.clientHeight });
              state.mainChart.timeScale().fitContent();
            }
          }
        });
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

  // ─── Main Menu Candlestick Chart ────────────────────────────────────
  function initMainChart() {
    const container = document.getElementById('main-chart-container');
    if (!container) return;

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
      const url = `/api/ohlcv?symbol=${encodeURIComponent(state.symbol)}&timeframe=${encodeURIComponent(state.timeframe)}&limit=5000`;
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

      if (badge) badge.textContent = `${state.symbol} · ${state.timeframe} · Verified 2-Year Feed`;
    } catch (err) {
      console.error('Candle load error:', err);
      if (badge) badge.textContent = `${state.symbol} · Load Error`;
      showToast(`Error loading ${state.symbol}: ${err.message}`);
    }
  }

  function setupMainChartControls() {
    const symGroup = document.getElementById('symbol-selector-group');
    if (symGroup) {
      symGroup.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-symbol]');
        if (!btn) return;
        symGroup.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.symbol = btn.getAttribute('data-symbol');
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
        loadMainCandleData();
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
    const badge = document.getElementById('badge-bt-chart-info');
    if (badge) badge.textContent = `${sym} · ${tf} · Execution Markers Plotted`;

    try {
      const url = `/api/ohlcv?symbol=${encodeURIComponent(sym)}&timeframe=${encodeURIComponent(tf)}&limit=5000`;
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

    // Symbol or timeframe change on backtest form reloads chart
    const btSymSelect = document.getElementById('bt-symbol');
    const btTfSelect = document.getElementById('bt-timeframe');
    if (btSymSelect) btSymSelect.addEventListener('change', () => loadBacktestCandlesAndTrades());
    if (btTfSelect) btTfSelect.addEventListener('change', () => loadBacktestCandlesAndTrades());

    // Lot Sizing Mode toggle (Dynamic vs Custom)
    const lotModeSelect = document.getElementById('bt-lot-mode');
    const customLotGroup = document.getElementById('bt-custom-lot-group');
    if (lotModeSelect && customLotGroup) {
      lotModeSelect.addEventListener('change', () => {
        customLotGroup.style.display = lotModeSelect.value === 'custom' ? 'block' : 'none';
      });
    }

    runBtn.addEventListener('click', async () => {
      const sym = document.getElementById('bt-symbol').value;
      const strategy = document.getElementById('bt-strategy').value;
      const tf = document.getElementById('bt-timeframe').value;
      const cap = parseFloat(document.getElementById('bt-capital').value) || 10000;
      const risk = parseFloat(document.getElementById('bt-risk').value) || 1.0;
      const sl = parseFloat(document.getElementById('bt-sl').value) || 1.5;
      const tp = parseFloat(document.getElementById('bt-tp').value) || 2.0;

      // 1:100 Leverage & 50% max margin cap settings
      const leverage = parseFloat(document.getElementById('bt-leverage')?.value) || 100.0;
      let maxMargin = parseFloat(document.getElementById('bt-max-margin')?.value) || 50.0;
      if (maxMargin > 50.0) maxMargin = 50.0; // Enforce strict 50% cap
      const lotMode = document.getElementById('bt-lot-mode')?.value || 'dynamic';
      const customLot = parseFloat(document.getElementById('bt-custom-lot')?.value) || 0.10;

      runBtn.disabled = true;
      runBtn.innerHTML = `<span>⏳ Simulating...</span>`;
      showToast(`Simulating ${strategy} on ${sym} (1:${Math.round(leverage)} lev, ≤${maxMargin}% margin)...`);

      try {
        const query = new URLSearchParams({
          symbol: sym,
          strategy: strategy,
          timeframe: tf,
          initial_capital: cap,
          risk_pct: risk,
          sl_multiplier: sl,
          tp_multiplier: tp,
          leverage: leverage,
          max_margin_pct: maxMargin,
          lot_mode: lotMode,
          custom_lot_size: customLot,
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
        document.getElementById('kpi-max-dd').textContent = `${(m.max_drawdown_pct || 0).toFixed(2)}%`;
        document.getElementById('kpi-total-trades').textContent = m.total_trades || 0;

        const levElem = document.getElementById('kpi-leverage-cap');
        if (levElem) levElem.textContent = `${m.leverage || '1:100'} (≤${m.max_margin_cap_pct || 50}%)`;

        const peakMarginElem = document.getElementById('kpi-peak-margin');
        if (peakMarginElem) {
          const pm = m.peak_margin_used_pct || 0.0;
          peakMarginElem.textContent = `${pm.toFixed(1)}%`;
          peakMarginElem.style.color = pm <= 50.0 ? 'var(--emerald)' : 'var(--red)';
        }

        // Render Equity Curve via SVG
        renderEquityCurve(res.equity_curve || []);

        // Load candles and plot trade markers on chart
        await loadBacktestCandlesAndTrades();

        // Render Trade Cards Log with Time & Lot Size (Zero Tables)
        renderTradeCards(res.trades || []);

        showToast(`Simulation complete: ${m.total_trades || 0} trades executed & plotted on chart.`);
      } catch (err) {
        console.error('Backtest error:', err);
        showToast(`Simulation error: ${err.message}`);
      } finally {
        runBtn.disabled = false;
        runBtn.innerHTML = `<span>⚡ Run Simulation</span>`;
      }
    });
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

  // ─── Trade Cards Log (Time & Lot Size, Zero Tables) ─────────────────
  function renderTradeCards(trades) {
    const list = document.getElementById('trade-cards-list');
    const counter = document.getElementById('trade-log-counter');
    if (!list) return;

    if (!trades || trades.length === 0) {
      if (counter) counter.textContent = '0 trades';
      list.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--txt3);">
          Zero trades executed under current parameters. Try adjusting risk or timeframe.
        </div>`;
      return;
    }

    if (counter) counter.textContent = `${trades.length} trades recorded`;

    list.innerHTML = trades.slice().reverse().map((t, idx) => {
      const isWin = (t.pnl || 0) >= 0;
      const isLong = t.direction === 'LONG';
      const badgeBg = isLong ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
      const badgeColor = isLong ? 'var(--emerald)' : 'var(--red)';
      const pnlColor = isWin ? 'var(--emerald)' : 'var(--red)';

      return `
        <div class="trade-card-row" style="display: flex; flex-direction: column; gap: 8px; padding: 12px 16px;">
          <!-- Top Row: ID, Direction, Lots, and PnL -->
          <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
            <div style="display: flex; align-items: center; gap: 10px;">
              <span class="trade-card-badge" style="background: ${badgeBg}; color: ${badgeColor}; font-weight: 700;">
                #${t.id || (idx + 1)} ${t.direction}
              </span>
              <span class="strat-param-tag" style="background: rgba(59,130,246,0.18); color: var(--blue); font-weight: 700;">
                Lot Size: ${Number(t.lots || 0.01).toFixed(2)} Lots
              </span>
              <span class="strat-param-tag" style="background: rgba(16,185,129,0.14); color: var(--emerald); font-weight: 600;">
                Margin: $${fmtNumber(Math.round(t.margin_used || 0))} (${Number(t.margin_pct || 0).toFixed(1)}%)
              </span>
              <span style="font-size: 0.72rem; color: var(--txt2); font-family: var(--mono);">
                ${t.reason || 'Take Profit'}
              </span>
            </div>

            <div style="font-weight: 700; font-size: 0.88rem; color: ${pnlColor}; font-family: var(--mono);">
              ${isWin ? '+' : ''}$${Number(t.pnl || 0).toFixed(2)} (${fmtPct(t.return_pct)})
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

  // ─── Global Sync & Refresh ──────────────────────────────────────────
  function setupGlobalEvents() {
    const syncBtn = document.getElementById('btn-sync-feed');
    if (syncBtn) {
      syncBtn.addEventListener('click', async () => {
        syncBtn.disabled = true;
        syncBtn.textContent = '🔄 Syncing...';
        showToast('Syncing 2-year Infoway market bars...');

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
  }

  // ─── Initialization ─────────────────────────────────────────────────
  async function init() {
    renderShell();

    const currentPath = window.location.pathname;
    const initialTab = ROUTE_MAP[currentPath] || 'main';
    switchTab(initialTab);

    ensureChartsReady(() => {
      if (initialTab === 'main') {
        loadMainCandleData();
      } else if (initialTab === 'backtest') {
        loadBacktestCandlesAndTrades();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
