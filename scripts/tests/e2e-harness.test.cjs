'use strict';
// Bootstrap checks for the end-to-end harness. They use only Node built-ins and
// a fake backend so they run before Playwright, browsers or Python exist.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');

const harness = require('../e2e/fixtures/harness.cjs');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const FAKE_BACKEND = String.raw`
const http=require('http'),{spawn}=require('child_process'),fs=require('fs');
const token=process.env.VISION_AI_STUDIO_API_TOKEN;
if(process.env.FAKE_MODE==='exit'){process.stderr.write('fake startup failure\n');process.exit(3);}
const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
if(process.env.FAKE_CHILD_PID_FILE)fs.writeFileSync(process.env.FAKE_CHILD_PID_FILE,String(child.pid));
const server=http.createServer((req,res)=>{
  if(req.url==='/health'){res.setHeader('content-type','application/json');res.end(JSON.stringify({status:'ok',device:'cpu'}));return;}
  if(req.url.startsWith('/api/echo')){res.setHeader('content-type','application/json');res.end(JSON.stringify({tokenOk:req.headers['x-vision-token']===token,host:req.headers.host,origin:req.headers.origin||null}));return;}
  res.statusCode=404;res.end();
});
server.on('upgrade',(req,socket)=>{
  if(req.headers['x-vision-token']===token)socket.end('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
  else socket.end('HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n');
});
server.listen(0,'127.0.0.1',()=>{
  const port=String(server.address().port);
  process.stdout.write('booting\nVISION_AI_STUDIO_PORT=0\nlog VISION_AI_STUDIO_PORT=1\nVISION_AI_STUDIO_PO');
  setTimeout(()=>process.stdout.write('RT='+port+'\n'),50);
  if(process.env.FAKE_MODE==='die-after-ready')setTimeout(()=>process.exit(7),600);
});
process.on('SIGTERM',()=>{server.close();process.exit(0);});
`;

function scratch(name) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `mv-harness-${name}-`));
}

function fakeBackendOptions(dir, extraEnv = {}) {
  const script = path.join(dir, 'fake-backend.cjs');
  fs.writeFileSync(script, FAKE_BACKEND);
  return { command: process.execPath, args: [script], cwd: dir, logDir: dir, env: extraEnv, timeoutMs: 15000, ownershipMarker: dir };
}

async function waitUntil(predicate, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return true;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  return false;
}

function get(port, requestPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: requestPath, headers }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('error', reject);
    req.end();
  });
}

function upgrade(port, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path: '/ws/telemetry',
      headers: { Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13', 'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==', ...headers },
    });
    req.on('upgrade', (res, socket) => { socket.destroy(); resolve(res.statusCode); });
    req.on('response', res => { res.resume(); resolve(res.statusCode); });
    req.on('error', reject);
    req.end();
  });
}

test('port announcements must be a whole line and a real port', () => {
  const parser = harness.createPortParser();
  assert.equal(parser('starting\nVISION_AI_STUDIO_PORT=0\nlog VISION_AI_STUDIO_PORT=9\nVISION_AI_STUDIO_PO'), null);
  assert.equal(parser('RT=512'), null, 'a line without its newline may still be incomplete');
  assert.equal(parser('34\r\nmore'), 51234);
  assert.equal(parser('VISION_AI_STUDIO_PORT=1\n'), 51234, 'the first announced port wins');
  assert.equal(harness.createPortParser()('VISION_AI_STUDIO_PORT=70000\n'), null);
});

test('python selection prefers explicit harness and app variables', () => {
  assert.equal(harness.resolvePython({ MV_E2E_PYTHON: '/a/python', VISION_AI_PYTHON: '/b/python' }, 'darwin'), '/a/python');
  assert.equal(harness.resolvePython({ VISION_AI_PYTHON: 'C:\\py\\python.exe' }, 'win32'), 'C:\\py\\python.exe');
  assert.equal(harness.resolvePython({}, 'win32'), 'python');
  assert.equal(harness.resolvePython({}, 'linux'), 'python3');
  assert.throws(() => harness.assertSpawnable('C:\\tools\\python.bat', 'win32'), /python\.exe/);
});

test('child environments drop app variables that could point at shared stores', () => {
  const env = harness.sanitizeEnv({
    PATH: '/bin', VISION_RESOURCE_LEASE_DB: '/shared/leases', VISION_AI_STUDIO_API_TOKEN: 't', MODU_VISION_LOCAL_WORKER_TOKEN: 'w',
    MV_E2E_PYTHON: '/py', ELECTRON_RUN_AS_NODE: '1',
  });
  assert.deepEqual(env, { PATH: '/bin' });
});

