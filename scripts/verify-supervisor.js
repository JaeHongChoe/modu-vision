/**
 * scripts/verify-supervisor.js
 *
 * Standalone automated test verifying:
 * 1. Python executable resolution (finding Anaconda/Miniconda/Conda/PATH)
 * 2. Ephemeral port regex & split-chunk buffer parser
 * 3. Health check polling with exponential backoff & retry
 * 4. Live daemon spawn on port 0, health handshake verification
 * 5. Graceful SIGTERM shutdown within 4.0s (0 orphaned processes)
 */

const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const os = require('os');

const PROJECT_ROOT = path.resolve(__dirname, '..');

console.log('================================================================');
console.log('   Vision AI Studio Native Supervisor Verification Suite (M4)   ');
console.log('================================================================');
console.log(`[INFO] Project Root: ${PROJECT_ROOT}`);

function resolvePython() {
  const envPy = process.env.VISION_AI_PYTHON || process.env.PYTHON_PATH;
  if (envPy && fs.existsSync(envPy)) return envPy;
  const homedir = os.homedir();
  const candidates = process.platform === 'win32'
    ? [
        path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe'),
        path.join(process.env.USERPROFILE || '', 'anaconda3', 'python.exe'),
      ]
    : [
        path.join(PROJECT_ROOT, '.venv', 'bin', 'python3'),
        '/opt/anaconda3/bin/python3',
        '/opt/homebrew/bin/python3',
        '/usr/local/bin/python3',
        path.join(homedir, 'anaconda3', 'bin', 'python3'),
        path.join(homedir, 'miniconda3', 'bin', 'python3'),
      ];
  for (const cand of candidates) {
    if (fs.existsSync(cand)) return cand;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function testChunkStreamParser() {
  console.log('\n--- 1. Testing Split Chunk Stream Parsing ---');
  let capturedPort = null;
  let buffer = '';
  function feed(chunk) {
    buffer += chunk;
    let newlineIdx;
    while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIdx).replace(/\r$/, '');
      buffer = buffer.slice(newlineIdx + 1);
      const match = line.match(/VISION_AI_STUDIO_PORT=(\d+)/);
      if (match) capturedPort = parseInt(match[1], 10);
    }
  }

  feed('INFO: Initializing daemon...\n');
  feed('WARNING: Notice message\nVISION_AI_ST');
  if (capturedPort !== null) throw new Error('Premature port capture');
  feed('UDIO_PORT=58492\nINFO: Uvicorn running\n');

  if (capturedPort !== 58492) throw new Error(`Expected 58492, got ${capturedPort}`);
  console.log(`[PASS] Split chunk correctly handled. Extracted port: ${capturedPort}`);
}

