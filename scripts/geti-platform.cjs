#!/usr/bin/env node
'use strict';

// M0 only: inspect a pinned checkout. Never install or execute upstream code.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

const DEFAULT_ROOT = path.resolve(__dirname, '..');
const LOCK_PATH = 'platforms/geti/upstream.lock.json';
const LIFECYCLE = ['data', 'annotation', 'training', 'evaluation', 'models', 'inference'];

function relativePath(value, name) {
  if (typeof value !== 'string' || !value || value.includes('\\') || value.includes('\0') || path.posix.isAbsolute(value)
      || value.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error(`${name} must be a contained relative path`);
  }
  return value;
}

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

function noLinkedAncestors(root, target, label) {
  if (!contained(root, target)) throw new Error(`${label} is outside the owning worktree`);
  let current = root;
  for (const part of path.relative(root, target).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error(`${label} cannot use a linked path`);
  }
  if (!contained(root, fs.realpathSync(target))) throw new Error(`${label} escapes the owning worktree`);
}

function readFile(root, relative, label) {
  const target = path.join(root, relativePath(relative, label));
  noLinkedAncestors(root, target, label);
  const stat = fs.statSync(target);
  if (!stat.isFile() || stat.size > 2 * 1024 * 1024) throw new Error(`${label} must be a regular file of at most 2 MiB`);
  return fs.readFileSync(target);
}

function readUpstreamLock(root = DEFAULT_ROOT) {
  const canonicalRoot = fs.realpathSync(root);
  const lock = JSON.parse(readFile(canonicalRoot, LOCK_PATH, 'Source manifest').toString('utf8'));
  if (lock.schemaVersion !== 1 || typeof lock.repository !== 'string' || !/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\.git$/.test(lock.repository)
      || typeof lock.tag !== 'string' || !lock.tag || !/^[a-f0-9]{40}$/.test(lock.commit) || !/^[a-f0-9]{40}$/.test(lock.tree)) {
    throw new Error('Invalid pinned source identity in manifest');
  }
  for (const key of ['sourcePath', 'applicationPath', 'libraryPath']) relativePath(lock[key], key);
  if (!lock.license || lock.license.spdx !== 'Apache-2.0' || !/^[a-f0-9]{64}$/.test(lock.license.sha256)) throw new Error('Invalid source license manifest');
  relativePath(lock.license.path, 'License path');
  if (!lock.expectedFiles || !Number.isSafeInteger(lock.expectedFiles.total) || lock.expectedFiles.total < 1) throw new Error('Invalid full source file count');
  for (const [owner, count] of Object.entries(lock.expectedFiles)) {
    if (owner !== 'total') relativePath(owner, 'Source owner');
    if (!Number.isSafeInteger(count) || count < 1) throw new Error('Invalid source owner file count');
  }
  if (!Array.isArray(lock.requiredPaths) || lock.requiredPaths.length < 1) throw new Error('Missing required source paths');
  for (const item of lock.requiredPaths) relativePath(item, 'Required source path');
  for (const owner of LIFECYCLE) {
    const paths = lock.lifecycleOwners?.[owner];
    if (!Array.isArray(paths) || !paths.length) throw new Error(`Missing lifecycle source owner: ${owner}`);
    for (const item of paths) relativePath(item, `Lifecycle ${owner} path`);
  }
  for (const key of ['backendPython', 'uiReact', 'uiNode', 'uiNpm']) {
    if (typeof lock.toolchains?.[key] !== 'string' || !lock.toolchains[key]) throw new Error(`Missing toolchain declaration: ${key}`);
  }
  if (lock.toolchains.separateEnvironmentRequired !== true) throw new Error('Separate platform toolchain environments are required');
  if (lock.qualification?.runtimeVerified !== false || lock.qualification?.productIntegrationVerified !== false) {
    throw new Error('Source qualification cannot claim verified runtime or product integration');
  }
  return lock;
}

function git(source, args) {
  // Built-in read commands only. Ignore caller Git injection and source hooks /
  // filesystem monitors; neither the upstream nor user Git config may run code.
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: os.devNull, GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', GIT_NO_REPLACE_OBJECTS: '1' });
  return execFileSync('git', ['--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devNull,
    '-c', 'core.untrackedCache=false', '-C', source, ...args], {
    encoding: 'utf8', env, stdio: ['ignore', 'pipe', 'pipe'], timeout: 15000, maxBuffer: 8 * 1024 * 1024,
  });
}

