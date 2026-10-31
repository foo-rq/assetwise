const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');

function setup() {
  const events = {};
  let timer;
  let open = false;
  const popup = { style: {}, offsetWidth: 200, offsetHeight: 80,
    setAttribute() {}, addEventListener() {},
    showPopover() { open = true; }, hidePopover() { open = false; } };
  const button = { dataset: { help: 'Explanation' }, closest() {}, setAttribute() {},
    getBoundingClientRect: () => ({ left: 20, top: 100, bottom: 120 }),
    addEventListener(name, callback) { events[name] = callback; } };
  vm.runInNewContext(readFileSync(require('node:path').join(__dirname, '../../prototype/help.js'), 'utf8'), {
    document: { activeElement: button, querySelectorAll: () => [button], createElement: () => popup,
      body: { append() {} }, addEventListener() {} },
    window: { addEventListener() {} }, innerWidth: 400, innerHeight: 800,
    setTimeout(callback) { timer = callback; }, clearTimeout() {}
  });
  return { events, open: () => open, leave: () => { events.pointerleave(); timer(); } };
}

test('mouse help closes after leaving even when a click retained focus', () => {
  const tip = setup();
  tip.events.pointerenter({ pointerType: 'mouse' });
  tip.events.focus();
  tip.events.click();
  assert.equal(tip.open(), true);
  tip.leave();
  assert.equal(tip.open(), false);
});

test('touch help stays open until its second activation', () => {
  const tip = setup();
  tip.events.pointerenter({ pointerType: 'touch' });
  tip.events.click();
  tip.leave();
  assert.equal(tip.open(), true);
  tip.events.click();
  assert.equal(tip.open(), false);
});
