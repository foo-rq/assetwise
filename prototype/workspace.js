"use strict";

// Load identifiers and names from the API so both sides use the same asset universe
let assets = {};
// The public workspace uses only dated historical prices
const apiPath = name => `/api/${name}?dataset=historical`;
let dataSource;
let historyDates = [];
const observationLabel = count => `${count.toLocaleString("en-US")} trading sessions`;
// Escape API strings before inserting them into table and dropdown HTML
const escapeHTML = value => String(value).replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[character]);
const $ = id => document.getElementById(id);
// Store 70 for 70%, not 0.7 Cash is calculated from whatever is left over
let weights = {};

// Each entry is the allocation before an edit Undo takes the last entry off this list
let history = [];

let statusFadeTimer;
let statusClearTimer;

// Keep feedback visible for 2 seconds A new action replaces the old message and restarts the timer
function showStatus(message) {
  clearTimeout(statusFadeTimer);
  clearTimeout(statusClearTimer);

  // Put dialog feedback inside the dialog, where it can be seen and announced while the page is inactive
  const output = $("scenarios-dialog").open ? $("scenario-feedback")
    : $("status");
  for (const id of ["status", "scenario-feedback"]) {
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
  // An edit makes any open suggestion comparison stale before a new analysis starts
  if ($("proposal-dialog").open) $("proposal-dialog").close();
}
function change(next, message) {
  // Copy the old object before replacing it, otherwise Undo could lose the previous weights

  // A holdings edit also invalidates any preview made from the old allocation
  history.push({ ...weights }); weights = next; clearPreview(); render(); savePortfolio(); showStatus(message);
}
function render() {
  // Dollar entry is converted at the boundary; API and SQL always receive percentage allocations
  const amountMode = $("entry-mode").value === "amount" && sqlStorage.profile?.amount > 0;
  const portfolioAmount = sqlStorage.profile?.amount || 0;
  $("weight-heading").textContent = amountMode ? "Amount (USD)" : "Weight %";
  // Escape names from the API before inserting them into the holdings table

  // data-weight and data-remove let the table listeners identify the affected ticker
  $("holdings-body").innerHTML = Object.entries(weights).map(([ticker, weight]) => `<tr><td><strong>${escapeHTML(ticker)}</strong><small>${escapeHTML(assets[ticker].name)}</small></td><td class="number"><input class="weight" type="number" min="0" max="${amountMode ? portfolioAmount : 100}" step="0.01" value="${amountMode ? (weight * portfolioAmount / 100).toFixed(2) : weight}" aria-label="${escapeHTML(ticker)} ${amountMode ? "amount in USD" : "weight in percent"}" aria-describedby="error" data-weight="${escapeHTML(ticker)}"></td><td class="number"><button class="remove" data-remove="${escapeHTML(ticker)}" aria-label="Remove ${escapeHTML(ticker)}">Remove</button></td></tr>`).join("");

  // Show the empty message only after the last holding is removed
  $("empty").hidden = Object.keys(weights).length > 0;
  $("cash").textContent = percent(cash(weights));
  $("allocation").innerHTML = Object.entries(weights).map(([ticker, weight]) => `<span style="width:${weight}%;background:${assets[ticker].colour}"></span>`).join("");

  $("undo").disabled = history.length === 0;
  $("error").textContent = ""; search();

  // Refresh analysis after every accepted edit, including Undo and Reset
  document.dispatchEvent(new Event("portfolio:changed"));
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
    return `<button type="button" data-add="${escapeHTML(ticker)}" ${held ? "disabled" : ""}>${held ? "Held" : "Add"} ${escapeHTML(ticker)} - ${escapeHTML(asset.name)}</button>`;
  }).join("");
  if (!matches.length) $("search-results").innerHTML = "<p>No match in this archive. Try a company name or ticker, or clear the search to browse.</p>";
  setDropdown("search", "search-results", document.activeElement === $("search"));
}