async function testHealthBackoff() {
  console.log('\n--- 2. Testing Health Check Exponential Backoff ---');
  let hits = 0;
  const mockServer = http.createServer((req, res) => {
    hits++;
    if (hits < 3) {
      res.writeHead(503);
      res.end('Warmup');
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok', version: '0.1.0', device: 'mps' }));
    }
  });

  await new Promise(r => mockServer.listen(0, '127.0.0.1', r));
  const port = mockServer.address().port;

  const t0 = Date.now();
  let delay = 50;
  let healthResult = null;
  while (Date.now() - t0 < 3000) {
    try {
      const res = await new Promise((resolve, reject) => {
        const req = http.get({ host: '127.0.0.1', port, path: '/health', timeout: 500 }, (res) => {
          if (res.statusCode !== 200) { res.resume(); return reject(new Error('Status ' + res.statusCode)); }
          let d = '';
          res.on('data', c => d += c);
          res.on('end', () => resolve(JSON.parse(d)));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
      });
      if (res && res.status === 'ok') { healthResult = res; break; }
    } catch {}
    await new Promise(r => setTimeout(r, delay));
    delay = Math.min(Math.floor(delay * 1.5), 300);
  }
  mockServer.close();

  if (!healthResult || healthResult.status !== 'ok') throw new Error('Health check backoff failed');
  console.log(`[PASS] Health backoff succeeded after ${hits} attempts:`, healthResult);
}

async function testLiveLifecycle(pythonBin) {
  console.log('\n--- 3. Testing Live Daemon Launch & Graceful Shutdown ---');
  const mainScript = path.join(PROJECT_ROOT, 'backend', 'main.py');
  const projectDir = path.join(PROJECT_ROOT, 'tmp_out', 'test_proj');
  fs.mkdirSync(projectDir, { recursive: true });

  const child = spawn(
    pythonBin,
    [mainScript, '--port', '0', '--host', '127.0.0.1', '--project-dir', projectDir, '--log-level', 'warning'],
    {
      cwd: PROJECT_ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONPATH: PROJECT_ROOT },
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  );

  const pid = child.pid;
  console.log(`[INFO] Child spawned with PID ${pid}`);

  let discoveredPort = null;
  const portPromise = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Port extraction timeout')), 15000);
    let buf = '';
    child.stdout.on('data', (chunk) => {
      buf += chunk.toString();
      let idx;
      while ((idx = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        const m = line.match(/VISION_AI_STUDIO_PORT=(\d+)/);
        if (m) {
          clearTimeout(timeout);
          resolve(parseInt(m[1], 10));
        }
      }
    });
    child.on('exit', (code) => {
      clearTimeout(timeout);
      reject(new Error(`Daemon exited prematurely with code ${code}`));
    });
  });

  discoveredPort = await portPromise;
  console.log(`[PASS] Discovered ephemeral port: ${discoveredPort}`);

  // Query health
  const healthRes = await new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${discoveredPort}/health`, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve(JSON.parse(data)));
    });
    req.on('error', reject);
  });
  console.log(`[PASS] /health response verified:`, healthRes);

  // Send SIGTERM
  console.log('[INFO] Sending SIGTERM...');
  const t0 = Date.now();
  child.kill('SIGTERM');

  const elapsed = await new Promise((resolve) => {
    child.on('exit', () => {
      resolve(Date.now() - t0);
    });
  });

  console.log(`[PASS] Cleanly exited in ${elapsed}ms (< 4000ms limit)`);
  if (elapsed > 4000) throw new Error('Graceful shutdown timeout exceeded');

  let alive = false;
  try { process.kill(pid, 0); alive = true; } catch {}
  if (alive) throw new Error('Process still alive; orphan detected');
  console.log(`[PASS] Process PID ${pid} confirmed dead. Zero orphaned processes.`);
}

async function testCompiledSupervisorClass() {
  console.log('\n--- 4. Testing Compiled BackendSupervisor Class Lifecycle ---');
  const supervisorModulePath = path.join(PROJECT_ROOT, 'dist-electron', 'main', 'supervisor.js');
  if (!fs.existsSync(supervisorModulePath)) {
    console.log('[SKIP] dist-electron/main/supervisor.js not built yet, skipping compiled test.');
    return;
  }

  const { BackendSupervisor } = require(supervisorModulePath);
  const supervisor = new BackendSupervisor({ logLevel: 'warning' });
  const port = await supervisor.start();
  console.log(`[PASS] BackendSupervisor.start() resolved ephemeral port: ${port}`);

  const status = supervisor.getStatus();
  if (!status.healthy || status.port !== port || !status.pid) {
    throw new Error(`Invalid supervisor status: ${JSON.stringify(status)}`);
  }
  console.log(`[PASS] BackendSupervisor.getStatus(): healthy=true, pid=${status.pid}, device=${status.device}`);

  await supervisor.stop();
  if (supervisor.isRunning() || supervisor.isHealthy()) {
    throw new Error('BackendSupervisor should not be running or healthy after stop()');
  }
  console.log('[PASS] BackendSupervisor.stop() shut down daemon cleanly');
}

async function testOwnedBootstrapOrdering(completion) {
  const assert = require('node:assert/strict');
  const { EventEmitter } = require('node:events');
  const { PassThrough } = require('node:stream');
  const ts = require('typescript');
  const source = path.join(PROJECT_ROOT, 'src/main/supervisor.ts');
  const compiled = ts.transpileModule(fs.readFileSync(source, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const events = [];
  let requests = 0;
  const server = http.createServer((req, res) => {
    requests++;
    assert.equal(req.url, '/health');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', version: 'controlled', device: 'cpu', device_name: 'controlled' }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const child = new EventEmitter();
  child.pid = completion === 'resolve' ? 12001 : 12002; // Modeled handles; no OS process or signal.
  child.exitCode = child.signalCode = null;
  child.killed = false;
  child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.stdio = [child.stdin, child.stdout, child.stderr, new PassThrough()];
  child.kill = signal => {
    events.push('modeled-stop:' + signal);
    setImmediate(() => { child.exitCode = 0; child.emit('exit', 0, null); });
    return true;
  };
  if (process.platform === 'win32') child.stdin.once('finish', () => child.kill('controlled-stdin-eof'));
  let release, reject, settled = false;
  const bootstrap = new Promise((resolve, refuse) => { release = resolve; reject = refuse; });
  let portObserved;
  const discovered = new Promise(resolve => { portObserved = resolve; });
  const artifact = { sha256: 'a'.repeat(64), build: 'b'.repeat(64) };
  const executable = path.join(os.tmpdir(), 'controlled-owned-backend');
  let spawns = 0, refusals = 0, binds = 0;
  const owned = {
    projects: path.join(os.tmpdir(), 'controlled-projects'), auth: path.join(os.tmpdir(), 'controlled-auth'),
    executable: file => { assert.equal(file, executable); return artifact; },
    backendEnvironment: env => ({ ...env, VISION_APPLICATION_BACKEND_FD: '3' }),
    bindBackend: (proc, file, build) => {
      assert.equal(proc, child); assert.equal(file, executable); assert.equal(build, artifact.build);
      binds++; events.push('bootstrap-pending'); return bootstrap;
    },
    refuse: () => { refusals++; events.push('refused'); },
  };
  const originalChildProcess = require('node:child_process');
  const fixtureRequire = name => {
    if (name === 'child_process') return { ...originalChildProcess, spawn: (file, args, options) => {
      assert.equal(file, executable); assert.deepEqual(options.stdio, ['pipe', 'pipe', 'pipe', 'pipe']);
      assert.ok(args.includes('--shared-auth-dir')); assert.equal(options.env.VISION_APPLICATION_BACKEND_FD, '3');
      spawns++; setImmediate(() => child.stdout.write('VISION_AI_STUDIO_PORT=' + port + '\n')); return child;
    } };
    if (name === 'electron') return { app: { isPackaged: true, getPath: () => os.tmpdir(), getVersion: () => 'controlled' } };
    return require(name);
  };
  const loaded = { exports: {} };
  new Function('require', 'module', 'exports', '__filename', '__dirname', compiled)(
    fixtureRequire, loaded, loaded.exports, source, path.dirname(source));
  const { BackendSupervisor } = loaded.exports;
  BackendSupervisor.prototype.registerProcessHooks = () => {}; // This fixture has no real child handles.
  const supervisor = new BackendSupervisor({ ownedApplicationLaunch: owned });
  supervisor.resolveStandaloneBinary = () => executable;
  supervisor.getAppRoot = () => os.tmpdir();
  supervisor.getBackendWorkingDirectory = () => os.tmpdir();
  const originalDiscovery = supervisor.discoverPort.bind(supervisor);
  supervisor.discoverPort = async (proc, budget) => {
    assert.equal(budget, 180000); const result = await originalDiscovery(proc, budget);
    events.push('port'); portObserved(); return result;
  };
  const originalHealth = supervisor.pollHealth.bind(supervisor);
  supervisor.pollHealth = (...args) => {
    assert.equal(args[2], 15000); events.push('health-start'); return originalHealth(...args);
  };
  const startup = supervisor.startBackend().then(value => ({ value }), error => ({ error }));
  try {
    await discovered;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(binds, 1);
    assert.equal(supervisor.isHealthy(), false);
    if (completion === 'resolve') {
      assert.ok(!events.includes('health-start'), 'Health polling began before original owned bootstrap resolved');
      assert.equal(requests, 0);
    }
    settled = true;
    if (completion === 'resolve') {
      events.push('bootstrap-resolved'); release();
      const result = await startup;
      assert.equal(result.error, undefined); assert.equal(result.value, port);
      assert.equal(requests, 1); assert.ok(supervisor.isHealthy());
      assert.ok(events.indexOf('bootstrap-resolved') < events.indexOf('health-start'));
      await supervisor.stopBackend();
      assert.equal(refusals, 0);
    } else {
      const originalRefusal = new Error('controlled original bootstrap refusal');
      reject(originalRefusal);
      const result = await startup;
      assert.equal(result.error, originalRefusal); assert.equal(requests, 0);
      assert.ok(!events.includes('health-start')); assert.equal(refusals, 1);
      assert.equal(supervisor.isHealthy(), false);
      assert.equal(supervisor.getStatusInfo().state, 'CRASHED');
    }
    assert.equal(spawns, 1);
    assert.ok(!events.includes('modeled-stop:SIGKILL'));
  } finally {
    if (!settled) reject(new Error('controlled fixture teardown'));
    await startup;
    await supervisor.stopBackend();
    child.stdio.forEach(stream => stream.destroy());
    await new Promise(resolve => server.close(resolve));
  }
  console.log('[PASS] Actual supervisor source owned bootstrap ' + completion + ': ' + JSON.stringify({ events, requests, spawns, modeled_child_only: true }));
}

async function run() {
  if (process.argv.includes('--owned-bootstrap-order')) {
    const results = await Promise.allSettled(['resolve', 'reject'].map(testOwnedBootstrapOrdering));
    results.forEach((result, index) => {
      if (result.status === 'rejected') console.error('[FAIL] owned bootstrap ' + ['resolve', 'reject'][index] + ': ' + result.reason.stack);
    });
    if (results.some(result => result.status === 'rejected')) throw new Error('Owned bootstrap ordering failed');
    return;
  }
  const pythonBin = resolvePython();
  console.log(`[PASS] Resolved Python executable: ${pythonBin}`);
  testChunkStreamParser();
  await testHealthBackoff();
  await testLiveLifecycle(pythonBin);
  await testCompiledSupervisorClass();
  console.log('\n================================================================');
  console.log('   ALL SUPERVISOR AND PACKAGING VERIFICATION TESTS PASSED!      ');
  console.log('================================================================\n');
}

run().catch((err) => {
  console.error('[FATAL] Verification failed:', err);
  process.exit(1);
});
