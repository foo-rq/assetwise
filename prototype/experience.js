"use strict";

let investorProfile = null;
let savedToResume = null;
let suggestionSerial = 0;
let suggestionController;
let suggestionTimer;

function showWorkspace() {
  // Experience changes explanatory depth, never the calculations or the assumed risk capacity
  document.body.classList.toggle("compact-explanations", investorProfile?.experience === "confident");
  $("onboarding").hidden = true;
  $("portfolio-workspace").hidden = false;
  $("entry-mode-label").hidden = !investorProfile?.amount;
  if (!investorProfile?.amount) $("entry-mode").value = "percent";
  render();
  $("holdings-title").setAttribute("tabindex", "-1");
  $("holdings-title").focus();
}

document.addEventListener("workspace:ready", () => {
  // Offer old work explicitly without filling a new exploration with somebody's earlier choices
  savedToResume = Object.keys(weights).length ? { ...weights } : null;
  weights = {};
  history = [];
  investorProfile = sqlStorage.profile || null;
  $("resume-note").hidden = !savedToResume;
  $("profile-submit").disabled = false;
  render();
  if (investorProfile) showWorkspace();
});

$("edit-profile").addEventListener("click", () => {
  // Editing preferences must not replace the working portfolio or require answering again from scratch
  $("profile-form").reset();
  const capacity = $("profile-form").elements.namedItem("capacity");
  capacity?.querySelector('option[value="some"]')?.remove();
  if (investorProfile?.capacity === "some") {
    // Keep this older answer selectable only for the person who already saved it
    const option = document.createElement("option");
    option.value = "some";
    option.textContent = "It could delay a goal (saved answer)";
    capacity.append(option);
  }
  for (const [name, value] of Object.entries(investorProfile || {})) {
    const field = $("profile-form").elements.namedItem(name);
    if (field) field.value = name === "goal" && value === "income" ? "" : value ?? "";
  }
  // Retired income answers stay saved until the user explicitly chooses a supported aim and submits
  $("profile-error").textContent = investorProfile?.goal === "income"
    ? "Income payments are not forecast. Choose another aim to update your preferences." : "";
  $("cancel-profile").hidden = false;
  $("profile-submit").textContent = "Save preferences";
  $("onboarding").hidden = false;
  $("portfolio-workspace").hidden = true;
  $("profile-form").elements.namedItem("experience").focus();
});
$("cancel-profile").addEventListener("click", showWorkspace);

function readProfile(form) {
  // Blank numeric answers stay unknown rather than silently becoming 0 or an assumed investment period
  const profile = Object.fromEntries(new FormData(form));
  for (const [name, minimum, maximum] of [["horizon_years", 0.25, 40], ["max_loss_percent", 0, 100]]) {
    const value = profile[name] === "" ? null : Number(profile[name]);
    if (value !== null && (!Number.isFinite(value) || value < minimum || value > maximum)) {
      throw new Error(name === "horizon_years" ? "Enter a period from 0.25 to 40 years." : "Enter a loss limit from 0% to 100%.");
    }
    profile[name] = value;
  }
  profile.amount = Number(profile.amount || 0);
  if (!Number.isFinite(profile.amount) || profile.amount < 0 || profile.amount > 1e12) {
    throw new Error("Enter an amount from $0 to $1,000,000,000,000.");
  }
  // Older SQL records still carry these categories; exact answers determine them for new saves
  profile.horizon = profile.horizon_years === null ? "unsure" : profile.horizon_years <= 3 ? "short"
    : profile.horizon_years <= 10 ? "medium" : "long";
  profile.tolerance = profile.max_loss_percent === null ? "unsure" : profile.max_loss_percent <= 10 ? "low"
    : profile.max_loss_percent <= 20 ? "moderate" : "high";
  profile.age = investorProfile?.age || "private";
  profile.existing = investorProfile?.existing || "no";
  return profile;
}

$("profile-form").addEventListener("submit", async event => {
  event.preventDefault();
  $("profile-submit").disabled = true;
  $("profile-error").textContent = "";
  const previous = sqlStorage.profile;
  try {
    const profile = readProfile(event.currentTarget);
    // Save the survey with the loaded SQL collection, without writing the empty visible draft over it
    if (!sqlStorage.ready) await sqlStorage.open();
    sqlStorage.profile = profile;
    sqlStorage.changeNumber++;
    sqlStorage.dirty = true;
    await sqlStorage.flush();
    investorProfile = profile;
    showWorkspace();
  } catch (error) {
    sqlStorage.profile = previous;
    $("profile-error").textContent = `${error.message} Your answers remain here. Retry to continue.`;
  } finally {
    $("profile-submit").disabled = false;
  }
});

