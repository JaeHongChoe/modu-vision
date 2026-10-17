const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function client() {
  const file = path.join(__dirname, 'api.ts'), loaded = new Module(file, module);
  loaded.filename = file; loaded.paths = Module._nodeModulePaths(__dirname);
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, file);
  return loaded.exports;
}
const reads = ['/api/compute/reservations', '/api/compute/targets', '/api/compute/jobs',
  '/api/compute/inputs?task=classification', '/api/dataset/revisions'];

test('shared project hydration refuses all five unbound reads before dispatch, then uses accepted headers', async () => {
  const previous = global.fetch, api = client(), calls = [];
  global.fetch = async (url, options) => {
    calls.push({url, headers: new Headers(options.headers)});
    return new Response('{}', {status: 200});
  };
  try {
    api.setSharedApiBase('https://shared.example.invalid');
    for (const route of reads) {
      await assert.rejects(api.request(route), error => error.name === 'ProjectContextRequiredError');
    }
    assert.deepEqual(calls, [], 'a server-side stored selection must never receive these unbound reads');
    const context = {workspace_id: 'owned-workspace', project_id: 'owned-project', actor_id: 'owned-user', mode: 'team'};
    api.setProjectContext(context);
    for (const route of reads) await api.request(route);
    assert.equal(calls.length, 5);
    for (const call of calls) {
      assert.equal(call.headers.get('X-Vision-Project'), 'owned-project');
      assert.deepEqual(JSON.parse(call.headers.get('X-Vision-Context')), context);
    }
    api.setProjectContext(null);
    await assert.rejects(api.request(reads[0]), {name: 'ProjectContextRequiredError'});
    assert.equal(calls.length, 5, 'retiring accepted context immediately fences future automatic reads');
  } finally {global.fetch = previous;}
});

test('the shared precondition leaves accounts, discovery, other methods and local requests unchanged', async () => {
  const previous = global.fetch, api = client(), calls = [];
  global.fetch = async (url, options) => {
    calls.push({url, method: options.method || 'GET'});
    return new Response('{}', {status: 200});
  };
  try {
    api.setCachedPort(56050); api.setSharedApiBase('https://shared.example.invalid');
    for (const route of ['/api/accounts/me', '/api/accounts/users', '/api/project/current', '/api/context', '/api/compute/jobs/owned']) {
      await api.request(route);
    }
    await api.request('/api/compute/jobs', {method: 'POST', body: '{}'});
    assert.equal(calls.length, 6);
    api.setSharedApiBase(null);
    for (const route of reads) await api.request(route);
    assert.equal(calls.length, 11);
    assert.ok(calls.slice(6).every(call => call.url.startsWith('http://127.0.0.1:56050/')));
  } finally {global.fetch = previous;}
});

test('explicit discovery null cannot bypass shared-read refusal and an explicit valid context remains usable', async () => {
  const previous = global.fetch, api = client(), calls = [];
  global.fetch = async (url, options) => {calls.push(options); return new Response('{}', {status: 200});};
  try {
    api.setSharedApiBase('https://shared.example.invalid');
    await assert.rejects(api.request('/api/compute/inputs?task=ocr', {projectContext: null, method: 'get'}), {name: 'ProjectContextRequiredError'});
    await assert.rejects(api.request('api/compute/jobs'), {name: 'ProjectContextRequiredError'});
    assert.equal(calls.length, 0);
    const context = {workspace_id: 'owned-workspace', project_id: 'owned-project', actor_id: 'owned-user', mode: 'team'};
    await api.request('/api/compute/jobs', {projectContext: context});
    assert.deepEqual(JSON.parse(new Headers(calls[0].headers).get('X-Vision-Context')), context);
    assert.equal(api.getProjectContext(), null, 'an explicitly scoped read grants no accepted global context');
  } finally {global.fetch = previous;}
});
