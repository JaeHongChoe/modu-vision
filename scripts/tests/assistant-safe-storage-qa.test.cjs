'use strict';
// These exercise the QA harness's isolation boundary, not OS encryption.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const qaPath = path.resolve(__dirname, '../qa/assistant-safe-storage.cjs');
const repoRoot = path.resolve(__dirname, '../..');

function scratch(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mv-safe-storage-harness-test-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  return root;
}

test('safeStorage QA creates a fresh named app and refuses any existing output without changing it', t => {
  assert.ok(fs.existsSync(qaPath), 'the repeatable safeStorage QA harness exists');
  const qa = require(qaPath);
  const root = scratch(t);
  const first = qa.prepareRun({outDir: path.join(root, 'first'), repoRoot});
  const second = qa.prepareRun({outDir: path.join(root, 'second'), repoRoot});
  assert.match(first.appName, /^modu-vision-safe-storage-qa-[a-f0-9-]{36}$/);
  assert.notEqual(first.appName, second.appName, 'each run has its own OS storage service');
  const pkg = JSON.parse(fs.readFileSync(path.join(first.appDir, 'package.json'), 'utf8'));
  assert.equal(pkg.name, first.appName);
  assert.equal(pkg.productName, first.appName);
  assert.equal(first.userData, path.join(first.outDir, 'userData'));
  assert.equal(first.sessionData, path.join(first.outDir, 'sessionData'));
  const marker = path.join(first.outDir, 'keep.txt');
  fs.writeFileSync(marker, 'keep');
  assert.throws(() => qa.prepareRun({outDir: first.outDir, repoRoot}), /already exists/);
  assert.equal(fs.readFileSync(marker, 'utf8'), 'keep');
});

test('safeStorage QA child inherits OS session paths but excludes secrets, shared app paths and mock flags', t => {
  assert.ok(fs.existsSync(qaPath), 'the repeatable safeStorage QA harness exists');
  const qa = require(qaPath);
  const root = scratch(t);
  const run = qa.prepareRun({outDir: path.join(root, 'run'), repoRoot});
  const options = qa.phaseLaunchOptions(run, 'save', '/qa/Electron', {
    HOME: '/native-home', PATH: '/usr/bin', TMPDIR: '/native-temp', LANG: 'ko_KR.UTF-8',
    OPENAI_API_KEY: 'foreign-secret', ANTHROPIC_API_KEY: 'foreign-secret',
    VISION_AI_STUDIO_USER_DATA_DIR: '/foreign-profile', ELECTRON_RUN_AS_NODE: '1',
    NODE_OPTIONS: '--require foreign.cjs', ELECTRON_FLAGS: '--use-mock-keychain',
  });
  assert.equal(options.command, '/qa/Electron');
  assert.equal(options.cwd, run.appDir);
  assert.ok(options.args.includes(run.appDir));
  assert.ok(!options.args.some(arg => /mock|password-store|remote-debugging/.test(arg)));
  assert.deepEqual(options.env, {
    HOME: '/native-home', PATH: '/usr/bin', TMPDIR: '/native-temp', LANG: 'ko_KR.UTF-8',
    MV_SAFE_STORAGE_QA_ROOT: run.outDir, MV_SAFE_STORAGE_QA_PHASE: 'save',
  });
  assert.throws(() => qa.phaseLaunchOptions(run, 'unknown', '/qa/Electron', {}), /Unknown phase/);
});
