const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const { pathToFileURL } = require('node:url');
const ts = require('typescript');

async function mainFixture() {
  let destroyed = false;
  let contentsDestroyed = false;
  let intercept;
  const filename = path.resolve(__dirname, '../src/main/index.ts');
  const rendererUrl = pathToFileURL(path.resolve(path.dirname(filename), '../../dist/index.html')).href;
  const contents = {
    id: 7, isDestroyed: () => contentsDestroyed, getURL: () => rendererUrl,
    session: { webRequest: { onBeforeSendHeaders(handler) { intercept = handler; } } },
    setWindowOpenHandler() {}, on() {},
  };
  const window = {
    isDestroyed: () => destroyed,
    get webContents() { if (destroyed) throw new Error('Object has been destroyed'); return contents; },
    once() {}, on() {}, loadFile: async () => {}, show() {},
  };
  const app = { isPackaged: true, on() {}, whenReady: () => Promise.resolve(), quit() {} };
  class Supervisor {
    startBackend() { return Promise.resolve(59509); }
    getPort() { return 59509; }
    getApiToken() { return 'fixture-local-capability'; }
  }
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const originalRequire = loaded.require.bind(loaded);
  loaded.require = specifier => {
    if (specifier === 'electron') return { app, BrowserWindow: function () { return window; }, nativeTheme: {}, shell: {} };
    if (specifier === './supervisor') return { BackendSupervisor: Supervisor };
    if (specifier === './ipc') return { registerIpcHandlers() {} };
    if (specifier === './instanceLock') return { acquireAppInstanceLock: () => true };
    if (specifier === './sharedSession') return { sharedHeaders: () => ({}) };
    return originalRequire(specifier);
  };
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText, filename);
  await new Promise(resolve => setImmediate(resolve));
  return { intercept, rendererUrl, close: () => { destroyed = true; }, destroyContents: () => { contentsDestroyed = true; } };
}

for (const state of ['live', 'closed window', 'destroyed contents']) {
  test(`late network request after ${state} preserves lifecycle and capability boundary`, async () => {
    const fixture = await mainFixture();
    if (state === 'closed window') fixture.close();
    if (state === 'destroyed contents') fixture.destroyContents();
    let result;
    assert.doesNotThrow(() => fixture.intercept({ method: 'GET', webContentsId: 7,
      frame: { url: fixture.rendererUrl }, url: 'http://127.0.0.1:59509/api/project/current',
      requestHeaders: { Accept: 'application/json' } }, response => { result = response; }));
    assert.equal(result.requestHeaders.Accept, 'application/json');
    assert.equal(result.requestHeaders['X-Vision-Token'], state === 'live' ? 'fixture-local-capability' : undefined);
  });
}
