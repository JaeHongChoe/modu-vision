// Tests-first: execute the original stream/port-discovery methods, never a backend.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const {EventEmitter} = require('node:events');
const ROOT = process.env.SUPERVISOR_STDERR_SOURCE_ROOT || path.resolve(__dirname, '../..');
const requireRoot = Module.createRequire(path.join(ROOT, 'package.json'));
const ts = requireRoot('typescript');

function fixture(token = null) {
  const file = path.join(ROOT, 'src/main/supervisor.ts'), m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  // This typed class is not used by the tested original methods. Avoid loading
  // unrelated Electron/ownership fixtures, as existing supervisor tests do.
  m.require = name => name === './applicationLaunch' ? {OwnedApplicationLaunch:class {}} : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions:{
    module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022, esModuleInterop:true,
  }}).outputText, file);
  // The test utility suppresses global process hooks; it does not replace any
  // original stream parsing, byte retention, port, error or exit method.
  m.exports.BackendSupervisor.prototype.registerProcessHooks = () => {};
  const supervisor = new m.exports.BackendSupervisor({autoRestart:false});
  const proc = new EventEmitter();
  Object.assign(proc, {pid:'not-a-real-process', exitCode:null, signalCode:null,
    stdout:new EventEmitter(), stderr:new EventEmitter(),
    stdin:{end(){throw Error('Port diagnostics must not close stdin');}},
    kill(){throw Error('Port diagnostics must not send a signal');}});
  supervisor.apiToken = token;
  supervisor.attachProcessListeners(proc);
  return {supervisor, proc};
}

function start(f) {
  return f.supervisor.discoverPort(f.proc, 1000).then(
    value => ({value}), error => ({error}));
}

function exited(f, code=1) {
  f.proc.exitCode = code; f.proc.emit('exit', code, null); f.proc.emit('close', code, null);
}

test('premature exit retains a split Python traceback including the last unterminated line', async () => {
  // Regression if onExit drops stderr, or retention only sees completed lines.
  const f = fixture(), pending = start(f);
  f.proc.stderr.emit('data', Buffer.from('Traceback (most recent call last):\n  File "owned/backend.py", '));
  f.proc.stderr.emit('data', Buffer.from('line 8, in startup\nValueError: exact owned bootstrap reason'));
  exited(f);
  const {error} = await pending;
  assert(error instanceof Error);
  assert(error.message.startsWith('Python process exited prematurely with code 1 before port discovery.'));
  assert(error.message.includes('Traceback (most recent call last):'));
  assert(error.message.includes('line 8, in startup'));
  assert(error.message.includes('ValueError: exact owned bootstrap reason'));
});

test('premature-exit diagnostic is byte bounded and redacts the actual API capability', async () => {
  // Regression if the error adopts an unbounded line/buffer or exposes apiToken.
  const f = fixture(), pending = start(f);
  const secret = 'fixture-sensitive-api-capability-never-display';
  f.supervisor.apiToken = secret;
  f.proc.stderr.emit('data', Buffer.from('오래된진단'.repeat(9000)));
  f.proc.stderr.emit('data', Buffer.from('\nVISION_AI_STUDIO_API_TOKEN=' + secret + '\nValueError: final causal line'));
  exited(f);
  const {error} = await pending;
  assert(error instanceof Error);
  assert(error.message.includes('ValueError: final causal line'));
  assert(!error.message.includes(secret), 'the current API token must never enter the error');
  assert(Buffer.byteLength(error.message, 'utf8') <= 17000, 'diagnostic must remain bounded in bytes');
});

test('a prior process stderr cannot be attributed to a fresh empty exiting process', async () => {
  // Regression if shared stderrBuffer or a prior child tail is used as evidence.
  const f = fixture();
  f.proc.stderr.emit('data', Buffer.from('PRIOR-CHILD-ONLY\n'));
  const next = new EventEmitter();
  Object.assign(next, {pid:'also-not-a-real-process', stdout:new EventEmitter(), stderr:new EventEmitter(),
    exitCode:null, signalCode:null});
  f.supervisor.attachProcessListeners(next);
  const pending = f.supervisor.discoverPort(next, 1000).then(value => ({value}), error => ({error}));
  next.exitCode = 7; next.emit('exit', 7, null); next.emit('close', 7, null);
  const {error} = await pending;
  assert(error instanceof Error);
  assert.equal(error.message, 'Python process exited prematurely with code 7 before port discovery.');
});

test('no stderr retains the original premature-exit message exactly', async () => {
  const f = fixture(), pending = start(f); exited(f);
  const {error} = await pending;
  assert.equal(error.message, 'Python process exited prematurely with code 1 before port discovery.');
});

