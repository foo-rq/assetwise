const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '../../prototype/planning.js'), 'utf8');

function setup(fetch) {
  const elements = {};
  const context = vm.createContext({ fetch, AbortController, setTimeout, clearTimeout,
    datasetVersion: 'demo-v2', dataSource: 'simulated', investorProfile: null,
    apiPath: name => `/api/${name}?dataset=demo`,
    observationLabel: count => `${count} simulated sessions`, weights: { A: 70 },
    metricFields: { risk: 'annualised_volatility', return: 'annualised_arithmetic_return', drawdown: 'maximum_drawdown' },
    percent: value => `${value.toFixed(2)}%`, escapeHTML: value => value,
    cash: weights => 100 - Object.values(weights).reduce((a, b) => a + b, 0),
    $: id => elements[id] ||= { replaceChildren() {}, showModal() {} }
  });

  // Exercise the request functions independently of browser event registration
  vm.runInContext(source.slice(0, source.indexOf('$("proposal-dialog").addEventListener')), context);
  return { context, elements };
}
const side = { source: 'simulated', dataset_version: 'demo-v2', return_observations: 252,
  annualised_volatility: .1, annualised_arithmetic_return: .02, maximum_drawdown: .12 };
const response = () => ({ ok: true, json: async () => ({ current: side, proposed: side }) });
const forecast = { model: 'chronological-v1:market_factor', as_of: '2017-11-10',
  current: { expected_annual_return: .08, estimated_annual_volatility: .2 },
  proposed: { expected_annual_return: .09, estimated_annual_volatility: .16 },
  return_change_range_90: [-.1, .12], assumptions: 'Cash earns 0%' };
const horizonForecast = { ...forecast, horizon_years: 9,
  current: { ...forecast.current, projected_growth: .72, projected_growth_range: [-.2, 1.8], drawdown_screen: .6 },
  proposed: { ...forecast.proposed, projected_growth: .86, projected_growth_range: [-.1, 1.7], drawdown_screen: .5 },
  projected_relative_wealth_range: [-.15, .25] };

test('forecast preview sends preferences and leads with growth for the selected period', async () => {
  let payload;
  const { context, elements } = setup(async (url, request) => {
    payload = JSON.parse(request.body);
    return { ok: true, json: async () => ({ current: side, proposed: side, forecast: horizonForecast }) };
  });
  context.investorProfile = { horizon_years: 9, max_loss_percent: 20 };
  await context.previewPortfolio('Forecast', { A: 50 });
  assert.deepEqual(payload.profile, { horizon_years: 9, max_loss_percent: 20 });
  assert.match(elements['proposal-results'].innerHTML, /Modelled growth over 9 years/);
  assert.match(elements['proposal-results'].innerHTML, /86.00%/);
  assert.match(elements['proposal-results'].innerHTML, /9.00%/);
  assert.match(elements['proposal-results'].innerHTML, /<summary>Historical comparison/);
  assert.match(elements['proposal-explanation'].textContent, /growth over 9 years: 72.00% to 86.00%/);
  assert.doesNotMatch(elements['proposal-results'].innerHTML, /Next 12 months/);
  assert.match(elements['proposal-results'].innerHTML, /final value relative to current: -15.00% to 25.00%/);
  assert.match(elements['proposal-results'].innerHTML, /<th scope="row">Growth range, lower end<\/th><td class="number">-20.00%<\/td><td class="number">-10.00%<\/td>/);
  assert.match(elements['proposal-results'].innerHTML, /<th scope="row">Growth range, upper end<\/th><td class="number">180.00%<\/td><td class="number">170.00%<\/td>/);
  assert.match(elements['proposal-results'].innerHTML, /<th scope="row">Drawdown screen<\/th><td class="number">60.00%<\/td><td class="number">50.00%<\/td>/);
  assert.match(elements['proposal-results'].innerHTML, /Actual losses can be larger/);
});

test('forecast distinguishes estimator disagreement from future-outcome uncertainty', () => {
  const { context } = setup(response);
  const value = { ...forecast, return_change_model_range: [-.01, .02] };
  context.value = value;
  const html = vm.runInContext('forecastTable(value)', context);
  assert.match(html, /Expected annual return change across estimates: -1\.00 to 2\.00 percentage points/);
  assert.match(html, /Annual return difference:/);
  assert.match(html, /Choose an investment period in Your preferences to compare growth/);
  context.comparison = { current: side, proposed: side, forecast: value };
  vm.runInContext('validateComparison(comparison)', context);
  for (const range of [[NaN, .02], [.02, -.01], [.01], { length: 2 }]) {
    context.comparison = { current: side, proposed: side,
      forecast: { ...forecast, return_change_model_range: range } };
    assert.throws(() => vm.runInContext('validateComparison(comparison)', context), /Forecast is incomplete/);
  }
});

