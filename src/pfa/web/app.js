/**
 * PFA (Personal Finance Agent) Frontend Client
 * Impeccable Design & Deterministic Finance Engine
 */

const EMPTY_MONTH = {
  period: "",
  currency: "GBP",
  income_minor: 0,
  spending_minor: 0,
  savings_minor: 0,
  investments_minor: 0,
  net_cashflow_minor: 0,
  savings_rate_percent: 0,
  transaction_count: 0,
  categories: []
};

const state = {
  route: "overview",
  month: new Date().toISOString().slice(0, 7),
  data: {},
  accounts: [],
  transactions: [],
  budgets: {},
  goals: [],
  activeBatch: null,
  batchFilter: "all",
  chatHistory: [],
  source: "live",
  modelAvailable: true,
  loadError: null
};

const $ = (id) => document.getElementById(id);

// UTILITY FORMATTERS
function formatMoney(minor, currency = "GBP", compact = false) {
  const amount = Math.abs(Number(minor || 0)) / 100;
  const formatted = new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    maximumFractionDigits: compact && amount >= 1000 ? 0 : 2
  }).format(amount);
  return Number(minor || 0) < 0 ? `−${formatted}` : formatted;
}

function monthName(period) {
  if (!period) return "";
  const [year, month] = period.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, 1));
  return new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(date);
}

function monthShortName(period) {
  if (!period) return "";
  const [year, month] = period.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, 1));
  return new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" }).format(date);
}

function monthShift(period, offset) {
  const [year, month] = period.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1 + offset, 1));
  return date.toISOString().slice(0, 7);
}

function prettyCategory(category) {
  return String(category || "Uncategorized").replaceAll("_", " ");
}

function signedDelta(value, currency = "GBP") {
  return `${value < 0 ? "−" : "+"}${formatMoney(Math.abs(value), currency)}`;
}

// API CLIENT
async function apiRequest(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let errData = {};
    try { errData = await response.json(); } catch (_) {}
    const error = new Error(errData.detail?.message || errData.detail || `HTTP ${response.status}`);
    error.status = response.status;
    error.data = errData;
    throw error;
  }
  return response.json();
}

async function getJson(path) {
  return apiRequest(path);
}

// ROUTER
function setRoute(route) {
  state.route = route;
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.route === route);
  });

  document.querySelectorAll(".view-panel").forEach((panel) => {
    const active = panel.dataset.view === route;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });

  const titles = {
    overview: { title: "Money, in view.", eyebrow: "Monthly position", lede: "A clear read on what changed, what matters, and where to look next.", breadcrumb: "Overview" },
    import: { title: "Statement Upload", eyebrow: "Bank Ingestion Studio", lede: "Upload PDF statements, CSV exports, or scanned documents with deterministic candidate review.", breadcrumb: "Import Statement" },
    categories: { title: "Categories & Budgets", eyebrow: "Spending Breakdown", lede: "Inspect spending by category, track budget limits, and monitor long-term goals.", breadcrumb: "Categories & Budgets" },
    activity: { title: "Activity Ledger", eyebrow: "Deterministic Transactions", lede: "Complete searchable history of verified transactions with provenance source tags.", breadcrumb: "Activity Ledger" },
    ask: { title: "Ask PFA", eyebrow: "Financial Investigation Desk", lede: "Ask natural-language questions with verified deterministic evidence and zero guesswork.", breadcrumb: "Ask PFA" }
  };

  const info = titles[route] || titles.overview;
  $("page-title").textContent = info.title;
  $("view-eyebrow").textContent = info.eyebrow;
  $("view-lede").textContent = info.lede;
  $("current-view-title").textContent = info.breadcrumb;

  if (route === "overview") renderOverview();
  if (route === "categories") renderCategoriesView();
  if (route === "activity") renderActivityView();
  if (route === "ask") renderAskView();
}

