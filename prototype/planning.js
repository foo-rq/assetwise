"use strict";

// Proposals remain separate from the editable portfolio until Apply is pressed
let proposalController;
let proposalSerial = 0;
let proposalWeights = null;

function explainComparison(comparison, profile) {
  // Cards and previews use the same figures and preference reason so a growth sacrifice stays explicit
  const forecast = comparison.forecast;
  const { current, proposed, horizon_years: years } = forecast;
  const limit = profile?.max_loss_percent;
  // Match the three loss checks used for suggestions, including the lower growth bound.
  const outsideLimit = Number.isFinite(limit) && (
    comparison.current.maximum_drawdown * 100 > limit + 1e-10 ||
    current.drawdown_screen * 100 > limit + 1e-10 ||
    current.projected_growth_range?.[0] * 100 < -limit - 1e-10);
  const lowerRisk = proposed.estimated_annual_volatility < current.estimated_annual_volatility;
  // An unanswered horizon must not turn an annual estimate into a period-growth claim.
  const hasGrowth = years != null && Number.isFinite(current.projected_growth) && Number.isFinite(proposed.projected_growth);
  const lowerGrowth = hasGrowth && proposed.projected_growth < current.projected_growth - 1e-10;
  let reason = "";
  if (lowerGrowth) {
    if (!lowerRisk) reason = "This option has lower estimated growth without reducing fluctuations. ";
    else if (outsideLimit) reason = `Your current mix exceeds your ${percent(limit)} loss limit in the risk checks. This option gives up estimated growth to reduce that risk. `;
    else if (profile?.capacity === "essential") reason = "You said this money covers essential needs. This option prioritises reducing risk over growth. ";
    else if (profile?.goal === "preserve") reason = "You chose to protect your money. This option trades some estimated growth for smaller fluctuations. ";
    else reason = "This option trades some estimated growth for smaller fluctuations. ";
  }
  const growth = hasGrowth ? `Estimated growth over ${years} ${years === 1 ? "year" : "years"}: ${percent(current.projected_growth * 100)} to ${percent(proposed.projected_growth * 100)}. ` : "";
  const risk = `Annual fluctuations: ${percent(current.estimated_annual_volatility * 100)} to ${percent(proposed.estimated_annual_volatility * 100)}.`;
  // Preserve disagreement between return estimators even when the headline estimate improves.
  const disagreement = forecast.return_change_model_range?.[0] < 0 && forecast.return_change_model_range?.[1] >= 0
    ? " Return estimates disagree." : "";
  return reason + growth + risk + disagreement;
}

function forecastTable(forecast) {
  if (!forecast) return "";
  const years = forecast.horizon_years;
  const rows = years == null ? [["Expected annual return", "expected_annual_return"]]
    : [[`Modelled growth over ${years} ${years === 1 ? "year" : "years"}`, "projected_growth"]];
  rows.push(["Estimated annual volatility", "estimated_annual_volatility"]);
  const range = forecast.return_change_range_90.map(value => (value * 100).toFixed(2)).join(" to ");
  const estimates = forecast.return_change_model_range
    ? `<p>Expected annual return change across estimates: ${forecast.return_change_model_range.map(value => (value * 100).toFixed(2)).join(" to ")} percentage points.</p>` : "";
  // The paired range compares final wealth, not percentage-point changes in cumulative return
  const relative = forecast.projected_relative_wealth_range;
  const relativeNote = relative ? `<p>Proposed final value relative to current: ${relative.map(value => percent(value * 100)).join(" to ")} (approximate 90% model range). Negative means less money than keeping the current mix.</p>` : "";
  // Separate range endpoints keep current/proposed figures aligned even in a narrow preview
  const growthRangeRows = ["Growth range, lower end", "Growth range, upper end"].map((label, index) =>
    `<tr><th scope="row">${label}</th>${[forecast.current, forecast.proposed].map(side =>
      `<td class="number">${side.projected_growth_range ? percent(side.projected_growth_range[index] * 100) : "Unavailable"}</td>`).join("")}</tr>`).join("");
  const projected = years == null ? "" : `<table><caption>Period estimates over ${years} ${years === 1 ? "year" : "years"}</caption><thead><tr><th>Estimate</th><th class="number">Current</th><th class="number">Proposed</th></tr></thead><tbody>${growthRangeRows}<tr><th scope="row">Drawdown screen</th>${[forecast.current, forecast.proposed].map(side => `<td class="number">${percent(side.drawdown_screen * 100)}</td>`).join("")}</tr><tr><th scope="row">Expected annual return</th>${[forecast.current, forecast.proposed].map(side => `<td class="number">${percent(side.expected_annual_return * 100)}</td>`).join("")}</tr></tbody></table><p>Growth range: approximate 90% model range. Drawdown screen: a conservative estimate of the largest fall during this period. Actual losses can be larger.</p>`;
  const periodNote = years == null ? "Choose an investment period in Your preferences to compare growth." : forecast.projection_notice;
  return `<table><caption>Forecast as of ${escapeHTML(forecast.as_of)}</caption><thead><tr><th>Estimate</th><th class="number">Current</th><th class="number">Proposed</th></tr></thead><tbody>${rows.map(([label, key]) =>
    `<tr><th scope="row">${label}</th>${[forecast.current, forecast.proposed].map(side => `<td class="number">${Number.isFinite(side[key]) ? percent(side[key] * 100) : "Unavailable"}</td>`).join("")}</tr>`).join("")}</tbody></table>${periodNote ? `<p>${escapeHTML(periodNote)}</p>` : ""}<details><summary>Uncertainty and assumptions</summary>${projected}${relativeNote}${estimates}<p>Annual return difference: ${range} percentage points (approximate 90% model range). A negative result means the change could earn less.</p><details><summary>Model calculations</summary><p>${escapeHTML(forecast.assumptions)}</p></details><p>Actual outcomes can fall outside these ranges.</p></details>`;
}