test('a temporary workspace has isolated folders and valid sample images', () => {
  const root = path.join(scratch('workspace'), '작업 공간');
  const workspace = harness.createWorkspace(root);
  for (const key of ['userData', 'projects', 'home', 'dataset', 'logs']) {
    assert.ok(fs.statSync(workspace[key]).isDirectory(), key);
  }
  assert.ok(workspace.images.length >= 2);
  for (const image of workspace.images) {
    const bytes = fs.readFileSync(image.path);
    assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.equal(bytes.readUInt32BE(16), 32, 'width');
    assert.equal(image.sha256, crypto.createHash('sha256').update(bytes).digest('hex'));
  }
  const marker = path.join(root, 'keep.txt');
  fs.writeFileSync(marker, 'existing');
  assert.throws(() => harness.createWorkspace(root), /not empty/);
  assert.equal(fs.readFileSync(marker, 'utf8'), 'existing', 'existing material is never removed');
});

test('owned backend reaches health and stop ends only its own processes', async () => {
  // A Korean, space-containing workspace exercises the locale-sensitive scan.
  const dir = path.join(scratch('backend'), '작업 공간');
  fs.mkdirSync(dir);
  const childPidFile = path.join(dir, 'child.pid');
  const escapedChildFile = path.join(dir, 'escaped-child.pid');
  const bystander = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore' });
  // A log viewer that mentions the workspace path is not a harness process.
  const viewer = process.platform === 'win32' ? null
    : spawn('/bin/sh', ['-c', `sleep 30; echo ${path.join(dir, 'backend.stderr.log')}`], { stdio: 'ignore' });
  let backend, escaped, escapedChildPid;
  try {
    // A separate tree must survive Windows taskkill /T on the backend, so the
    // later workspace-marker scan is exercised on every platform.
    const script = "const c=require('child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});require('fs').writeFileSync(process.argv[2],String(c.pid));setInterval(()=>{},1000)";
    escaped = spawn(process.execPath, ['-e', script, path.join(dir, 'marker'), escapedChildFile], { stdio: 'ignore', detached: true });
    backend = await harness.startOwnedBackend(fakeBackendOptions(dir, { FAKE_CHILD_PID_FILE: childPidFile }));
    assert.ok(backend.port > 1);
    assert.equal(backend.health.status, 'ok');
    assert.equal(backend.baseUrl, `http://127.0.0.1:${backend.port}`);
    assert.equal(backend.portPolicy, 'os_assigned_announced');
    assert.match(backend.token, /^[0-9a-f]{64}$/);
    assert.doesNotMatch(JSON.stringify(backend), new RegExp(backend.token), 'the token is not enumerable');
    assert.ok(await waitUntil(() => [childPidFile, escapedChildFile].every(file => fs.existsSync(file))));
    const childPid = Number(fs.readFileSync(childPidFile, 'utf8'));
    const escapedPid = escaped.pid;
    escapedChildPid = Number(fs.readFileSync(escapedChildFile, 'utf8'));
    assert.ok(harness.processAlive(childPid) && harness.processAlive(escapedPid));

    const stopped = await backend.stop();
    assert.equal(stopped.exited, true);
    assert.equal(stopped.exitedBeforeStop, false);
    assert.equal(stopped.groupAlive, false);
    assert.equal(harness.processAlive(backend.pid), false);
    assert.ok(await waitUntil(() => !harness.processAlive(childPid)), 'group descendant exits');
    assert.deepEqual(stopped.escaped.map(row => row.pid).sort(), [escapedPid, escapedChildPid].sort(),
      'escaped worker and its child are found by the workspace marker and the process tree');
    assert.ok(await waitUntil(() => !harness.processAlive(escapedPid)), 'escaped worker is stopped');
    assert.ok(await waitUntil(() => !harness.processAlive(escapedChildPid)), 'children of an escaped worker are stopped');
    assert.equal(stopped.scanError, null);
    assert.equal(harness.processAlive(bystander.pid), true, 'unrelated processes are preserved');
    if (viewer) {
      assert.equal(harness.processAlive(viewer.pid), true, 'a non-worker process that mentions the workspace is not killed');
      assert.ok(stopped.unownedReferences.some(row => row.pid === viewer.pid), 'and it is reported');
    }
    assert.deepEqual(await backend.stop(), stopped, 'stop is idempotent');
  } finally {
    try { await backend?.stop(); } finally {
      if (escaped && harness.processAlive(escaped.pid)) {
        if (process.platform === 'win32') {
          try { execFileSync('taskkill', ['/pid', String(escaped.pid), '/T', '/F'], { stdio: 'ignore' }); } catch { /* fixture already exited */ }
        } else {
          try { process.kill(-escaped.pid, 'SIGKILL'); } catch { /* fixture already exited */ }
        }
      }
      if (escapedChildPid && harness.processAlive(escapedChildPid)) {
        try { process.kill(escapedChildPid, 'SIGKILL'); } catch { /* fixture already exited */ }
      }
      bystander.kill();
      viewer?.kill();
    }
  }
});