// Enter in the search field must not reload the page and erase the session
$("search-form").addEventListener("submit", event => event.preventDefault());
$("search").addEventListener("input", search);
$("search-results").addEventListener("click", event => {
  // Listen on the container because search replaces its buttons on each keystroke
  const ticker = event.target.dataset.add; if (!ticker) return;
  $("search").value = ""; change({ ...weights, [ticker]: 0 }, `${escapeHTML(ticker)} added at 0%.`);

  // Move to the new weight field so the user can type an allocation straight away
  document.querySelector(`[data-weight="${escapeHTML(ticker)}"]`).focus();
});
$("holdings-body").addEventListener("change", event => {
  // Commit when the field changes, rather than rebuilding the row after every digit
  const ticker = event.target.dataset.weight; if (!ticker) return;
  const input = Number(event.target.value);
  const amountMode = $("entry-mode").value === "amount" && sqlStorage.profile?.amount > 0;
  // Round down converted tails so an exact budget cannot exceed 100% through floating-point noise
  const value = amountMode ? Math.floor(input / sqlStorage.profile.amount * 1e10) / 1e8 : input;

  // Try the edit in a separate object first Invalid input must not reach saved state
  const next = { ...weights, [ticker]: value };

  // Number('') is 0, so check blank input separately The small tolerance allows rounding noise
  if (event.target.value === "" || !Number.isFinite(value) || value < 0 || value > 100 || total(next) > 100 + 1e-8) {
    const limit = amountMode
      ? `enter a nonnegative USD amount and keep the total within your $${sqlStorage.profile.amount.toLocaleString("en-US")} budget`
      : "enter 0 to 100% and keep invested weights at or below 100%";
    $("error").textContent = `Cannot change ${ticker}: ${limit}. Previous allocation restored.`;
    event.target.value = amountMode ? (weights[ticker] * sqlStorage.profile.amount / 100).toFixed(2) : weights[ticker]; return;
  }
  change(next, `${escapeHTML(ticker)} updated to ${percent(value)}. Cash is ${percent(cash(next))}.`);
  document.querySelector(`[data-weight="${escapeHTML(ticker)}"]`).focus();
});
$("holdings-body").addEventListener("click", event => {
  // Removing a key automatically releases its weight to cash on the next render
  const ticker = event.target.dataset.remove; if (!ticker) return;
  const next = { ...weights }; delete next[ticker]; change(next, `${escapeHTML(ticker)} removed; its allocation is now cash.`); $("search").focus();
});

// Do not call change() here: that would add another history entry while undoing one
$("undo").addEventListener("click", () => { if (!history.length) return; weights = history.pop(); clearPreview(); render(); savePortfolio(); showStatus("Previous allocation restored."); });

// Reset is a normal edit so it can be undone too
$("reset").addEventListener("click", () => {
  // Preserve the current draft before clearing it so starting again is recoverable after reload
  if (Object.keys(weights).length) savedToResume = { ...weights };
  change({}, "Started fresh. Your previous portfolio is saved.");
});
// showModal puts the explanation above the page and keeps keyboard focus inside it

// The browser handles Escape and the form's Close button without changing the allocation
$("about-analysis").addEventListener("click", () => {
  $("analysis-dialog").showModal();
});

