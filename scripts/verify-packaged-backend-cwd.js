const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const Module = require('node:module');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/main/supervisor.ts');
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
}).outputText;

const calls = [];
const electronApp = {
  isPackaged: false,
  getAppPath: () => '',
  getPath: () => '',
};
const sourceModule = new Module(sourcePath, module);
sourceModule.filename = sourcePath;
sourceModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
const originalRequire = sourceModule.require.bind(sourceModule);
sourceModule.require = (specifier) => {
  if (specifier === 'electron') return { app: electronApp };
  if (specifier === 'child_process') {
    return {
      ...originalRequire(specifier),
      spawn: (binary, args, options) => {
        calls.push({ binary, args, options });
        const child = new EventEmitter();
        child.pid = 12345;
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.killed = false;
        child.kill = (signal) => {
          child.killed = true;
          queueMicrotask(() => child.emit('exit', 0, signal));
          return true;
        };
        return child;
      },
    };
  }
  return originalRequire(specifier);
};
sourceModule._compile(compiled, sourcePath);
const { BackendSupervisor } = sourceModule.exports;
BackendSupervisor.prototype.registerProcessHooks = () => {};

async function inspectLaunch() {
  const supervisor = new BackendSupervisor({ autoRestart: false });
  supervisor.resolveStandaloneBinary = () => '/fake/backend';
  supervisor.discoverPort = async () => 45000;
  supervisor.pollHealth = async () => ({ status: 'ok', version: 'test', device: 'cpu', device_name: 'CPU' });
  await supervisor.startBackend();
  const call = calls.at(-1);
  await supervisor.stopBackend();
  return call;
}

test('packaged backend uses userData cwd and resources PYTHONPATH', async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-packaged-cwd-'));
  const resourcesPath = path.join(temporary, 'App.app', 'Contents', 'Resources');
  const userData = path.join(temporary, 'Application Support', 'Modu Vision');
  const originalResourcesPath = process.resourcesPath;
  fs.mkdirSync(resourcesPath, { recursive: true });
  process.resourcesPath = resourcesPath;
  electronApp.isPackaged = true;
  electronApp.getPath = (key) => {
    assert.equal(key, 'userData');
    return userData;
  };

  try {
    const call = await inspectLaunch();
    assert.equal(call.options.cwd, userData);
    assert.equal(call.options.env.PYTHONPATH, resourcesPath);
    assert.equal(call.args[call.args.indexOf('--project-dir') + 1], path.join(userData, 'projects'));
    assert.ok(fs.statSync(userData).isDirectory());
  } finally {
    electronApp.isPackaged = false;
    process.resourcesPath = originalResourcesPath;
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});

test('development backend keeps repository cwd and existing relative artifacts', async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-dev-cwd-'));
  const appPath = path.join(temporary, 'repo');
  fs.mkdirSync(appPath, { recursive: true });
  electronApp.isPackaged = false;
  electronApp.getAppPath = () => appPath;
  try {
    const call = await inspectLaunch();
    assert.equal(call.options.cwd, appPath);
    assert.equal(call.options.env.PYTHONPATH, appPath);
    assert.equal(call.args[call.args.indexOf('--project-dir') + 1], path.join(appPath, 'projects'));
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
