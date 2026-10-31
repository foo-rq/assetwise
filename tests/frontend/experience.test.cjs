const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../../prototype/experience.js'), 'utf8');

function setup(apiPlanning) {
  const nodes = {};
  const makeNode = () => ({ children: [], hidden: false, textContent: '', listeners: {},
    classList: { toggle() {} }, addEventListener(name, action) { this.listeners[name] = action; },
    setAttribute() {}, append(...items) { this.children.push(...items); },
    replaceChildren() { this.children = []; } });
  const context = vm.createContext({
    apiPlanning, AbortController, setTimeout, clearTimeout, Map,
    FormData: function (form) { return Object.entries(form); },
    weights: { A: 100 }, latestAnalysis: {}, datasetVersion: 'archive',
    assets: { A: { name: 'Asset A' }, B: { name: 'Asset B' } },
    total: values => Object.values(values).reduce((a, b) => a + b, 0),
    cash: values => 100 - Object.values(values).reduce((a, b) => a + b, 0),
    percent: value => `${value.toFixed(2)}%`, apiPath: name => name,
    validateComparison() {}, render() {}, previewPortfolio() {}, comparisonTable: () => '<table></table>',
    forecastTable: () => '<table></table>',
    document: { addEventListener() {}, createElement: makeNode, querySelector: makeNode },
    $: id => nodes[id] ||= makeNode()
  });
  vm.runInContext(source, context);
  return { context, nodes };
}

const side = { annualised_volatility: .2, annualised_arithmetic_return: .1, maximum_drawdown: .3 };
const proposal = risk => ({ name: 'B', weights: { A: 70, B: 30 }, comparison: {
  current: side, proposed: { ...side, annualised_volatility: risk }
} });

test('automatic suggestions deduplicate assets and leave holdings unchanged', async () => {
  const { context, nodes } = setup(async (path, body) => ({ dataset_version: 'archive',
    proposals: [proposal(body.target_percent === 15 ? .15 : .1)] }));
  await context.loadSuggestions();
  assert.equal(nodes['suggestion-list'].children.length, 1);
  assert.equal(context.weights.A, 100);
  assert.match(nodes['suggestion-list'].children[0].children[1].textContent, /10.00 percentage points/);
});

test('exact survey answers preserve unknowns and derive legacy categories without guessing', () => {
  const { context } = setup();
  const form = { experience: 'new', goal: 'growth', capacity: 'spare',
    horizon_years: '', max_loss_percent: '', amount: '' };
  const unknown = context.readProfile(form);
  assert.equal(unknown.horizon_years, null);
  assert.equal(unknown.max_loss_percent, null);
  assert.equal(unknown.horizon, 'unsure');
  assert.equal(unknown.tolerance, 'unsure');
  assert.equal(unknown.age, 'private');
  assert.equal(unknown.existing, 'no');
  const explicit = context.readProfile({ ...form, horizon_years: '9.25', max_loss_percent: '20', amount: '25000' });
  assert.equal(explicit.horizon_years, 9.25);
  assert.equal(explicit.max_loss_percent, 20);
  assert.equal(explicit.horizon, 'medium');
  assert.equal(explicit.tolerance, 'moderate');
  assert.equal(explicit.amount, 25000);
  assert.equal(context.readProfile({ ...form, horizon_years: '.25', max_loss_percent: '0' }).tolerance, 'low');
  assert.equal(context.readProfile({ ...form, horizon_years: '40', max_loss_percent: '100' }).horizon, 'long');
  for (const horizon_years of ['0', '40.1', 'Infinity', 'not a number']) {
    assert.throws(() => context.readProfile({ ...form, horizon_years }), /period/);
  }
  for (const max_loss_percent of ['-1', '101', 'NaN']) {
    assert.throws(() => context.readProfile({ ...form, max_loss_percent }), /loss limit/);
  }
});