// Return to the help button when the explanation closes so keyboard users keep their place
$("analysis-dialog").addEventListener("close", () => {
  $("about-analysis").focus();
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
$("search").addEventListener("focus", search);
$("search").addEventListener("click", search);

// Hold one request at a time and ignore older results after the allocation changes
let analysisRequest;
let requestNumber = 0;
let datasetVersion;
let latestAnalysis = null;
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
  $("sharpe").textContent = "Calculating";
  $("sharpe").classList.remove("available");
  $("expected-return").textContent = "Calculating";
  $("estimated-volatility").textContent = "Calculating";
  $("forecast-details").hidden = true;
  $("forecast-period-note").textContent = "";
  latestAnalysis = null;
  document.dispatchEvent(new Event("analysis:pending"));
  $("analysis-state").textContent = "Calculating";
  $("retry-analysis").hidden = true;
  try {
    const response = await fetch(apiPath("analysis"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weights, profile: investorProfile }),
      signal: controller.signal
    });
    if (!response.ok) throw new Error("Analysis failed");
    const result = await response.json();
    if (current !== requestNumber) return;

    // A server restart with a different dataset must not silently change the comparison basis
    if (result.source !== dataSource || result.dataset_version !== datasetVersion) {
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
    // Sharpe is dimensionless and may legitimately be unavailable for a constant path
    $("sharpe").textContent = Number.isFinite(result.sharpe_ratio) ? result.sharpe_ratio.toFixed(2) : "Unavailable";
    $("sharpe").classList.toggle("available", Number.isFinite(result.sharpe_ratio));
    $("sharpe").title = result.sharpe_unavailable_reason || "Historical annualised Sharpe ratio";
    const forecast = result.forecast;
    if (forecast) validateForecast(forecast, historyDates.at(-1));
    const years = forecast?.horizon_years;
    const growth = years == null ? forecast?.current.expected_annual_return : forecast.current.projected_growth;
    $("forecast-growth-label").textContent = years == null ? "Expected annual return"
      : `Modelled growth over ${years} ${years === 1 ? "year" : "years"}`;
    $("forecast-growth-hint").textContent = years == null ? "Model estimate, not a promised return"
      : "Compound growth before inflation, costs and taxes";
    $("expected-return").textContent = Number.isFinite(growth) ? percent(growth * 100) : "Unavailable";
    $("estimated-volatility").textContent = forecast ? percent(forecast.current.estimated_annual_volatility * 100) : "Unavailable";
    $("expected-return").classList.toggle("available", Number.isFinite(growth));
    $("estimated-volatility").classList.toggle("available", Boolean(forecast));
    $("forecast-period-note").textContent = !forecast ? "" : years == null
      ? "Choose an investment period in Your preferences to compare growth." : forecast.projection_notice || "";
    $("forecast-details").hidden = !forecast;
    if (forecast) {
      // Keep the range beside its model assumptions without adding more headline metrics
      const range = forecast.current.projected_growth_range;
      const projection = range ? `<p>Growth over ${years} ${years === 1 ? "year" : "years"}: ${range.map(value => percent(value * 100)).join(" to ")} (approximate 90% model range).</p>` : "";
      const annual = years == null ? "" : `<p>Expected annual return: ${percent(forecast.current.expected_annual_return * 100)}.</p>`;
      const drawdown = years == null ? "" : `<p>Drawdown screen over ${years} ${years === 1 ? "year" : "years"}: ${percent(forecast.current.drawdown_screen * 100)}. A conservative peak-to-trough loss estimate, not a guaranteed maximum.</p>`;
      $("forecast-detail-content").innerHTML = `${projection}${drawdown}${annual}<p>${escapeHTML(forecast.assumptions)}</p><p>These ranges are model estimates, not reliable limits on future outcomes.</p>`;
    }
    latestAnalysis = result;
    document.dispatchEvent(new Event("portfolio:analysed"));
    $("analysis-state").textContent = observationLabel(result.return_observations);
  } catch (error) {
    if (current !== requestNumber) return;

    // Failures stay visible until a successful retry or edit replaces them
    for (const id of Object.keys(metricFields)) {
      $(id).textContent = "Unavailable";
      $(id).classList.remove("pending", "available");
    }
    $("sharpe").textContent = "Unavailable";
    $("analysis-state").textContent = "Analysis unavailable. Keep your edits here and retry when the connection returns.";
    for (const id of ["expected-return", "estimated-volatility"]) $(id).classList.remove("available");
    $("expected-return").textContent = "Unavailable";
    $("estimated-volatility").textContent = "Unavailable";
    $("retry-analysis").hidden = false;
    document.dispatchEvent(new Event("analysis:failed"));
  } finally {
    clearTimeout(timeout);
  }
}
$("retry-analysis").addEventListener("click", updateAnalysis);

