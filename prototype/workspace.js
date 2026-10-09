"use strict";

// Interaction study only No market data or financial-risk model is included
const assets = {

  // This is the whole searchable list for now These colours only identify bar segments
  AAPL: { name: "Apple", colour: "#25332f" },
  SPY: { name: "SPDR S&P 500 ETF", colour: "#566f62" },
  IEF: { name: "iShares 7–10 Year Treasury Bond ETF", colour: "#8d9f94" },
  GLD: { name: "SPDR Gold Shares", colour: "#b8c5bd" }
};
const $ = id => document.getElementById(id);

// Store 70 for 70%, not 0.7 Cash is calculated from whatever is left over
let weights = { AAPL: 70, SPY: 20 };

// Each entry is the allocation before an edit Undo takes the last entry off this list
let history = [];

// Keep the preview separate so trying an allocation cannot change the current holdings
let proposed = null;

let statusFadeTimer;
let statusClearTimer;

// Keep feedback visible for 2 seconds A new action replaces the old message and restarts the timer
function showStatus(message) {
  clearTimeout(statusFadeTimer);
  clearTimeout(statusClearTimer);

  // Put dialog feedback inside the dialog, where it can be seen and announced while the page is inactive
  const output = $("allocation-dialog").open ? $("allocation-status") : $("status");
  for (const id of ["status", "allocation-status"]) {
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
function clearPreview() { proposed = null; $("preview").hidden = true; $("preview-error").textContent = ""; }
function change(next, message) {

  // Copy the old object before replacing it, otherwise Undo could lose the previous weights

  // A holdings edit also invalidates any preview made from the old allocation
  history.push({ ...weights }); weights = next; clearPreview(); render(); showStatus(message);
}
function render() {

  // These names come from our fixed list, not user text Escape them before using API data here

  // data-weight and data-remove let the table listeners identify the affected ticker
  $("holdings-body").innerHTML = Object.entries(weights).map(([ticker, weight]) => `<tr><td><strong>${ticker}</strong><small>${assets[ticker].name}</small></td><td class="number"><input class="weight" type="number" min="0" max="100" step="0.01" value="${weight}" aria-label="${ticker} weight in percent" aria-describedby="error" data-weight="${ticker}"></td><td class="number"><button class="remove" data-remove="${ticker}" aria-label="Remove ${ticker}">Remove</button></td></tr>`).join("");

  // Show the empty message only after the last holding is removed
  $("empty").hidden = Object.keys(weights).length > 0;
  $("cash").textContent = percent(cash(weights));
  $("allocation").innerHTML = Object.entries(weights).map(([ticker, weight]) => `<span style="width:${weight}%;background:${assets[ticker].colour}"></span>`).join("");

  // Include cash before sorting This also gives us a result when there are no holdings
  const largest = Object.entries({ ...weights, Cash: cash(weights) }).sort((a, b) => b[1] - a[1])[0];
  $("largest").textContent = `${largest[0]} ${percent(largest[1])}`;
  $("undo").disabled = history.length === 0;
  $("error").textContent = ""; search();
}
function search() {

  // Lowercase both sides so typing apple or AAPL works Ignore spaces at either end
  const query = $("search").value.trim().toLowerCase();

  // Exclude held tickers so the same asset cannot be added twice
  const matches = Object.entries(assets).filter(([ticker, asset]) => !(ticker in weights) && `${ticker} ${asset.name}`.toLowerCase().includes(query));
  $("search-results").innerHTML = query ? matches.map(([ticker, asset]) => `<button type="button" data-add="${ticker}">Add ${ticker} · ${asset.name}</button>`).join("") : "";
  if (query && !matches.length) $("search-results").innerHTML = "<p>No matches outside your holdings.</p>";
  setDropdown("search", "search-results", Boolean(query) && document.activeElement === $("search"));
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
$("undo").addEventListener("click", () => { if (!history.length) return; weights = history.pop(); clearPreview(); render(); showStatus("Previous allocation restored."); });

// Reset is a normal edit so it can be undone too
$("reset").addEventListener("click", () => change({ AAPL: 70, SPY: 20 }, "Example restored."));
$("candidate-options").innerHTML = Object.entries(assets).map(([ticker, asset]) => `<button type="button" data-candidate="${ticker}">${ticker} · ${asset.name}</button>`).join("");
$("candidate").value = "IEF";
$("candidate-value").textContent = "IEF · Treasury bond ETF";

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
  proposed = Object.fromEntries(Object.entries(weights).filter(([key]) => key !== ticker).map(([key, value]) => [key, value * factor]));
  proposed[ticker] = target;

  // Include tickers from both versions, without duplicates, so new assets appear in the table
  const rows = [...new Set([...Object.keys(weights), ...Object.keys(proposed)])];
  $("preview-body").innerHTML = rows.map(key => `<tr><th scope="row">${key}</th><td class="number">${percent(weights[key] || 0)}</td><td class="number">${percent(proposed[key] || 0)}</td></tr>`).join("") + `<tr><th scope="row">Cash</th><td class="number">${percent(cash(weights))}</td><td class="number">${percent(cash(proposed))}</td></tr>`;
  $("preview-title").textContent = `Set ${ticker} to ${percent(target)}`;
  $("preview").hidden = false; showStatus("Preview ready.");
});

// Apply uses the same edit path as a manual weight change, including the Undo snapshot
$("apply").addEventListener("click", () => {
  if (!proposed) return;
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
    const buttons = [...panel.querySelectorAll("button")];
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
$("search").addEventListener("focus", search);
$("candidate-toggle").addEventListener("click", () => {
  setDropdown("candidate-toggle", "candidate-options", $("candidate-options").hidden);
});
$("candidate-options").addEventListener("click", event => {
  const ticker = event.target.dataset.candidate;
  if (!ticker) return;
  const shortNames = { AAPL: "Apple", SPY: "S&P 500 ETF", IEF: "Treasury bond ETF", GLD: "Gold ETF" };
  $("candidate").value = ticker;
  $("candidate-value").textContent = `${ticker} · ${shortNames[ticker]}`;
  clearPreview();
  setDropdown("candidate-toggle", "candidate-options", false);
  $("candidate-toggle").focus();
});

// Reset the picker when its dialog closes so reopening it never exposes an old options list
$("allocation-dialog").addEventListener("close", () => {
  setDropdown("candidate-toggle", "candidate-options", false);
});

// Populate the starting table after the deferred script has access to the page elements
render();
