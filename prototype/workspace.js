"use strict";

// Load identifiers and names from the API so both sides use the same asset universe
let assets = {};
const startingWeights = { "DEMO-EQ1": 70, "DEMO-EQ2": 20 };

// Escape API strings before inserting them into table and dropdown HTML
const escapeHTML = value => String(value).replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[character]);
const $ = id => document.getElementById(id);

// Store 70 for 70%, not 0.7 Cash is calculated from whatever is left over
let weights = {};

// Each entry is the allocation before an edit Undo takes the last entry off this list
let history = [];

// Keep the preview separate so trying an allocation cannot change the current holdings
let proposed = null;

// A changed input or closed dialog cancels the previous comparison
let comparisonRequest;
let comparisonNumber = 0;

let statusFadeTimer;
let statusClearTimer;

// Keep feedback visible for 2 seconds A new action replaces the old message and restarts the timer
function showStatus(message) {
  clearTimeout(statusFadeTimer);
  clearTimeout(statusClearTimer);

  // Put dialog feedback inside the dialog, where it can be seen and announced while the page is inactive
  const output = $("scenarios-dialog").open ? $("scenario-feedback")
    : $("allocation-dialog").open ? $("allocation-status") : $("status");
  for (const id of ["status", "allocation-status", "scenario-feedback"]) {
    $(id).textContent = "";
    $(id).classList.remove("is-visible");
  }
  output.textContent = message;
  output.classList.add("is-visible");
  statusFadeTimer = setTimeout(() => {
    output.classList.remove("is-visible");

    // Clear the live-region text after the fade Error messages use separate elements and never expire
    statusClearTimer = setTimeout(() => { output.textContent = ""; }, 150);
  }, 2000);
}

// Object.values gives us just the weights Start the sum at 0 so an empty portfolio works
const total = values => Object.values(values).reduce((sum, value) => sum + value, 0);

// The maximum protects against tiny negative remainders from decimal arithmetic

// Validation below still rejects allocations that actually exceed 100%
const cash = values => Math.max(0, 100 - total(values));

// Round for display only Keep the original values for the next allocation calculation
const percent = value => `${value.toFixed(2)}%`;
function clearPreview() {
  comparisonNumber += 1;
  comparisonRequest?.abort();
  proposed = null;
  $("preview").hidden = true;
  $("preview-error").textContent = "";
  $("comparison-state").textContent = "";
  $("apply").disabled = true;
}
function change(next, message) {

  // Copy the old object before replacing it, otherwise Undo could lose the previous weights

  // A holdings edit also invalidates any preview made from the old allocation
  history.push({ ...weights }); weights = next; clearPreview(); render(); savePortfolio(); showStatus(message);
}
function render() {

  // These names come from our fixed list, not user text Escape them before using API data here

  // data-weight and data-remove let the table listeners identify the affected ticker
  $("holdings-body").innerHTML = Object.entries(weights).map(([ticker, weight]) => `<tr><td><strong>${ticker}</strong><small>${escapeHTML(assets[ticker].name)}</small></td><td class="number"><input class="weight" type="number" min="0" max="100" step="0.01" value="${weight}" aria-label="${ticker} weight in percent" aria-describedby="error" data-weight="${ticker}"></td><td class="number"><button class="remove" data-remove="${ticker}" aria-label="Remove ${ticker}">Remove</button></td></tr>`).join("");

  // Show the empty message only after the last holding is removed
  $("empty").hidden = Object.keys(weights).length > 0;
  $("cash").textContent = percent(cash(weights));
  $("allocation").innerHTML = Object.entries(weights).map(([ticker, weight]) => `<span style="width:${weight}%;background:${assets[ticker].colour}"></span>`).join("");

  // Include cash before sorting This also gives us a result when there are no holdings
  const largest = Object.entries({ ...weights, Cash: cash(weights) }).sort((a, b) => b[1] - a[1])[0];
  $("largest").textContent = `${largest[0]} ${percent(largest[1])}`;
  $("undo").disabled = history.length === 0;
  $("error").textContent = ""; search();

  // Refresh results after accepted edits, including Undo and Reset
  updateAnalysis();
}
function search() {

  // Lowercase names and identifiers so search ignores letter case and surrounding spaces
  const query = $("search").value.trim().toLowerCase();

  // Empty search includes every asset so there is no need to guess a name
  const matches = Object.entries(assets).filter(([ticker, asset]) =>
    `${ticker} ${asset.name}`.toLowerCase().includes(query)
  );
  $("search-results").innerHTML = matches.map(([ticker, asset]) => {

    // Held assets stay visible but cannot be added a second time
    const held = ticker in weights;
    return `<button type="button" data-add="${escapeHTML(ticker)}" ${held ? "disabled" : ""}>${held ? "Held" : "Add"} ${escapeHTML(ticker)} · ${escapeHTML(asset.name)}</button>`;
  }).join("");
  if (!matches.length) $("search-results").innerHTML = "<p>No matching assets.</p>";
  setDropdown("search", "search-results", document.activeElement === $("search"));
}

