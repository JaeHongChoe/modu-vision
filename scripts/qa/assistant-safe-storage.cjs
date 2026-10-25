'use strict';
// Real Electron safeStorage QA. This loads only the key store and provider value
// helpers, never the application, backend, renderer, or a provider client call.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const Module = require('node:module');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const PHASES = ['save', 'read-after-restart', 'remove', 'confirm-after-restart'];
const DUMMY = 'modu-vision-non-secret-safe-storage-qa-0001';
const PROVIDER = 'openai-compatible';
const REPO_ROOT = path.resolve(__dirname, '../..');
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const writeJson = (file, value) => fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', {mode: 0o600});

function prepareRun({outDir, repoRoot = REPO_ROOT}) {
  outDir = path.resolve(outDir);
  // Never reuse, clear, or overwrite an existing profile, including a symlink.
  if (fs.existsSync(outDir) || (() => {try {fs.lstatSync(outDir);return true;} catch {return false;}})())
    throw new Error('QA output already exists: ' + outDir);
  fs.mkdirSync(outDir, {recursive: true, mode: 0o700});
  const appName = 'modu-vision-safe-storage-qa-' + crypto.randomUUID();
  const run = {outDir, repoRoot: fs.realpathSync(repoRoot), appName,
    appDir: path.join(outDir, 'app'), userData: path.join(outDir, 'userData'),
    sessionData: path.join(outDir, 'sessionData'), logs: path.join(outDir, 'logs'),
    crashDumps: path.join(outDir, 'crashDumps')};
  for (const name of ['appDir', 'userData', 'sessionData', 'logs', 'crashDumps']) fs.mkdirSync(run[name], {mode: 0o700});
  writeJson(path.join(run.appDir, 'package.json'), {name: appName, productName: appName, version: '0.0.0', main: 'main.cjs'});
  fs.writeFileSync(path.join(run.appDir, 'main.cjs'),
    `'use strict';\nrequire(${JSON.stringify(__filename)}).runElectronPhase();\n`, {mode: 0o600});
  writeJson(path.join(outDir, 'run.json'), {...run, schema_version: 1, phases: PHASES,
    mode: 'real-os-safeStorage', keychainService: appName + ' Safe Storage', keychainAccount: appName,
    sourceHashes: Object.fromEntries(['keyStore.ts', 'providers.ts'].map(name => [name, hash(path.join(run.repoRoot, 'src/main/assistant', name))]))});
  return run;
}

function phaseLaunchOptions(run, phase, electronPath, baseEnv = process.env) {
  if (!PHASES.includes(phase)) throw new Error('Unknown phase: ' + phase);
  // Preserve the native OS session for Keychain, but inherit no API credentials,
  // Node startup hooks, application store paths, or Electron/mock switches.
  const env = {};
  for (const [key, value] of Object.entries(baseEnv)) {
    if (/^(HOME|USERPROFILE|USER|LOGNAME|PATH|TMPDIR|TEMP|TMP|LANG|LC_.*|SHELL|SYSTEMROOT|WINDIR|COMSPEC|DISPLAY|WAYLAND_DISPLAY|DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR|XAUTHORITY|__CF_USER_TEXT_ENCODING|SECURITYSESSIONID)$/i.test(key)) env[key] = value;
  }
  env.MV_SAFE_STORAGE_QA_ROOT = run.outDir;
  env.MV_SAFE_STORAGE_QA_PHASE = phase;
  return {command: electronPath, args: ['--disable-background-networking', '--disable-component-update',
    '--disable-domain-reliability', '--dns-prefetch-disable', '--host-resolver-rules=MAP * ~NOTFOUND', run.appDir], cwd: run.appDir, env};
}

function loadTypeScript(file, repoRoot, cache = new Map()) {
  if (cache.has(file)) return cache.get(file).exports;
  const ts = require(path.join(repoRoot, 'node_modules/typescript'));
  const loaded = new Module(file, module);
  loaded.filename = file;
  loaded.paths = Module._nodeModulePaths(path.dirname(file));
  cache.set(file, loaded);
  const original = loaded.require.bind(loaded);
  loaded.require = name => {
    const local = name.startsWith('.') ? path.resolve(path.dirname(file), name) + '.ts' : null;
    return local && fs.existsSync(local) ? loadTypeScript(local, repoRoot, cache) : original(name);
  };
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  }}).outputText, file);
  return loaded.exports;
}

