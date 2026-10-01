// A plain-browser renderer derives backend health from /health (no desktop bridge): backoff, heartbeat, timeout, cleanup.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file) {
  const name = path.resolve(__dirname, file);
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, name);
  return m.exports;
}

const { needsBrowserHealthProbe, startBrowserHealthProbe } = load('browserHealth.ts');
const settle = () => new Promise(resolve => setImmediate(resolve));
const ok = (device = 'mps', deviceName = 'Apple GPU') => ({ healthy: true, device, deviceName });
const DOWN = new Error('connection refused');

function harness(answers, { port = async () => 4321, onStatus } = {}) {
  const statuses = [];
  const delays = [];
  const signals = [];
  const warnings = [];
  const timeouts = [];
  let pending = null;
  let cancelled = 0;
  const stop = startBrowserHealthProbe({
    check: signal => {
      signals.push(signal);
      const answer = answers.shift();
      if (typeof answer === 'function') return answer(signal);
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    },
    port,
    onStatus: status => { statuses.push(status); onStatus?.(status, stop); },
    schedule: (callback, delay) => { delays.push(delay); pending = callback; return delays.length; },
    cancel: () => { cancelled += 1; pending = null; },
    arm: (onTimeout, ms) => { const entry = { onTimeout, ms, disarmed: false }; timeouts.push(entry); return () => { entry.disarmed = true; }; },
    warn: message => warnings.push(message),
  });
  return {
    statuses, delays, signals, warnings, timeouts, stop,
    get cancelled() { return cancelled; },
    get pending() { return pending; },
    async tick() { const next = pending; pending = null; assert.ok(next, 'a probe is scheduled'); next(); await settle(); },
  };
}

test('a healthy backend is reported once with its port and device, then kept on a slow heartbeat', async () => {
  const probe = harness([ok(), ok(), ok()]);
  await settle();
  assert.deepEqual(probe.statuses, [{ port: 4321, healthy: true, pid: null, device: 'mps', deviceName: 'Apple GPU' }]);
  await probe.tick();
  await probe.tick();
  assert.equal(probe.statuses.length, 1, 'an unchanged status is not reported again');
  assert.deepEqual(probe.delays, [5000, 5000, 5000]);
  assert.ok(probe.timeouts.every(entry => entry.ms === 4000 && entry.disarmed), 'every request had a disarmed 4 s timeout');
  probe.stop();
});

test('an unreachable backend is retried with a bounded backoff until it answers', async () => {
  const probe = harness([DOWN, { healthy: false }, DOWN, DOWN, DOWN, DOWN, ok()]);
  await settle();
  for (let i = 0; i < 6; i += 1) await probe.tick();
  assert.deepEqual(probe.delays, [500, 1000, 2000, 4000, 5000, 5000, 5000], 'doubling, capped at 5 s, heartbeat once healthy');
  assert.deepEqual(probe.statuses.map(s => s.healthy), [false, true]);
  assert.equal(probe.statuses[0].port, null);
  assert.equal(probe.warnings.length, 1, 'the transition to unreachable is logged once');
  probe.stop();
});

test('a healthy backend is reported lost only after two failed heartbeats, and the backoff restarts from the beginning', async () => {
  const probe = harness([DOWN, DOWN, DOWN, ok(), DOWN, DOWN, DOWN]);
  await settle();
  for (let i = 0; i < 6; i += 1) await probe.tick();
  assert.deepEqual(probe.statuses.map(s => s.healthy), [false, true, false]);
  assert.deepEqual(probe.delays, [500, 1000, 2000, 5000, 500, 1000, 2000], 'retry delays restart at 0.5 s after the healthy period');
  probe.stop();
});

test('one dropped heartbeat does not report a loss or force a resync', async () => {
  const probe = harness([ok(), DOWN, ok()]);
  await settle();
  await probe.tick();
  await probe.tick();
  assert.deepEqual(probe.statuses.map(s => s.healthy), [true]);
  assert.deepEqual(probe.delays, [5000, 500, 5000], 'a failed heartbeat is confirmed quickly');
  probe.stop();
});

test('the failure count restarts after a success, so a later single dropped heartbeat is not a loss', async () => {
  const probe = harness([DOWN, ok(), DOWN, ok()]);
  await settle();
  await probe.tick();
  await probe.tick();
  await probe.tick();
  assert.deepEqual(probe.statuses.map(s => s.healthy), [false, true]);
  probe.stop();
});

test('a hung request times out, counts as a failure and the polling continues', async () => {
  const probe = harness([signal => new Promise((_, reject) => signal.addEventListener('abort', () => reject(signal.reason)))]);
  await settle();
  assert.equal(probe.timeouts.length, 1);
  probe.timeouts[0].onTimeout();
  await settle();
  assert.equal(probe.signals[0].aborted, true);
  assert.deepEqual(probe.statuses.map(s => s.healthy), [false]);
  assert.deepEqual(probe.delays, [500], 'the next probe is scheduled');
  assert.match(probe.warnings[0], /timed out after 4000 ms/, 'the warning names the timeout');
  probe.stop();
});

test('a failing port lookup is reported as unhealthy, never as healthy', async () => {
  const probe = harness([ok()], { port: async () => { throw new Error('no port'); } });
  await settle();
  assert.deepEqual(probe.statuses, [{ port: null, healthy: false, pid: null }]);
  probe.stop();
});

test('a device change on a healthy backend is reported', async () => {
  const probe = harness([ok('mps'), ok('cpu', 'CPU')]);
  await settle();
  await probe.tick();
  assert.deepEqual(probe.statuses.map(s => s.device), ['mps', 'cpu']);
  probe.stop();
});

test('stopping aborts the in-flight probe, ignores its late answer and schedules nothing more', async () => {
  let resolveLate;
  const probe = harness([() => new Promise(resolve => { resolveLate = resolve; })]);
  await settle();
  probe.stop();
  assert.equal(probe.signals[0].aborted, true, 'the in-flight request is aborted');
  resolveLate(ok());
  await settle();
  assert.deepEqual(probe.statuses, [], 'a late answer after cleanup changes nothing');
  assert.deepEqual(probe.delays, [], 'no further probe is scheduled');
});

test('stopping between probes cancels the scheduled one', async () => {
  const probe = harness([ok()]);
  await settle();
  probe.stop();
  assert.equal(probe.cancelled, 1);
});

test('stopping from the status callback schedules nothing more', async () => {
  const probe = harness([ok()], { onStatus: (_status, stop) => stop() });
  await settle();
  assert.equal(probe.statuses.length, 1);
  assert.deepEqual(probe.delays, []);
  assert.equal(probe.pending, null);
});

test('only a renderer without the desktop bridge probes; Electron never does', () => {
  const chrome = 'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/140.0 Safari/537.36';
  const electron = 'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/140.0 Electron/44.5.1 Safari/537.36';
  assert.equal(needsBrowserHealthProbe({ navigator: { userAgent: chrome } }), true);
  assert.equal(needsBrowserHealthProbe({ api: {}, navigator: { userAgent: electron } }), false, 'the preload reports status');
  assert.equal(needsBrowserHealthProbe({ navigator: { userAgent: electron } }), false, 'a window whose preload failed does not poll');
  assert.equal(needsBrowserHealthProbe(undefined), false);
});