// Enter in the search field must not reload the page and erase the session
$("search-form").addEventListener("submit", event => event.preventDefault());
$("search").addEventListener("input", search);
$("search-results").addEventListener("click", event => {

  // Listen on the container because search replaces its buttons on each keystroke
  const ticker = event.target.dataset.add; if (!ticker) return;
  $("search").value = ""; change({ ...weights, [ticker]: 0 }, `${ticker} added at 0%.`);

  // Move to the new weight field so the user can type an allocation straight away
  document.querySelector(`[data-weight="${ticker}"]`).focus();
});
$("holdings-body").addEventListener("change", event => {

  // Commit when the field changes, rather than rebuilding the row after every digit
  const ticker = event.target.dataset.weight; if (!ticker) return;
  const value = Number(event.target.value);

  // Try the edit in a separate object first Invalid input must not reach saved state
  const next = { ...weights, [ticker]: value };

  // Number('') is 0, so check blank input separately The small tolerance allows rounding noise
  if (event.target.value === "" || !Number.isFinite(value) || value < 0 || value > 100 || total(next) > 100 + 1e-8) {
    $("error").textContent = `Cannot change ${ticker}: enter 0–100% and keep invested weights at or below 100%. Previous weight restored.`;
    event.target.value = weights[ticker]; return;
  }
  change(next, `${ticker} updated to ${percent(value)}. Cash is ${percent(cash(next))}.`);
  document.querySelector(`[data-weight="${ticker}"]`).focus();
});
$("holdings-body").addEventListener("click", event => {

  // Removing a key automatically releases its weight to cash on the next render
  const ticker = event.target.dataset.remove; if (!ticker) return;
  const next = { ...weights }; delete next[ticker]; change(next, `${ticker} removed; its allocation is now cash.`); $("search").focus();
});

// Do not call change() here: that would add another history entry while undoing one
$("undo").addEventListener("click", () => { if (!history.length) return; weights = history.pop(); clearPreview(); render(); savePortfolio(); showStatus("Previous allocation restored."); });

// Reset is a normal edit so it can be undone too
$("reset").addEventListener("click", () => change({ ...startingWeights }, "Example restored."));
// Once either input changes, the old preview no longer describes the requested change
for (const id of ["candidate", "target"]) $(id).addEventListener("input", clearPreview);
$("preview-form").addEventListener("submit", event => {
  event.preventDefault(); clearPreview();
  const ticker = $("candidate").value, target = Number($("target").value);
  if (!Number.isFinite(target) || target < 0 || target > 100) return;

  // Exclude the selected asset from the pool being scaled The pool includes cash
  const other = 100 - (weights[ticker] || 0);

  // A 100% holding leaves no other funded positions to scale Avoid dividing by 0
  if (other < 1e-8 && target < 100) { $("preview-error").textContent = "No other funded positions. Lower this holding in the editor first."; return; }

  // Adding a new asset at 15% gives a factor of 0.85: an existing 70% becomes 59.5%
  const factor = other > 0 ? (100 - target) / other : 0;

  // Scale every other holding, then set the selected asset to its requested weight

  // The remaining cash scales by the same factor because it is calculated as the remainder
  // Trim tiny fractional tails so floating-point noise cannot push funded weights above 100%
  proposed = Object.fromEntries(Object.entries(weights).filter(([key]) => key !== ticker).map(([key, value]) => [key, Math.floor(value * factor * 1e10) / 1e10]));
  proposed[ticker] = target;

  // Include tickers from both versions, without duplicates, so new assets appear in the table
  const rows = [...new Set([...Object.keys(weights), ...Object.keys(proposed)])];
  $("preview-body").innerHTML = rows.map(key => `<tr><th scope="row">${escapeHTML(key)}</th><td class="number">${percent(weights[key] || 0)}</td><td class="number">${percent(proposed[key] || 0)}</td></tr>`).join("") + `<tr><th scope="row">Cash</th><td class="number">${percent(cash(weights))}</td><td class="number">${percent(cash(proposed))}</td></tr>`;
  $("preview-title").textContent = `Set ${escapeHTML(ticker)} to ${percent(target)}`;
  $("preview").hidden = false;
  updateComparison();
});

