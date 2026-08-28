const FALLBACK_DATA = {
  "2026-06": { period: "2026-06", currency: "GBP", income_minor: 350000, spending_minor: 178599, savings_minor: 40000, investments_minor: 30000, net_cashflow_minor: 171401, savings_rate_percent: 20, transaction_count: 11, categories: [{ category: "housing", total_minor: 120000 }, { category: "groceries", total_minor: 32000 }, { category: "eating_out", total_minor: 11000 }, { category: "transport", total_minor: 9000 }, { category: "utilities", total_minor: 8500 }, { category: "subscriptions", total_minor: 1599 }] },
  "2026-07": { period: "2026-07", currency: "GBP", income_minor: 350000, spending_minor: 200099, savings_minor: 40000, investments_minor: 30000, net_cashflow_minor: 149901, savings_rate_percent: 20, transaction_count: 11, categories: [{ category: "housing", total_minor: 120000 }, { category: "groceries", total_minor: 36000 }, { category: "eating_out", total_minor: 18000 }, { category: "transport", total_minor: 10500 }, { category: "utilities", total_minor: 14000 }, { category: "subscriptions", total_minor: 1599 }] },
  "2026-08": { period: "2026-08", currency: "GBP", income_minor: 350000, spending_minor: 311099, savings_minor: 40000, investments_minor: 30000, net_cashflow_minor: 38901, savings_rate_percent: 20, transaction_count: 12, categories: [{ category: "housing", total_minor: 120000 }, { category: "shopping", total_minor: 78000 }, { category: "groceries", total_minor: 39000 }, { category: "eating_out", total_minor: 26000 }, { category: "debt_payment", total_minor: 25000 }, { category: "transport", total_minor: 12000 }, { category: "utilities", total_minor: 9500 }, { category: "subscriptions", total_minor: 1599 }] }
};

const state = { month: "2026-08", data: {}, source: "demo" };
const $ = (id) => document.getElementById(id);
const formatMoney = (minor, currency = "GBP", compact = false) => {
  const amount = Math.abs(Number(minor || 0)) / 100;
  const value = new Intl.NumberFormat("en-GB", { style: "currency", currency, maximumFractionDigits: compact && amount >= 1000 ? 0 : 2 }).format(amount);
  return Number(minor || 0) < 0 ? `−${value}` : value;
};
const monthName = (period) => new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(new Date(`${period}-01T00:00:00`));
const monthShift = (period, offset) => { const [year, month] = period.split("-").map(Number); const date = new Date(Date.UTC(year, month - 1 + offset, 1)); return date.toISOString().slice(0, 7); };
const prettyCategory = (category) => String(category || "uncategorized").replaceAll("_", " ");
const signedDelta = (value, currency = "GBP") => `${value < 0 ? "−" : "+"}${formatMoney(Math.abs(value), currency)}`;

