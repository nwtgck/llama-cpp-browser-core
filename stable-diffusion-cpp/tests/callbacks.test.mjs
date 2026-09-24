import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const code = readFileSync(new URL('../bridge/callbacks.js', import.meta.url), 'utf8');
function instance(options = {}) {
  const warnings = [];
  runInNewContext(code, { Module: options, console: { warn: (...args) => warnings.push(args) } });
  return { core: options, warnings };
}
test('factory options and subsequent registration have the same signatures', () => {
  const received = [];
  const { core } = instance({ onProgress: (...args) => received.push(args) });
  core.sdbDispatchProgress(1, 4, 0.125);
  assert.deepEqual(received, [[1, 4, 0.125]]);
  core.setCallbacks({ onLog: (...args) => received.push(args) });
  core.sdbDispatchProgress(2, 4, 0.25);
  core.sdbDispatchLog(4, 'diagnostic');
  assert.deepEqual(received, [[1, 4, 0.125], [4, 'diagnostic']]);
});
test('callbacks are isolated between Wasm instances', () => {
  let one = 0, two = 0;
  const a = instance({ onLog: () => one++ }).core;
  const b = instance({ onLog: () => two++ }).core;
  a.sdbDispatchLog(1, 'a'); b.sdbDispatchLog(1, 'b');
  a.setCallbacks({}); a.sdbDispatchLog(1, 'ignored');
  assert.equal(one, 1); assert.equal(two, 1);
});
test('listener failures do not escape into Wasm', () => {
  const { core, warnings } = instance({ onLog() { throw Error('UI failed'); } });
  assert.doesNotThrow(() => core.sdbDispatchLog(1, 'log'));
  assert.equal(warnings.length, 1);
});
test('invalid registration is rejected atomically', () => {
  let calls = 0;
  const { core } = instance({ onLog: () => calls++ });
  assert.throws(() => core.setCallbacks({ onProgress() {}, onLog: 12 }), /onLog must/);
  core.sdbDispatchLog(1, 'preserved'); assert.equal(calls, 1);
  assert.throws(() => core.setCallbacks(null), /Expected callback object/);
  assert.throws(() => instance({ onProgress: 'bad' }), /onProgress must/);
});