let saveSerial = 0;
function savingFailed(error) {
  // This message has no fade timer because a failed save needs to stay visible until recovery
  $("save-state").textContent = `${error.message || "Saving failed."} Changes are not confirmed saved.`;
  $("retry-save").hidden = false;
}
async function savePortfolio() {
  // New edits increment this counter so an older SQL response cannot claim the latest edit is saved
  const serial = ++saveSerial;
  try {
    // Keep the accepted allocation in memory even when SQL is temporarily unavailable
    preservePreviousPortfolio();
    portfolioStorage.write(sqlStorage, weights, datasetVersion);
    $("save-state").textContent = "Saving";
    await sqlStorage.flush().catch(error => { savingFailed(error); throw error; });
    if (serial !== saveSerial) return;
    // Reach this confirmation only after SQL has committed the captured allocation
    $("save-state").textContent = "Saved to your private workspace.";
    $("retry-save").hidden = true;
  } catch (error) {
    if (serial === saveSerial) savingFailed(error);
  }
}
$("retry-save").addEventListener("click", () => {
  // A load failure must not overwrite SQL with an incomplete in-memory collection
  if (!sqlStorage.ready) {
    $("save-state").textContent = "Export your edits before reloading to reconnect saving.";
    return;
  }
  savePortfolio();
});
$("export-backup").addEventListener("click", () => {
  // Include the visible weights even if they have not yet been accepted by SQL
  try {
    sqlStorage.exportBackup({ version: 1, datasetVersion, weights: { ...weights } });
  } catch (error) { savingFailed(error); }
});
window.addEventListener("beforeunload", event => {
  // Browser-native leave warnings also cover scenario edits still waiting on a network response
  if (sqlStorage.dirty || !$("retry-save").hidden) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("import-backup").addEventListener("click", () => $("backup-file").click());
$("backup-file").addEventListener("change", async event => {
  // Cancelling the file picker is not a failed import and must leave the saved collection alone
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 200000) throw new Error("Backup is too large");
    await sqlStorage.importBackup(await file.text());
    // A successful import replaces the collection; reload through the ordinary validated loading path
    $("retry-save").hidden = true;
    location.reload();
  } catch (error) {
    $("scenario-error").textContent = `Backup was not imported. ${error.message}`;
  } finally {
    // Clear the file input so choosing the same backup after an error triggers change again
    event.target.value = "";
  }
});

