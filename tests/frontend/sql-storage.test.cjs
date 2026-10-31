const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function setup(fetch, values = new Map()) {
  const localStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value)
  };
  const context = vm.createContext({ fetch, localStorage, crypto: webcrypto,
    AbortSignal, setTimeout, Map, Uint8Array });
  for (const file of ['portfolio-storage.js', 'sql-storage.js']) {
    vm.runInContext(readFileSync(join(__dirname, '../../prototype', file), 'utf8'), context);
  }
  return { context, values, storage: vm.runInContext('sqlStorage', context) };
}
const snapshot = { version: 1, datasetVersion: 'demo-v2', weights: { 'DEMO-EQ1': 70 } };
const empty = { revision: 0, current: [], scenarios: [], removed: null };
const response = result => ({ ok: true, json: async () => result });

test('imports legacy browser saves once without deleting their backup', async () => {
  const values = new Map([
    ['assetwise.portfolio.v1', JSON.stringify(snapshot)],
    ['assetwise.scenarios.v1', JSON.stringify({ version: 1, items: [{ ...snapshot, name: 'Original' }] })]
  ]);
  const writes = [];
  const { storage } = setup(async (url, request) => {
    if (request.method === 'GET') return response(empty);
    const state = JSON.parse(request.body);
    writes.push(state);
    return response({ ...state, revision: 1 });
  }, values);
  await storage.open();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].current[0].weights['DEMO-EQ1'], 70);
  assert.equal(writes[0].scenarios[0].name, 'Original');
  assert.equal(values.get('assetwise.portfolio.v1'), JSON.stringify(snapshot));
  assert.match(values.get('assetwise.workspace-key.v1'), /^[a-f0-9]{64}$/);
});

test('existing SQL state takes precedence over legacy browser backups', async () => {
  const values = new Map([['assetwise.portfolio.v1', JSON.stringify(snapshot)]]);
  let puts = 0;
  const { storage } = setup(async (url, request) => {
    if (request.method === 'PUT') puts++;
    return response({ ...empty, revision: 3, current: [{ ...snapshot, weights: {} }] });
  }, values);
  await storage.open();
  assert.equal(puts, 0);
  assert.equal(JSON.parse(storage.getItem('assetwise.portfolio.v1:demo-v2')).weights['DEMO-EQ1'], undefined);
});

test('queued edits use successive confirmed revisions and retain independent snapshots', async () => {
  const writes = [];
  const { storage } = setup(async (url, request) => {
    if (request.method === 'GET') return response(empty);
    const state = JSON.parse(request.body);
    writes.push(state);
    return response({ ...state, revision: state.revision + 1 });
  });
  await storage.open();
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify(snapshot));
  const first = storage.flush();
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify({ ...snapshot, weights: {} }));
  await Promise.all([first, storage.flush()]);
  assert.deepEqual(writes.map(state => state.revision), [0, 1]);
  assert.equal(writes[0].current[0].weights['DEMO-EQ1'], 70);
  assert.deepEqual(writes[1].current[0].weights, {});
});

test('a conflict blocks later writes and preserves unsaved edits for export', async () => {
  let writes = 0;
  const { storage } = setup(async (url, request) => {
    if (request.method === 'GET') return response(empty);
    writes++;
    return { ok: false, status: 409, json: async () => ({ detail: 'Another tab saved changes' }) };
  });
  await storage.open();
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify(snapshot));
  await assert.rejects(storage.flush(), /Another tab/);
  await assert.rejects(storage.flush(), /Another tab/);
  assert.equal(writes, 1);
  assert.equal(storage.snapshot().current[0].weights['DEMO-EQ1'], 70);
});

test('a failed connection cannot overwrite unseen SQL state', async () => {
  let puts = 0;
  const { storage } = setup(async (url, request) => {
    if (request.method === 'PUT') puts++;
    throw new Error('Offline');
  });
  await assert.rejects(storage.open(), /Cannot reach saving/);
  await assert.rejects(storage.flush(), /Saving could not connect/);
  assert.equal(puts, 0);
});

test('a transient write failure can be retried without losing accepted weights', async () => {
  let fail = true;
  const { storage } = setup(async (url, request) => {
    if (request.method === 'GET') return response(empty);
    if (fail) throw new Error('Offline');
    return response({ revision: 1 });
  });
  await storage.open();
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify(snapshot));
  await assert.rejects(storage.flush(), /Cannot reach saving/);
  fail = false;
  await storage.flush();
  assert.equal(storage.revision, 1);
  assert.equal(storage.snapshot().current[0].weights['DEMO-EQ1'], 70);
});

test('unsent newer edits remain dirty after an earlier write succeeds', async () => {
  let finish;
  const { storage } = setup(async (url, request) => {
    if (request.method === 'GET') return response(empty);
    return new Promise(resolve => { finish = () => resolve(response({ revision: 1 })); });
  });
  await storage.open();
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify(snapshot));
  const pending = storage.flush();
  await Promise.resolve();
  await Promise.resolve();
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify({ ...snapshot, weights: {} }));
  finish();
  await pending;
  assert.equal(storage.dirty, true);
});

test('backup import validates its format and only clears dirty state after SQL accepts it', async () => {
  const writes = [];
  const { storage } = setup(async (url, request) => {
    if (request.method === 'GET') return response(empty);
    writes.push(JSON.parse(request.body));
    return response({ revision: 1 });
  });
  await storage.open();
  await assert.rejects(storage.importBackup('{"format":"unknown"}'), /Assetwise backup/);
  assert.equal(writes.length, 0);
  await storage.importBackup(JSON.stringify({ format: 'assetwise-backup-v1', current: [snapshot], scenarios: [], removed: null }));
  assert.equal(writes[0].current[0].weights['DEMO-EQ1'], 70);
  assert.equal(storage.dirty, false);
});

test('a failed scenario mutation restores its name and recovery record without rolling back holdings', async () => {
  const { context, storage } = setup();
  storage.setItem('assetwise.scenarios.v1', JSON.stringify({ version: 1, items: [{ ...snapshot, name: 'Original' }] }));
  storage.setItem('assetwise.portfolio.v1:demo-v2', JSON.stringify(snapshot));
  storage.flush = async () => { throw new Error('Offline'); };
  context.savingFailed = () => {};
  const workspace = readFileSync(join(__dirname, '../../prototype/workspace.js'), 'utf8');
  vm.runInContext(workspace.slice(workspace.indexOf('let scenarioSaving = false;'), workspace.indexOf('function renderScenarios()')), context);
  await assert.rejects(vm.runInContext('persistScenarioChange(() => scenarioStorage.rename(sqlStorage, "Original", "Changed"))', context), /Offline/);
  assert.equal(JSON.parse(storage.getItem('assetwise.scenarios.v1')).items[0].name, 'Original');
  assert.equal(JSON.parse(storage.getItem('assetwise.portfolio.v1:demo-v2')).weights['DEMO-EQ1'], 70);
  await assert.rejects(vm.runInContext('persistScenarioChange(() => scenarioStorage.remove(sqlStorage, "Original"))', context), /Offline/);
  assert.equal(JSON.parse(storage.getItem('assetwise.scenarios.v1')).items.length, 1);
  assert.equal(storage.getItem('assetwise.scenarios.v1.removed'), null);
});