test('stale periods and malformed growth ranges never enable Apply', async () => {
  const { context, elements } = setup(async () => ({ ok: true,
    json: async () => ({ current: side, proposed: side, forecast: horizonForecast }) }));
  context.investorProfile = { horizon_years: 5 };
  await context.previewPortfolio('Stale period', { A: 50 });
  assert.equal(elements['apply-proposal'].disabled, true);
  assert.match(elements['proposal-error'].textContent, /investment period/);
  context.investorProfile = { horizon_years: 9 };
  for (const range of [[NaN, .2], [.2, -.1], [-1.1, .2], [.2]]) {
    assert.throws(() => context.validateForecast({ ...horizonForecast,
      proposed: { ...horizonForecast.proposed, projected_growth_range: range } }, '2017-11-10'));
  }
  assert.throws(() => context.validateForecast({ ...horizonForecast,
    projected_relative_wealth_range: [.5, .2] }, '2017-11-10'));
  context.investorProfile = {};
  assert.throws(() => context.validateForecast(horizonForecast, '2017-11-10'));
});

test('a selected period requires a finite drawdown screen within 0% to 100%', () => {
  const { context } = setup();
  context.investorProfile = { horizon_years: 9 };
  for (const drawdown_screen of [null, undefined, NaN, -.01, 1.01]) {
    assert.throws(() => context.validateForecast({ ...horizonForecast,
      proposed: { ...horizonForecast.proposed, drawdown_screen } }, '2017-11-10'));
  }
  for (const drawdown_screen of [0, 1]) {
    context.validateForecast({ ...horizonForecast,
      proposed: { ...horizonForecast.proposed, drawdown_screen } }, '2017-11-10');
  }
  context.investorProfile = {};
  assert.throws(() => context.validateForecast({ ...forecast,
    proposed: { ...forecast.proposed, drawdown_screen: .3 } }, '2017-11-10'));
});

test('an unavailable numerical projection needs a visible explanation', () => {
  const { context } = setup();
  context.investorProfile = { horizon_years: 9 };
  const unavailable = { ...horizonForecast, projected_relative_wealth_range: null,
    current: { ...forecast.current, projected_growth: null, projected_growth_range: null, drawdown_screen: 1 },
    proposed: { ...forecast.proposed, projected_growth: null, projected_growth_range: null, drawdown_screen: 1 } };
  assert.throws(() => context.validateForecast(unavailable, '2017-11-10'));
  unavailable.projection_notice = 'This period produces an unstable numerical projection.';
  context.validateForecast(unavailable, '2017-11-10');
  assert.match(context.forecastTable(unavailable), /Unavailable/);
  assert.match(context.forecastTable(unavailable), /unstable numerical projection/);
});

test('invalid forecast numbers and future cutoffs cannot enable a comparison', () => {
  const { context } = setup();
  assert.throws(() => context.validateComparison({ current: side, proposed: side,
    forecast: { ...forecast, return_change_range_90: [NaN, .1] } }));
  assert.throws(() => context.validateComparison({ current: { ...side, dates: ['2016-12-30'] },
    proposed: side, forecast }));
});

test('preview does not edit current holdings and enables Apply only on success', async () => {
  const { context, elements } = setup(async () => response());
  await context.previewPortfolio('Example', { A: 50 });
  assert.equal(context.weights.A, 70);
  assert.equal(elements['apply-proposal'].disabled, false);
  assert.match(elements['proposal-results'].innerHTML, /70.00%/);
});

test('late preview response cannot reenable Apply after cancellation', async () => {
  let resolve;
  const { context, elements } = setup(() => new Promise(done => { resolve = done; }));
  const pending = context.previewPortfolio('Example', { A: 50 });
  vm.runInContext('proposalSerial += 1', context);
  resolve(response());
  await pending;
  assert.equal(elements['apply-proposal'].disabled, true);
});

test('dataset mismatch and mismatched periods reject comparisons', () => {
  const { context } = setup();
  assert.throws(() => context.validateComparison({ current: side, proposed: { ...side, dataset_version: 'old' } }));
  assert.throws(() => context.validateComparison({ current: side, proposed: { ...side, return_observations: 125 } }));
});