test('legacy preferences reopen with blank numeric fields rather than invented answers', () => {
  const { context, nodes } = setup();
  vm.runInContext('investorProfile = { experience: "some", horizon: "long", tolerance: "low", amount: 0 }', context);
  const fields = { experience: { focus() {} }, horizon_years: { value: 9 }, max_loss_percent: { value: 20 }, amount: {} };
  nodes['profile-form'].elements = { namedItem: name => fields[name] };
  nodes['profile-form'].reset = () => {
    for (const field of Object.values(fields)) field.value = '';
  };
  nodes['edit-profile'].listeners.click();
  assert.equal(fields.horizon_years.value, '');
  assert.equal(fields.max_loss_percent.value, '');
  assert.equal(fields.experience.value, 'some');
});

test('retired income answers remain saved and require a new aim only when editing', () => {
  const { context, nodes } = setup();
  vm.runInContext('investorProfile = { experience: "some", goal: "income" }', context);
  const fields = { experience: { focus() {} }, goal: { value: 'income' } };
  nodes['profile-form'].elements = { namedItem: name => fields[name] };
  nodes['profile-form'].reset = () => {};
  context.latestAnalysis = { maximum_drawdown: .2 };
  context.refreshExperience();
  assert.match(nodes['profile-reading'].textContent, /cannot assess your income goal/);
  nodes['edit-profile'].listeners.click();
  assert.equal(fields.goal.value, '');
  assert.match(nodes['profile-error'].textContent, /Income payments are not forecast/);
  assert.equal(vm.runInContext('investorProfile.goal', context), 'income');
  const html = readFileSync(require('node:path').join(__dirname, '../../prototype/index.html'), 'utf8');
  assert.doesNotMatch(html, /<option value="income">/);
});

test('legacy goal-delay capacity stays selectable without returning to the new survey', () => {
  const { context, nodes } = setup();
  vm.runInContext('investorProfile = { experience: "some", capacity: "some" }', context);
  const capacity = { options: [], querySelector() { return { remove: () => { this.options = []; } }; },
    append(option) { this.options.push(option); } };
  const fields = { experience: { focus() {} }, capacity };
  nodes['profile-form'].elements = { namedItem: name => fields[name] };
  nodes['profile-form'].reset = () => {};
  nodes['edit-profile'].listeners.click();
  nodes['edit-profile'].listeners.click();
  assert.equal(capacity.options.length, 1);
  assert.equal(capacity.options[0].value, 'some');
  assert.equal(capacity.value, 'some');
  assert.equal(vm.runInContext('investorProfile.capacity', context), 'some');
  const html = readFileSync(require('node:path').join(__dirname, '../../prototype/index.html'), 'utf8');
  assert.doesNotMatch(html, /<option value="some">It could delay a goal/);
});

test('the report names breached loss screens and exact years override older categories', () => {
  const { context, nodes } = setup();
  vm.runInContext('investorProfile = { experience: "some", horizon: "long", horizon_years: 1, max_loss_percent: 5, goal: "growth" }', context);
  context.latestAnalysis = { maximum_drawdown: .12,
    forecast: { current: { projected_growth_range: [.2, .3], drawdown_screen: .2 } } };
  context.refreshExperience();
  assert.match(nodes['profile-reading'].textContent, /need this money soon/);
  assert.match(nodes['profile-reading'].textContent, /past drawdown and modelled peak-to-trough loss exceed your stated 5.00% loss limit/);
  assert.doesNotMatch(nodes['profile-reading'].textContent, /Compare the return trade-off/);
  vm.runInContext('investorProfile = { horizon: "short", horizon_years: 9, tolerance: "low", max_loss_percent: 30 }', context);
  context.refreshExperience();
  assert.equal(nodes['profile-reading'].textContent, '');
});

test('cash reserve suggestions use the remainder without an invented CASH asset', async () => {
  const item = { name: 'Cash reserve', weights: { A: 70 }, comparison: {
    current: { ...side, cash_weight_percent: 0 },
    proposed: { ...side, annualised_volatility: .1, cash_weight_percent: 30 }
  } };
  const { context, nodes } = setup(async () => ({ dataset_version: 'archive', proposals: [item] }));
  let preview;
  context.previewPortfolio = (name, weights) => { preview = { name, weights }; };
  await context.loadSuggestions();
  const card = nodes['suggestion-list'].children[0];
  assert.equal(card.children[0].textContent, 'Keep 30.00% in cash');
  assert.match(card.children[2].textContent, /earns 0%/);
  card.children[3].listeners.click();
  assert.equal(preview.name, 'Keep 30.00% in cash');
  assert.equal(preview.weights.A, 70);
  assert.equal(preview.weights.CASH, undefined);
});