test('a backend that exits before announcing a port fails with its error output', async () => {
  const dir = scratch('backend-fail');
  await assert.rejects(
    harness.startOwnedBackend(fakeBackendOptions(dir, { FAKE_MODE: 'exit' })),
    error => /fake startup failure/.test(error.message) && /exit/i.test(error.message),
  );
});

test('the scan never classifies the harness or its ancestors as owned', () => {
  const marker = path.join(scratch('ancestors'), 'run');
  const scan = harness.findProcessesReferencing(marker);
  assert.equal(scan.error, null);
  const rows = scan.rows;
  const own = rows.find(row => row.pid === process.pid);
  assert.ok(own, 'the scanner sees itself');
  const fakeRows = rows.map(row => (row.pid === process.ppid ? { ...row, command: `node wrapper.js ${marker}/report.json` } : row));
  const owned = [];
  for (const row of fakeRows) if (row.pid === process.ppid) owned.push(row);
  return harness.terminateProcesses(owned, fakeRows).then(result => {
    assert.deepEqual(result, [], 'an ancestor is never targeted');
    assert.equal(harness.processAlive(process.ppid), true);
  });
});

test('an exit nobody requested is reported by stop', async () => {
  const dir = scratch('backend-died');
  const backend = await harness.startOwnedBackend(fakeBackendOptions(dir, { FAKE_MODE: 'die-after-ready' }));
  assert.ok(await waitUntil(() => !backend.running()));
  const stopped = await backend.stop();
  assert.equal(stopped.exitedBeforeStop, true);
  assert.equal(stopped.exitCode, 7);
});

test('renderer server serves static files, blocks traversal and injects the token for its own origin only', async () => {
  const dir = scratch('renderer');
  const staticDir = path.join(dir, 'static');
  fs.mkdirSync(path.join(staticDir, 'assets'), { recursive: true });
  fs.writeFileSync(path.join(staticDir, 'index.html'), '<!doctype html><title>shell</title>');
  fs.writeFileSync(path.join(staticDir, 'assets', 'app.js'), 'console.log(1)');
  fs.writeFileSync(path.join(staticDir, '..notes.txt'), 'dot-prefixed name');
  fs.writeFileSync(path.join(dir, 'secret.txt'), 'outside-static-root');
  const backend = await harness.startOwnedBackend(fakeBackendOptions(dir));
  const renderer = await harness.startRendererServer({ staticDir, backend });
  try {
    assert.equal(renderer.url, `http://127.0.0.1:${renderer.port}/index.html?port=${renderer.port}`);
    const index = await get(renderer.port, '/');
    assert.equal(index.status, 200);
    assert.match(index.body, /shell/);
    assert.match((await get(renderer.port, '/assets/app.js')).headers['content-type'], /javascript/);
    assert.equal((await get(renderer.port, '/..notes.txt')).status, 200, 'names that merely start with dots are served');
    for (const attempt of ['/../secret.txt', '/%2e%2e/secret.txt', '/assets/..%2f..%2fsecret.txt', '/assets/..%5c..%5csecret.txt']) {
      const response = await get(renderer.port, attempt);
      assert.ok([400, 403, 404].includes(response.status), attempt);
      assert.doesNotMatch(response.body, /outside-static-root/);
    }
    const proxied = await get(renderer.port, '/api/echo', { 'x-vision-token': 'client-supplied' });
    assert.equal(proxied.status, 200);
    assert.equal(JSON.parse(proxied.body).tokenOk, true, 'client token is replaced by the owned token');
    assert.doesNotMatch(proxied.body + JSON.stringify(proxied.headers), new RegExp(backend.token));
    const sameOrigin = await get(renderer.port, '/api/echo', { origin: renderer.origin });
    assert.equal(JSON.parse(sameOrigin.body).tokenOk, true);
    assert.equal((await get(renderer.port, '/api/echo', { origin: 'http://evil.example' })).status, 403);
    assert.equal((await get(renderer.port, '/api/echo', { 'sec-fetch-site': 'cross-site' })).status, 403, 'origin-less cross-site loads');
    assert.equal((await get(renderer.port, '/api/echo', { 'sec-fetch-site': 'same-origin' })).status, 200);
    assert.equal(await upgrade(renderer.port), 101);
    assert.equal(await upgrade(renderer.port, { origin: 'http://evil.example' }), 403);
    assert.equal(renderer.stats().foreignOrigin, 3);
    assert.ok(renderer.stats().proxied >= 3);
  } finally {
    await renderer.close();
    await backend.stop();
  }
});

