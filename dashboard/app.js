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

fields.forEach(id => document.getElementById(id).addEventListener("input", () => { updateCostScenario(); updateRiskScenario(); }));
updateCostScenario();
updateRiskScenario();