async function loadAssets() {
  // Keep controls disabled until there are names and identifiers to render safely
  for (const id of ["search", "reset", "open-scenarios"]) $(id).disabled = true;
  try {
    const response = await fetch(apiPath("history"), { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Asset loading failed");
    const data = await response.json();
    if (data.source !== "historical" || !Array.isArray(data.assets)) throw new Error("Invalid source");
    const colours = ["#25332f", "#566f62", "#8d9f94", "#b8c5bd"];
    assets = Object.fromEntries(data.assets.map((asset, index) => {
      if (!/^[A-Z][A-Z0-9.-]{0,31}$/.test(asset.symbol) || typeof asset.name !== "string") {
        throw new Error("Invalid asset");
      }
      return [asset.symbol, { name: asset.name, colour: colours[index % colours.length] }];
    }));
    dataSource = data.source;
    historyDates = data.dates || [];
    datasetVersion = data.dataset_version;
    // Saved weights from another version must never be interpreted as holdings in this universe
    portfolioStorage.key = `assetwise.portfolio.v1:${datasetVersion}`;
    $("data-notice").textContent = `Historical prices: ${historyDates[0]} to ${historyDates.at(-1)}. Corrected prices; saved weights are recalculated.`;
    $("analysis-basis").textContent = `Forecast as of ${historyDates.at(-1)}`;
    $("search-note").textContent = `Browse ${data.assets.length} supported stocks and funds or search by name. Prices cover 2013 to 2017.`;
    $("analysis-dialog-copy").textContent = `${data.notice} ${data.value_basis}. Weights rebalance each trading session. Cash earns 0%; costs and taxes are excluded. Return is the arithmetic mean multiplied by 252; volatility uses the square root of 252. Maximum drawdown is the largest decline from a prior peak. Sharpe uses monthly US Treasury-bill returns converted to daily equivalents from the Kenneth French Data Library. Annualisation assumes uncorrelated daily excess returns. Underlying fund exposure is not included in these return calculations. The archive covers a fixed selection of assets that survived this period, which can make results look better than a wider market sample. Suggestions use a separate model fitted only to this archive. Your investment period sets its compound-growth projection. The model assumes its estimated returns and risks persist; it does not predict later economic events. Forecasts and their uncertainty appear in the suggestion preview. Historical statistics describe the observed sample.`;
    // Link directly to the archive provenance without adding another source picker
    $("source-link").hidden = !data.source_url;
    if (data.source_url) $("source-link").href = data.source_url;
    $("proposal-assumptions").textContent = "2017 adjusted prices. The model assumes daily trades to keep these percentages. Leaving holdings untouched changes the risk. Cash earns 0%; costs and taxes are excluded.";
    // The detailed chart shares the loaded source limitation instead of retaining the old fictional label
    $("detail-assumptions").textContent = "2017 adjusted prices. Dated sector and overlap coverage is shown in the portfolio report. Uncovered funds remain unknown.";
    weights = {};
    try {
      // Restore only after the API supplies the current universe and dataset version
      await sqlStorage.open();
      $("save-state").textContent = "Edits save to your private workspace.";
      const saved = portfolioStorage.read(sqlStorage, assets, datasetVersion);
      if (saved !== null) {
        weights = saved;
        $("save-state").textContent = "Saved to your private workspace.";
      }
    } catch (error) {
      // Keep an unreadable saved collection intact while the user explores an unsaved draft
      sqlStorage.ready = false;
      savingFailed(error);
      // Preserve the SQL collection after load failure and keep calculations usable
      $("save-state").textContent += " Saving is disconnected; reload to retry.";
    }
    $("export-backup").disabled = false;
    for (const id of ["search", "reset", "open-scenarios"]) $(id).disabled = false;
    render();
    document.dispatchEvent(new Event("workspace:ready"));
  } catch (error) {
    $("profile-error").textContent = "Data could not load. Reload to retry.";
    $("error").textContent = "Assets could not load. Start the API and reload this page at http://127.0.0.1:8000/workspace/";
    $("analysis-state").textContent = "Analysis unavailable";
  }
}
// Wait for all deferred controllers before emitting workspace lifecycle events
document.addEventListener("DOMContentLoaded", loadAssets, { once: true });


let scenarioSaving = false;
async function persistScenarioChange(action) {
  // Only 1 scenario mutation may be in flight, otherwise a failed rename could undo a later removal
  if (scenarioSaving) throw new Error("Wait for the current scenario save to finish");
  scenarioSaving = true;
  const keys = [scenarioStorage.key, `${scenarioStorage.key}.removed`];
  const before = keys.map(key => sqlStorage.getItem(key));
  let attemptedWrite = false;
  try {
    // Existing helpers check names and construct the next collection before anything reaches SQL
    action();
    attemptedWrite = true;
    await sqlStorage.flush();
  } catch (error) {
    // Put failed scenario operations back so their buttons can retry against the original names
    // Current holdings are separate keys and must not be rolled back by a scenario-only failure
    keys.forEach((key, index) => {
      if (before[index] === null) sqlStorage.removeItem(key);
      else sqlStorage.setItem(key, before[index]);
    });
    if (attemptedWrite) savingFailed(error);
    throw error;
  } finally {
    // Always release the guard, including input errors caught before an SQL request was sent
    scenarioSaving = false;
  }
}

function renderScenarios() {
  $("scenario-list").replaceChildren();
  const items = scenarioStorage.read(sqlStorage);
  $("restore-scenario").hidden = !sqlStorage.getItem(`${scenarioStorage.key}.removed`);
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
    const rename = document.createElement("button");
    rename.textContent = "Rename";
    rename.className = "rename-scenario";
    rename.setAttribute("aria-label", `Rename ${item.name}`);
    rename.addEventListener("click", () => {
      const input = document.createElement("input");
      input.value = item.name;
      input.maxLength = 60;
      input.setAttribute("aria-label", `Rename ${item.name}`);
      input.setAttribute("aria-describedby", "scenario-rename-error");
      button.replaceWith(input);
      rename.textContent = "Save";
      let finished = false;

      // Keep the row in place so renaming does not reset the list's scroll position
      const cancel = (afterSave = false) => {
        // Once Enter/Save starts SQL, Escape or blur cannot cancel a write already sent to the server
        if (finished || (saving && !afterSave)) return;
        finished = true;
        rename.removeEventListener("click", saveClick, true);
        input.replaceWith(button);
        rename.textContent = "Rename";
        $("scenario-rename-error").textContent = "";
        button.focus();
      };
      let saving = false;
      const save = async () => {
        // Repeated Enter presses should not queue duplicate renames while SQL is still responding
        if (finished || saving) return;
        saving = true;
        input.disabled = true;
        rename.disabled = true;
        try {
          await persistScenarioChange(() => scenarioStorage.rename(sqlStorage, item.name, input.value));
          item.name = input.value.trim();
          button.textContent = item.name + (button.disabled ? " (unavailable for this dataset)" : "");
          compare.setAttribute("aria-label", `Compare ${item.name}`);
          remove.setAttribute("aria-label", `Remove scenario ${item.name}`);
          button.setAttribute("aria-label", `Load ${item.name}`);
          rename.setAttribute("aria-label", `Rename ${item.name}`);
          cancel(true);
        } catch (error) {
          // Keep invalid names editable and put the error in the picker's reserved space
          $("scenario-rename-error").textContent = /^(Enter a name|That name|Scenario no longer)/.test(error.message)
            ? error.message : "Name is not confirmed saved. Close, then Retry saving before trying again.";
          input.setAttribute("aria-invalid", "true");
        } finally {
          saving = false;
          input.disabled = false;
          rename.disabled = false;
        }
      };
      input.addEventListener("keydown", event => {
        if (!["Enter", "Escape"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Enter") save();
        else cancel();
      });

      // The row action saves for mouse users; moving away cancels an unfinished edit
      const saveClick = event => { event.stopImmediatePropagation(); save(); };
      rename.addEventListener("click", saveClick, { capture: true });
      input.addEventListener("blur", event => {
        if (event.relatedTarget !== rename) cancel();
        if (finished) rename.removeEventListener("click", saveClick, true);
      });
      rename.addEventListener("blur", event => {
        if (event.relatedTarget !== input && !finished) cancel();
      }, { once: true });
      input.focus();
      input.select();
    });
    const compare = document.createElement("button");
    compare.textContent = "Compare";
    compare.className = "scenario-utility";
    compare.setAttribute("aria-label", `Compare ${item.name}`);
    compare.disabled = button.disabled;
    compare.addEventListener("click", () => {
      setScenarioMenu(false);
      previewPortfolio(item.name, item.weights);
    });
    const remove = document.createElement("button");
    remove.textContent = "Remove";
    remove.className = "scenario-utility";
    remove.setAttribute("aria-label", `Remove scenario ${item.name}`);
    remove.addEventListener("click", async () => {
      try {
        await persistScenarioChange(() => scenarioStorage.remove(sqlStorage, item.name));
        renderScenarios();
        $("restore-scenario").hidden = false;
        $("restore-scenario").focus();
      } catch (error) {
        $("scenario-rename-error").textContent = "Removal is not confirmed saved. Close, then Retry saving before trying again.";
      }
    });
    row.append(button, rename, compare, remove);
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
    $("scenario-error").textContent = "Saved scenarios could not load. Check the server connection.";
  }
});

$("save-scenario-form").addEventListener("submit", async event => {
  event.preventDefault();
  $("scenario-error").textContent = "";
  try {
    // Read the latest collection on every save so another tab's new snapshots are retained
    await persistScenarioChange(() => scenarioStorage.add(sqlStorage, $("scenario-name").value, weights, datasetVersion));
    $("scenario-search").value = "";
    renderScenarios();
    $("scenario-name").value = "";
    showStatus("Portfolio saved.");
  } catch (error) {
    $("scenario-error").textContent = /^(That name|Enter a name|You can save)/.test(error.message)
      ? error.message
      : "Scenario could not be saved. Close, then Retry saving before trying again.";
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


$("restore-scenario").addEventListener("click", async () => {
  try {
    await persistScenarioChange(() => scenarioStorage.restore(sqlStorage));
    $("scenario-search").value = "";
    renderScenarios();
    $("scenario-rename-error").textContent = "";
  } catch (error) {
    $("scenario-rename-error").textContent = "Could not restore. Check for an existing scenario with the same name.";
  }
});