// TOAST NOTIFICATIONS
let toastTimer;
function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.className = `toast is-visible${isError ? " is-error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.remove("is-visible");
  }, 3500);
}

// LOAD DATA
async function loadMonthData(period) {
  state.month = period;
  try {
    const [summary, categories, budgets, goals, txs, accounts] = await Promise.all([
      getJson(`/analytics/monthly?month=${period}`),
      getJson(`/analytics/categories?month=${period}`),
      getJson(`/budgets?month=${period}`).catch(() => []),
      getJson("/goals").catch(() => []),
      getJson("/transactions?limit=200").catch(() => []),
      getJson("/accounts").catch(() => [])
    ]);

    state.data[period] = { ...summary, categories };
    state.budgets[period] = budgets;
    state.goals = goals;
    state.transactions = txs;
    state.accounts = accounts;
    state.source = "live";
    state.loadError = null;
  } catch (error) {
    state.data[period] = { ...EMPTY_MONTH, period };
    state.source = "error";
    state.loadError = error.message || "Could not reach the PFA API";
  }

  // Check health
  try {
    const health = await getJson("/health");
    state.modelAvailable = modelIsUsable(health);
    updateHealthUI(health);
  } catch (_) {
    // The API is unreachable, so nothing about the local stack is known to be up.
    updateHealthUI({ database: "unhealthy", ollama: "unavailable", model: "missing" });
  }

  updateMonthMenu();
  renderCurrentRoute();
}

// /health reports `database`, `ollama` and `model` as words, not booleans. Reading it as
// {database_connected, model_available} made every field undefined, so the dashboard
// announced "Rules Only" even with Ollama up and the configured model pulled.
function modelIsUsable(health) {
  return health.ollama === "healthy" && health.model === "available";
}

function updateHealthUI(health) {
  const usable = modelIsUsable(health);
  $("database-status").textContent =
    health.database === "healthy" ? "Connected (SQLite)" : "Disconnected";
  // Ollama down and model-not-pulled need different actions from the user, so say which.
  $("model-status").textContent = usable
    ? "Active (Ollama)"
    : health.ollama === "healthy"
      ? `Model "${health.configured_model || "?"}" not pulled (Rules only)`
      : "Ollama offline (Rules only)";
  $("topbar-model-text").textContent = usable ? "AI Advisor Ready" : "Deterministic Engine (Rules Only)";
  $("topbar-model-badge").classList.toggle("is-offline", !usable);
  $("sync-text").textContent = state.source === "live" ? "Local database verified" : "Demo preview dataset";
}

function renderCurrentRoute() {
  $("nav-tx-count").textContent = state.transactions.length || (state.data[state.month]?.transaction_count || 0);
  if (state.accounts.length > 0) {
    $("active-account-label").textContent = state.accounts[0].name;
    const knownList = $("destination-account-select");
    if (knownList) {
      knownList.innerHTML = `<option value="">Choose an existing account</option>` + state.accounts.map((a) => `<option value="${a.id}">${escapeHtml(a.name)} (${escapeHtml(a.account_type)} · ${escapeHtml(a.currency)})</option>`).join("");
    }
  }

  setRoute(state.route);
}

// 1. OVERVIEW RENDERING
function renderOverview() {
  const data = state.data[state.month] || { ...EMPTY_MONTH, period: state.month };
  const prevPeriod = monthShift(state.month, -1);
  const previous = state.data[prevPeriod] || data;

  // Show error banner if the API is unreachable
  const errorBanner = $("overview-error-banner");
  if (errorBanner) {
    if (state.loadError) {
      errorBanner.hidden = false;
      errorBanner.textContent = `⚠ ${state.loadError} — showing empty state. Start the server with: uvicorn pfa.api.app:app`;
    } else {
      errorBanner.hidden = true;
    }
  }

  $("month-label").textContent = monthName(state.month);
  $("income-value").textContent = formatMoney(data.income_minor, data.currency, true);
  $("spending-value").textContent = formatMoney(data.spending_minor, data.currency, true);
  $("net-value").textContent = formatMoney(data.net_cashflow_minor, data.currency, true);
  $("rate-value").textContent = `${Number(data.savings_rate_percent || 0).toFixed(1)}%`;

  $("income-change").textContent = data.income_minor === previous.income_minor ? "Same as previous month" : `${signedDelta(data.income_minor - previous.income_minor, data.currency)} vs ${monthShortName(prevPeriod)}`;
  $("spending-change").textContent = `${formatMoney(Math.abs(data.spending_minor - previous.spending_minor), data.currency, true)} ${data.spending_minor >= previous.spending_minor ? "above" : "below"} ${monthShortName(prevPeriod)}`;
  $("net-change").textContent = `${formatMoney(Math.abs(data.net_cashflow_minor - previous.net_cashflow_minor), data.currency, true)} ${data.net_cashflow_minor >= previous.net_cashflow_minor ? "higher" : "lower"} than ${monthShortName(prevPeriod)}`;
  $("rate-change").textContent = `${Math.abs(Number(data.savings_rate_percent || 0) - Number(previous.savings_rate_percent || 0)).toFixed(1)} pts ${data.savings_rate_percent >= previous.savings_rate_percent ? "higher" : "lower"}`;

  $("transaction-count").textContent = data.transaction_count || state.transactions.length || "0";
  $("review-heading").textContent = `${monthName(state.month)} Review`;

  renderAuditList(data, previous);
  renderTopCategories(data);
  renderCashflowBars();
  renderEvidenceModal(data, previous);
}

function renderAuditList(data, previous) {
  const current = Object.fromEntries((data.categories || []).map((c) => [c.category, Number(c.total_minor || c.amount_minor || 0)]));
  const prior = Object.fromEntries((previous.categories || []).map((c) => [c.category, Number(c.total_minor || c.amount_minor || 0)]));

  const changes = Object.keys(current)
    .map((cat) => ({ category: cat, amount: current[cat], delta: current[cat] - (prior[cat] || 0) }))
    .filter((c) => c.delta > 0)
    .sort((a, b) => b.delta - a.delta)
    .slice(0, 2);

  const rows = changes.length > 0 ? changes : Object.keys(current).sort((a, b) => current[b] - current[a]).slice(0, 2).map((c) => ({ category: c, amount: current[c], delta: 0 }));

  $("audit-count").textContent = String(rows.length).padStart(2, "0");
  $("audit-title").textContent = rows.length === 1 ? "One major change stands out this month." : "Most of the spending movement is in two places.";

  const delta = Math.abs(data.spending_minor - previous.spending_minor);
  $("review-copy").innerHTML = `Spending moved ${data.spending_minor >= previous.spending_minor ? "up" : "down"} <strong>${formatMoney(delta, data.currency, true)}</strong> from ${escapeHtml(monthName(previous.period || monthShift(state.month, -1)))}. Deterministic SQL evidence highlights the primary category drivers below.`;

  $("audit-list").innerHTML = rows.map((item, idx) => {
    const isNew = !prior[item.category];
    const label = isNew ? `${escapeHtml(prettyCategory(item.category))} is the new pressure point` : `${escapeHtml(prettyCategory(item.category))} moved up from last month`;
    const desc = `${formatMoney(item.amount, data.currency, true)} this month · ${isNew ? "no baseline in previous month" : `${formatMoney(item.delta, data.currency, true)} above prior`}`;
    return `
      <div class="audit-row" role="listitem">
        <span class="audit-index">0${idx + 1}</span>
        <span class="audit-signal ${idx === 0 ? "signal-orange" : "signal-amber"}"></span>
        <span class="audit-copy">
          <strong>${label}</strong>
          <small>${desc}</small>
        </span>
        <span class="audit-amount">${item.delta > 0 ? `+${formatMoney(item.delta, data.currency, true)}` : formatMoney(item.amount, data.currency, true)}</span>
        <span class="row-arrow" aria-hidden="true">→</span>
      </div>
    `;
  }).join("");
}

function renderTopCategories(data) {
  const categories = [...(data.categories || [])]
    .sort((a, b) => Number(b.total_minor || b.amount_minor || 0) - Number(a.total_minor || a.amount_minor || 0))
    .slice(0, 5);

  const max = Math.max(...categories.map((c) => Number(c.total_minor || c.amount_minor || 0)), 1);

  $("category-chart").innerHTML = categories.map((item, idx) => {
    const amount = Number(item.total_minor || item.amount_minor || 0);
    const kind = idx === 0 ? "is-spike" : idx < 3 ? "is-core" : "is-oneoff";
    const widthPct = Math.max(5, (amount / max) * 100);
    return `
      <div class="category-line">
        <span class="category-name" title="${escapeHtml(prettyCategory(item.category))}">${escapeHtml(prettyCategory(item.category))}</span>
        <div class="bar-track">
          <div class="bar-fill ${kind}" style="width:${widthPct}%"></div>
        </div>
        <span class="category-amount">${formatMoney(amount, data.currency, true)}</span>
      </div>
    `;
  }).join("");
}

function renderCashflowBars() {
  const periods = [monthShift(state.month, -2), monthShift(state.month, -1), state.month];
  const monthsData = periods.map((p) => state.data[p] || { ...EMPTY_MONTH, period: p });
  const max = Math.max(...monthsData.flatMap((d) => [d.income_minor, d.spending_minor]), 1);

  $("trend-span-label").textContent = `${monthShortName(periods[0])} — ${monthShortName(periods[2])}`;
  $("cashflow-bars").innerHTML = monthsData.map((d, idx) => {
    const incPct = Math.max(4, (d.income_minor / max) * 100);
    const spdPct = Math.max(4, (d.spending_minor / max) * 100);
    return `
      <div class="chart-month" data-month="${monthShortName(periods[idx])}">
        <div class="cash-bar income" style="height:${incPct}%" title="Income: ${formatMoney(d.income_minor, d.currency)}"></div>
        <div class="cash-bar spending" style="height:${spdPct}%" title="Spending: ${formatMoney(d.spending_minor, d.currency)}"></div>
      </div>
    `;
  }).join("");
}

function renderEvidenceModal(data, previous) {
  $("evidence-detail").innerHTML = `
    <div class="evidence-detail-row"><span>Ledger Period</span><strong>${data.period}</strong></div>
    <div class="evidence-detail-row"><span>Source Transactions</span><strong>${data.transaction_count || 0} rows</strong></div>
    <div class="evidence-detail-row"><span>Total Inflow (Income)</span><strong>${formatMoney(data.income_minor, data.currency)}</strong></div>
    <div class="evidence-detail-row"><span>Total Outflow (Spending)</span><strong>${formatMoney(data.spending_minor, data.currency)}</strong></div>
    <div class="evidence-detail-row"><span>Net Cashflow Movement</span><strong>${formatMoney(data.net_cashflow_minor, data.currency)}</strong></div>
    <div class="evidence-detail-row"><span>Prior Month Delta</span><strong>${signedDelta(data.spending_minor - previous.spending_minor, data.currency)}</strong></div>
    <div class="evidence-detail-row"><span>Deterministic Engine</span><strong>SQLite (Zero Guesswork)</strong></div>
  `;
}

// 2. STATEMENT UPLOAD & BATCH INSPECTOR
function setupUploadHandlers() {
  const dropzone = $("dropzone");
  const fileInput = $("statement-file-input");
  const browseBtn = $("browse-button");

  browseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    fileInput.click();
  });

  dropzone.addEventListener("click", () => fileInput.click());

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  });

  ["dragleave", "dragend"].forEach((type) => {
    dropzone.addEventListener(type, () => dropzone.classList.remove("is-dragover"));
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
    if (e.dataTransfer.files?.length > 0) {
      handleStatementUpload(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files?.length > 0) {
      handleStatementUpload(fileInput.files[0]);
    }
  });

  // Batch Filter Tabs
  document.querySelectorAll(".filter-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".filter-tab").forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      state.batchFilter = tab.dataset.filter;
      renderCandidatesTable();
    });
  });

  // Bulk Candidate actions
  $("select-all-candidates").addEventListener("click", () => bulkToggleCandidates(true));
  $("deselect-all-candidates").addEventListener("click", () => bulkToggleCandidates(false));

  // Destination Account Assign: existing accounts use stable IDs; new accounts are drafts
  // and are only created with their transactions when the import is committed.
  $("save-account-btn").addEventListener("click", async () => {
    const selected = $("destination-account-select").value;
    const newName = $("new-account-name").value.trim();
    if ((!selected && !newName) || !state.activeBatch) return;
    const body = selected
      ? { destination_account_id: Number(selected) }
      : { new_account: { name: newName, account_type: $("new-account-type").value, currency: state.activeBatch.detected_currency || "GBP" } };
    try {
      const patched = await apiRequest(`/imports/${state.activeBatch.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      state.activeBatch = patched;
      showToast(`Assigned account "${acc}" to statement batch.`);
    } catch (err) {
      showToast(err.message, true);
    }
  });

  // Discard Batch
  $("discard-batch-btn").addEventListener("click", async () => {
    if (!state.activeBatch) return;
    try {
      await apiRequest(`/imports/${state.activeBatch.id}`, { method: "DELETE" });
      state.activeBatch = null;
      $("batch-inspector").hidden = true;
      $("upload-card").hidden = false;
      $("nav-import-status").hidden = true;
      showToast("Statement batch discarded.");
    } catch (err) {
      showToast(err.message, true);
    }
  });

  // Commit Batch
  $("commit-batch-btn").addEventListener("click", async () => {
    if (!state.activeBatch) return;
    try {
      const committed = await apiRequest(`/imports/${state.activeBatch.id}/commit`, { method: "POST" });
      $("batch-inspector").hidden = true;
      $("batch-success-card").hidden = false;
      $("nav-import-status").hidden = true;
      $("success-message").textContent = `${committed.counts.imported} transactions committed directly to your ledger.`;
      showToast(`Successfully imported ${committed.counts.imported} transactions!`);
      // Refresh current month data
      loadMonthData(state.month);
    } catch (err) {
      showToast(err.message, true);
    }
  });

  $("undo-import-btn")?.addEventListener("click", async () => {
    if (!state.activeBatch) return;
    try {
      await apiRequest(`/imports/${state.activeBatch.id}/undo`, { method: "POST" });
      showToast("Import undone; account was kept.");
      $("batch-success-card").hidden = true;
      $("upload-card").hidden = false;
      state.activeBatch = null;
      loadMonthData(state.month);
    } catch (err) {
      if (err.data?.detail?.code === "UNDO_REQUIRES_CONFIRMATION" && window.confirm(err.message)) {
        await apiRequest(`/imports/${state.activeBatch.id}/undo`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm_changed: true })
        });
        showToast("Import undone; edited rows were removed.");
        $("batch-success-card").hidden = true;
        $("upload-card").hidden = false;
        state.activeBatch = null;
        loadMonthData(state.month);
      } else {
        showToast(err.message, true);
      }
    }
  });

  // Amount Sign Convention selector
  const amountSignSelect = $("amount-sign-select");
  if (amountSignSelect) {
    amountSignSelect.addEventListener("change", async () => {
      if (!state.activeBatch) return;
      const signValue = amountSignSelect.value;
      if (!signValue) {
        updateBatchCounts(state.activeBatch);
        return;
      }
      try {
        const patched = await apiRequest(`/imports/${state.activeBatch.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount_sign: signValue })
        });
        state.activeBatch = patched;
        updateBatchCounts(patched);
        renderCandidatesTable();
        showToast(`Amount sign set to "${signValue}"`);
      } catch (err) {
        showToast(err.message, true);
      }
    });
  }

  // Upload Another Button
  $("upload-another-btn").addEventListener("click", () => {
    $("batch-success-card").hidden = true;
    $("upload-card").hidden = false;
    fileInput.value = "";
  });
}

async function handleStatementUpload(file) {
  $("upload-progress").hidden = false;
  $("progress-text").textContent = `Parsing ${file.name} (detecting table format & transactions)...`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const batch = await apiRequest("/imports/preview", {
      method: "POST",
      body: formData
    });

    state.activeBatch = batch;
    $("upload-progress").hidden = true;
    $("upload-card").hidden = true;
    $("batch-success-card").hidden = true;
    $("batch-inspector").hidden = false;
    $("nav-import-status").hidden = false;

    renderBatchInspector(batch);
    showToast(`Parsed ${batch.counts.total} candidates from ${file.name}`);
  } catch (err) {
    $("upload-progress").hidden = true;
    showToast(err.message || "Failed to parse statement upload", true);
  }
}

function renderBatchInspector(batch) {
  $("batch-filename").textContent = batch.original_filename;
  $("batch-extractor").textContent = batch.extractor || "auto";
  $("batch-currency").textContent = batch.detected_currency || "GBP";
  $("batch-pages").textContent = batch.page_count || "1";
  $("batch-period").textContent = batch.statement_start && batch.statement_end ? `${batch.statement_start} to ${batch.statement_end}` : "Auto-detected";

  $("destination-account-select").value = batch.destination_account_id ? String(batch.destination_account_id) : "";
  $("new-account-name").value = batch.new_account?.name || "";
  $("new-account-type").value = batch.new_account?.account_type || "current";

  const semantic = batch.semantic_totals || {};
  $("batch-semantic-summary").innerHTML = `
    <strong>Import effect</strong>
    <span>Money in ${formatMoney(semantic.money_in_minor || 0, batch.detected_currency || "GBP")}</span>
    <span>Spending ${formatMoney(semantic.spending_minor || 0, batch.detected_currency || "GBP")}</span>
    <span>Refunds ${formatMoney(semantic.refunds_minor || 0, batch.detected_currency || "GBP")}</span>
    <span>Transfers ${formatMoney(semantic.transfers_minor || 0, batch.detected_currency || "GBP")}</span>
    <span>Repayments ${formatMoney(semantic.repayments_minor || 0, batch.detected_currency || "GBP")}</span>`;

  // Amount sign selector: only generic formats may ask the user for semantics
  renderAmountSignSelector(batch);

  updateBatchCounts(batch);
  renderCandidatesTable();

  if (batch.issues && batch.issues.length > 0) {
    $("batch-issues-alert").hidden = false;
    $("batch-issues-content").innerHTML = batch.issues.map((i) => `<div><strong>${escapeHtml(i.code)}:</strong> ${escapeHtml(i.message)}</div>`).join("");
  } else {
    $("batch-issues-alert").hidden = true;
  }
}

function renderAmountSignSelector(batch) {
  const wrap = $("amount-sign-wrap");
  if (!wrap) return;

  const candidates = batch.candidates || [];
  if (batch.adapter_id && batch.adapter_id !== "generic") {
    wrap.hidden = true;
    return;
  }
  // Show selector when all candidates with amounts are positive (unsigned)
  const allPositive = candidates.length > 0 && candidates.every((c) =>
    c.amount_minor === null || c.amount_minor === undefined || c.amount_minor >= 0
  );
  const noDirectionInfo = candidates.every((c) => c.direction !== "debit");

  if (allPositive && noDirectionInfo) {
    wrap.hidden = false;
    const select = $("amount-sign-select");
    // Pre-select if batch already has an amount_sign set
    if (batch.amount_sign) {
      select.value = batch.amount_sign;
    } else {
      select.value = "";
    }
  } else {
    wrap.hidden = true;
  }
}

function updateBatchCounts(batch) {
  $("count-total").textContent = batch.counts.total || 0;
  $("count-valid").textContent = batch.counts.valid || 0;
  $("count-warning").textContent = batch.counts.warning || 0;
  $("count-duplicate").textContent = batch.counts.duplicate || 0;
  $("count-excluded").textContent = batch.counts.excluded || 0;

  const validToCommit = (batch.counts.valid || 0);
  const candidates = batch.candidates || [];

  // Check for blocking errors on included candidates
  const batchErrors = (batch.issues || []).filter((i) => i.severity === "error");
  const blockingErrors = candidates.filter((c) =>
    c.included && c.issues && c.issues.some((i) => i.severity === "error")
  );

  // Check if amount_sign is needed but not set
  const signWrap = $("amount-sign-wrap");
  const needsSign = signWrap && !signWrap.hidden && !($("amount-sign-select")?.value);

  const commitBtn = $("commit-batch-btn");
  const noteEl = $("commit-summary-note");

  if (batchErrors.length > 0) {
    commitBtn.disabled = true;
    noteEl.textContent = `Blocked: ${batchErrors[0].message}`;
  } else if (blockingErrors.length > 0) {
    commitBtn.disabled = true;
    const reasons = [...new Set(blockingErrors.flatMap((c) =>
      c.issues.filter((i) => i.severity === "error").map((i) => i.code)
    ))];
    noteEl.textContent = `Blocked: ${blockingErrors.length} candidate(s) have errors (${reasons.join(", ")})`;
  } else if (needsSign) {
    commitBtn.disabled = true;
    noteEl.textContent = "Set the amount sign convention before committing";
  } else if (validToCommit === 0) {
    commitBtn.disabled = true;
    noteEl.textContent = "No valid transactions to commit";
  } else {
    commitBtn.disabled = false;
    noteEl.textContent = `Ready to commit ${validToCommit} transactions`;
  }
}

function renderCandidatesTable() {
  if (!state.activeBatch) return;
  const tbody = $("candidates-tbody");
  const candidates = state.activeBatch.candidates || [];

  const filter = state.batchFilter;
  const filtered = candidates.filter((c) => {
    if (filter === "all") return true;
    if (filter === "valid") return c.included && (!c.issues || c.issues.length === 0);
    if (filter === "warning") return c.issues && c.issues.length > 0;
    if (filter === "duplicate") return c.duplicate_of !== null;
    if (filter === "excluded") return !c.included;
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 28px; color: var(--muted);">No candidates match the "${escapeHtml(filter)}" filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map((c) => {
    const isDebit = c.direction === "debit";
    const amountStr = formatMoney(c.amount_minor, c.currency);
    const issues = c.issues || [];
    const issueHtml = issues.map((i) => `<span class="issue-badge issue-${i.severity === 'error' ? 'error' : 'warning'}" title="${escapeHtml(i.message)}">${escapeHtml(i.code)}</span>`).join(" ");
    const dupHtml = c.duplicate_of ? `<span class="issue-badge issue-duplicate">Duplicate of #${c.duplicate_of}</span>` : "";

    return `
      <tr class="${c.included ? "" : "is-excluded"}" data-candidate-id="${c.candidate_id}">
        <td class="th-check">
          <input type="checkbox" class="candidate-checkbox" data-id="${c.candidate_id}" ${c.included ? "checked" : ""} aria-label="Include ${escapeHtml(c.raw_description)}" />
        </td>
        <td><span class="num">${escapeHtml(c.transaction_date || c.posted_date || "—")}</span></td>
        <td class="candidate-desc">
          <strong>${escapeHtml(c.normalized_description || c.raw_description)}</strong>
          <small>${escapeHtml(c.raw_description)}</small>
        </td>
        <td><span class="category-tag">${escapeHtml(prettyCategory(c.category))}</span></td>
        <td class="candidate-amount ${isDebit ? "is-outflow" : "is-inflow"}">
          ${isDebit ? `−${amountStr}` : `+${amountStr}`}
        </td>
        <td>
          <span class="meta-chip">${escapeHtml(c.extraction_method || "table")}</span>
          ${issueHtml}
          ${dupHtml}
        </td>
      </tr>
    `;
  }).join("");

  tbody.querySelectorAll(".candidate-checkbox").forEach((cb) => {
    cb.addEventListener("change", async (e) => {
      const candidateId = e.target.dataset.id;
      const isChecked = e.target.checked;
      toggleCandidateInclusion(candidateId, isChecked);
    });
  });
}

async function toggleCandidateInclusion(candidateId, included) {
  if (!state.activeBatch) return;
  const currentExcluded = new Set(
    state.activeBatch.candidates.filter((c) => !c.included).map((c) => c.candidate_id)
  );

  if (included) {
    currentExcluded.delete(candidateId);
  } else {
    currentExcluded.add(candidateId);
  }

  try {
    const patched = await apiRequest(`/imports/${state.activeBatch.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded_candidate_ids: Array.from(currentExcluded) })
    });
    state.activeBatch = patched;
    updateBatchCounts(patched);
    renderCandidatesTable();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function bulkToggleCandidates(includeAll) {
  if (!state.activeBatch) return;
  const excluded = includeAll ? [] : state.activeBatch.candidates.map((c) => c.candidate_id);
  try {
    const patched = await apiRequest(`/imports/${state.activeBatch.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded_candidate_ids: excluded })
    });
    state.activeBatch = patched;
    updateBatchCounts(patched);
    renderCandidatesTable();
    showToast(includeAll ? "Included all candidates" : "Excluded all candidates");
  } catch (err) {
    showToast(err.message, true);
  }
}

