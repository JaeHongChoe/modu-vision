'use strict';

// Real, tiny Git repositories exercise the source guard. No upstream code runs.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync, spawnSync } = require('node:child_process');

const SCRIPT = path.resolve(__dirname, '../geti-platform.cjs');
const REPOSITORY = 'https://github.com/open-edge-platform/geti.git';
const gitEnv = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: os.devNull, GIT_TERMINAL_PROMPT: '0' };

function guard() {
  assert.ok(fs.existsSync(SCRIPT), 'The source guard must exist before a source can be verified');
  return require(SCRIPT);
}

function git(source, ...args) {
  return execFileSync('git', ['-C', source, ...args], { env: gitEnv, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}

function write(root, name, content) {
  const file = path.join(root, name);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'mv-geti-source-')));
  const source = path.join(root, 'platforms/geti/upstream');
  fs.mkdirSync(source, { recursive: true });
  git(source, 'init', '-q');
  git(source, 'config', 'user.name', 'Source Contract Fixture');
  git(source, 'config', 'user.email', 'source-fixture@example.invalid');
  git(source, 'remote', 'add', 'origin', REPOSITORY);
  write(source, 'LICENSE', 'Fixture Apache-2.0 source license\n');
  write(source, 'application/backend/pyproject.toml', '[project]\nrequires-python = ">=3.14,<3.15"\n');
  write(source, 'application/backend/app/api/routers/projects.py', '# inert fixture project route\n');
  write(source, 'application/ui/package.json', JSON.stringify({ dependencies: { react: '~19.1.1' }, engines: { node: '>=v24.2.0', npm: '>=11.14.0' } }));
  write(source, 'application/ui/src/modules/annotator/index.ts', '// inert fixture annotator\n');
  write(source, 'library/src/getitune/engine/engine.py', '# inert fixture engine\n');
  git(source, 'add', '.');
  git(source, '-c', 'core.hooksPath=' + os.devNull, 'commit', '-qm', 'Fixture source');
  const lock = {
    schemaVersion: 1, repository: REPOSITORY, tag: 'app/v3.3.0',
    commit: git(source, 'rev-parse', 'HEAD'), tree: git(source, 'rev-parse', 'HEAD^{tree}'),
    sourcePath: 'platforms/geti/upstream', applicationPath: 'application', libraryPath: 'library',
    license: { path: 'LICENSE', spdx: 'Apache-2.0', sha256: crypto.createHash('sha256').update('Fixture Apache-2.0 source license\n').digest('hex') },
    expectedFiles: { total: 6, 'application/backend': 2, 'application/ui': 2, library: 1 },
    requiredPaths: ['LICENSE', 'application/backend/pyproject.toml', 'application/ui/package.json', 'library/src/getitune/engine/engine.py'],
    lifecycleOwners: { data: ['application/backend/app/api/routers/projects.py'], annotation: ['application/ui/src/modules/annotator'], training: ['library/src/getitune/engine'], evaluation: ['library/src/getitune/engine'], models: ['application/backend/app/api/routers/projects.py'], inference: ['library/src/getitune/engine'] },
    toolchains: { backendPython: '>=3.14,<3.15', uiReact: '~19.1.1', uiNode: '>=v24.2.0', uiNpm: '>=11.14.0', separateEnvironmentRequired: true },
    qualification: { runtimeVerified: false, productIntegrationVerified: false },
  };
  const saveLock = () => write(root, 'platforms/geti/upstream.lock.json', JSON.stringify(lock, null, 2));
  saveLock();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, source, lock, saveLock };
}

test('a complete pinned checkout verifies only source, never runtime or product integration', t => {
  const f = fixture(t);
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, true, JSON.stringify(result.errors));
  assert.equal(result.status, 'source_verified');
  assert.equal(result.runtimeVerified, false);
  assert.equal(result.productIntegrationVerified, false);
  assert.equal(result.observed.commit, f.lock.commit);
  assert.equal(result.observed.files.total, 6);
  assert.deepEqual(result.errors, []);
});