test('ordinary split port discovery still resolves and removes its original exit listener', async () => {
  // Regression if diagnostics changes stdout parsing/normal resolution/cleanup.
  const f = fixture(), pending = start(f);
  const before = f.proc.listenerCount('exit');
  f.proc.stdout.emit('data', Buffer.from('VISION_AI_STUDIO_PO'));
  f.proc.stdout.emit('data', Buffer.from('RT=43129\n'));
  const result = await pending;
  assert.deepEqual(result, {value:43129});
  assert.equal(f.proc.listenerCount('exit'), before-1);
});


test('stderr delivered after exit is retained before the original stdio close', async () => {
  const f = fixture(), pending = start(f);
  f.proc.exitCode = 1; f.proc.emit('exit', 1, null);
  f.proc.stderr.emit('data', Buffer.from('ValueError: final pipe drain'));
  f.proc.emit('close', 1, null);
  const {error} = await pending;
  assert(error.message.includes('ValueError: final pipe drain'));
  assert.equal(f.proc.listenerCount('close'), 0);
});

test('a port line drained after the original child exited cannot announce readiness', async () => {
  const f = fixture(), pending = start(f);
  f.proc.exitCode = 1; f.proc.emit('exit', 1, null);
  f.proc.stdout.emit('data', Buffer.from('VISION_AI_STUDIO_PORT=43129\n'));
  f.proc.stderr.emit('data', Buffer.from('ValueError: already exited'));
  f.proc.emit('close', 1, null);
  const {error, value} = await pending;
  assert.equal(value, undefined);
  assert(error.message.includes('ValueError: already exited'));
});

test('the exited child token is redacted after the crash handler clears current credentials', async () => {
  const secret = 'fixture-original-child-capability-never-display';
  const f = fixture(secret), pending = start(f);
  f.supervisor.childProcess = f.proc;
  f.proc.exitCode = 1; f.proc.emit('exit', 1, null);
  assert.equal(f.supervisor.apiToken, null);
  f.proc.stderr.emit('data', Buffer.from('API_TOKEN=' + secret + '\nValueError: original child failure'));
  f.proc.emit('close', 1, null);
  const {error} = await pending;
  assert(error.message.includes('ValueError: original child failure'));
  assert(error.message.includes('[REDACTED]'));
  assert(!error.message.includes(secret));
});

test('a capability suffix cut by the byte bound is also redacted', async () => {
  const secret = '0123456789abcdef'.repeat(4);
  const f = fixture(secret), pending = start(f);
  f.proc.stderr.emit('data', Buffer.from(secret + 'x'.repeat(16368)));
  exited(f);
  const {error} = await pending;
  assert(!error.message.includes(secret.slice(-16)));
  assert(Buffer.byteLength(error.message, 'utf8') <= 16500);
});

test('an exited child with inherited open stdio still stops at the original discovery budget', async () => {
  const f = fixture();
  const pending = f.supervisor.discoverPort(f.proc, 25).then(value => ({value}), error => ({error}));
  f.proc.exitCode = 1; f.proc.emit('exit', 1, null);
  f.proc.stderr.emit('data', Buffer.from('ValueError: inherited pipe remains open'));
  const {error, value} = await pending;
  assert.equal(value, undefined);
  assert(error.message.includes('ValueError: inherited pipe remains open'));
  assert.equal(f.proc.listenerCount('close'), 0);
});