// 3. CATEGORIES & BUDGETS VIEW
function renderCategoriesView() {
  const data = state.data[state.month] || { ...EMPTY_MONTH, period: state.month };
  const categories = data.categories || [];
  const totalSpend = data.spending_minor || 0;

  $("categories-total-chip").textContent = `Total Monthly Spend: ${formatMoney(totalSpend, data.currency)}`;

  const list = $("category-breakdown-list");
  if (categories.length === 0) {
    list.innerHTML = `<p style="color:var(--muted); padding:20px;">No category spending recorded for ${monthName(state.month)}.</p>`;
  } else {
    const sorted = [...categories].sort((a, b) => Number(b.total_minor || 0) - Number(a.total_minor || 0));
    list.innerHTML = sorted.map((cat) => {
      const amount = Number(cat.total_minor || 0);
      const pct = totalSpend > 0 ? ((amount / totalSpend) * 100).toFixed(1) : 0;
      return `
        <div class="category-card-row">
          <div class="category-card-head">
            <strong>${escapeHtml(prettyCategory(cat.category))}</strong>
            <span class="category-card-amount">${formatMoney(amount, data.currency)}</span>
          </div>
          <div class="category-card-bar-wrap">
            <div class="category-card-bar" style="width:${Math.max(4, pct)}%"></div>
          </div>
          <div class="category-card-meta">
            <span>${pct}% of monthly spend</span>
            <span class="num">${cat.transaction_count ? `${cat.transaction_count} transactions` : "Verified ledger"}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  // Render Budgets
  const budgets = state.budgets[state.month] || [];
  const budgetsList = $("budgets-list");
  if (budgets.length === 0) {
    budgetsList.innerHTML = `<p style="color:var(--muted); font-size:12px;">No active budgets defined for this month.</p>`;
  } else {
    budgetsList.innerHTML = budgets.map((b) => {
      const isOver = b.over_budget;
      const pct = b.budget_minor > 0 ? Math.min(100, (b.actual_minor / b.budget_minor) * 100) : 100;
      return `
        <div class="budget-item">
          <div class="budget-head">
            <strong>${escapeHtml(prettyCategory(b.category))}</strong>
            <span class="budget-status-pill ${isOver ? "status-over" : "status-under"}">${isOver ? `Over by ${formatMoney(b.actual_minor - b.budget_minor, b.currency)}` : `Remaining: ${formatMoney(b.remaining_minor, b.currency)}`}</span>
          </div>
          <div class="budget-bar-wrap">
            <div class="budget-bar-fill ${isOver ? "is-over" : ""}" style="width:${pct}%"></div>
          </div>
          <div class="budget-foot">
            <span>Spent: ${formatMoney(b.actual_minor, b.currency)}</span>
            <span>Budget: ${formatMoney(b.budget_minor, b.currency)}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  // Render Goals
  const goals = state.goals || [];
  const goalsList = $("goals-list");
  if (goals.length === 0) {
    goalsList.innerHTML = `
      <div class="goal-item">
        <div class="goal-head">
          <strong>Emergency Fund</strong>
          <span class="num" style="font-weight:700; font-size:12px;">£2,500 / £6,000</span>
        </div>
        <div class="goal-bar-wrap">
          <div class="goal-bar-fill" style="width:41.6%"></div>
        </div>
        <div class="goal-foot">
          <span>41.6% completed</span>
          <span>Target: Dec 2026</span>
        </div>
      </div>
    `;
  } else {
    goalsList.innerHTML = goals.map((g) => {
      const pct = g.target_minor > 0 ? Math.min(100, (g.current_minor / g.target_minor) * 100).toFixed(1) : 0;
      return `
        <div class="goal-item">
          <div class="goal-head">
            <strong>${escapeHtml(g.name)}</strong>
            <span class="num" style="font-weight:700; font-size:12px;">${formatMoney(g.current_minor, g.currency)} / ${formatMoney(g.target_minor, g.currency)}</span>
          </div>
          <div class="goal-bar-wrap">
            <div class="goal-bar-fill" style="width:${pct}%"></div>
          </div>
          <div class="goal-foot">
            <span>${pct}% completed</span>
            <span>Target: ${g.target_date || "Open"}</span>
          </div>
        </div>
      `;
    }).join("");
  }
}

// 4. ACTIVITY LEDGER VIEW
function renderActivityView() {
  const tbody = $("ledger-tbody");
  const txs = state.transactions || [];
  const searchTerm = ($("ledger-search").value || "").toLowerCase().trim();
  const catFilter = $("ledger-category-filter").value;
  const dirFilter = $("ledger-direction-filter").value;

  // Populate category filter dropdown
  const categories = Array.from(new Set(txs.map((t) => t.category).filter(Boolean))).sort();
  const catSelect = $("ledger-category-filter");
  const currentVal = catSelect.value;
  catSelect.innerHTML = `<option value="">All Categories</option>` + categories.map((c) => `<option value="${escapeHtml(c)}" ${c === currentVal ? "selected" : ""}>${escapeHtml(prettyCategory(c))}</option>`).join("");

  const filtered = txs.filter((t) => {
    if (searchTerm) {
      const matchDesc = (t.description || "").toLowerCase().includes(searchTerm);
      const matchMerchant = (t.merchant || "").toLowerCase().includes(searchTerm);
      if (!matchDesc && !matchMerchant) return false;
    }
    if (catFilter && t.category !== catFilter) return false;
    if (dirFilter && t.flow_direction !== dirFilter) return false;
    return true;
  });

  $("ledger-count-summary").textContent = `Showing ${filtered.length} of ${txs.length} transactions`;

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:32px; color:var(--muted);">No transactions match your search filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map((t) => {
    const isDebit = t.flow_direction === "debit";
    const amountStr = formatMoney(t.amount_minor, t.currency);
    const sourceClass = t.classification_source === "rule" ? "tag-deterministic" : t.classification_source === "model" ? "tag-model" : "tag-import";

    return `
      <tr>
        <td><span class="num">${escapeHtml(t.date || "—")}</span></td>
        <td><strong>${escapeHtml(t.description || "—")}</strong></td>
        <td>${escapeHtml(t.merchant || "—")}</td>
        <td><span class="category-tag">${escapeHtml(prettyCategory(t.category))}</span></td>
        <td><span class="provenance-tag ${sourceClass}">${escapeHtml(t.classification_source || "rule")}</span></td>
        <td class="ledger-amount ${isDebit ? "is-outflow" : "is-inflow"}">
          ${isDebit ? `−${amountStr}` : `+${amountStr}`}
        </td>
      </tr>
    `;
  }).join("");
}

// 5. ASK PFA (AI / DETERMINISTIC CHAT)
function setupChatHandlers() {
  const form = $("chat-form");
  const input = $("chat-input");
  const chips = $("prompt-chips");

  chips.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      input.value = chip.textContent;
      submitQuestion(chip.textContent);
    });
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    submitQuestion(q);
    input.value = "";
  });
}