test('a missing checkout is refused without installing or creating it', t => {
  const f = fixture(t);
  const missing = path.join(f.root, 'missing-source');
  const result = guard().verifyUpstream(f.root, { source: missing });
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /source.*missing|source.*exist/i);
  assert.equal(fs.existsSync(missing), false);
});

test('a newer clean commit is refused instead of accepting a moving branch', t => {
  const f = fixture(t);
  write(f.source, 'new-file.txt', 'Unexpected moving head\n');
  git(f.source, 'add', '.');
  git(f.source, '-c', 'core.hooksPath=' + os.devNull, 'commit', '-qm', 'Unexpected head');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /commit.*differ|commit.*mismatch/i);
});

test('an unstaged tracked edit is refused even when commit and origin still match', t => {
  const f = fixture(t);
  write(f.source, 'library/src/getitune/engine/engine.py', '# changed source\n');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /tracked.*dirty|tracked.*change/i);
});

test('a staged tracked edit is refused', t => {
  const f = fixture(t);
  write(f.source, 'application/backend/app/api/routers/projects.py', '# staged change\n');
  git(f.source, 'add', '.');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /tracked.*dirty|tracked.*change/i);
});

test('ignored local runtime output does not claim a product source change', t => {
  const f = fixture(t);
  write(f.source, '.git/info/exclude', 'owned-runtime/\n');
  write(f.source, 'owned-runtime/run.log', 'Local output\n');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, true, JSON.stringify(result.errors));
  assert.equal(result.runtimeVerified, false);
});

test('wrong origin and tree identities are refused independently of head', t => {
  const f = fixture(t);
  git(f.source, 'remote', 'set-url', 'origin', 'https://example.invalid/other.git');
  f.lock.tree = '0'.repeat(40); f.saveLock();
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /origin.*differ|origin.*mismatch/i);
  assert.match(result.errors.join('\n'), /tree.*differ|tree.*mismatch/i);
});

test('incomplete source ownership and license bytes are refused', t => {
  const f = fixture(t);
  f.lock.expectedFiles.library = 2;
  f.lock.license.sha256 = '0'.repeat(64);
  f.lock.requiredPaths.push('application/backend/app/missing.py');
  f.saveLock();
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /count.*library|library.*count/i);
  assert.match(result.errors.join('\n'), /license.*hash|license.*sha256/i);
  assert.match(result.errors.join('\n'), /missing.*required|required.*missing/i);
});

test('toolchain declarations must agree with the source manifest', t => {
  const f = fixture(t);
  f.lock.toolchains.backendPython = '>=3.13';
  f.lock.toolchains.uiReact = '~18.3.1';
  f.saveLock();
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /python.*differ|python.*mismatch/i);
  assert.match(result.errors.join('\n'), /react.*differ|react.*mismatch/i);
});

test('a source outside the owning worktree is refused', t => {
  const f = fixture(t);
  const outside = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'mv-geti-other-')));
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));
  const result = guard().verifyUpstream(f.root, { source: outside });
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /source.*outside|source.*escape/i);
});

test('a linked source cannot make a different checkout appear to belong to the worktree', t => {
  const f = fixture(t);
  const linked = path.join(f.root, 'linked-source');
  try { fs.symlinkSync(f.source, linked, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) { t.skip('Directory links unavailable'); return; } throw error; }
  const result = guard().verifyUpstream(f.root, { source: linked });
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /source.*link/i);
});

test('manifest path traversal is refused before reading an outside file', t => {
  const f = fixture(t);
  f.lock.license.path = '../outside-license'; f.saveLock();
  assert.throws(() => guard().readUpstreamLock(f.root), /relative|escape|path/i);
});

test('a lock cannot mark source checks as completed runtime integration', t => {
  const f = fixture(t);
  f.lock.qualification.runtimeVerified = true; f.saveLock();
  assert.throws(() => guard().readUpstreamLock(f.root), /qualification|runtime/i);
});