// Apply uses the same edit path as a manual weight change, including the Undo snapshot
$("apply").addEventListener("click", () => {
  if (!proposed || $("apply").disabled) return;
  change({ ...proposed }, "Allocation applied.");

  // Close before focusing Undo because controls outside an open modal cannot receive focus
  $("allocation-dialog").close("applied");
});

// Discard only clears the preview It never writes to weights or history
$("discard").addEventListener("click", () => { clearPreview(); showStatus("Preview discarded. Holdings unchanged."); $("candidate-toggle").focus(); });

// showModal puts the explanation above the page and keeps keyboard focus inside it

// The browser handles Escape and the form's Close button without changing the allocation
$("about-analysis").addEventListener("click", () => {
  $("analysis-dialog").showModal();
});

// Return to the help button when the explanation closes so keyboard users keep their place
$("analysis-dialog").addEventListener("close", () => {
  $("about-analysis").focus();
});

// Start each visit with a fresh preview Opening the dialog never edits the holdings
$("open-allocation").addEventListener("click", () => {
  clearPreview();
  $("allocation-dialog").returnValue = "";
  $("allocation-dialog").showModal();
  $("candidate-toggle").focus();
});

// Close and Escape discard any unapplied proposal After Apply, put Undo within easy reach
$("allocation-dialog").addEventListener("close", () => {
  clearPreview();
  const destination = $("allocation-dialog").returnValue === "applied" ? "undo" : "open-allocation";
  $(destination).focus();

  // Once Apply closes the dialog, repeat its confirmation on the main page rather than leaving it hidden
  if (destination === "undo") showStatus("Allocation applied.");
});

// Keep the visible panel and its expanded state in sync for mouse and keyboard users
function setDropdown(triggerId, panelId, open) {
  // Position the dialog menu against the viewport so it cannot grow the dialog's scroll area
  if (panelId === "candidate-options" && open) {
    const field = $(triggerId).getBoundingClientRect();
    const panel = $(panelId);
    const below = window.innerHeight - field.bottom - 12;
    const above = field.top - 12;
    const useBelow = below >= Math.min(220, above);
    panel.style.left = `${field.left}px`;
    panel.style.width = `${field.width}px`;
    panel.style.maxHeight = `${Math.min(220, useBelow ? below : above)}px`;
    panel.style.top = useBelow ? `${field.bottom + 4}px` : "auto";
    panel.style.bottom = useBelow ? "auto" : `${window.innerHeight - field.top + 4}px`;
  }
  $(panelId).hidden = !open;
  $(triggerId).setAttribute("aria-expanded", String(open));
}

// Share dismissal and arrow-key navigation between the search and allocation pickers
function wireDropdown(rootId, triggerId, panelId) {
  const root = $(rootId), trigger = $(triggerId), panel = $(panelId);
  root.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      event.stopPropagation();
      trigger.focus();
      setDropdown(triggerId, panelId, false);
      return;
    }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const buttons = [...panel.querySelectorAll("button:not(:disabled)")];
    if (!buttons.length) return;
    event.preventDefault();
    setDropdown(triggerId, panelId, true);
    const index = buttons.indexOf(document.activeElement);
    const next = index < 0 ? (event.key === "ArrowDown" ? 0 : buttons.length - 1) : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
  });

  // Tab may leave the picker normally instead of trapping focus inside its options
  root.addEventListener("focusout", event => {
    if (!root.contains(event.relatedTarget)) setDropdown(triggerId, panelId, false);
  });
  document.addEventListener("pointerdown", event => {
    if (!root.contains(event.target)) setDropdown(triggerId, panelId, false);
  });
}
wireDropdown("search-dropdown", "search", "search-results");
wireDropdown("candidate-dropdown", "candidate-toggle", "candidate-options");
// Dismiss the floating menu when its field moves, rather than leaving it detached
window.addEventListener("resize", () => setDropdown("candidate-toggle", "candidate-options", false));
$("allocation-dialog").addEventListener("scroll", () => setDropdown("candidate-toggle", "candidate-options", false));
$("search").addEventListener("focus", search);