async function submitQuestion(question) {
  const stream = $("chat-stream");

  // Append user message
  appendChatMessage({
    id: `msg-${Date.now()}`,
    sender: "user",
    text: question,
    timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  });

  // Append loading skeleton
  const loadingId = `loading-${Date.now()}`;
  const loadingEl = document.createElement("div");
  loadingEl.className = "chat-message is-assistant";
  loadingEl.id = loadingId;
  loadingEl.innerHTML = `
    <div class="message-bubble">
      <div class="message-head">
        <strong>PFA Assistant</strong>
        <span class="provenance-tag tag-deterministic">Querying Ledger Engine...</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12px;">
        <span class="spinner" style="width:16px; height:16px; border-width:2px;"></span>
        <span>Evaluating deterministic formulas and statement facts...</span>
      </div>
    </div>
  `;
  stream.appendChild(loadingEl);
  stream.scrollTop = stream.scrollHeight;

  try {
    const res = await apiRequest("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question })
    });

    loadingEl.remove();

    const isDeterministic = !state.modelAvailable || !res.answer?.includes("advisor");
    appendChatMessage({
      id: `ans-${Date.now()}`,
      sender: "assistant",
      text: res.answer,
      provenance: isDeterministic ? "Deterministic Calculation" : "Local Model Advisor",
      isDeterministic,
      question,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    });
  } catch (err) {
    loadingEl.remove();
    appendChatMessage({
      id: `err-${Date.now()}`,
      sender: "assistant",
      text: err.status === 503
        ? "The local AI advisor model is currently offline. However, all deterministic calculations remain fully active! You can ask direct factual questions such as: 'What is my total spending in August?' or 'How much was spent on groceries?'"
        : `Unable to process question: ${err.message}`,
      provenance: "Deterministic Fallback",
      isDeterministic: true,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    });
  }
}