function trackedCheckoutClean(source, treeRows) {
  // `git status` may run a configured clean filter. Compare Git object IDs to
  // native file bytes instead; symlinks are hashed as link text without opening
  // their targets. This also ignores misleading Git stat-cache shortcuts.
  const indexRows = git(source, ['ls-files', '--stage', '-z']).split('\0').filter(Boolean);
  const expectedIndex = treeRows.map(row => `${row.mode} ${row.oid} 0\t${row.name}`);
  let clean = indexRows.length === expectedIndex.length && indexRows.every((row, i) => row === expectedIndex[i]);
  for (const row of treeRows) {
    const name = relativePath(row.name, 'Tracked source path');
    const filename = path.join(source, name);
    noLinkedAncestors(source, path.dirname(filename), 'Tracked source directory');
    if (!fs.existsSync(filename) && !fs.lstatSync(filename, { throwIfNoEntry: false })) { clean = false; continue; }
    const stat = fs.lstatSync(filename);
    let contents;
    if (row.mode === '120000' && stat.isSymbolicLink()) contents = Buffer.from(fs.readlinkSync(filename), 'utf8');
    else if (['100644', '100755'].includes(row.mode) && stat.isFile() && !stat.isSymbolicLink()) {
      contents = fs.readFileSync(filename);
      if (process.platform !== 'win32' && Boolean(stat.mode & 0o111) !== (row.mode === '100755')) clean = false;
    } else { clean = false; continue; }
    const oid = crypto.createHash('sha1').update(Buffer.from(`blob ${contents.length}\0`)).update(contents).digest('hex');
    if (oid !== row.oid) clean = false;
  }
  return clean;
}