test('git source checks do not execute a checkout-configured filesystem monitor', t => {
  const f = fixture(t);
  const marker = path.join(f.root, 'unexpected-execution');
  const monitor = path.join(f.root, 'source-monitor.cjs');
  write(f.root, 'source-monitor.cjs', `#!${process.execPath}\nrequire('node:fs').writeFileSync(${JSON.stringify(marker)}, 'executed');\n`);
  fs.chmodSync(monitor, 0o755);
  git(f.source, 'config', 'core.fsmonitor', monitor);
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, true, JSON.stringify(result.errors));
  assert.equal(fs.existsSync(marker), false);
});

test('source inspection does not execute a checkout-configured clean filter', t => {
  const f = fixture(t);
  write(f.source, '.gitattributes', 'library/src/getitune/engine/engine.py filter=sourceguard-test\n');
  git(f.source, 'add', '.gitattributes');
  git(f.source, '-c', 'core.hooksPath=' + os.devNull, 'commit', '-qm', 'Source attributes fixture');
  f.lock.commit = git(f.source, 'rev-parse', 'HEAD');
  f.lock.tree = git(f.source, 'rev-parse', 'HEAD^{tree}');
  f.lock.expectedFiles.total = 7; f.saveLock();
  const marker = path.join(f.root, 'unexpected-filter-execution');
  const filter = path.join(f.root, 'source-filter.cjs');
  write(f.root, 'source-filter.cjs', `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'executed'); process.stdout.write(require('node:fs').readFileSync(0));\n`);
  git(f.source, 'config', 'filter.sourceguard-test.clean', `${JSON.stringify(process.execPath)} ${JSON.stringify(filter)}`);
  write(f.source, 'library/src/getitune/engine/engine.py', '# dirty fixture engine\n');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.equal(fs.existsSync(marker), false, 'Checking source must not execute a configured clean filter');
});

test('skip-worktree and assume-unchanged cannot hide tracked source changes', t => {
  const f = fixture(t);
  git(f.source, 'update-index', '--assume-unchanged', 'library/src/getitune/engine/engine.py');
  write(f.source, 'library/src/getitune/engine/engine.py', '# hidden change\n');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /index.*flag|assume|skip.worktree/i);
});

test('Git replacement objects cannot hide a different tracked source tree behind the pinned IDs', t => {
  const f = fixture(t);
  write(f.source, 'library/src/getitune/engine/engine.py', '# replaced fixture engine\n');
  git(f.source, 'add', 'library/src/getitune/engine/engine.py');
  const replacement = git(f.source, 'write-tree');
  git(f.source, 'replace', f.lock.tree, replacement);
  assert.equal(git(f.source, 'rev-parse', 'HEAD'), f.lock.commit, 'The replaced source keeps the pinned commit identity');
  const result = guard().verifyUpstream(f.root);
  assert.equal(result.valid, false, 'Object replacement must not make modified source verify as the pinned checkout');
  assert.equal(result.observed.trackedClean, false);
});

test('check --json prints a source-only receipt and returns a nonzero code on invalid input', t => {
  const f = fixture(t);
  guard();
  const good = spawnSync(process.execPath, [SCRIPT, 'check', '--json', '--root', f.root, '--source', f.source], { encoding: 'utf8', env: gitEnv });
  assert.equal(good.status, 0, good.stderr);
  assert.equal(JSON.parse(good.stdout).status, 'source_verified');
  const bad = spawnSync(process.execPath, [SCRIPT, 'check', '--json', '--root', f.root, '--source', path.join(f.root, 'missing')], { encoding: 'utf8', env: gitEnv });
  assert.equal(bad.status, 1);
  assert.equal(JSON.parse(bad.stdout).runtimeVerified, false);
  const argumentsError = spawnSync(process.execPath, [SCRIPT, 'install', '--json'], { encoding: 'utf8', env: gitEnv });
  assert.equal(argumentsError.status, 2);
  assert.equal(JSON.parse(argumentsError.stdout).valid, false);
});
