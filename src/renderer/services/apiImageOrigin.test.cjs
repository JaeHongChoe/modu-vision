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

test('a changed local port retires old project headers until the new project is accepted', async () => {
  const priorWindow = global.window, priorFetch = global.fetch;
  try {
    global.window = {location: {search: ''}, api: {}};
    const api = client(), calls = [];
    const original = {workspace_id: 'workspace-a', project_id: 'same-id', actor_id: 'actor', mode: 'local'};
    const current = {...original, workspace_id: 'workspace-b'};
    const project = {id: 'same-id', name: 'B', project_dir: '/projects/B', task: 'classification', source_dataset_dir: '/source'};
    api.setCachedPort(56050); api.setProjectContext(original);
    const epoch = api.getProjectContextGeneration();
    api.setCachedPort(56052);
    assert.equal(api.getProjectContext(), null, 'old authority cannot survive a local transport change');
    assert.equal(api.getProjectContextGeneration(), epoch + 1);
    global.fetch = async (url, options) => {
      calls.push({url, headers: new Headers(options.headers)});
      return {ok: true, status: 200, headers: new Headers({'X-Vision-Context': JSON.stringify(current)}), json: async () => project};
    };
    await api.request('/api/dataset/current-summary?folder_path=/source');
    assert.equal(calls[0].headers.get('X-Vision-Context'), null);
    assert.equal(calls[0].headers.get('X-Vision-Project'), null);
    const selected = await api.api.project.getCurrent();
    assert.equal(api.getProjectContext(), null, 'discovery alone cannot commit the new authority');
    api.api.project.acceptContext(selected);
    assert.deepEqual(api.getProjectContext(), current);
    await api.request('/api/dataset/current-summary?folder_path=/source');
    assert.deepEqual(JSON.parse(calls.at(-1).headers.get('X-Vision-Context')), current);
    assert.equal(calls.at(-1).headers.get('X-Vision-Project'), current.project_id);
  } finally {global.window = priorWindow; global.fetch = priorFetch;}
});

test('same local port and unrelated local-port updates preserve accepted shared authority', () => {
  const api = client(), local = {workspace_id: 'w', project_id: 'p', actor_id: 'a', mode: 'local'};
  api.setCachedPort(56050); api.setProjectContext(local);
  let epoch = api.getProjectContextGeneration(); api.setCachedPort(56050);
  assert.deepEqual(api.getProjectContext(), local); assert.equal(api.getProjectContextGeneration(), epoch);
  api.setSharedApiBase('https://shared.example.invalid/studio');
  const shared = {...local, mode: 'team'}; api.setProjectContext(shared);
  epoch = api.getProjectContextGeneration(); api.setCachedPort(56052);
  assert.deepEqual(api.getProjectContext(), shared); assert.equal(api.getProjectContextGeneration(), epoch);
});

for (const returning of [false, true]) {
test(`a pending discovery with no accepted context cannot cross local ports${returning ? ' A B A' : ' A B'}`, async () => {
  const priorWindow = global.window, priorFetch = global.fetch;
  try {
    global.window = {location: {search: ''}, api: {}};
    const api = client(); api.setCachedPort(56050);
    let release; const pending = new Promise(resolve => {release = resolve;});
    global.fetch = async () => pending;
    const discovery = api.api.project.getCurrent(); await new Promise(setImmediate);
    const epoch = api.getProjectContextGeneration();
    api.setCachedPort(56052); if (returning) api.setCachedPort(56050);
    const context = {workspace_id: 'old-workspace', project_id: 'same-id', actor_id: 'actor', mode: 'local'};
    release({ok: true, status: 200, headers: new Headers({'X-Vision-Context': JSON.stringify(context)}), json: async () => ({id: 'same-id'})});
    const candidate = await discovery;
    assert.throws(() => api.api.project.acceptContext(candidate), /프로젝트 선택 문맥이 바뀌었습니다/);
    assert.ok(api.getProjectContextGeneration() > epoch);
    assert.equal(api.getProjectContext(), null);
  } finally {global.window = priorWindow; global.fetch = priorFetch;}
});
}