function verifyUpstream(root = DEFAULT_ROOT, options = {}) {
  const result = { schemaVersion: 1, valid: false, status: 'source_unverified', runtimeVerified: false,
    productIntegrationVerified: false, errors: [], observed: {} };
  try {
    const canonicalRoot = fs.realpathSync(root);
    const lock = readUpstreamLock(canonicalRoot);
    const source = options.source === undefined ? path.join(canonicalRoot, lock.sourcePath) : options.source;
    if (typeof source !== 'string' || !path.isAbsolute(source)) throw new Error('Source override must be an absolute path');
    const resolvedSource = path.resolve(source);
    if (!contained(canonicalRoot, resolvedSource)) throw new Error('Source is outside the owning worktree');
    if (!fs.existsSync(resolvedSource)) throw new Error('Source checkout is missing; initialize the pinned source explicitly');
    noLinkedAncestors(canonicalRoot, resolvedSource, 'Source checkout');
    if (!fs.statSync(resolvedSource).isDirectory()) throw new Error('Source checkout must be a directory');
    const sourceRoot = fs.realpathSync(git(resolvedSource, ['rev-parse', '--show-toplevel']).trim());
    if (sourceRoot !== fs.realpathSync(resolvedSource)) throw new Error('Source must be its own Git checkout');
    result.observed.sourcePath = path.relative(canonicalRoot, resolvedSource).split(path.sep).join('/');
    const commit = git(resolvedSource, ['rev-parse', 'HEAD']).trim();
    const tree = git(resolvedSource, ['rev-parse', 'HEAD^{tree}']).trim();
    const origin = git(resolvedSource, ['config', '--get', 'remote.origin.url']).trim();
    Object.assign(result.observed, { commit, tree, origin });
    if (commit !== lock.commit) result.errors.push('Source commit differs from the pinned manifest');
    if (tree !== lock.tree) result.errors.push('Source tree differs from the pinned manifest');
    if (origin !== lock.repository) result.errors.push('Source origin differs from the pinned manifest');
    const hidden = git(resolvedSource, ['ls-files', '-v', '-z']).split('\0').filter(Boolean).filter(row => row[0] !== 'H');
    if (hidden.length) result.errors.push('Source index flags can hide changes (assume-unchanged or skip-worktree)');
    const treeRows = git(resolvedSource, ['ls-tree', '-r', '-z', 'HEAD']).split('\0').filter(Boolean).map(row => {
      const match = row.match(/^(100644|100755|120000) blob ([a-f0-9]{40})\t([\s\S]+)$/);
      if (!match) throw new Error('Unsupported tracked source object');
      return { mode: match[1], oid: match[2], name: match[3] };
    });
    result.observed.trackedClean = trackedCheckoutClean(resolvedSource, treeRows);
    if (!result.observed.trackedClean) result.errors.push('Tracked source is dirty');
    const files = treeRows.map(row => row.name);
    const observedFiles = { total: files.length };
    for (const owner of Object.keys(lock.expectedFiles)) {
      if (owner !== 'total') observedFiles[owner] = files.filter(file => file.startsWith(owner + '/')).length;
      if (observedFiles[owner] !== lock.expectedFiles[owner]) result.errors.push(`Source file count differs for ${owner}`);
    }
    result.observed.files = observedFiles;
    const hasPath = item => files.some(file => file === item || file.startsWith(item + '/'));
    for (const required of lock.requiredPaths) {
      if (!hasPath(required)) result.errors.push(`Missing required source path: ${required}`);
    }
    for (const [owner, paths] of Object.entries(lock.lifecycleOwners)) {
      for (const item of paths) if (!hasPath(item)) result.errors.push(`Missing lifecycle source owner ${owner}: ${item}`);
    }
    const licenseHash = crypto.createHash('sha256').update(readFile(resolvedSource, lock.license.path, 'License')).digest('hex');
    result.observed.licenseSha256 = licenseHash;
    if (licenseHash !== lock.license.sha256) result.errors.push('Source license SHA256 hash differs');
    const python = readFile(resolvedSource, lock.applicationPath + '/backend/pyproject.toml', 'Backend metadata').toString('utf8').match(/^requires-python\s*=\s*["']([^"']+)["']/m)?.[1];
    const ui = JSON.parse(readFile(resolvedSource, lock.applicationPath + '/ui/package.json', 'UI metadata').toString('utf8'));
    const declared = { backendPython: python, uiReact: ui.dependencies?.react, uiNode: ui.engines?.node, uiNpm: ui.engines?.npm };
    result.observed.toolchains = declared;
    for (const key of ['backendPython', 'uiReact', 'uiNode', 'uiNpm']) {
      if (declared[key] !== lock.toolchains[key]) result.errors.push(`${key} declaration differs from the source manifest`);
    }
    result.valid = result.errors.length === 0;
    if (result.valid) result.status = 'source_verified';
  } catch (error) {
    // Do not include child-process output: a source config must not inject logs
    // or user configuration into a public source-verification receipt.
    result.errors.push(error.code === 'ENOENT' ? 'Source or required source manifest path is missing' :
      error.cmd || error.status !== undefined ? 'Git source inspection failed' : error.message);
  }
  return result;
}

function main(args) {
  let json = args.includes('--json');
  try {
    if (args.length === 1 && args[0] === '--help') {
      process.stdout.write('Usage: node scripts/geti-platform.cjs check [--json] [--source ABSOLUTE_PATH] [--root ABSOLUTE_PATH]\nRead-only source identity check. Does not install or start Geti.\n');
      return 0;
    }
    if (args[0] !== 'check') throw new Error('Only the read-only check command is supported');
    let root = DEFAULT_ROOT, source;
    const seen = new Set();
    for (let i = 1; i < args.length; i++) {
      const option = args[i];
      if (seen.has(option) || !['--json', '--source', '--root'].includes(option)) throw new Error('Unknown or duplicate check option');
      seen.add(option);
      if (option === '--json') { json = true; continue; }
      const value = args[++i];
      if (!value || !path.isAbsolute(value)) throw new Error(`${option} requires an absolute path`);
      if (option === '--root') root = value; else source = value;
    }
    const result = verifyUpstream(root, source === undefined ? {} : { source });
    process.stdout.write(json ? JSON.stringify(result, null, 2) + '\n' : `${result.status}\n${result.errors.join('\n')}${result.errors.length ? '\n' : ''}`);
    return result.valid ? 0 : 1;
  } catch (error) {
    const result = { valid: false, status: 'invalid_arguments', runtimeVerified: false, productIntegrationVerified: false, errors: [error.message] };
    (json ? process.stdout : process.stderr).write(json ? JSON.stringify(result) + '\n' : error.message + '\n');
    return 2;
  }
}

module.exports = { readUpstreamLock, verifyUpstream };
if (require.main === module) process.exitCode = main(process.argv.slice(2));