// Clicking an already focused field reopens the list after Escape
$("search").addEventListener("click", search);
$("candidate-toggle").addEventListener("click", () => {
  setDropdown("candidate-toggle", "candidate-options", $("candidate-options").hidden);
});
$("candidate-options").addEventListener("click", event => {
  const ticker = event.target.dataset.candidate;
  if (!ticker) return;
  $("candidate").value = ticker;
  $("candidate-value").textContent = `${ticker} · ${escapeHTML(assets[ticker].name)}`;
  clearPreview();
  setDropdown("candidate-toggle", "candidate-options", false);
  $("candidate-toggle").focus();
});

// Reset the picker when its dialog closes so reopening it never exposes an old options list
$("allocation-dialog").addEventListener("close", () => {
  setDropdown("candidate-toggle", "candidate-options", false);
});

// Hold one request at a time and ignore older results after the allocation changes
let analysisRequest;
let requestNumber = 0;
let datasetVersion;
const metricFields = {
  volatility: "annualised_volatility",
  return: "annualised_arithmetic_return",
  drawdown: "maximum_drawdown"
};

async function updateAnalysis() {
  const current = ++requestNumber;
  analysisRequest?.abort();
  const controller = new AbortController();
  analysisRequest = controller;
  const timeout = setTimeout(() => controller.abort(), 10000);

  // Hide stale numbers without changing their space while the new allocation is calculated
  for (const id of Object.keys(metricFields)) {
    $(id).classList.add("pending");
  }
  $("analysis-state").textContent = "Calculating";
  $("retry-analysis").hidden = true;
  try {
    const response = await fetch("/demo/analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weights }),
      signal: controller.signal
    });
    if (!response.ok) throw new Error("Analysis failed");
    const result = await response.json();
    if (current !== requestNumber) return;

    // A server restart with a different dataset must not silently change the comparison basis
    if (result.source !== "simulated" || result.dataset_version !== datasetVersion) {
      throw new Error("Dataset changed");
    }
    for (const field of Object.values(metricFields)) {
      if (!Number.isFinite(result[field])) throw new Error("Invalid result");
    }
    for (const [id, field] of Object.entries(metricFields)) {
      $(id).textContent = percent(result[field] * 100);
      $(id).classList.remove("pending");
      $(id).classList.add("available");
    }
    $("analysis-state").textContent = `${result.return_observations} simulated sessions`;
  } catch (error) {
    if (current !== requestNumber) return;

    // Failures stay visible until a successful retry or edit replaces them
    for (const id of Object.keys(metricFields)) {
      $(id).textContent = "Unavailable";
      $(id).classList.remove("pending", "available");
    }
    $("analysis-state").textContent = "Analysis unavailable. Check the server or reload the page.";
    $("retry-analysis").hidden = false;
  } finally {
    clearTimeout(timeout);
  }
}
$("retry-analysis").addEventListener("click", updateAnalysis);

function savePortfolio() {
  try {

    // Accessing localStorage can itself fail when the browser blocks persistence
    portfolioStorage.write(window.localStorage, weights, datasetVersion);
    $("save-state").textContent = "Saved in this browser.";
  } catch (error) {

    // Keep editing usable but do not claim that a blocked or full store saved the change
    $("save-state").textContent = "Changes could not be saved. Keep this tab open to retain them.";
  }
}

