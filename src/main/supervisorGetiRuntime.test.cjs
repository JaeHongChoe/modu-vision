const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const http = require('node:http'), {EventEmitter} = require('node:events');
const {PassThrough} = require('node:stream'), ts = require('typescript');

const source = path.join(__dirname, 'supervisor.ts');
const compiled = ts.transpileModule(fs.readFileSync(source, 'utf8'), {compilerOptions:{
  module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2022, esModuleInterop:true,
}}).outputText;
const resources = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-supervisor-geti-'));
test.after(() => fs.rmSync(resources, {recursive:true, force:true}));

async function nativeBoot({isPackaged, resourcesPath, inherited}) {
  let requests = 0, spawns = 0, binds = 0, artifacts = 0, refusals = 0, spawnOptions;
  const signals = [], events = [];
  const server = http.createServer((request, response) => {
    requests++; assert.equal(request.url, '/health');
    response.writeHead(200, {'Content-Type':'application/json'});
    response.end(JSON.stringify({status:'ok', version:'controlled', device:'cpu', device_name:'controlled'}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const child = new EventEmitter();
  child.pid = 'modeled-child-no-real-process'; child.exitCode = child.signalCode = null;
  child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.stdio = [child.stdin, child.stdout, child.stderr, new PassThrough()];
  let exitScheduled = false;
  function exit() {
    if (exitScheduled) return; exitScheduled = true;
    setImmediate(() => { child.exitCode = 0; child.emit('exit', 0, null); });
  }
  child.stdin.once('finish', exit);
  child.kill = signal => { signals.push(signal); exit(); return true; };
  const executable = path.join(resources, 'controlled-owned-backend');
  const artifact = {sha256:'a'.repeat(64), build:'b'.repeat(64)};
  let release, rejectBootstrap;
  const bootstrap = new Promise((resolve, reject) => { release = resolve; rejectBootstrap = reject; });
  bootstrap.catch(() => {}); // Teardown can refuse before the original bind was reached.
  const owned = {
    projects:path.join(resources, 'projects'), auth:path.join(resources, 'auth'),
    executable(file) { artifacts++; assert.equal(file, executable); return artifact; },
    backendEnvironment(env) { return {...env, VISION_APPLICATION_BACKEND_FD:'3'}; },
    bindBackend(proc, file, build) {
      assert.equal(proc, child); assert.equal(file, executable); assert.equal(build, artifact.build);
      binds++; events.push('bootstrap-pending'); return bootstrap;
    },
    refuse() { refusals++; },
  };
  const environment = inherited === undefined ? {} : {MODU_GETI_RUNTIME_DESCRIPTOR:inherited};
  const runtimeProcess = Object.create(process);
  Object.defineProperties(runtimeProcess, {env:{value:environment}, resourcesPath:{value:resourcesPath}});
  const loaded = {exports:{}};
  const fixtureRequire = name => {
    if (name === 'electron') return {app:{isPackaged, getPath:() => resources, getVersion:() => 'controlled'}};
    if (name === './applicationLaunch') return {OwnedApplicationLaunch:class {}};
    if (name === 'child_process') return {...require('node:child_process'), spawn(file, args, options) {
      assert.equal(file, executable); assert.ok(args.includes('--shared-auth-dir'));
      assert.deepEqual(options.stdio, ['pipe','pipe','pipe','pipe']);
      assert.equal(options.env.VISION_APPLICATION_BACKEND_FD, '3');
      spawns++; spawnOptions = options;
      setImmediate(() => child.stdout.write('VISION_AI_STUDIO_PORT=' + port + '\n'));
      return child;
    }};
    return require(name);
  };
  new Function('require','module','exports','__filename','__dirname','process',compiled)(
    fixtureRequire, loaded, loaded.exports, source, __dirname, runtimeProcess);
  const {BackendSupervisor} = loaded.exports;
  BackendSupervisor.prototype.registerProcessHooks = () => {}; // No real child or global process hook.
  const supervisor = new BackendSupervisor({ownedApplicationLaunch:owned, autoRestart:false});
  supervisor.resolveStandaloneBinary = () => executable;
  supervisor.getAppRoot = () => resources;
  supervisor.getBackendWorkingDirectory = () => resources;
  let portObserved;
  const discovered = new Promise(resolve => { portObserved = resolve; });
  const originalDiscovery = supervisor.discoverPort.bind(supervisor);
  supervisor.discoverPort = async (proc, budget) => {
    assert.equal(budget, isPackaged ? 180000 : 15000);
    const result = await originalDiscovery(proc, budget); portObserved(); return result;
  };
  const originalHealth = supervisor.pollHealth.bind(supervisor);
  supervisor.pollHealth = (...args) => {
    assert.equal(args[2], 15000); events.push('health-start'); return originalHealth(...args);
  };
  const startup = supervisor.startBackend().then(value => ({value}), error => ({error}));
  try {
    await Promise.race([discovered, startup.then(result => {
      if (result.error) throw result.error;
      throw Error('Startup settled before original discovery observation');
    })]);
    assert.equal(artifacts, 1); assert.equal(spawns, 1); assert.equal(binds, 1);
    assert.equal(requests, 0); assert.equal(supervisor.isHealthy(), false);
    assert.deepEqual(events, ['bootstrap-pending']);
    events.push('bootstrap-resolved'); release();
    const result = await startup;
    assert.equal(result.error, undefined); assert.equal(result.value, port);
    assert.equal(requests, 1); assert.ok(events.indexOf('bootstrap-resolved') < events.indexOf('health-start'));
    assert.deepEqual(supervisor.getStatusInfo().runtimeIdentity, {mode:'frozen',build_identity_sha256:artifact.build,executable_sha256:artifact.sha256});
    assert.equal(refusals, 0); assert.equal(spawnOptions.env.PYTHONDONTWRITEBYTECODE, '1');
    return spawnOptions.env;
  } finally {
    rejectBootstrap(new Error('Controlled fixture teardown'));
    await startup;
    await supervisor.stopBackend();
    child.stdio.forEach(stream => stream.destroy());
    await new Promise(resolve => server.close(resolve));
    assert.ok(!signals.includes('SIGKILL'));
  }
}

for (const [label, resourcesPath] of [['undefined',undefined], ['empty',''], ['relative','relative-resources']]) {
  test('packaged native boot without ' + label + ' Electron resources does not inject Geti', {timeout:10000}, async () => {
    const env = await nativeBoot({isPackaged:true, resourcesPath});
    assert.equal(Object.hasOwn(env, 'MODU_GETI_RUNTIME_DESCRIPTOR'), false);
  });
}

test('packaged absolute resources override inherited Geti descriptor even when the manifest is missing', {timeout:10000}, async () => {
  const expected = path.join(resources, 'geti-platform', 'runtime.json');
  assert.equal(fs.existsSync(expected), false);
  const env = await nativeBoot({isPackaged:true, resourcesPath:resources + path.sep, inherited:'explicit-inherited-runtime'});
  assert.equal(env.MODU_GETI_RUNTIME_DESCRIPTOR, expected);
});

test('nonpackaged boot never automatically selects an Electron Geti descriptor', {timeout:10000}, async () => {
  const env = await nativeBoot({isPackaged:false, resourcesPath:resources});
  assert.equal(Object.hasOwn(env, 'MODU_GETI_RUNTIME_DESCRIPTOR'), false);
});

test('nonpackaged boot preserves the explicit runtime descriptor environment', {timeout:10000}, async () => {
  const inherited = path.join(resources, 'explicit-runtime.json');
  const env = await nativeBoot({isPackaged:false, resourcesPath:undefined, inherited});
  assert.equal(env.MODU_GETI_RUNTIME_DESCRIPTOR, inherited);
});
