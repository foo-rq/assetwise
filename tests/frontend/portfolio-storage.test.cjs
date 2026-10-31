const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const storage = vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../prototype/portfolio-storage.js'), 'utf8') + '\nportfolioStorage');
const assets = { A: {}, B: {} };
function memory(value = null) {
  return { getItem: () => value, setItem: (key, next) => { value = next; } };
}

test('restores edited allocations and all-cash portfolios', () => {
  for (const weights of [{ A: 59.5, B: 17 }, {}]) {
    const store = memory();
    storage.write(store, weights, 'demo-v2');
    assert.equal(JSON.stringify(storage.read(store, assets, 'demo-v2')), JSON.stringify(weights));
  }
});

test('missing save starts fresh', () => {
  assert.equal(storage.read(memory(), assets, 'demo-v2'), null);
});

test('corrected archive reuses allocation weights without overwriting the original save', () => {
  const original = JSON.stringify({ version: 1, datasetVersion: 'archive-2017-v1', weights: { A: 70 } });
  const values = new Map([['assetwise.portfolio.v1:archive-2017-v1', original]]);
  const store = { getItem: key => values.get(key) ?? null };
  const previousKey = storage.key;
  storage.key = 'assetwise.portfolio.v1:archive-2017-v2';
  try {
    assert.equal(storage.read(store, assets, 'archive-2017-v2').A, 70);
    assert.equal(values.size, 1);
    assert.equal(values.get('assetwise.portfolio.v1:archive-2017-v1'), original);
    values.set(storage.key, JSON.stringify({ version: 1, datasetVersion: 'archive-2017-v2', weights: {} }));
    assert.equal(JSON.stringify(storage.read(store, assets, 'archive-2017-v2')), '{}');
  } finally {
    storage.key = previousKey;
  }
});

test('rejects corrupt or incompatible saves without overwriting them', () => {
  const invalid = ['broken', 'null', ...[
    { version: 2, datasetVersion: 'demo-v2', weights: {} },
    { version: 1, datasetVersion: 'old', weights: {} },
    ...[{ UNKNOWN: 10 }, { A: '10' }, { A: -1 }, { A: 80, B: 30 }, []].map(weights =>
      ({ version: 1, datasetVersion: 'demo-v2', weights }))
  ].map(JSON.stringify)];
  for (const raw of invalid) {
    const store = memory(raw);
    assert.throws(() => storage.read(store, assets, 'demo-v2'));
    assert.equal(store.getItem(), raw);
  }
});

test('storage failure is surfaced to the interface', () => {
  const store = { setItem() { throw new Error('Quota exceeded'); } };
  assert.throws(() => storage.write(store, { A: 70 }, 'demo-v2'));
});

const scenarios = vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../prototype/portfolio-storage.js'), 'utf8') + '\nscenarioStorage');
test('named snapshots survive edits and preserve empty portfolios', () => {
  const store = memory();
  const weights = { A: 70 };
  scenarios.add(store, ' Original ', weights, 'demo-v2');
  weights.A = 10;
  scenarios.add(store, 'Cash', {}, 'demo-v2');
  const items = scenarios.read(store);
  assert.equal(items[0].name, 'Original');
  assert.equal(scenarios.allocation(items[0], assets, 'demo-v2').A, 70);
  assert.equal(JSON.stringify(scenarios.allocation(items[1], assets, 'demo-v2')), '{}');
});
test('duplicate names and damaged collections cannot overwrite saved scenarios', () => {
  const store = memory();
  scenarios.add(store, 'Original', { A: 70 }, 'demo-v2');
  const before = store.getItem();
  assert.throws(() => scenarios.add(store, ' original ', {}, 'demo-v2'));
  assert.equal(store.getItem(), before);
  const broken = memory('broken');
  assert.throws(() => scenarios.add(broken, 'New', {}, 'demo-v2'));
  assert.equal(broken.getItem(), 'broken');
});
test('scenario loading rejects changed datasets', () => {
  const store = memory();
  scenarios.add(store, 'Old', {}, 'demo-v1');
  assert.throws(() => scenarios.allocation(scenarios.read(store)[0], assets, 'demo-v2'));
});

test('rename preserves weights and other snapshots', () => {
  const store = memory();
  scenarios.add(store, 'Original', { A: 70 }, 'demo-v2');
  scenarios.add(store, 'Cash', {}, 'demo-v2');
  scenarios.rename(store, 'Original', ' Renamed ');
  const items = scenarios.read(store);
  assert.equal(items[0].name, 'Renamed');
  assert.equal(items[0].weights.A, 70);
  assert.equal(items[1].name, 'Cash');
});
test('invalid renames preserve storage', () => {
  const store = memory();
  scenarios.add(store, 'Original', { A: 70 }, 'demo-v2');
  scenarios.add(store, 'Cash', {}, 'demo-v2');
  const before = store.getItem();
  for (const [oldName, newName] of [['Original', 'cash'], ['Original', ' '], ['Missing', 'New']]) {
    assert.throws(() => scenarios.rename(store, oldName, newName));
    assert.equal(store.getItem(), before);
  }
});


test('removal can be restored without changing allocation data', () => {
  const values = new Map();
  const store = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  scenarios.add(store, 'Recover me', { A: 60 }, 'demo-v2');
  scenarios.remove(store, 'Recover me');
  assert.equal(scenarios.read(store).length, 0);
  scenarios.restore(store);
  assert.equal(scenarios.read(store)[0].weights.A, 60);
  assert.equal(store.getItem(`${scenarios.key}.removed`), null);
});