async function loadAssets() {
  // Keep controls disabled until there are names and identifiers to render safely
  for (const id of ["search", "reset", "open-allocation", "open-scenarios"]) $(id).disabled = true;
  try {
    const response = await fetch("/demo/history", { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Asset loading failed");
    const data = await response.json();
    if (data.source !== "simulated" || !Array.isArray(data.assets)) throw new Error("Invalid source");
    const colours = ["#25332f", "#566f62", "#8d9f94", "#b8c5bd"];
    assets = Object.fromEntries(data.assets.map((asset, index) => {
      if (!/^DEMO-[A-Z0-9]+$/.test(asset.symbol) || typeof asset.name !== "string") {
        throw new Error("Invalid asset");
      }
      return [asset.symbol, { name: asset.name, colour: colours[index % colours.length] }];
    }));
    if (![...Object.keys(startingWeights), "DEMO-BOND"].every(symbol => assets[symbol])) {
      throw new Error("Starting assets missing");
    }
    datasetVersion = data.dataset_version;
    weights = { ...startingWeights };
    try {

      // Restore only after the API supplies the current universe and dataset version
      const saved = portfolioStorage.read(window.localStorage, assets, datasetVersion);
      if (saved !== null) {
        weights = saved;
        $("save-state").textContent = "Saved in this browser.";
      }
    } catch (error) {

      // Preserve an unreadable save until the user deliberately edits the example
      $("save-state").textContent = "Saved portfolio could not load. Showing the example; your next edit will try to replace the save.";
    }
    $("candidate-options").innerHTML = Object.entries(assets).map(([ticker, asset]) =>
      `<button type="button" data-candidate="${escapeHTML(ticker)}">${escapeHTML(ticker)} · ${escapeHTML(asset.name)}</button>`
    ).join("");
    $("candidate").value = "DEMO-BOND";
    $("candidate-value").textContent = assets["DEMO-BOND"].name;
    for (const id of ["search", "reset", "open-allocation", "open-scenarios"]) $(id).disabled = false;
    render();
  } catch (error) {
    $("error").textContent = "Assets could not load. Start the API and reload this page at http://127.0.0.1:8000/workspace/";
    $("analysis-state").textContent = "Analysis unavailable";
  }
}
loadAssets();


async function updateComparison() {
  // Capture this request so an older response cannot replace a newer preview
  const current = ++comparisonNumber;
  const controller = new AbortController();
  comparisonRequest?.abort();
  comparisonRequest = controller;
  const timeout = setTimeout(() => controller.abort(), 10000);
  $("apply").disabled = true;
  $("comparison-results").hidden = true;
  $("comparison-state").textContent = "Calculating comparison";
  try {
    const response = await fetch("/demo/comparison", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current: { weights }, proposed: { weights: proposed } }),
      signal: controller.signal
    });
    if (response.status === 404) throw new Error("Server update required");
    if (!response.ok) throw new Error("Comparison failed");
    const result = await response.json();
    if (current !== comparisonNumber) return;

    // Both columns must describe the dataset loaded into this workspace
    for (const side of [result.current, result.proposed]) {
      if (side.source !== "simulated" || side.dataset_version !== datasetVersion) {
        throw new Error("Dataset changed");
      }
      for (const field of Object.values(metricFields)) {
        if (!Number.isFinite(side[field])) throw new Error("Invalid result");
      }
    }
    if (result.current.return_observations !== result.proposed.return_observations) {
      throw new Error("Mismatched sessions");
    }
    const labels = {
      annualised_volatility: "Annualised volatility",
      annualised_arithmetic_return: "Annualised return (arithmetic)",
      maximum_drawdown: "Maximum drawdown"
    };

    // Reuse the main report's percentage formatting and show both sides without claiming an improvement
    $("comparison-body").innerHTML = Object.entries(labels).map(([field, label]) =>
      `<tr><th scope="row">${label}</th><td class="number">${percent(result.current[field] * 100)}</td><td class="number">${percent(result.proposed[field] * 100)}</td></tr>`
    ).join("");
    $("comparison-results").hidden = false;
    $("comparison-state").textContent = `${result.current.return_observations} simulated sessions. Same assumptions as portfolio analysis.`;
    $("apply").disabled = false;
  } catch (error) {
    if (current !== comparisonNumber) return;

    // Keep the weights for inspection but require a successful preview before applying
    $("comparison-state").textContent = "";
    $("preview-error").textContent = error.message === "Server update required"
      ? "Restart the Assetwise server to load allocation comparisons, then try Preview change again."
      : "Comparison unavailable. Try Preview change again or close and keep your holdings.";
  } finally {
    clearTimeout(timeout);
  }
}