function validateForecast(forecast, asOf, profile = investorProfile) {
  // Compare the returned period with the saved answer so an old response cannot label a new forecast
  const years = forecast.horizon_years ?? null;
  const expectedYears = profile?.horizon_years ?? null;
  const validRange = range => Array.isArray(range) && range.length === 2 &&
    range.every(Number.isFinite) && range[0] <= range[1];
  const sides = [forecast.current, forecast.proposed];
  const validProjection = side => {
    if (years === null) return side.projected_growth == null && side.projected_growth_range == null && side.drawdown_screen == null;
    if (!Number.isFinite(side.drawdown_screen) || side.drawdown_screen < 0 || side.drawdown_screen > 1) return false;
    if (side.projected_growth == null && side.projected_growth_range == null) return Boolean(forecast.projection_notice);
    return Number.isFinite(side.projected_growth) && side.projected_growth >= -1 &&
      validRange(side.projected_growth_range) && side.projected_growth_range[0] >= -1;
  };
  if ((asOf && forecast.as_of !== asOf) || years !== expectedYears ||
    (years !== null && (!Number.isFinite(years) || years < 0.25 || years > 40)) ||
    !sides.every(side => side && Number.isFinite(side.expected_annual_return) &&
      Number.isFinite(side.estimated_annual_volatility) && side.estimated_annual_volatility >= 0 && validProjection(side)) ||
    !validRange(forecast.return_change_range_90) ||
    (forecast.return_change_model_range != null && !validRange(forecast.return_change_model_range)) ||
    (forecast.projected_relative_wealth_range != null && (years === null ||
      !validRange(forecast.projected_relative_wealth_range) || forecast.projected_relative_wealth_range[0] < -1))) {
    throw new Error("Forecast is incomplete or uses a different date or investment period. Retry the comparison.");
  }
}

function comparisonTable(comparison) {
  // Use the same metric order for suggestion and saved-portfolio previews
  const rows = [
    ["Annualised volatility", "annualised_volatility"],
    ["Annualised return (arithmetic)", "annualised_arithmetic_return"],
    ["Maximum drawdown", "maximum_drawdown"]
  ];
  // API results use decimal units; multiply by 100 exactly once before formatting percentages
  const sharpe = side => Number.isFinite(side.sharpe_ratio) ? side.sharpe_ratio.toFixed(2) : "Unavailable";
  return `<table><caption>Historical comparison</caption><thead><tr><th>Metric</th><th class="number">Current</th><th class="number">Proposed</th></tr></thead><tbody>${rows.map(([label, key]) =>
    `<tr><th scope="row">${label}</th><td class="number">${percent(comparison.current[key] * 100)}</td><td class="number">${percent(comparison.proposed[key] * 100)}</td></tr>`).join("")}<tr><th scope="row">Sharpe ratio</th><td class="number">${sharpe(comparison.current)}</td><td class="number">${sharpe(comparison.proposed)}</td></tr></tbody></table>`;
}

function validateComparison(comparison) {
  // A result from a different source/version must not enable Apply in the currently loaded workspace
  for (const side of [comparison.current, comparison.proposed]) {
    if (side.source !== dataSource || side.dataset_version !== datasetVersion ||
        !Object.values(metricFields).every(key => Number.isFinite(side[key]))) {
      throw new Error("The dataset changed. Reload the workspace before comparing.");
    }
  }
  if (comparison.current.return_observations !== comparison.proposed.return_observations) {
    // Equal-looking metrics are still incomparable if their samples have different lengths
    throw new Error("The comparison periods do not match.");
  }
  if (comparison.forecast) {
    validateForecast(comparison.forecast, comparison.current.dates?.at(-1));
  }
}

