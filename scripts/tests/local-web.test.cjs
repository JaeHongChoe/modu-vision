'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const ROOT = path.resolve(__dirname, '../..');
const WRAPPER = path.join(ROOT, 'scripts/local-web.cjs');

function fixture(t, body) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-web-wrapper-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const python = path.join(root, 'python with spaces');
  fs.writeFileSync(python, `#!${process.execPath}\n${body}\n`, { mode: 0o700 });
  return { root, python };
}

test('Python resolver prefers explicit executable then existing repo virtualenv', (t) => {
  const { resolvePython } = require(WRAPPER);
  const { root } = fixture(t, '');
  fs.mkdirSync(path.join(root, '.venv/bin'), { recursive: true });
  const virtual = path.join(root, '.venv/bin/python');
  fs.writeFileSync(virtual, '');
  assert.equal(resolvePython(root, { VISION_AI_PYTHON: '/custom/python with spaces' }, 'darwin'), '/custom/python with spaces');
  assert.equal(resolvePython(root, {}, 'linux'), virtual);
  fs.rmSync(path.join(root, '.venv'), { recursive: true });
  assert.equal(resolvePython(root, {}, 'linux'), 'python3');
  assert.equal(resolvePython(root, {}, 'win32'), 'python');
});

test('arguments are literal, source cwd is fixed, and child status is returned', (t) => {
  const { root, python } = fixture(t, `
    require('node:fs').writeFileSync(process.env.WEB_CAPTURE, JSON.stringify({args:process.argv.slice(2),cwd:process.cwd()}));
    process.exit(23);
  `);
  const marker = path.join(root, 'shell-must-not-run');
  const literal = `data with spaces; $(touch ${marker})`;
  const capture = path.join(root, 'capture.json');
  const result = spawnSync(process.execPath, [WRAPPER, '--data-dir', literal, '--port', '0', '--no-open'], {
    cwd: root, env: { ...process.env, VISION_AI_PYTHON: python, WEB_CAPTURE: capture }, encoding: 'utf8', timeout: 5000,
  });
  assert.equal(result.status, 23, result.stderr);
  assert.deepEqual(JSON.parse(fs.readFileSync(capture, 'utf8')), {
    args: ['-m', 'backend.web', '--data-dir', literal, '--port', '0', '--no-open'], cwd: ROOT,
  });
  assert.equal(fs.existsSync(marker), false);
});

test('help only forwards help to Python without building or starting a server', (t) => {
  const { root, python } = fixture(t, `
    const args = process.argv.slice(2);
    if (JSON.stringify(args) !== JSON.stringify(['-m','backend.web','--help'])) process.exit(91);
    console.log('Python help only');
  `);
  const result = spawnSync(process.execPath, [WRAPPER, '--help'], {
    cwd: root, env: { ...process.env, VISION_AI_PYTHON: python }, encoding: 'utf8', timeout: 5000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), 'Python help only');
});

test('missing Python is a clear launch failure', (t) => {
  const { root } = fixture(t, '');
  const result = spawnSync(process.execPath, [WRAPPER, '--help'], {
    env: { ...process.env, VISION_AI_PYTHON: path.join(root, 'absent') }, encoding: 'utf8', timeout: 5000,
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Python.*VISION_AI_PYTHON/);
});

function childClosed(child) {
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({code, signal}));
  });
}

test('owned child receives forwarded termination once and wrapper waits for cleanup', { timeout: 5000 }, async (t) => {
  const { root, python } = fixture(t, `
    process.on('SIGTERM', () => {
      require('node:fs').writeFileSync(process.env.WEB_CAPTURE, 'owned cleanup');
      setTimeout(() => process.exit(0), 50);
    });
    console.log('CHILD_WAITING');
    setInterval(() => {}, 1000);
  `);
  const capture = path.join(root, 'cleanup');
  const child = spawn(process.execPath, [WRAPPER], { env: { ...process.env, VISION_AI_PYTHON: python, WEB_CAPTURE: capture } });
  t.after(() => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM'); });
  const closed = childClosed(child);
  await new Promise((resolve, reject) => {
    child.stdout.once('data', resolve);
    child.once('error', reject);
  });
  child.kill('SIGTERM');
  assert.deepEqual(await closed, { code: 0, signal: null });
  assert.equal(fs.readFileSync(capture, 'utf8'), 'owned cleanup');
});

test('child signal termination is returned as the same signal', { timeout: 5000 }, async (t) => {
  const { python } = fixture(t, `process.kill(process.pid, 'SIGTERM');`);
  const child = spawn(process.execPath, [WRAPPER], { env: { ...process.env, VISION_AI_PYTHON: python } });
  assert.deepEqual(await childClosed(child), { code: null, signal: 'SIGTERM' });
});