async function runElectronPhase() {
  const {app, safeStorage, session} = require('electron');
  const root = process.env.MV_SAFE_STORAGE_QA_ROOT;
  const phase = process.env.MV_SAFE_STORAGE_QA_PHASE;
  let run;
  try {
    run = JSON.parse(fs.readFileSync(path.join(root, 'run.json'), 'utf8'));
    assert.ok(PHASES.includes(phase), 'known QA phase');
    assert.equal(fs.realpathSync(root), fs.realpathSync(run.outDir));
    assert.equal(app.getAppPath(), run.appDir);
    assert.equal(app.getName(), run.appName, 'unique app name comes from package.json before OS storage initialization');
    assert.ok(!process.argv.some(arg => /mock-keychain|password-store|remote-debugging/.test(arg)), 'no mocked storage or remote debugger');
    app.setName(run.appName);
    for (const name of ['userData', 'sessionData', 'logs', 'crashDumps']) app.setPath(name, run[name]);
    const progress = stage => writeJson(path.join(root, phase + '-progress.json'), {phase, stage, pid: process.pid, time: new Date().toISOString()});
    progress('waiting-for-ready');
    await app.whenReady();
    // No BrowserWindow is created. Reject both Node and Chromium HTTP paths.
    let blockedNetworkRequests = 0;
    const deny = () => {blockedNetworkRequests += 1;throw new Error('Network is disabled in safeStorage QA');};
    globalThis.fetch = deny;
    for (const mod of ['node:http', 'node:https']) {
      const client = require(mod);client.request = deny;client.get = deny;
    }
    session.defaultSession.webRequest.onBeforeRequest((_request, callback) => {blockedNetworkRequests += 1;callback({cancel: true});});
    const {AssistantKeyStore, electronEncryptor} = loadTypeScript(path.join(run.repoRoot, 'src/main/assistant/keyStore.ts'), run.repoRoot);
    const store = new AssistantKeyStore(path.join(run.userData, 'assistant'), electronEncryptor(safeStorage));
    progress('checking-real-os-storage');
    assert.equal(app.getPath('userData'), run.userData);
    const initial = store.state();
    assert.equal(initial.encryptionAvailable, true, 'real OS protected storage must be available');
    progress('executing-' + phase);
    let state, fileBeforeSha256 = fs.existsSync(store.file) ? hash(store.file) : null;
    if (phase === 'save') {
      assert.equal(fileBeforeSha256, null, 'fresh profile contains no provider file');
      state = store.save({provider: PROVIDER, baseUrl: 'https://safe-storage-qa.invalid/v1', model: 'non-secret-qa',
        supportsImages: false, key: DUMMY});
      assert.equal(state.providers[0].hasKey, true);
    } else if (phase === 'read-after-restart') {
      assert.equal(initial.providers[0].hasKey, true);
      assert.equal(store.key(PROVIDER), DUMMY, 'a new Electron process decrypts the saved dummy');
      assert.equal(hash(store.file), fileBeforeSha256, 'reading does not rewrite the provider file');
      state = store.state();
    } else if (phase === 'remove') {
      assert.equal(initial.providers[0].hasKey, true);
      state = store.removeKey(PROVIDER);
      assert.equal(store.key(PROVIDER), null);
      assert.equal(state.providers[0].hasKey, false);
    } else {
      state = store.state();
      assert.equal(state.providers[0].hasKey, false);
      assert.equal(store.key(PROVIDER), null, 'another new process confirms removal');
    }
    const raw = fs.readFileSync(store.file, 'utf8');
    const stored = JSON.parse(raw).providers[PROVIDER];
    assert.ok(!raw.includes(DUMMY), 'no dummy plaintext on disk');
    assert.ok(!JSON.stringify(state).includes(DUMMY), 'no dummy in public state');
    assert.ok(!Object.hasOwn(state.providers[0], 'key'), 'public state contains no key property');
    if (phase === 'remove' || phase === 'confirm-after-restart') assert.ok(!Object.hasOwn(stored, 'key'), 'stored key field removed');
    else assert.ok(stored.key && Buffer.from(stored.key, 'base64').length > 3, 'encrypted bytes persisted');
    const fileMode = fs.statSync(store.file).mode & 0o777, directoryMode = fs.statSync(path.dirname(store.file)).mode & 0o777;
    if (process.platform !== 'win32') {assert.equal(fileMode, 0o600);assert.equal(directoryMode, 0o700);}
    assert.deepEqual(fs.readdirSync(path.dirname(store.file)), ['providers.json']);
    assert.equal(blockedNetworkRequests, 0, 'no provider or other network request was attempted');
    writeJson(path.join(root, phase + '-result.json'), {outcome: 'passed', phase, pid: process.pid,
      platform: process.platform, versions: process.versions, appName: app.getName(), userData: app.getPath('userData'),
      encryptionAvailable: true, hasKey: state.providers[0].hasKey, dummyMatched: phase === 'read-after-restart',
      fileBeforeSha256, fileAfterSha256: hash(store.file), fileMode: fileMode.toString(8), directoryMode: directoryMode.toString(8),
      plaintextOnDisk: false, blockedNetworkRequests, mockKeychain: false});
    progress('finished');
    app.exit(0);
  } catch (error) {
    if (run) writeJson(path.join(run.outDir, phase + '-result.json'), {outcome: 'failed', phase, message: error.message});
    process.stderr.write('safeStorage QA failed: ' + error.message + '\n');
    app.exit(1);
  }
}

