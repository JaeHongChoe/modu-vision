'use strict';
// Callback models only: no network, child, PID, signal or product runtime imports.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const cleanup = require('../e2e/fixtures/fleet-cleanup.cjs');

function fixture({stopErrors = new Map(), disconnectErrors = new Map()} = {}) {
  const events = [], notes = [];
  const agents = [0, 1].map(index => ({
    root: 'callback-only-owned-agent-' + index, port: 0, pids: [],
    child: Object.freeze({kind: 'retained-callback-model', index}),
    log: {end() {events.push('log:' + index);}},
  }));
  const start = async index => {
    events.push('start:' + index);
    assert.fail('Both original callback-model children are retained, so no restart is allowed');
  };
  const remote = async (index, route, body) => {
    assert.equal(route, '/agent/v1/stop'); assert.deepEqual(body, {});
    events.push('stop:' + index);
    if (stopErrors.has(index)) throw stopErrors.get(index);
    return {status: 'stopped', modeled_agent: index};
  };
  const disconnect = async index => {
    events.push('disconnect:' + index);
    if (disconnectErrors.has(index)) throw disconnectErrors.get(index);
    agents[index].child = null;
  };
  const evidence = {note(key, value) {notes.push({key, value});}};
  return {agents, start, remote, disconnect, evidence, events, notes};
}

function contains(error, original) {
  return error === original || (error instanceof AggregateError && error.errors.some(item => contains(item, original)));
}

test('original success cleans both retained callback owners exactly once', async () => {
  const f = fixture();
  await cleanup(f);
  assert.deepEqual(f.events, ['stop:0', 'disconnect:0', 'log:0', 'stop:1', 'disconnect:1', 'log:1']);
  assert.ok(f.agents.every(agent => agent.child === null));
  assert.equal(f.notes.length, 1); assert.equal(f.notes[0].key, 'owned_agents_cleanup');
  assert.deepEqual(f.notes[0].value, f.agents.map((agent, index) => ({
    root: agent.root, port: agent.port, pids: agent.pids,
    result: {status: 'stopped', modeled_agent: index},
  })));
});

test('stop rejection retains its original error and still attempts every owner cleanup', async () => {
  const refused = Object.assign(new Error('controlled first stop refusal'), {code: 'ECONNREFUSED'});
  const f = fixture({stopErrors: new Map([[0, refused]])});
  let actual;
  try {await cleanup(f);} catch (error) {actual = error;}
  // Causal preconditions: the exact original callback ran and its actual error
  // remains observable. A different setup failure must not earn the target RED.
  assert.ok(contains(actual, refused));
  assert.equal(f.events[0], 'stop:0');
  // Intended RED is here: the exact original loop stops at the first rejection.
  assert.deepEqual(f.events, ['stop:0', 'disconnect:0', 'log:0', 'stop:1', 'disconnect:1', 'log:1']);
  assert.ok(f.agents.every(agent => agent.child === null));
});

test('primary body error stays primary and retains all secondary cleanup errors', async () => {
  const primary = new Error('controlled original body failure');
  const secondary = [new Error('stop0'), new Error('disconnect0'), new Error('stop1'), new Error('disconnect1')];
  const f = fixture({stopErrors: new Map([[0, secondary[0]], [1, secondary[2]]]),
    disconnectErrors: new Map([[0, secondary[1]], [1, secondary[3]]])});
  let actual;
  try {
    try {throw primary;} finally {
      // Original host try/finally semantics are retained. The original extracted
      // cleanup ignores this explicit primaryError context, exposing masking.
      await cleanup({...f, primaryError: primary});
    }
  } catch (error) {actual = error;}
  assert.equal(f.events[0], 'stop:0');
  assert.ok(actual === primary || contains(actual, secondary[0]));
  assert.strictEqual(actual, primary, 'Cleanup must not replace the original body failure');
  assert.deepEqual(f.events, ['stop:0', 'disconnect:0', 'log:0', 'stop:1', 'disconnect:1', 'log:1']);
  assert.ok(Array.isArray(primary.secondary_errors));
  const descriptor = Object.getOwnPropertyDescriptor(primary, 'secondary_errors');
  assert.ok(descriptor);
  assert.equal(descriptor.enumerable, false);
  assert.equal(primary.secondary_errors.length, secondary.length);
  secondary.forEach((error, index) => assert.strictEqual(primary.secondary_errors[index], error));
});

test('log and both evidence failures retain order and fail after all owners are attempted', async () => {
  const failures = [new Error('log0'), new Error('cleanup note'), new Error('error note')];
  const f = fixture();
  f.agents[0].log.end = () => {f.events.push('log:0'); throw failures[0];};
  f.evidence.note = (key, value) => {
    f.notes.push({key, value});
    throw key === 'owned_agents_cleanup' ? failures[1] : failures[2];
  };
  let actual;
  try {await cleanup(f);} catch (error) {actual = error;}
  assert.ok(actual instanceof AggregateError);
  assert.equal(actual.errors.length, failures.length);
  failures.forEach((error, index) => assert.strictEqual(actual.errors[index], error));
  assert.deepEqual(f.events, ['stop:0', 'disconnect:0', 'log:0', 'stop:1', 'disconnect:1', 'log:1']);
  assert.deepEqual(f.notes.map(row => row.key), ['owned_agents_cleanup', 'owned_agents_cleanup_errors']);
  assert.ok(f.agents.every(agent => agent.child === null));
});

test('a failed original restart still attempts disconnect log closure and the next owner', async () => {
  const failed = new Error('controlled original start refusal');
  const f = fixture(); f.agents[0].child = null;
  f.start = async index => {assert.equal(index, 0); f.events.push('start:0'); throw failed;};
  let actual;
  try {await cleanup(f);} catch (error) {actual = error;}
  assert.ok(actual instanceof AggregateError);
  assert.strictEqual(actual.errors[0], failed); assert.equal(actual.errors.length, 1);
  assert.deepEqual(f.events, ['start:0', 'disconnect:0', 'log:0', 'stop:1', 'disconnect:1', 'log:1']);
});

test('a frozen primary stays failed and exposes the limited attachment in evidence', async () => {
  const primary = Object.freeze(new Error('frozen original body failure'));
  const refused = new Error('controlled original stop rejection');
  const f = fixture({stopErrors: new Map([[0, refused]])});
  let actual, report;
  try {try {throw primary;} finally {report = await cleanup({...f, primaryError: primary});}}
  catch (error) {actual = error;}
  assert.strictEqual(actual, primary);
  assert.equal(Object.getOwnPropertyDescriptor(primary, 'secondary_errors'), undefined);
  assert.equal(report.secondaryAttachmentFailed, true);
  assert.strictEqual(report.errors[0], refused); assert.equal(report.errors.length, 2);
  assert.ok(report.errors[1] instanceof TypeError);
  const note = f.notes.find(row => row.key === 'owned_agents_cleanup_errors');
  assert.equal(note.value.status, 'failed'); assert.equal(note.value.secondary_attachment, 'failed');
  assert.deepEqual(note.value.errors.map(row => row.message), report.errors.map(error => error.message));
  assert.deepEqual(f.events, ['stop:0', 'disconnect:0', 'log:0', 'stop:1', 'disconnect:1', 'log:1']);
});