function renderScenarios() {
  $("scenario-list").replaceChildren();
  const items = scenarioStorage.read(window.localStorage);
  $("scenario-status").textContent = "";
  if (!items.length) {
    $("scenario-status").textContent = "No saved scenarios yet.";
    return;
  }
  const query = $("scenario-search").value.trim().toLowerCase();
  const matches = items.filter(item => item.name.toLowerCase().includes(query));
  if (!matches.length) $("scenario-status").textContent = "No matching scenarios.";
  for (const item of matches) {

    // Use textContent so a user-entered name is always text, never executable HTML
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = item.name;
    button.setAttribute("aria-label", `Load ${item.name}`);
    try {
      const allocation = scenarioStorage.allocation(item, assets, datasetVersion);
      button.addEventListener("click", () => {

        // Load through the normal edit path so autosave, analysis and Undo stay in sync
        $("scenarios-dialog").close();
        change({ ...allocation }, `Loaded ${item.name}.`);
        $("undo").focus();
      });
    } catch (error) {

      // Keep incompatible snapshots visible rather than dropping them from the collection
      button.disabled = true;
      button.textContent += " (unavailable for this dataset)";
    }
    row.append(button);
    $("scenario-list").append(row);
  }
}

$("open-scenarios").addEventListener("click", () => {
  $("scenario-error").textContent = "";
  $("scenario-status").textContent = "";
  $("scenario-search").value = "";
  setScenarioMenu(false);
  $("scenarios-dialog").showModal();
  try {
    renderScenarios();
  } catch (error) {
    $("scenario-error").textContent = "Saved scenarios could not load. Browser storage may be blocked or damaged.";
  }
});

$("save-scenario-form").addEventListener("submit", event => {
  event.preventDefault();
  $("scenario-error").textContent = "";
  try {

    // Read the latest collection on every save so another tab's new snapshots are retained
    scenarioStorage.add(window.localStorage, $("scenario-name").value, weights, datasetVersion);
    $("scenario-search").value = "";
    renderScenarios();
    $("scenario-name").value = "";
    showStatus("Scenario saved.");
  } catch (error) {
    $("scenario-error").textContent = error.message.startsWith("That name") || error.message.startsWith("Enter a name")
      ? error.message
      : "Scenario could not be saved. Browser storage may be blocked, full or damaged.";
  }
});

$("scenarios-dialog").addEventListener("close", () => $("open-scenarios").focus());


// Filter names without changing the saved collection or current holdings
$("scenario-search").addEventListener("input", () => {
  try {
    renderScenarios();
  } catch (error) {
    $("scenario-error").textContent = "Saved scenarios could not load. Try reopening this dialog.";
  }
});


function setScenarioMenu(open) {
  $("scenario-menu").hidden = !open;
  $("open-scenario-list").setAttribute("aria-expanded", String(open));
  if (!open) return;

  // Anchor the menu to Load while keeping its edges inside the viewport
  const field = $("open-scenario-list").getBoundingClientRect();
  const menu = $("scenario-menu");
  menu.style.left = `${Math.max(12, Math.min(field.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 12))}px`;
  menu.style.bottom = `${window.innerHeight - field.top + 4}px`;
  menu.querySelector(".scenario-scroll").style.maxHeight = `${Math.max(60, Math.min(200, field.top - 155))}px`;
}
$("open-scenario-list").addEventListener("click", () => {
  setScenarioMenu($("scenario-menu").hidden);
  if (!$("scenario-menu").hidden) $("scenario-search").focus();
});
$("scenarios-dialog").addEventListener("pointerdown", event => {
  if (!$("scenario-picker").contains(event.target)) setScenarioMenu(false);
});
$("scenario-picker").addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("scenario-menu").hidden) {
    event.preventDefault();
    event.stopPropagation();
    setScenarioMenu(false);
    $("open-scenario-list").focus();
  }
});
window.addEventListener("resize", () => setScenarioMenu(false));