function launchPhase(run, phase, electronPath) {
  const options = phaseLaunchOptions(run, phase, electronPath);
  return new Promise(resolve => {
    const logFile = path.join(run.logs, phase + '.log');
    const log = fs.openSync(logFile, 'wx', 0o600);
    const child = spawn(options.command, options.args, {cwd: options.cwd, env: options.env,
      detached: process.platform !== 'win32', stdio: ['ignore', log, log]});
    fs.closeSync(log);
    let timedOut = false, force;
    const signal = name => {try {process.kill(process.platform === 'win32' ? child.pid : -child.pid, name);} catch { /* already exited */ }};
    const deadline = setTimeout(() => {
      timedOut = true;signal('SIGTERM');
      force = setTimeout(() => signal('SIGKILL'), 2000);
    }, 20000);
    child.once('error', error => {clearTimeout(deadline);clearTimeout(force);resolve({phase, pid: child.pid, outcome: 'failed', message: error.message});});
    child.once('close', (code, signalName) => {
      clearTimeout(deadline);clearTimeout(force);
      const resultPath = path.join(run.outDir, phase + '-result.json');
      const result = fs.existsSync(resultPath) ? JSON.parse(fs.readFileSync(resultPath, 'utf8')) : null;
      resolve({phase, pid: child.pid, code, signal: signalName, timedOut,
        outcome: timedOut ? 'blocked-or-timeout' : code === 0 && result?.outcome === 'passed' ? 'passed' : 'failed', resultPath, logFile});
    });
  });
}

async function main(argv) {
  let outDir, prepareOnly = false;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--out-dir' && argv[i + 1]) outDir = argv[++i];
    else if (argv[i] === '--prepare') prepareOnly = true;
    else throw new Error('Usage: node scripts/qa/assistant-safe-storage.cjs [--prepare] [--out-dir NEW_DIRECTORY]');
  }
  const run = prepareRun({outDir: outDir || path.join(os.tmpdir(), 'modu-vision-safe-storage-qa-' + crypto.randomUUID())});
  if (prepareOnly) {console.log(JSON.stringify({outcome: 'prepared', outDir: run.outDir, appName: run.appName}));return;}
  const electronPath = require(path.join(run.repoRoot, 'node_modules/electron'));
  const results = [];
  for (const phase of PHASES) {
    console.log(JSON.stringify({phase, status: 'starting', outDir: run.outDir}));
    const result = await launchPhase(run, phase, electronPath);results.push(result);
    writeJson(path.join(run.outDir, 'summary.json'), {outcome: result.outcome === 'passed' && results.length === PHASES.length ? 'passed' : 'incomplete', mode: 'real-os-safeStorage', results});
    console.log(JSON.stringify(result));
    if (result.outcome !== 'passed') {process.exitCode = 1;break;}
  }
}

module.exports = {prepareRun, phaseLaunchOptions, runElectronPhase};
if (require.main === module) main(process.argv.slice(2)).catch(error => {console.error(error.message);process.exitCode = 1;});