// Exercise the original start/stream/exit/discovery/catch chain. Only external
// process, filesystem path selection and HTTP transport boundaries are doubles.
// No backend, Electron app, network request, stdin close or signal is executed.
function startupFixture({children=1, autoRestart=false, owned=false, timeout=1000} = {}) {
  const procs = Array.from({length:children}, (_, index) => {
    const proc = new EventEmitter(), stdin = new EventEmitter();
    proc.io = [];
    stdin.end = () => { proc.io.push('stdin closed'); };
    Object.assign(proc, {pid:`fixture-only-${index}`, exitCode:null, signalCode:null,
      stdout:new EventEmitter(), stderr:new EventEmitter(), stdin,
      kill(signal) { proc.io.push(signal); return true; }});
    return proc;
  });
  const spawns = [], spawnWaiters = [];
  const file = path.join(ROOT, 'src/main/supervisor.ts'), m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = name => {
    if (name === './applicationLaunch') return {OwnedApplicationLaunch:class {}};
    if (name === 'electron') return {};
    if (name === 'child_process') return {
      spawn(binary, args, options) {
        const proc = procs[spawns.length];
        assert(proc, 'the supervisor must not spawn an unplanned child');
        spawns.push({binary, args, options, proc});
        for (const waiter of spawnWaiters.splice(0)) waiter();
        return proc;
      },
      execSync() { throw Error('startup delivery tests must not execute commands'); },
    };
    if (name === 'http') return {get(_options, receive) {
      const request = new EventEmitter(); request.destroy = () => {};
      queueMicrotask(() => {
        const response = new EventEmitter();
        Object.assign(response, {statusCode:200, setEncoding(){}, resume(){}});
        receive(response);
        response.emit('data', '{"status":"ok","version":"fixture","device":"cpu","device_name":"fixture"}');
        response.emit('end');
      });
      return request;
    }};
    return original(name);
  };
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions:{
    module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022, esModuleInterop:true,
  }}).outputText, file);
  m.exports.BackendSupervisor.prototype.registerProcessHooks = () => {};
  const refusals = [];
  const ownership = owned ? {
    projects:'/fixture/projects', auth:'/fixture/auth',
    executable() { return {build:'fixture-build', sha256:'fixture-executable'}; },
    backendEnvironment(env) { return env; },
    bindBackend() { return Promise.resolve(); },
    refuse() { refusals.push('refused'); },
  } : undefined;
  const supervisor = new m.exports.BackendSupervisor({autoRestart,
    portDiscoveryTimeoutMs:timeout, healthCheckTimeoutMs:1000,
    ...(ownership ? {ownedApplicationLaunch:ownership} : {})});
  supervisor.resolveStandaloneBinary = () => '/fixture/backend';
  supervisor.resolveProjectDir = () => '/fixture/projects';
  supervisor.getAppRoot = () => '/fixture/app';
  supervisor.getBackendWorkingDirectory = () => '/fixture/cwd';
  const crashes = []; supervisor.on('crashed', event => crashes.push(event));
  return {supervisor, procs, spawns, crashes, refusals,
    begin:() => supervisor.startBackend().then(value => ({value}), error => ({error})),
    nextSpawn:() => new Promise(resolve => spawnWaiters.push(resolve)),
  };
}

function startupExit(proc, code=1, signal=null) {
  proc.exitCode = code; proc.signalCode = signal; proc.emit('exit', code, signal);
}
function startupClose(proc, code=proc.exitCode, signal=proc.signalCode) {
  proc.stderr.readableEnded = true; proc.emit('close', code, signal);
}

test('the delivered startup crash waits for late stderr and contains the original child traceback once', async () => {
  // A generic exit event must not consume the only UI notification before pipe drain.
  const f = startupFixture(), pending = f.begin(), proc = f.procs[0];
  startupExit(proc, 7);
  assert.equal(f.supervisor.getStatusInfo().state, 'CRASHED', 'crash state is immediate');
  assert.equal(f.crashes.length, 0, 'only diagnostic notification waits for original stream close');
  proc.stderr.emit('data', Buffer.from('Traceback: split '));
  proc.stderr.emit('data', Buffer.from('startup\nValueError: final unterminated cause'));
  startupClose(proc);
  const {error} = await pending;
  assert.equal(f.crashes.length, 1);
  assert.equal(f.crashes[0].exitCode, 7); assert.equal(f.crashes[0].signal, null);
  assert.equal(f.crashes[0].message, error.message);
  assert(f.crashes[0].message.includes('Traceback: split startup'));
  assert(f.crashes[0].message.includes('ValueError: final unterminated cause'));
  proc.emit('close', 7, null);
  assert.equal(f.crashes.length, 1); assert.deepEqual(proc.io, []);
});

for (const cutAtBound of [false, true]) test(`delivered startup diagnostics redact the original token${cutAtBound ? ' suffix at the byte bound' : ' after credential clearing'}`, async () => {
  const f = startupFixture(), pending = f.begin(), proc = f.procs[0];
  const token = f.spawns[0].options.env.VISION_AI_STUDIO_API_TOKEN;
  startupExit(proc);
  assert.equal(f.supervisor.getApiToken(), null);
  proc.stderr.emit('data', Buffer.from(cutAtBound
    ? token + 'x'.repeat(16368)
    : 'API_TOKEN=' + token + '\nValueError: original causal detail'));
  startupClose(proc); await pending;
  assert.equal(f.crashes.length, 1);
  const message = f.crashes[0].message;
  assert(message.includes('[REDACTED]'));
  assert(!message.includes(token));
  if (cutAtBound) assert(!message.includes(token.slice(-16)));
  else assert(message.includes('ValueError: original causal detail'));
  assert(Buffer.byteLength(message, 'utf8') <= 17000);
});

test('a fresh startup crash message does not inherit a prior child diagnostic', async () => {
  const f = startupFixture({children:2});
  const first = f.begin();
  f.procs[0].stderr.emit('data', Buffer.from('PRIOR-CHILD-ONLY\n'));
  startupExit(f.procs[0]); startupClose(f.procs[0]); await first;
  const second = f.begin(); startupExit(f.procs[1], 9); startupClose(f.procs[1]); await second;
  assert.equal(f.crashes.length, 2);
  assert(f.crashes[0].message.includes('PRIOR-CHILD-ONLY'));
  assert.equal(f.crashes[1].message, 'Python process exited prematurely with code 9 before port discovery.');
});