test('network failure leaves Apply disabled and a visible error', async () => {
  const { context, elements } = setup(async () => { throw new Error('Server unavailable'); });
  await context.previewPortfolio('Example', { A: 50 });
  assert.equal(elements['apply-proposal'].disabled, true);
  assert.equal(elements['proposal-error'].textContent, 'Server unavailable');
});

test('comparison shows Sharpe as a ratio and keeps missing values explicit', () => {
  const { context } = setup();
  const table = context.comparisonTable({current: {...side, sharpe_ratio: .89}, proposed: side});
  assert.match(table, /Sharpe ratio/);
  assert.match(table, />0.89</);
  assert.match(table, /Unavailable/);
  assert.doesNotMatch(table, /89.00%/);
});

test('composition comparison retains unknown exposure and rejects different snapshot dates', () => {
  const { context } = setup();
  const before = {as_of:'2017-09-30',sectors:{Technology:100},unclassified_percent:0,overlaps:[]};
  const after = {...before,sectors:{Technology:70},unclassified_percent:30};
  const result = context.exposureComparison({current:{exposure:before},proposed:{exposure:after}});
  assert.match(result,/Unclassified holdings/);
  assert.match(result,/30.00%/);
  assert.match(result,/None identified in covered holdings/);
  assert.equal(context.exposureComparison({current:{exposure:before},proposed:{exposure:{...after,as_of:'2016-09-30'}}}), '');
});


test('comparing identical allocations explains why Apply is disabled', async () => {
  const { context, elements } = setup(async () => response());
  await context.previewPortfolio('Same portfolio', { A: 70, B: 0 });
  assert.equal(elements['apply-proposal'].disabled, true);
  assert.match(elements['proposal-explanation'].textContent, /no change to apply/);
});

for (const experience of ['new', 'some', 'confident']) {
  test(`${experience}: lower-growth explanations identify the actual preference trade-off`, () => {
    const { context } = setup(response);
    const comparison = { current: { maximum_drawdown: .15 }, forecast: {
      horizon_years: 3,
      current: { projected_growth: .5, estimated_annual_volatility: .2, drawdown_screen: .3, projected_growth_range: [-.25, 1] },
      proposed: { projected_growth: .2, estimated_annual_volatility: .1 }
    } };
    const base = { experience, goal: 'growth', capacity: 'spare', max_loss_percent: 10 };
    const limited = context.explainComparison(comparison, base);
    assert.match(limited, /exceeds your 10.00% loss limit/);
    assert.match(limited, /gives up estimated growth/);
    assert.match(limited, /50.00% to 20.00%/);
    const preserve = context.explainComparison(comparison, { ...base, goal: 'preserve', max_loss_percent: 80 });
    assert.match(preserve, /chose to protect your money/);
    const essential = context.explainComparison(comparison, { ...base, capacity: 'essential', max_loss_percent: null });
    assert.match(essential, /covers essential needs/);
    const explore = context.explainComparison(comparison, { ...base, goal: 'learn', max_loss_percent: null });
    assert.match(explore, /trades some estimated growth/);
    assert.doesNotMatch(explore, /exceeds your|protect your money/);
    // A manual comparison can lower growth and increase risk; never call that a risk reduction
    comparison.forecast.proposed.estimated_annual_volatility = .3;
    assert.match(context.explainComparison(comparison, { ...base, max_loss_percent: null }), /without reducing fluctuations/);
    comparison.forecast.horizon_years = null;
    comparison.forecast.current.projected_growth = null;
    comparison.forecast.proposed.projected_growth = null;
    assert.doesNotMatch(context.explainComparison(comparison, base), /growth over|gives up/);
  });
}

test('analysis limits separate essential caveats from optional calculations', () => {
  const html = readFileSync(join(__dirname, '../../prototype/index.html'), 'utf8');
  const dialog = html.slice(html.indexOf('<dialog id="analysis-dialog"'), html.indexOf('<!-- Saved snapshots'));
  assert.match(dialog, /<summary>Calculation details<\/summary>/);
  assert.match(dialog, /<summary>Data coverage<\/summary>/);
  assert.match(dialog, /<summary>Forecast assumptions<\/summary>/);
  assert.doesNotMatch(dialog, /<details[^>]* open/);
  assert.match(dialog, /Cash earns 0%/);
});
