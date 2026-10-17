'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
// Original integrated target is scripts/tests; the private control remains
// runnable here without importing any app/test fixture or starting a service.
const fs = require('node:fs');
const path = require('node:path');
const integrated = path.join(__dirname, '..', 'e2e', 'fixtures', 'u007-response-join.cjs');
const {joinOriginalResponse} = require(fs.existsSync(integrated) ? integrated : './u007-response-join.cjs');

function receipt(changes = {}) {
  return {started: 100, deadline: 10_100, finished: 140, status: 200, size: 2,
    body: 'retained-original.body', sha256: 'a'.repeat(64), done: Promise.resolve(), ...changes};
}
function withinAt(now, seen = []) {
  return async (pending, deadline) => {
    seen.push({pending, deadline});
    if (now() >= deadline) throw Error('Original deadline expired');
    return pending;
  };
}

test('completed on-time body remains valid after wall-clock advances beyond its original deadline', async () => {
  const row = receipt(), seen = []; let now = 200;
  await joinOriginalResponse(row, withinAt(() => now, seen)); now = 50_000;
  await joinOriginalResponse(row, withinAt(() => now, seen)); assert.deepEqual(seen, []);
  assert.equal(row.deadline, 10_100); assert.equal(row.finished, 140);
});
test('genuinely pending response is awaited with the exact unchanged original deadline', async () => {
  let resolve; const pending = new Promise(done => {resolve = done;});
  const row = receipt({finished: undefined, done: pending}), seen = [];
  let finished = false; const joined = joinOriginalResponse(row, withinAt(() => 200, seen)).then(() => {finished = true;});
  await Promise.resolve(); assert.equal(finished, false); assert.deepEqual(seen, [{pending, deadline: 10_100}]);
  row.finished = 220; resolve(); await joined; assert.equal(finished, true); assert.equal(row.deadline, 10_100);
});
test('genuinely pending response at the original expired deadline still rejects', async () => {
  const row = receipt({finished: undefined, done: new Promise(() => {})}), seen = [];
  await assert.rejects(joinOriginalResponse(row, withinAt(() => 10_100, seen)), /Original deadline expired/);
  assert.equal(seen.length, 1); assert.equal(seen[0].deadline, row.deadline);
});
test('original pending rejection is preserved', async () => {
  const failure = Error('real original response rejected'), done = Promise.reject(failure); void done.catch(() => {});
  await assert.rejects(joinOriginalResponse(receipt({finished: undefined, done}), withinAt(() => 200)), error => error === failure);
});
test('already finished failure is never hidden by skipping the obsolete wait', async () => {
  await assert.rejects(joinOriginalResponse(receipt({failure: 'original transport failure'}), withinAt(() => 50_000)), /original transport failure/);
});
test('late completion and absent completion still reject', async () => {
  await assert.rejects(joinOriginalResponse(receipt({finished: 10_101}), withinAt(() => 50_000)), /outside its original deadline/);
  await assert.rejects(joinOriginalResponse(receipt({finished: undefined}), withinAt(() => 200)), /completion was absent/);
});
test('retained body, full-body bound and original status gates still reject', async () => {
  for (const change of [{body: undefined}, {sha256: 'bad'}, {size: 2 * 1024 * 1024 + 1}])
    await assert.rejects(joinOriginalResponse(receipt(change), withinAt(() => 50_000)), /body receipt is absent or invalid/);
  await assert.rejects(joinOriginalResponse(receipt({status: 409}), withinAt(() => 50_000)), /unchanged success gate/);
});