test('return-model disagreement remains visible on a qualifying suggestion', async () => {
  const item = proposal(.1);
  item.comparison.forecast = {
    current: { estimated_annual_volatility: .2 }, proposed: { estimated_annual_volatility: .1 },
    return_change_model_range: [-.01, .02]
  };
  const { context, nodes } = setup(async () => ({ dataset_version: 'archive', proposals: [item] }));
  await context.loadSuggestions();
  assert.equal(nodes['suggestion-list'].children.length, 1);
  assert.match(nodes['suggestion-list'].children[0].children[1].textContent, /Return estimates disagree/);
  assert.equal(context.weights.A, 100);
});

test('editing while suggestions are pending cannot reveal stale proposals', async () => {
  const finish = [];
  const { context, nodes } = setup(() => new Promise(resolve => finish.push(resolve)));
  const pending = context.loadSuggestions();
  context.invalidateSuggestions();
  finish.forEach(resolve => resolve({ dataset_version: 'archive', proposals: [proposal(.1)] }));
  await pending;
  assert.equal(nodes['suggestion-list'].children.length, 0);
});

test('empty results allow no change and failures offer retry', async () => {
  const first = setup(async () => ({ dataset_version: 'archive', proposals: [] }));
  await first.context.loadSuggestions();
  assert.match(first.nodes['suggestion-state'].textContent, /Keeping this mix/);
  const second = setup(async () => { throw new Error('Offline'); });
  await second.context.loadSuggestions();
  assert.equal(second.nodes['retry-suggestions'].hidden, false);
  assert.equal(second.nodes['suggestion-list'].children.length, 0);
});

test('a missing investment period shows the server recovery instruction rather than a no-change verdict', async () => {
  const reason = 'Choose an investment period in Your preferences to compare growth and possible losses';
  const { context, nodes } = setup(async () => ({ dataset_version: 'archive', proposals: [], evaluated: 0, criteria: reason }));
  await context.loadSuggestions();
  assert.equal(nodes['suggestion-state'].textContent, reason);
  assert.doesNotMatch(nodes['suggestion-state'].textContent, /Keeping this mix/);
});

test('a suggestion from another dataset never reaches the list', async () => {
  const { context, nodes } = setup(async () => ({ dataset_version: 'wrong', proposals: [proposal(.1)] }));
  await context.loadSuggestions();
  assert.equal(nodes['suggestion-list'].children.length, 0);
  assert.equal(nodes['retry-suggestions'].hidden, false);
});



test('zero allocations retain the cash report while a clean draft stays empty at every experience level', () => {
  const { context, nodes } = setup(async () => ({}));
  for (const experience of ['new', 'some', 'confident']) {
    vm.runInContext(`investorProfile = { experience: '${experience}', horizon: 'short', tolerance: 'low', goal: 'preserve' }`, context);
    context.weights = { A: 0 };
    context.refreshExperience();
    assert.equal(nodes['holding-details'].hidden, false);
    assert.equal(nodes['portfolio-analysis'].hidden, false);
    assert.match(nodes['allocation-help'].textContent, /above 0/);
    assert.match(nodes['portfolio-reading'].textContent, /cash, modelled at 0% return/);
    assert.match(nodes['portfolio-reading'].textContent, /Inflation/);
    assert.equal(nodes['profile-reading'].textContent, '');
    context.weights = {};
    context.refreshExperience();
    assert.equal(nodes['holding-details'].hidden, true);
    assert.equal(nodes['portfolio-analysis'].hidden, true);
    assert.match(nodes['allocation-help'].textContent, /Choose an asset/);
  }
});


test('pending analysis clears stale concentration, exposure and loss guidance', () => {
  const { context, nodes } = setup(async () => ({}));
  context.latestAnalysis = null;
  context.refreshExperience();
  assert.equal(nodes['portfolio-reading'].textContent, '');
  assert.equal(nodes['profile-reading'].textContent, '');
  assert.equal(nodes['overlap-reading'].textContent, '');
  assert.equal(nodes['exposure-details'].hidden, true);
});