function exposureComparison(comparison) {
  const before = comparison.current.exposure;
  const after = comparison.proposed.exposure;
  if (!before || !after || before.as_of !== after.as_of) return "";
  const sectors = [...new Set([...Object.keys(before.sectors), ...Object.keys(after.sectors)])].sort();
  const rows = sectors.map(sector => [sector, before.sectors[sector] || 0, after.sectors[sector] || 0]);
  rows.push(["Unclassified holdings", before.unclassified_percent, after.unclassified_percent]);
  // Unknown coverage stays visible so a fund addition cannot look like a proven sector reduction
  const table = rows.map(([label, current, proposed]) => `<tr><th scope="row">${escapeHTML(label)}</th><td class="number">${percent(current)}</td><td class="number">${percent(proposed)}</td></tr>`).join("");
  const overlap = side => side.overlaps.length
    ? `${side.overlaps[0].name || side.overlaps[0].symbol}: at least ${percent(side.overlaps[0].total_percent)}`
    : "None identified in covered holdings";
  return `<details><summary>Sector and overlap changes</summary><p>Holdings snapshot: ${escapeHTML(before.as_of)}. Uncovered funds may add exposure.</p><table><caption>Known sector exposure</caption><thead><tr><th>Sector</th><th class="number">Current</th><th class="number">Proposed</th></tr></thead><tbody>${table}</tbody></table><p>Largest overlapping holding before: ${escapeHTML(overlap(before))}. After: ${escapeHTML(overlap(after))}.</p></details>`;
}

async function apiPlanning(path, body, signal) {
  // The caller supplies its abort signal so closing a dialog can stop an obsolete calculation
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  const result = await response.json();
  if (!response.ok) {
    // Older running servers may lack the route; other input errors can return a specific reason
    if (response.status === 404) throw new Error("Restart the Assetwise server to load this feature.");
    throw new Error(typeof result.detail === "string" ? result.detail : "Check the entered limits and try again.");
  }
  return result;
}

async function previewPortfolio(name, next) {
  // Cancel the previous preview before starting another, then tag this request to ignore late responses
  proposalController?.abort();
  const controller = new AbortController();
  proposalController = controller;
  const serial = ++proposalSerial;
  const timeout = setTimeout(() => controller.abort(), 15000);

  // Clear accepted proposal weights until the new baseline comparison has passed validation
  proposalWeights = null;
  $("proposal-title").textContent = name;
  $("proposal-results").replaceChildren();
  $("proposal-error").textContent = "";
  $("proposal-explanation").textContent = "";
  $("proposal-state").textContent = "Calculating comparison";
  $("apply-proposal").disabled = true;
  $("proposal-dialog").showModal();
  try {
    // Recalculate against current holdings even if a saved candidate came from an earlier request
    const comparison = await apiPlanning(apiPath("comparison"), {
      current: { weights }, proposed: { weights: next }, profile: investorProfile
    }, controller.signal);
    if (serial !== proposalSerial) return;
    validateComparison(comparison);
    // Describe direction and trade-offs before asking the reader to compare table values
    const direction = (key, lower, higher) => comparison.proposed[key] < comparison.current[key] ? lower
      : comparison.proposed[key] > comparison.current[key] ? higher : "is unchanged";
    $("proposal-explanation").textContent = `In this sample, volatility ${direction("annualised_volatility", "falls", "rises")}, return ${direction("annualised_arithmetic_return", "falls", "rises")} and the largest peak-to-low loss ${direction("maximum_drawdown", "shrinks", "grows")}. Applying changes your hypothetical portfolio only.`;
    if (comparison.forecast) {
      $("proposal-explanation").textContent = explainComparison(comparison, investorProfile);
    }
    // Show both removed and added symbols by taking the union of current/proposed holdings
    const symbols = [...new Set([...Object.keys(weights), ...Object.keys(next)])];
    const allocationRows = symbols.map(symbol => `<tr><th scope="row">${escapeHTML(symbol)}</th><td class="number">${percent(weights[symbol] || 0)}</td><td class="number">${percent(next[symbol] || 0)}</td></tr>`).join("");
    $("proposal-results").innerHTML = (comparison.forecast ? forecastTable(comparison.forecast) +
      `<details><summary>Historical comparison</summary>${comparisonTable(comparison)}</details>` : comparisonTable(comparison)) +
      `<table><caption>Allocation of your money (%)</caption><thead><tr><th>Position</th><th class="number">Current</th><th class="number">Proposed</th></tr></thead><tbody>${allocationRows}<tr><th scope="row">Cash</th><td class="number">${percent(cash(weights))}</td><td class="number">${percent(cash(next))}</td></tr></tbody></table>` + exposureComparison(comparison);
    // Copy only after successful validation; editing the source object must not change Apply's allocation
    proposalWeights = { ...next };
    $("proposal-state").textContent = observationLabel(comparison.current.return_observations);
    const unchanged = symbols.every(symbol => (weights[symbol] || 0) === (next[symbol] || 0));
    $("apply-proposal").disabled = unchanged;
    if (unchanged) $("proposal-explanation").textContent = "This portfolio matches your current allocations. There is no change to apply.";
  } catch (error) {
    // A closed or superseded dialog must not acquire an error or re-enable its Apply button later
    if (serial !== proposalSerial) return;
    $("proposal-state").textContent = "";
    $("proposal-error").textContent = error.name === "AbortError" ? "Comparison timed out. Close and try again." : error.message;
  } finally {
    clearTimeout(timeout);
  }
}

