const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function client() {
  const file = path.join(__dirname, 'api.ts');
  const loaded = new Module(file, module);
  loaded.filename = file;
  loaded.paths = Module._nodeModulePaths(__dirname);
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, file);
  return loaded.exports;
}

test('a reopened browser resolves image requests to its own launch port before an async API lookup', () => {
  const prior = global.window;
  try {
    global.window = {location: {search: '?port=56050'}, api: {getBackendPort: async () => 56050}};
    const api = client();
    assert.equal(api.resolveApiUrl('/api/dataset/thumbnail/owned.png'),
      'http://127.0.0.1:56050/api/dataset/thumbnail/owned.png');
    api.setCachedPort(56052);
    assert.equal(api.resolveApiUrl('/image'), 'http://127.0.0.1:56052/image');
    assert.equal(api.resolveApiUrl('/image', 56054), 'http://127.0.0.1:56054/image');
    api.setSharedApiBase('https://worker.example.invalid/studio');
    assert.equal(api.resolveApiUrl('/image'), 'https://worker.example.invalid/studio/image');
  } finally {global.window = prior;}
});

test('an unresolved desktop bridge does not send a persisted image path to an unrelated default backend', () => {
  const prior = global.window;
  try {
    global.window = {location: {search: ''}, api: {getBackendPort: async () => 56050}};
    const api = client();
    assert.equal(api.resolveApiUrl('/api/dataset/thumbnail/owned.png'), '');
    api.setCachedPort(56050);
    assert.equal(api.resolveApiUrl('/api/dataset/thumbnail/owned.png'),
      'http://127.0.0.1:56050/api/dataset/thumbnail/owned.png');
    assert.equal(api.resolveApiUrl('data:image/png;base64,AA=='), 'data:image/png;base64,AA==');
  } finally {global.window = prior;}
});

test('standalone fallback remains available and an invalid browser port is never used', () => {
  const prior = global.window;
  try {
    for (const search of ['', '?port=bad', '?port=0', '?port=70000', '?port=2.5']) {
      global.window = {location: {search}};
      assert.equal(client().resolveApiUrl('/image'), 'http://127.0.0.1:8000/image');
    }
  } finally {global.window = prior;}
});