$("resume-portfolio").addEventListener("click", () => {
  if (!savedToResume) return;
  const next = { ...savedToResume };
  savedToResume = null;
  $("resume-note").hidden = true;
  change(next, "Saved portfolio resumed.");
});

function preservePreviousPortfolio() {
  // A first fresh edit keeps the previous draft as a named snapshot instead of silently replacing it
  if (!savedToResume) return;
  const names = new Set(scenarioStorage.read(sqlStorage).map(item => item.name.toLowerCase()));
  let number = 1;
  while (names.has(`previous portfolio ${number}`)) number++;
  scenarioStorage.add(sqlStorage, `Previous portfolio ${number}`, savedToResume, datasetVersion);
  savedToResume = null;
  $("resume-note").hidden = true;
}

function refreshExperience() {
  const hasRows = Object.keys(weights).length > 0;
  const invested = total(weights) > 0;
  $("holding-details").hidden = !hasRows;
  // Zero-weight rows can be the result of applying a cash reserve, which still needs a visible report
  $("allocation-help").textContent = hasRows && !invested
    ? "100% remains in cash, modelled at 0% return. Enter an allocation above 0 to invest part of this hypothetical portfolio."
    : "Choose an asset, then enter its share of your portfolio. 25% means 25 of every 100. Anything unallocated stays as cash.";
  $("allocation-help").classList.toggle("beginner-copy", !hasRows || invested);
  $("portfolio-analysis").hidden = !hasRows;
  $("suggestions").hidden = !invested;
  document.querySelector(".workspace").classList.toggle("empty-workspace", !hasRows);
  // Retain the last layout during recalculation; aria-busy hides its stale values
  if (hasRows && $("portfolio-analysis").ariaBusy === "true") return;
  if (!latestAnalysis || !hasRows) {
    // Old exposure and loss figures must not accompany a newly edited allocation
    $("portfolio-reading").textContent = "";
    $("profile-reading").textContent = "";
    $("overlap-reading").textContent = "";
    $("exposure-details").hidden = true;
    return;
  }

  const positions = Object.entries(weights).filter(([, value]) => value > 0).sort((a, b) => b[1] - a[1]);
  const [symbol, weight] = positions[0] || [];
  // Describe measured concentration without claiming to know unprovided fund constituents or sectors
  let reading = !invested ? "100% is held in cash, modelled at 0% return. Inflation can reduce what this money buys."
    : positions.length === 1
    ? `${percent(weight)} is allocated to ${assets[symbol].name}. Your invested portion depends on this single holding.`
    : `${assets[symbol].name} is your largest holding at ${percent(weight)} across ${positions.length} assets. Multiple holdings can still share exposure.`;
  if (invested && cash(weights) > 0.01) reading += ` ${percent(cash(weights))} remains in cash, modelled at 0% return.`;
  $("portfolio-reading").textContent = reading;

  // Show evidence-backed duplicate exposure while keeping incomplete composition visibly separate
  const exposure = latestAnalysis.exposure;
  const leadingSector = exposure && Object.entries(exposure.sectors)[0];
  if (leadingSector && leadingSector[1] >= 50) {
    // Surface measured concentration even when the detailed composition table is closed
    $("portfolio-reading").textContent += ` At least ${percent(leadingSector[1])} is in ${leadingSector[0].toLowerCase()} in the dated holdings snapshot.`;
  }
  $("exposure-details").hidden = !exposure;
  $("overlap-reading").textContent = "";
  if (exposure) {
    const first = exposure.overlaps[0];
    if (first) $("overlap-reading").textContent = `${first.name || first.symbol} appears through ${Object.keys(first.routes).join(" and ")}, giving at least ${percent(first.total_percent)} combined exposure.`;
    const rows = Object.entries(exposure.sectors).map(([sector, value]) =>
      `<tr><th scope="row">${escapeHTML(sector)}</th><td class="number">${percent(value)}</td></tr>`).join("");
    const overlaps = exposure.overlaps.map(item =>
      `<li>${escapeHTML(item.name || item.symbol)}: at least ${percent(item.total_percent)} combined (${Object.entries(item.routes).map(([route, value]) => `${escapeHTML(route)}: ${percent(value)}`).join("; ")})</li>`).join("");
    $("exposure-content").innerHTML = `<p>Composition snapshot: ${escapeHTML(exposure.as_of)}. ${escapeHTML(exposure.classification_note)}. This snapshot does not describe every date in the return chart.</p><table><caption>Share of your portfolio by known business group</caption><tbody>${rows}<tr><th scope="row">Unclassified or missing fund holdings</th><td class="number">${percent(exposure.unclassified_percent)}</td></tr><tr><th scope="row">Unallocated cash</th><td class="number">${percent(exposure.cash_percent)}</td></tr></tbody></table><p>${escapeHTML(exposure.note)}</p>${overlaps ? `<ul>${overlaps}</ul>` : "<p>No overlap identified in the covered holdings. Uncovered funds may still overlap.</p>"}`;
    for (const source of exposure.sources) {
      // Sources are validated local metadata; links let the reader inspect the actual dated filing
      const link = document.createElement("a");
      link.href = source.url;
      link.textContent = `${source.symbol} filing, ${source.as_of} (${percent(source.holdings_coverage_percent)} of fund mapped)`;
      const paragraph = document.createElement("p");
      paragraph.append(link);
      $("exposure-content").append(paragraph);
    }
  }

  const context = [];
  const years = investorProfile?.horizon_years;
  const short = years == null ? investorProfile?.horizon === "short" : years <= 3;
  if (invested && (short || investorProfile?.capacity === "essential")) {
    context.push("You may need this money soon or for essentials. A market fall could force you to withdraw at a loss.");
  }
  // Match the server's stated loss screens without treating either historical data or the model as a safety guarantee
  const limit = investorProfile?.max_loss_percent;
  const lowTolerance = limit == null ? investorProfile?.tolerance === "low" : limit <= 10;
  const pathLoss = latestAnalysis.forecast?.current.drawdown_screen;
  const breaches = [];
  if (invested && Number.isFinite(limit)) {
    if (latestAnalysis.maximum_drawdown * 100 > limit + 1e-10) breaches.push("past drawdown");
    if (Number.isFinite(pathLoss) && pathLoss * 100 > limit + 1e-10) breaches.push("modelled peak-to-trough loss");
  }
  if (breaches.length) {
    context.push(`The ${breaches.join(" and ")} ${breaches.length === 1 ? "exceeds" : "exceed"} your stated ${percent(limit)} loss limit. Future losses can be larger.`);
  } else if (invested && lowTolerance) {
    context.push(`You prefer smaller losses; this mix fell ${percent(latestAnalysis.maximum_drawdown * 100)} from its peak in this sample. Future losses could be larger.`);
  }
  if (investorProfile?.goal === "income") context.push("Income payments are not modelled separately, so these figures cannot assess your income goal.");
  if (invested && !breaches.length && investorProfile?.goal === "preserve") context.push("Even portfolios with smaller fluctuations can lose money.");
  if (invested && !breaches.length && investorProfile?.goal === "growth") context.push("Compare estimated growth and possible losses before choosing a change.");
  $("profile-reading").textContent = context.join(" ");
}

