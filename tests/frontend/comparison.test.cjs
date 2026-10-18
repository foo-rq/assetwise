const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');

// Run the request handler with controlled responses so slow requests are reproducible
const source = readFileSync(require('node:path').join(__dirname, '../../prototype/workspace.js'), 'utf8');
function setup(fetch) {
  const elements = {};
  const context = vm.createContext({
    fetch, AbortController, setTimeout, clearTimeout,
    comparisonNumber: 0, comparisonRequest: undefined, datasetVersion: 'demo-v2',
    weights: { A: 70 }, proposed: { A: 50 },
    metricFields: { volatility: 'annualised_volatility', return: 'annualised_arithmetic_return', drawdown: 'maximum_drawdown' },
    percent: value => `${value.toFixed(2)}%`,
    $: id => elements[id] ||= {}
  });
  vm.runInContext(source.slice(source.indexOf('async function updateComparison()'), source.indexOf('function renderScenarios()') < 0 ? source.length : source.indexOf('function renderScenarios()')), context);
  return { context, elements };
}
const side = { source: 'simulated', dataset_version: 'demo-v2', return_observations: 252,
  annualised_volatility: .1, annualised_arithmetic_return: .05, maximum_drawdown: .2 };
const response = (proposed = side) => ({ ok: true, json: async () => ({ current: side, proposed }) });

test('successful comparison shows percentages and enables Apply', async () => {
  const { context, elements } = setup(async () => response());
  await context.updateComparison();
  assert.equal(elements.apply.disabled, false);
  assert.match(elements['comparison-body'].innerHTML, /10.00%/);
});

test('failed request keeps Apply disabled and gives recovery instructions', async () => {
  const { context, elements } = setup(async () => { throw new Error('offline'); });
  await context.updateComparison();
  assert.equal(elements.apply.disabled, true);
  assert.match(elements['preview-error'].textContent, /Try Preview change again/);
});

test('incompatible dataset cannot enable Apply', async () => {
  const { context, elements } = setup(async () => response({ ...side, dataset_version: 'new' }));
  await context.updateComparison();
  assert.equal(elements.apply.disabled, true);
});

test('an old response cannot reveal a cleared preview', async () => {
  let finish;
  const { context, elements } = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = context.updateComparison();

  // Editing a field or closing the dialog increments this same request counter
  context.comparisonNumber += 1;
  finish(response());
  await pending;
  assert.equal(elements.apply.disabled, true);
  assert.equal(elements['comparison-results'].hidden, true);
});

test('old server explains that a restart is needed', async () => {
  const { context, elements } = setup(async () => ({ ok: false, status: 404 }));
  await context.updateComparison();
  assert.equal(elements.apply.disabled, true);
  assert.match(elements['preview-error'].textContent, /Restart the Assetwise server/);
});