async function getJson(path) { const response = await fetch(path); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
async function loadMonth(period) {
  state.month = period;
  try {
    const [summary, categories] = await Promise.all([getJson(`/analytics/monthly?month=${period}`), getJson(`/analytics/categories?month=${period}`)]);
    state.data[period] = { ...summary, categories };
    state.source = "live";
  } catch (error) {
    state.data[period] = FALLBACK_DATA[period] || { ...FALLBACK_DATA["2026-08"], period };
    state.source = "demo";
  }
  render();
}

function render() {
  const data = state.data[state.month];
  const previous = state.data[monthShift(state.month, -1)] || FALLBACK_DATA[monthShift(state.month, -1)] || data;
  $("month-label").textContent = monthName(state.month);
  $("income-value").textContent = formatMoney(data.income_minor, data.currency, true);
  $("spending-value").textContent = formatMoney(data.spending_minor, data.currency, true);
  $("net-value").textContent = formatMoney(data.net_cashflow_minor, data.currency, true);
  $("rate-value").textContent = `${Number(data.savings_rate_percent || 0).toFixed(1)}%`;
  $("income-change").textContent = data.income_minor === previous.income_minor ? "Same as previous month" : `${signedDelta(data.income_minor - previous.income_minor, data.currency)} vs previous`;
  $("spending-change").textContent = `${formatMoney(data.spending_minor - previous.spending_minor, data.currency, true)} ${data.spending_minor >= previous.spending_minor ? "above" : "below"} previous month`;
  $("net-change").textContent = `${formatMoney(Math.abs(data.net_cashflow_minor - previous.net_cashflow_minor), data.currency, true)} ${data.net_cashflow_minor >= previous.net_cashflow_minor ? "higher" : "lower"} than previous`;
  $("rate-change").textContent = `${Math.abs(Number(data.savings_rate_percent || 0) - Number(previous.savings_rate_percent || 0)).toFixed(1)} pts ${data.savings_rate_percent >= previous.savings_rate_percent ? "higher" : "lower"} than previous`;
  $("transaction-count").textContent = data.transaction_count || "—";
  $("review-delta").textContent = formatMoney(Math.abs(data.spending_minor - previous.spending_minor), data.currency, true);
  $("review-copy").innerHTML = `${data.spending_minor >= previous.spending_minor ? "Spending moved up" : "Spending moved down"} <strong id="review-delta">${formatMoney(Math.abs(data.spending_minor - previous.spending_minor), data.currency, true)}</strong> from ${monthName(previous.period || monthShift(state.month, -1))}. The evidence points to the clearest changes in this period.`;
  $("review-heading").textContent = `${monthName(state.month)} review`;
  $("review-heading").closest("[aria-labelledby]")?.setAttribute("aria-label", `${monthName(state.month)} review`);
  $("metric-band").setAttribute("aria-label", `${monthName(state.month)} financial summary`);
  renderAudit(data, previous);
  renderCategories(data);
  renderCashflow();
  renderEvidence(data, previous);
  $("sync-note").innerHTML = `<span class="sync-pulse"></span>${state.source === "live" ? "Synced moments ago" : "Demo dataset · local preview"}`;
}

function renderAudit(data, previous) {
  const current = Object.fromEntries((data.categories || []).map((item) => [item.category, Number(item.total_minor || item.amount_minor || 0)]));
  const prior = Object.fromEntries((previous.categories || []).map((item) => [item.category, Number(item.total_minor || item.amount_minor || 0)]));
  const changes = Object.keys(current).map((category) => ({ category, amount: current[category], delta: current[category] - (prior[category] || 0) })).filter((item) => item.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 2);
  const rows = changes.length ? changes : Object.keys(current).sort((a, b) => current[b] - current[a]).slice(0, 2).map((category) => ({ category, amount: current[category], delta: 0 }));
  $("audit-list").innerHTML = rows.map((item, index) => { const newCategory = !prior[item.category]; const label = newCategory ? `${prettyCategory(item.category)} is the new pressure point` : `${prettyCategory(item.category)} moved up from last month`; const detail = `${formatMoney(item.amount, data.currency, true)} this month - ${newCategory ? "no previous baseline" : `${formatMoney(item.delta, data.currency, true)} above previous`}`; return `<button class="audit-row${index === 0 ? " is-primary" : ""}" type="button" data-evidence="${item.category}"><span class="audit-index">0${index + 1}</span><span class="audit-signal ${index === 0 ? "signal-orange" : "signal-amber"}"></span><span class="audit-copy"><strong>${label}</strong><small>${detail}</small></span><span class="audit-amount">${item.delta > 0 ? `+${formatMoney(item.delta, data.currency, true)}` : formatMoney(item.amount, data.currency, true)}</span><span class="row-arrow" aria-hidden="true">-></span></button>`; }).join("");
  $("audit-list").querySelectorAll(".audit-row").forEach((button) => button.addEventListener("click", () => showToast("This inspection is queued for the next workspace slice.")));
  const headline = rows.length === 1 ? "One change stands out this month." : "Most of the change is in two places.";
  document.querySelector("#audit-list")?.closest(".audit-panel")?.querySelector("h3")?.replaceChildren(document.createTextNode(headline));
}

function renderCategories(data) {
  const categories = [...(data.categories || [])].sort((a, b) => Number(b.total_minor || b.amount_minor || 0) - Number(a.total_minor || a.amount_minor || 0)).slice(0, 6);
  const max = Math.max(...categories.map((item) => Number(item.total_minor || item.amount_minor || 0)), 1);
  $("category-chart").innerHTML = categories.map((item, index) => { const amount = Number(item.total_minor || item.amount_minor || 0); const kind = item.category === "shopping" ? "is-spike" : index === 0 ? "is-core" : index > 3 ? "is-oneoff" : ""; return `<div class="category-line"><span class="category-name">${prettyCategory(item.category)}</span><span class="bar-track"><span class="bar-fill ${kind}" style="width:${Math.max(4, amount / max * 100)}%"></span></span><span class="category-amount">${formatMoney(amount, data.currency, true)}</span></div>`; }).join("");
  document.querySelector("#category-table tbody").innerHTML = categories.map((item) => `<tr><td>${prettyCategory(item.category)}</td><td>${formatMoney(Number(item.total_minor || item.amount_minor || 0), data.currency)}</td></tr>`).join("");
}

function renderCashflow() {
  const periods = [monthShift(state.month, -2), monthShift(state.month, -1), state.month];
  const data = periods.map((period) => state.data[period] || FALLBACK_DATA[period] || FALLBACK_DATA[state.month]);
  const max = Math.max(...data.flatMap((item) => [item.income_minor, item.spending_minor]), 1);
  $("cashflow-bars").innerHTML = data.map((item, index) => `<div class="chart-month" data-month="${new Date(`${periods[index]}-01T00:00:00`).toLocaleString("en-GB", { month: "short" })}"><span class="cash-bar income" style="height:${Math.max(4, item.income_minor / max * 100)}%"></span><span class="cash-bar spending" style="height:${Math.max(4, item.spending_minor / max * 100)}%"></span></div>`).join("");
}

function renderEvidence(data, previous) {
  $("evidence-detail").innerHTML = `<div class="evidence-detail-row"><span>Current period</span><strong>${data.period}</strong></div><div class="evidence-detail-row"><span>Source transactions</span><strong>${data.transaction_count || "—"} rows</strong></div><div class="evidence-detail-row"><span>Spending calculation</span><strong>${formatMoney(data.spending_minor, data.currency)}</strong></div><div class="evidence-detail-row"><span>Previous period delta</span><strong>${signedDelta(data.spending_minor - previous.spending_minor, data.currency)}</strong></div><div class="evidence-detail-row"><span>Interpretation layer</span><strong>${state.source === "live" ? "Available" : "Demo preview"}</strong></div>`;
}

function setupMonthMenu() {
  const menu = $("month-menu"); const periods = Object.keys(FALLBACK_DATA).sort().reverse();
  menu.innerHTML = periods.map((period) => `<button class="month-option" type="button" role="option" data-period="${period}" aria-selected="${period === state.month}">${monthName(period)}</button>`).join("");
  $("month-current").addEventListener("click", () => { const open = !menu.hidden; menu.hidden = open; $("month-current").setAttribute("aria-expanded", String(!open)); if (!open) menu.querySelector("[aria-selected=true]")?.focus(); });
  menu.addEventListener("click", (event) => { const option = event.target.closest("[data-period]"); if (!option) return; menu.hidden = true; $("month-current").setAttribute("aria-expanded", "false"); loadMonth(option.dataset.period); });
  document.addEventListener("click", (event) => { if (!event.target.closest(".month-controls")) { menu.hidden = true; $("month-current").setAttribute("aria-expanded", "false"); } });
  document.querySelectorAll("[data-month-step]").forEach((button) => button.addEventListener("click", () => loadMonth(monthShift(state.month, Number(button.dataset.monthStep)))));
}

function setupInteractions() {
  const dialog = $("evidence-dialog"); const toast = $("toast");
  $("open-evidence").addEventListener("click", () => dialog.showModal());
  dialog.querySelectorAll(".dialog-close").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector(".button-primary").addEventListener("click", () => { dialog.close(); showToast("Activity view is next in the workspace sequence."); });
  document.querySelectorAll(".audit-row, .queue-item, .queue-footer, .prompt-button").forEach((button) => button.addEventListener("click", () => { if (button.id !== "open-evidence") showToast("This inspection is queued for the next workspace slice."); }));
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", (event) => { if (item.dataset.route !== "overview") { event.preventDefault(); showToast(`${item.textContent.trim().replace(/\d+$/, "")} is coming next.`); } }));
  window.addEventListener("keydown", (event) => { if (event.key === "Escape" && !dialog.open) toast.classList.remove("is-visible"); });
}
let toastTimer;
function showToast(message) { const toast = $("toast"); toast.textContent = message; toast.classList.add("is-visible"); clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2800); }

setupMonthMenu();
setupInteractions();
loadMonth(state.month);
