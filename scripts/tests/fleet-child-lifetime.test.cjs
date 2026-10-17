"use strict";
// EventEmitter callback models only: no actual child process or OS signal.
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const {test} = require('node:test');
const {retainOriginalChildExit, disconnectOriginalChild} = require('../e2e/fixtures/fleet-child-lifetime.cjs');

function model({killResult = true} = {}) {
  const child = new EventEmitter();
  child.exitCode = null; child.signalCode = null;
  const events = [];
  function noOsModelKillCallback(signal) {
    events.push({kind: 'no-os-model-kill-callback', signal});
    return killResult;
  }
  child.kill = noOsModelKillCallback;
  // This is the proposed immediate-at-spawn registration, before any exit.
  const agent = {child, childExit: retainOriginalChildExit(child)};
  const emitOriginalExit = (code = 0, signal = null) => {
    child.exitCode = code; child.signalCode = signal;
    events.push({kind: 'modeled-original-exit', code, signal});
    child.emit('exit', code, signal);
  };
  return {agent, child, events, emitOriginalExit};
}

function observe(promise) {
  const state = {status: 'pending', error: undefined};
  promise.then(() => {state.status = 'fulfilled';}, error => {
    state.status = 'rejected';state.error = error;
  });
  return state;
}
async function checkpoint() {
  await Promise.resolve();await Promise.resolve();
  await Promise.resolve();await Promise.resolve();
}

test('ordinary original exit resolves the retained pair and clears only that child', async () => {
  const f = model(), lifetime = f.agent.childExit;
  const promise = disconnectOriginalChild(f.agent), state = observe(promise);
  await checkpoint();
  assert.equal(state.status, 'pending');
  assert.deepEqual(f.events, [{kind: 'no-os-model-kill-callback', signal: 'SIGTERM'}]);
  assert.strictEqual(f.agent.child, f.child);assert.strictEqual(f.agent.childExit, lifetime);
  f.emitOriginalExit();await promise;
  assert.equal(lifetime.observed, true);
  assert.strictEqual(f.agent.child, null);assert.strictEqual(f.agent.childExit, null);
});

test('already-emitted original exit settles without a second modeled signal', async () => {
  const f = model(), lifetime = f.agent.childExit;
  f.emitOriginalExit();
  assert.equal(lifetime.observed, true);
  const promise = disconnectOriginalChild(f.agent), state = observe(promise);
  await checkpoint();
  assert.equal(state.status, 'fulfilled');assert.equal(state.error, undefined);
  await promise;
  assert.deepEqual(f.events, [{kind: 'modeled-original-exit', code: 0, signal: null}]);
  assert.strictEqual(f.agent.child, null);assert.strictEqual(f.agent.childExit, null);
});

test('mismatched or later replacement child is refused and never signaled or cleared', async () => {
  const before = model(), foreign = model();
  before.agent.child = foreign.child;
  const mismatched = before.agent.childExit;
  await assert.rejects(disconnectOriginalChild(before.agent), /exit handle is unavailable or replaced/);
  assert.strictEqual(before.agent.child, foreign.child);assert.strictEqual(before.agent.childExit, mismatched);
  assert.deepEqual(before.events, []);assert.deepEqual(foreign.events, []);

  const old = model(), later = model();
  const pending = disconnectOriginalChild(old.agent);
  old.agent.child = later.child;old.agent.childExit = later.agent.childExit;
  old.emitOriginalExit();
  await assert.rejects(pending, /replaced during disconnect/);
  assert.strictEqual(old.agent.child, later.child);assert.strictEqual(old.agent.childExit, later.agent.childExit);
  assert.equal(later.agent.childExit.observed, false);assert.deepEqual(later.events, []);
  assert.deepEqual(old.events, [{kind: 'no-os-model-kill-callback', signal: 'SIGTERM'},
    {kind: 'modeled-original-exit', code: 0, signal: null}]);
});

test('kill false without an original exit event never earns completion', async () => {
  const f = model({killResult: false}), lifetime = f.agent.childExit;
  const state = observe(disconnectOriginalChild(f.agent));
  await checkpoint();
  assert.equal(state.status, 'pending');assert.equal(state.error, undefined);
  assert.equal(lifetime.observed, false);
  assert.strictEqual(f.agent.child, f.child);assert.strictEqual(f.agent.childExit, lifetime);
  assert.deepEqual(f.events, [{kind: 'no-os-model-kill-callback', signal: 'SIGTERM'}]);
});


test('exitCode without the retained original exit event never earns completion', async () => {
  const f = model(), lifetime = f.agent.childExit;
  // Model an exitCode property becoming populated WITHOUT any exit callback.
  // A property alone must not manufacture the retained event proof.
  f.child.exitCode = 0;
  assert.equal(lifetime.observed, false);
  const state = observe(disconnectOriginalChild(f.agent));
  await checkpoint();
  assert.equal(state.status, 'pending');assert.equal(state.error, undefined);
  assert.equal(lifetime.observed, false);
  assert.strictEqual(f.agent.child, f.child);assert.strictEqual(f.agent.childExit, lifetime);
  assert.deepEqual(f.events, []);
});