function invalidateSuggestions() {
  // Abort immediately on an edit, not after the debounce, so stale suggestions cannot remain clickable
  ++suggestionSerial;
  suggestionController?.abort();
  clearTimeout(suggestionTimer);
  // Prevent a temporary empty list from pulling the page upward while new suggestions load
  $("suggestion-list").style.minHeight = `${$("suggestion-list").offsetHeight}px`;
  $("suggestion-list").replaceChildren();
  $("suggestion-state").textContent = "Updating suggestions";
  $("retry-suggestions").hidden = true;
}

document.addEventListener("portfolio:changed", () => {
  invalidateSuggestions();
  refreshExperience();
});
document.addEventListener("analysis:pending", () => {
  invalidateSuggestions();
  refreshExperience();
});
document.addEventListener("analysis:failed", () => {
  refreshExperience();
  $("suggestion-list").style.minHeight = "";
  $("suggestion-state").textContent = "Suggestions need a successful analysis. Use Retry above.";
});
document.addEventListener("portfolio:analysed", () => {
  refreshExperience();
  // Wait briefly after a valid analysis so rapid edits do not run several expensive candidate scans
  suggestionTimer = setTimeout(loadSuggestions, 300);
});
$("retry-suggestions").addEventListener("click", loadSuggestions);