$("proposal-dialog").addEventListener("close", () => {
  // Invalidating the request number also protects against responses arriving after abort was requested
  ++proposalSerial;
  proposalController?.abort();
  proposalWeights = null;
});
$("apply-proposal").addEventListener("click", () => {
  // The normal change path updates Undo, analysis and SQL saving together after closing the previews
  if (!proposalWeights || $("apply-proposal").disabled) return;
  const next = { ...proposalWeights };
  $("proposal-dialog").close();
  $("scenarios-dialog").close();
  change(next, "Allocation applied.");
  $("undo").focus();
});

$("show-detail").addEventListener("click", () => {
  // Use only the latest accepted analysis, never the last report from before a holdings edit
  const result = latestAnalysis;
  const content = $("detail-content");
  content.replaceChildren();
  if (!result) {
    content.textContent = "Analysis is not ready. Close and retry once the current calculation finishes.";
  } else {
    const summary = document.createElement("p");
    summary.textContent = `Cumulative return: ${percent(result.cumulative_return * 100)} over ${observationLabel(result.return_observations)}.`;
    content.append(summary);

    // Use a labelled index chart, not invented dates or dollar values
    const values = result.portfolio_index;
    const low = Math.min(...values), high = Math.max(...values);

    // A flat all-cash path has no range; use 1 as the drawing scale to avoid dividing by zero
    const range = high - low || 1;
    const points = values.map((value, index) => `${60 + index / (values.length - 1) * 400},${140 - (value - low) / range * 110}`).join(" ");
    const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chart.setAttribute("viewBox", "0 0 480 175");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", `Portfolio index, starting at 1 and ending at ${values.at(-1).toFixed(3)}. Range ${low.toFixed(3)} to ${high.toFixed(3)}.`);
    // Use archive dates when available and numbered sessions if dates are missing
    chart.innerHTML = `<polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2"/><text x="20" y="16">Growth of 100, ending at ${(values.at(-1) * 100).toFixed(2)}</text><text x="52" y="34" text-anchor="end">${(high * 100).toFixed(0)}</text><text x="52" y="144" text-anchor="end">${(low * 100).toFixed(0)}</text><text x="60" y="167">${historyDates[0] || "Session 0"}</text><text x="460" y="167" text-anchor="end">${historyDates.at(-1) || `Session ${result.return_observations}`}</text>`;
    content.append(chart);
    if (!result.asset_correlations.length) {
      const empty = document.createElement("p");
      empty.textContent = "Correlation needs at least 2 funded assets. Cash is excluded.";
      content.append(empty);
    } else {
      const table = document.createElement("table");
      table.innerHTML = `<caption>Return correlations</caption><thead><tr><th>Pair</th><th class="number">Correlation</th></tr></thead><tbody>${result.asset_correlations.map(pair => `<tr><th scope="row">${escapeHTML(pair.asset_a)}/${escapeHTML(pair.asset_b)}</th><td class="number">${pair.value === null ? "Undefined" : pair.value.toFixed(2)}</td></tr>`).join("")}</tbody>`;
      content.append(table);
      const explanation = document.createElement("p");
      explanation.textContent = "Values near 1 moved together; near -1 moved oppositely. Constant returns have undefined correlation. These relationships can change over time.";
      content.append(explanation);
    }
  }
  $("detail-dialog").showModal();
  // Opening a long report should start at its title, not scroll down to Close
  $("detail-title").focus();
});