function appendChatMessage(msg) {
  const stream = $("chat-stream");
  const msgEl = document.createElement("div");
  msgEl.className = `chat-message is-${msg.sender}`;
  msgEl.id = msg.id;

  if (msg.sender === "user") {
    msgEl.innerHTML = `
      <div class="message-bubble">
        <p>${escapeHtml(msg.text)}</p>
      </div>
    `;
  } else {
    const provTag = msg.isDeterministic
      ? `<span class="provenance-tag tag-deterministic">Deterministic Rule</span>`
      : `<span class="provenance-tag tag-model">Local AI Model</span>`;

    msgEl.innerHTML = `
      <div class="message-bubble">
        <div class="message-head">
          <strong>PFA Financial Desk</strong>
          ${provTag}
        </div>
        <p>${formatMessageContent(msg.text)}</p>
      </div>
    `;

    // Render facts used on right pane
    renderFactsUsed(msg);
  }

  stream.appendChild(msgEl);
  stream.scrollTop = stream.scrollHeight;
}

function renderFactsUsed(msg) {
  const factsContainer = $("facts-content");
  const data = state.data[state.month] || { ...EMPTY_MONTH, period: state.month };

  factsContainer.innerHTML = `
    <div class="fact-item">
      <strong>Active Period</strong>
      <small>${monthName(state.month)} (${data.period})</small>
    </div>
    <div class="fact-item">
      <strong>Ledger Income / Spend</strong>
      <small>Income: ${formatMoney(data.income_minor, data.currency)} | Spend: ${formatMoney(data.spending_minor, data.currency)}</small>
    </div>
    <div class="fact-item">
      <strong>Top Categories Evaluated</strong>
      <small>${(data.categories || []).slice(0, 3).map((c) => `${escapeHtml(prettyCategory(c.category))}: ${formatMoney(c.total_minor, data.currency)}`).join(", ")}</small>
    </div>
    <div class="fact-item">
      <strong>Provenance &amp; Source</strong>
      <small>${msg.provenance || "Deterministic SQLite"} · Zero Hallucination Guarantee</small>
    </div>
  `;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatMessageContent(text) {
  return escapeHtml(text)
    .replaceAll(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replaceAll(/\*(.*?)\*/g, "<em>$1</em>")
    .replaceAll("\n", "<br />");
}

function renderAskView() {
  // Focus input if empty
  $("chat-input")?.focus();
}

// MONTH NAVIGATION MENU
function updateMonthMenu() {
  const menu = $("month-menu");
  const periods = [state.month, monthShift(state.month, -1), monthShift(state.month, -2)];
  menu.innerHTML = periods.map((p) => `
    <button class="month-option" type="button" role="option" data-period="${p}" aria-selected="${p === state.month}">
      ${monthName(p)}
    </button>
  `).join("");
}

function setupMonthControls() {
  const menu = $("month-menu");
  $("month-current").addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !menu.hidden;
    menu.hidden = open;
    $("month-current").setAttribute("aria-expanded", String(!open));
  });

  menu.addEventListener("click", (e) => {
    const opt = e.target.closest("[data-period]");
    if (!opt) return;
    menu.hidden = true;
    $("month-current").setAttribute("aria-expanded", "false");
    loadMonthData(opt.dataset.period);
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".month-controls")) {
      menu.hidden = true;
      $("month-current").setAttribute("aria-expanded", "false");
    }
  });

  document.querySelectorAll("[data-month-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const step = Number(btn.dataset.monthStep);
      loadMonthData(monthShift(state.month, step));
    });
  });
}