test('electron launch options isolate the profile and never carry backend or shared-store variables', () => {
  const workspace = { root: '/tmp/w', userData: '/tmp/w/userData', home: '/tmp/w/home' };
  const options = harness.electronLaunchOptions({
    appDir: '/runs/app', workspace, python: '/py/python', devServerUrl: 'http://127.0.0.1:9',
    baseEnv: { PATH: '/bin', VISION_AI_STUDIO_API_TOKEN: 'leak', VISION_RESOURCE_LEASE_DB: '/shared' },
  });
  assert.deepEqual(options.args, ['/runs/app']);
  assert.equal(options.cwd, '/runs/app');
  assert.equal(options.env.VISION_AI_STUDIO_USER_DATA_DIR, '/tmp/w/userData');
  assert.equal(options.env.VISION_AI_PYTHON, '/py/python');
  assert.equal(options.env.VISION_AI_STUDIO_DEV_SOURCE_BACKEND, '1');
  assert.equal(options.env.VITE_DEV_SERVER_URL, 'http://127.0.0.1:9');
  assert.equal(options.env.PATH, '/bin');
  assert.equal(options.env.VISION_AI_STUDIO_API_TOKEN, undefined);
  assert.equal(options.env.VISION_RESOURCE_LEASE_DB, undefined);
});

test('preflight reports missing browsers, version drift and interpreter problems before any test runs', () => {
  const ready = {
    playwright: { locked: '1.0.0', installed: '1.0.0' }, electron: { locked: '2.0.0', installed: '2.0.0' },
    config: { browserProject: true, electronProject: true }, browserChannel: null,
    bundledChromium: { present: true }, pythonImports: { python: 'py', ok: true },
  };
  assert.deepEqual(harness.evaluatePreflight(ready), []);
  assert.match(harness.evaluatePreflight({ ...ready, bundledChromium: { present: false } }).join('\n'), /playwright install chromium/);
  assert.deepEqual(harness.evaluatePreflight({ ...ready, bundledChromium: { present: false }, browserChannel: 'chrome' }), []);
  assert.match(harness.evaluatePreflight({ ...ready, playwright: { locked: '1.0.0', installed: '0.9.0' } }).join('\n'), /lockfile/);
  assert.match(harness.evaluatePreflight({ ...ready, pythonImports: { python: 'py', ok: false, error: 'No module named torch' } }).join('\n'), /torch/);
  assert.deepEqual(harness.evaluatePreflight({ ...ready, bundledChromium: { present: false } }, { needBrowser: false }), []);

  const facts = harness.collectPreflightFacts({ repoRoot: REPO_ROOT, env: {}, checkPython: false });
  assert.ok(facts.playwright.locked, 'the lockfile pins @playwright/test');
  assert.equal(facts.playwright.installed, facts.playwright.locked);
  assert.deepEqual(facts.config, { browserProject: true, electronProject: true });
  assert.equal(facts.electron.installed, facts.electron.locked);
});

test('manifests record file hashes, the source identity and redact secrets', () => {
  const dir = scratch('manifest');
  const file = path.join(dir, 'shot.png');
  fs.writeFileSync(file, 'image-bytes');
  const manifestPath = path.join(dir, 'manifest.json');
  harness.writeManifest(manifestPath, { kind: 'test', files: [file], note: 'token=abc123secret' }, { redact: ['abc123secret'] });
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.equal(manifest.kind, 'test');
  assert.equal(manifest.note, 'token=[redacted]');
  assert.equal(manifest.files[0].sha256, crypto.createHash('sha256').update('image-bytes').digest('hex'));
  assert.equal(manifest.files[0].bytes, 11);
  const identity = harness.sourceIdentity(REPO_ROOT);
  assert.match(identity.commit, /^[0-9a-f]{40}$/);
  assert.equal(typeof identity.dirty, 'boolean');
});

test('closed loopback ports are detected', async () => {
  const url = await harness.closedLoopbackUrl();
  const port = Number(new URL(url).port);
  assert.equal(await harness.waitForPortClosed(port, 2000), true);
});
