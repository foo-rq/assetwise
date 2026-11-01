const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const workspace = readFileSync(join(__dirname, '../../prototype/workspace.js'), 'utf8');
const planning = readFileSync(join(__dirname, '../../prototype/planning.js'), 'utf8');

function setup(fetch, profile) {
  const nodes = {};
  const context = vm.createContext({ fetch, AbortController, Event, setTimeout, clearTimeout,
    investorProfile: profile, weights: { A: 100 }, historyDates: ['2017-11-10'],
    requestNumber: 0, analysisRequest: null, latestAnalysis: null,
    datasetVersion: 'archive', dataSource: 'historical',
    metricFields: { volatility: 'annualised_volatility', return: 'annualised_arithmetic_return', drawdown: 'maximum_drawdown' },
    apiPath: name => name, percent: value => `${value.toFixed(2)}%`, escapeHTML: String,
    observationLabel: count => `${count} trading sessions`,
    document: { dispatchEvent() {} },
    $: id => nodes[id] ||= { textContent: '', hidden: false,
      classList: { add() {}, remove() {}, toggle() {} } }
  });
  // Exercise the real validation and analysis update without starting the page's asset loader
  vm.runInContext(planning.slice(0, planning.indexOf('function comparisonTable')), context);
  vm.runInContext(workspace.slice(workspace.indexOf('async function updateAnalysis()'),
    workspace.indexOf('$("retry-analysis").addEventListener')), context);
  return { context, nodes };
}

const metrics = { expected_annual_return: .08, estimated_annual_volatility: .2 };
const forecast = { as_of: '2017-11-10', horizon_years: 9, return_change_range_90: [0, 0],
  current: { ...metrics, projected_growth: .72, projected_growth_range: [-.2, 1.8], drawdown_screen: .6 },
  proposed: { ...metrics, projected_growth: .72, projected_growth_range: [-.2, 1.8], drawdown_screen: .6 },
  assumptions: 'Cash earns 0%; nominal values' };
const result = { source: 'historical', dataset_version: 'archive', annualised_volatility: .2,
  annualised_arithmetic_return: .08, maximum_drawdown: .3, return_observations: 1225, forecast };

test('analysis sends survey answers and displays cumulative growth separately from annual risk', async () => {
  let payload;
  const { context, nodes } = setup(async (url, request) => {
    payload = JSON.parse(request.body);
    return { ok: true, json: async () => result };
  }, { horizon_years: 9, max_loss_percent: 20 });
  await context.updateAnalysis();
  assert.deepEqual(payload.profile, { horizon_years: 9, max_loss_percent: 20 });
  assert.equal(nodes['forecast-growth-label'].textContent, 'Modelled growth over 9 years');
  assert.equal(nodes['expected-return'].textContent, '72.00%');
  assert.equal(nodes['estimated-volatility'].textContent, '20.00%');
  assert.match(nodes['forecast-detail-content'].innerHTML, /Expected annual return<\/th><td class="number">8.00%/);
  assert.match(nodes['forecast-detail-content'].innerHTML, /-20.00% to 180.00%/);
  assert.match(nodes['forecast-detail-content'].innerHTML, /Modelled peak-to-trough loss<\/th><td class="number">60.00%/);
  assert.match(nodes['forecast-detail-content'].innerHTML, /Actual growth and losses can fall outside/);
  assert.equal(nodes['forecast-details'].hidden, false);
});

test('an unknown investment period stays explicit while annual metrics remain available', async () => {
  const { context, nodes } = setup(async () => ({ ok: true,
    json: async () => ({ ...result, forecast: { ...forecast, horizon_years: null,
      current: metrics, proposed: metrics } }) }), {});
  await context.updateAnalysis();
  assert.equal(nodes['forecast-growth-label'].textContent, 'Expected annual return');
  assert.equal(nodes['expected-return'].textContent, '8.00%');
  assert.match(nodes['forecast-period-note'].textContent, /Choose an investment period/);
  assert.doesNotMatch(nodes['forecast-detail-content'].innerHTML, /Growth over/);
});

test('analysis rejects a response for an old investment period', async () => {
  const { context, nodes } = setup(async () => ({ ok: true, json: async () => result }), { horizon_years: 5 });
  await context.updateAnalysis();
  assert.equal(nodes['expected-return'].textContent, 'Unavailable');
  assert.equal(nodes['retry-analysis'].hidden, false);
  assert.equal(nodes['forecast-details'].hidden, true);
  assert.equal(context.latestAnalysis, null);
});


test('pending analysis retains disclosure layout and clears busy state after success or failure', async () => {
  let finish;
  const { context, nodes } = setup(() => new Promise(resolve => { finish = resolve; }), { horizon_years: 9 });
  const first = context.updateAnalysis();
  nodes['forecast-details'] = { hidden: false };
  nodes['forecast-period-note'] = { textContent: 'Existing period' };
  finish({ ok: true, json: async () => result });
  await first;
  const pending = context.updateAnalysis();
  assert.equal(nodes['portfolio-analysis'].ariaBusy, 'true');
  assert.equal(nodes['forecast-details'].hidden, false);
  assert.equal(context.latestAnalysis, null);
  finish({ ok: false });
  await pending;
  assert.equal(nodes['portfolio-analysis'].ariaBusy, 'false');
  assert.equal(nodes['forecast-details'].hidden, true);
  assert.equal(nodes['retry-analysis'].hidden, false);
});
