/* Shared dashboard script: animation/interaction layer + index calculators.
   Loaded by every page. Every feature is progressive enhancement:
   - without JS nothing is hidden (reveal classes are added by this script);
   - prefers-reduced-motion disables motion, values render final immediately;
   - sections that are not present on a page are skipped silently. */
(() => {
  "use strict";

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------- nav scroll progress */
  const nav = document.querySelector(".nav");
  if (nav) {
    const bar = document.createElement("i");
    bar.className = "nav-progress";
    bar.setAttribute("aria-hidden", "true");
    nav.appendChild(bar);
    const updateBar = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = `${max > 0 ? (window.scrollY / max) * 100 : 0}%`;
    };
    window.addEventListener("scroll", updateBar, { passive: true });
    window.addEventListener("resize", updateBar);
    updateBar();
  }

  /* ------------------------------------------------------- scroll reveal */
  const REVEAL = [
    ".meta-strip", ".kpi", ".panel", ".link-card", ".step",
    ".decision-list li", ".gate-list li", ".timeframe-table > div",
    ".check-item", ".check-grid label", ".blocked-row",
  ].join(",");
  const revealables = Array.from(document.querySelectorAll(REVEAL));

  if (!reduced && "IntersectionObserver" in window && revealables.length) {
    // Stagger within each parent so rows cascade instead of popping together.
    const seenParent = new Map();
    revealables.forEach(el => {
      const parent = el.parentElement;
      const i = seenParent.get(parent) || 0;
      seenParent.set(parent, i + 1);
      el.classList.add("reveal");
      el.style.setProperty("--d", `${Math.min(i, 7) * 65}ms`);
    });
    const io = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    revealables.forEach(el => io.observe(el));
  }

  /* ---------------------------------------------------------- counters */
  /* Animates the first number inside KPI/meta values ("2,302 trades",
     "+0.742%"). Non-numeric values are left untouched. */
  const countTargets = Array.from(
    document.querySelectorAll(".kpi strong, .meta-strip b")
  );
  const parseValue = text => {
    const m = /^([^\d]*)(\d[\d,]*)(\.?)(\d*)(.*)$/.exec(text.trim());
    if (!m) return null;
    const hasDecimals = m[4] !== "";
    return {
      prefix: m[1], target: Number(m[2].replace(/,/g, "")),
      dot: hasDecimals ? "." : "", decimals: hasDecimals ? m[4].length : 0,
      suffix: m[5],
      useCommas: m[2].includes(","),
    };
  };
  const formatFixed = (v, p) => {
    let n = v.toFixed(p.decimals);
    if (p.useCommas) {
      const parts = n.split(".");
      parts[0] = Number(parts[0]).toLocaleString("en-US");
      n = parts.join(".");
    }
    return `${p.prefix}${n}${p.suffix}`;
  };

  const animateCount = el => {
    if (el.dataset.counted) return;
    el.dataset.counted = "1";
    const parsed = parseValue(el.textContent);
    if (!parsed || parsed.target === 0) return;
    if (reduced) return;
    const dur = 950;
    const start = performance.now();
    const ease = t => 1 - Math.pow(1 - t, 3);
    const tick = now => {
      const t = Math.min((now - start) / dur, 1);
      el.textContent = formatFixed(parsed.target * ease(t), parsed);
      if (t < 1) requestAnimationFrame(tick);
      else el.textContent = formatFixed(parsed.target, parsed);
    };
    requestAnimationFrame(tick);
  };

  if ("IntersectionObserver" in window && countTargets.length) {
    const cio = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          animateCount(entry.target);
          cio.unobserve(entry.target);
        }
      });
    }, { threshold: 0.4 });
    countTargets.forEach(el => cio.observe(el));
  }

  /* --------------------------------------------------- chart line draw-in */
  /* Solid line paths draw themselves once their chart scrolls into view.
     Dashed/dotted paths and the backtest replay chart are excluded. */
  const DRAW_EXCLUDE = ".equity-chart, .replay-chart";
  const lineSel = [
    ".chart .px", ".chart .vwap", ".chart .ind", ".chart .px-daily",
    ".chart .depth-daily", ".chart .depth-minute",
  ].join(",");
  const drawLines = Array.from(document.querySelectorAll(lineSel))
    .filter(p => !p.closest(DRAW_EXCLUDE));

  const prepareLine = path => {
    try {
      const len = path.getTotalLength();
      if (!len || len > 60000) return false;
      path.style.strokeDasharray = `${len}`;
      path.style.strokeDashoffset = `${len}`;
      return true;
    } catch (e) { return false; }
  };
  const prepared = drawLines.filter(prepareLine);

  if (reduced) {
    prepared.forEach(p => { p.style.strokeDasharray = ""; p.style.strokeDashoffset = ""; });
  } else if ("IntersectionObserver" in window && prepared.length) {
    const lio = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const p = entry.target;
        p.style.transition = "stroke-dashoffset 1.5s cubic-bezier(.22,.61,.21,1)";
        p.style.strokeDashoffset = "0";
        lio.unobserve(p);
        // Drop the inline dash properties after the draw so dashed overrides
        // (if any) and hit-testing return to normal.
        setTimeout(() => {
          p.style.strokeDasharray = "";
          p.style.strokeDashoffset = "";
          p.style.transition = "";
        }, 1700);
      });
    }, { threshold: 0.25 });
    prepared.forEach(p => lio.observe(p));
  }

  /* --------------------------------------------------- scrollspy for nav */
  const spyLinks = Array.from(document.querySelectorAll('.nav-links a[href^="#"]'));
  if (spyLinks.length && "IntersectionObserver" in window) {
    const sections = spyLinks
      .map(a => document.getElementById(a.getAttribute("href").slice(1)))
      .filter(Boolean);
    const sio = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        spyLinks.forEach(a => a.classList.toggle(
          "is-active", a.getAttribute("href") === `#${entry.target.id}`));
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    sections.forEach(s => sio.observe(s));
  }

  /* ------------------------------------------------- checklist persistence */
  const checkGrid = document.querySelector(".check-grid");
  if (checkGrid) {
    const KEY = "fx-research-checklist-v1";
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { saved = {}; }
    const boxes = Array.from(checkGrid.querySelectorAll('input[type="checkbox"]'));
    boxes.forEach((box, i) => {
      const id = box.id || `check-${i}`;
      if (saved[id]) box.checked = true;
      box.addEventListener("change", () => {
        saved[id] = box.checked;
        try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* private mode */ }
      });
    });
  }

  /* --------------------------------------------------------- hero canvas */
  /* Decorative animated wave field behind the hero. Purely ornamental:
     it encodes no market data and implies no live feed. */
  const canvas = document.getElementById("hero-canvas");
  if (canvas && !reduced) {
    const ctx = canvas.getContext("2d");
    let w = 0, h = 0, dpr = 1, raf = 0, t = 0, running = true;

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      w = Math.max(rect.width, 1);
      h = Math.max(rect.height, 1);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    // Three layered sine waves with phase drift; blue ladder only.
    const waves = [
      { amp: 0.16, len: 0.0042, speed: 0.55, alpha: 0.16, width: 1.4, color: "101,169,255" },
      { amp: 0.10, len: 0.0071, speed: -0.38, alpha: 0.11, width: 1.1, color: "156,198,255" },
      { amp: 0.22, len: 0.0026, speed: 0.24, alpha: 0.07, width: 2.2, color: "75,134,214" },
    ];

    const draw = () => {
      if (!running) return;
      t += 1;
      ctx.clearRect(0, 0, w, h);

      // Faint vertical tape grid.
      ctx.strokeStyle = "rgba(101,169,255,0.05)";
      ctx.lineWidth = 1;
      const gap = 64;
      const offset = (t * 0.12) % gap;
      for (let x = -gap + offset; x < w + gap; x += gap) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }

      waves.forEach((wave, wi) => {
        ctx.beginPath();
        const mid = h * (0.5 + (wi - 1) * 0.12);
        for (let x = 0; x <= w; x += 6) {
          const y = mid
            + Math.sin(x * wave.len + t * 0.012 * wave.speed * 10 + wi * 2.1) * h * wave.amp
            + Math.sin(x * wave.len * 2.7 - t * 0.009 * wave.speed * 10) * h * wave.amp * 0.3;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = `rgba(${wave.color},${wave.alpha})`;
        ctx.lineWidth = wave.width;
        ctx.stroke();
      });

      // Slow drifting motes for depth.
      for (let i = 0; i < 14; i++) {
        const seed = i * 97.3;
        const px = ((seed * 7.7 + t * (0.14 + (i % 3) * 0.05)) % (w + 40)) - 20;
        const py = h * (0.2 + ((seed * 3.1) % 1) * 0.7)
          + Math.sin(t * 0.008 + i) * 12;
        const a = 0.10 + 0.12 * (0.5 + 0.5 * Math.sin(t * 0.02 + i * 1.7));
        ctx.beginPath();
        ctx.arc(px, py, 1.4, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(156,198,255,${a})`;
        ctx.fill();
      }

      raf = requestAnimationFrame(draw);
    };

    resize();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(draw);
      }
    });
    raf = requestAnimationFrame(draw);
  }

  /* --------------------------------------------------------- calculators */
  /* Index-page only; every other page skips this block. */
  const costInput = document.getElementById("grossReturn");
  if (!costInput) return;

  const fields = ["grossReturn", "spreadPips", "pairPrice", "commission", "slippage", "financing", "equity", "riskPct", "stopPct", "dailyLoss"];
  const number = id => Number(document.getElementById(id).value) || 0;
  const money = value => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);

  function updateCostScenario() {
    const gross = number("grossReturn") / 100;
    const spread = number("spreadPips") * 0.0001 / number("pairPrice");
    const otherCosts = (number("commission") + number("slippage") + number("financing")) / 100;
    const net = (gross - spread - otherCosts) * 100;
    const output = document.getElementById("netReturn");
    output.textContent = `${net.toFixed(4)}%`;
    output.className = net >= 0 ? "positive" : "negative";
    document.getElementById("costMessage").textContent = net >= 0
      ? "This scenario remains positive, but must still survive out-of-sample and stress testing."
      : "Costs exceed the assumed gross edge in this scenario.";
  }

  function updateRiskScenario() {
    const equity = number("equity");
    const risk = number("riskPct") / 100;
    const stop = number("stopPct") / 100;
    const notional = stop > 0 ? equity * risk / stop : 0;
    document.getElementById("notional").textContent = money(notional);
    document.getElementById("riskMessage").textContent = `Maximum planned loss at the stop: ${money(equity * risk)}. Daily halt threshold: ${money(equity * number("dailyLoss") / 100)}.`;
  }

  fields.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => { updateCostScenario(); updateRiskScenario(); });
  });
  updateCostScenario();
  updateRiskScenario();
})();
