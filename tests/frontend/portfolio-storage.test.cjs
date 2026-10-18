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