test('late failure settlement from an older startup cannot notify as the newer attempt', async () => {
  const f = startupFixture({children:2});
  const first = f.begin(); startupExit(f.procs[0]);
  const second = f.begin(); startupExit(f.procs[1], 9);
  f.procs[1].stderr.emit('data', Buffer.from('NEW-ATTEMPT-ONLY'));
  startupClose(f.procs[1]); await second;
  f.procs[0].stderr.emit('data', Buffer.from('STALE-ATTEMPT-ONLY'));
  startupClose(f.procs[0]); await first;
  assert.equal(f.crashes.length, 1);
  assert(f.crashes[0].message.includes('NEW-ATTEMPT-ONLY'));
  assert(!f.crashes[0].message.includes('STALE-ATTEMPT-ONLY'));
  assert.deepEqual(f.procs.map(proc => proc.io), [[], []]);
});

test('concurrent callers receive the one delivered startup diagnostic', async () => {
  const f = startupFixture(), first = f.begin(), second = f.begin(), proc = f.procs[0];
  startupExit(proc); proc.stderr.emit('data', Buffer.from('ValueError: shared original attempt'));
  startupClose(proc);
  const results = await Promise.all([first, second]);
  assert.equal(f.spawns.length, 1); assert.equal(f.crashes.length, 1);
  for (const result of results) assert.equal(result.error.message, f.crashes[0].message);
  assert(f.crashes[0].message.includes('ValueError: shared original attempt'));
});

test('an exited child with open inherited stdio delivers at the original discovery deadline', async () => {
  const f = startupFixture({timeout:25}), pending = f.begin(), proc = f.procs[0];
  startupExit(proc); proc.stderr.emit('data', Buffer.from('ValueError: original deadline detail'));
  await pending;
  assert.equal(f.crashes.length, 1);
  assert(f.crashes[0].message.includes('ValueError: original deadline detail'));
  assert.equal(proc.listenerCount('close'), 0);
  startupClose(proc); assert.equal(f.crashes.length, 1);
  assert.deepEqual(proc.io, []);
});

test('successful startup and a later crash retain their existing health and generic crash behavior', async () => {
  const f = startupFixture(), pending = f.begin(), proc = f.procs[0];
  proc.stdout.emit('data', Buffer.from('VISION_AI_STUDIO_PORT=43129\n'));
  assert.deepEqual(await pending, {value:43129});
  assert.equal(f.supervisor.getStatusInfo().state, 'HEALTHY'); assert.equal(f.crashes.length, 0);
  startupExit(proc, 3); startupClose(proc);
  assert.equal(f.crashes.length, 1);
  assert.equal(f.crashes[0].message, 'Python backend process terminated unexpectedly (code: 3, signal: null).');
  assert.equal(f.crashes[0].exitCode, 3); assert.deepEqual(proc.io, []);
});

test('owned startup failure still refuses ownership and never schedules an automatic successor', async () => {
  const f = startupFixture({autoRestart:true, owned:true}), pending = f.begin(), proc = f.procs[0];
  startupExit(proc); proc.stderr.emit('data', Buffer.from('ValueError: owned startup refusal'));
  startupClose(proc); await pending;
  assert(f.refusals.length > 0); assert.equal(f.spawns.length, 1);
  assert.equal(f.supervisor.getStatusInfo().restartCount, 0);
  assert.equal(f.crashes.length, 1);
  assert(f.crashes[0].message.includes('ValueError: owned startup refusal'));
  assert.deepEqual(proc.io, []);
});

test('an ordinary healthy backend crash retains the original single automatic restart', async () => {
  const f = startupFixture({children:2, autoRestart:true});
  const first = f.begin(); f.procs[0].stdout.emit('data', Buffer.from('VISION_AI_STUDIO_PORT=43129\n'));
  await first;
  const spawned = f.nextSpawn();
  const ready = new Promise(resolve => f.supervisor.once('ready', resolve));
  startupExit(f.procs[0], 3); startupClose(f.procs[0]);
  assert.equal(f.supervisor.getStatusInfo().restartCount, 1); assert.equal(f.crashes.length, 0);
  await spawned;
  f.procs[1].stdout.emit('data', Buffer.from('VISION_AI_STUDIO_PORT=43130\n'));
  assert.equal(await ready, 43130); assert.equal(f.spawns.length, 2);
  assert.equal(f.supervisor.getStatusInfo().state, 'HEALTHY'); assert.equal(f.crashes.length, 0);
  assert.deepEqual(f.procs.map(proc => proc.io), [[], []]);
});