async function loadSuggestions() {
  if (!latestAnalysis || !total(weights)) return;
  invalidateSuggestions();
  const serial = suggestionSerial;
  const controller = new AbortController();
  suggestionController = controller;
  const timeout = setTimeout(() => controller.abort(), 30000);
  const baseline = { ...weights };
  try {
    // These disclosed example weights reuse the existing calculator, without inventing suitability scores
    const results = await Promise.all([15, 30].map(target => apiPlanning(apiPath("candidates"), {
      current: { weights: baseline }, target_percent: target, max_position_percent: 100, cash_percent: 0,
      profile: investorProfile
    }, controller.signal)));
    if (serial !== suggestionSerial) return;
    const unique = new Map();
    for (const result of results) {
      if (result.dataset_version !== datasetVersion) throw new Error("Data changed. Reload to retry.");
      for (const proposal of result.proposals) {
        validateComparison(proposal.comparison);
        // Keep the best accepted score per asset using the same preference rule as the server
        const previous = unique.get(proposal.name);
        if (!previous || (proposal.rank_score ?? proposal.comparison.proposed.annualised_volatility) < (previous.rank_score ?? previous.comparison.proposed.annualised_volatility)) unique.set(proposal.name, proposal);
      }
    }
    const proposals = [...unique.values()].sort((a, b) =>
      (a.rank_score ?? a.comparison.proposed.annualised_volatility) - (b.rank_score ?? b.comparison.proposed.annualised_volatility)
    ).slice(0, 3);
    // A missing answer can prevent evaluation entirely; keep the server's recovery instruction visible
    const stopped = results.every(result => result.evaluated === 0) ? results[0].criteria : "";
    $("suggestion-state").textContent = proposals.length
      ? "Compare the trade-offs before applying a change. You can keep your current mix."
      : stopped ? stopped
      : "No tested change met your preferences. Keep this mix or edit your holdings and preferences.";
    for (const proposal of proposals) {
      const symbol = proposal.name;
      const before = proposal.comparison.current;
      const after = proposal.comparison.proposed;
      const article = document.createElement("article");
      const title = document.createElement("h3");
      // Cash is the unallocated remainder, so it has no asset lookup or ticker in stored weights
      const cashReserve = symbol === "Cash reserve";
      const name = cashReserve ? "cash" : assets[symbol].name;
      const target = cashReserve ? cash(proposal.weights) : proposal.weights[symbol];
      title.textContent = cashReserve ? `Keep ${percent(target)} in cash`
        : `${symbol in baseline ? "Set" : "Add"} ${name} at ${percent(target)}`;
      const reason = document.createElement("p");
      reason.textContent = `Volatility is ${percent((before.annualised_volatility - after.annualised_volatility) * 100).replace("%", " percentage points")} lower in this sample.`;
      if (proposal.comparison.forecast) {
        reason.textContent = explainComparison(proposal.comparison, investorProfile);
      }
      const tradeoff = document.createElement("p");
      tradeoff.hidden = true;
      // Expose cash-driven risk reductions instead of presenting them as diversification
      if (Math.abs(after.cash_weight_percent - before.cash_weight_percent) > 0.01) {
        tradeoff.hidden = false;
        tradeoff.textContent = `Cash changes from ${percent(before.cash_weight_percent)} to ${percent(after.cash_weight_percent)} and earns 0% in this model.`;
      }
      const preview = document.createElement("button");
      preview.type = "button";
      preview.textContent = "Preview change";
      preview.setAttribute("aria-label", `Preview ${name}`);
      preview.addEventListener("click", () => previewPortfolio(title.textContent, proposal.weights));
      article.append(title, reason, tradeoff, preview);
      $("suggestion-list").append(article);
    }
  } catch (error) {
    if (serial !== suggestionSerial) return;
    $("suggestion-state").textContent = "Suggestions could not load. Your holdings are unchanged. Retry when the server is available.";
    $("retry-suggestions").hidden = false;
  } finally {
    // An aborted older request must not release space reserved by its replacement.
    if (serial === suggestionSerial) $("suggestion-list").style.minHeight = "";
    clearTimeout(timeout);
  }
}

// Changing display units reuses stored percentage weights rather than changing the portfolio
$("entry-mode").addEventListener("change", render);