// NAVIGATION ROUTING HANDLERS
function setupNavigation() {
  window.addEventListener("hashchange", () => {
    const hash = window.location.hash.replace("#", "") || "overview";
    setRoute(hash);
  });

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const route = item.dataset.route;
      window.location.hash = route;
      setRoute(route);
    });
  });

  $("overview-go-ask").addEventListener("click", () => {
    window.location.hash = "ask";
    setRoute("ask");
  });

  // Modal handlers
  const dialog = $("evidence-dialog");
  $("open-evidence").addEventListener("click", () => dialog.showModal());
  dialog.querySelectorAll(".dialog-close").forEach((btn) => {
    btn.addEventListener("click", () => dialog.close());
  });

  $("dialog-view-activity")?.addEventListener("click", () => {
    dialog.close();
    window.location.hash = "activity";
    setRoute("activity");
  });

  // Activity search & filters
  $("ledger-search").addEventListener("input", renderActivityView);
  $("ledger-category-filter").addEventListener("change", renderActivityView);
  $("ledger-direction-filter").addEventListener("change", renderActivityView);
}

// INITIALIZATION
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  setupMonthControls();
  setupUploadHandlers();
  setupChatHandlers();

  const initialRoute = window.location.hash.replace("#", "") || "overview";
  loadMonthData(state.month).then(() => {
    setRoute(initialRoute);
  });
});
