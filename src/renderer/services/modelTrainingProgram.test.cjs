const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Run the real API rejection and workbench formatter. Only the external HTTP
// response and unrelated training dispatch module are controlled.
function program() {
  const cache = new Map();
  function load(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const m = new Module(file, module);
    m.filename = file;
    m.paths = Module._nodeModulePaths(path.dirname(file));
    cache.set(file, m);
    const original = m.require.bind(m);
    m.require = name => {
      if (name === './modelExecution') return {};
      if (name.startsWith('.')) {
        const target = path.resolve(path.dirname(file), name + '.ts');
        if (fs.existsSync(target)) return load(target);
      }
      return original(name);
    };
    m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    }).outputText, file);
    return m.exports;
  }
  return load(path.join(__dirname, 'modelTrainingProgram.ts'));
}

test('patch preparation exposes the field and message from the actual API validation rejection', async () => {
  const {modelTrainingProgram, programError} = program();
  const options = {patch_size: 15, stride: 1, normal_class: 'OK', minimum_overlap: .05};
  const validation = [{type: 'greater_than_equal', loc: ['body', 'patch_size'],
    msg: 'Input should be greater than or equal to 16', input: 'private-input-marker',
    ctx: {ge: 16, private: 'private-context-marker'}, url: 'private-url-marker'}];
  const previous = global.fetch;
  const calls = [];
  global.fetch = async (url, request) => {
    calls.push({url, method: request.method, body: JSON.parse(request.body)});
    return new Response(JSON.stringify({detail: validation}), {
      status: 422, headers: {'Content-Type': 'application/json'},
    });
  };
  try {
    let rejected;
    try { await modelTrainingProgram.patch.prepare(options); }
    catch (cause) { rejected = cause; }
    assert.equal(calls.length, 1);
    assert.equal(new URL(calls[0].url).pathname, '/api/patch-classification/prepare');
    assert.equal(calls[0].method, 'POST');
    assert.deepEqual(calls[0].body, options);
    assert.ok(Array.isArray(rejected));
    assert.equal(rejected.status, 422);
    const before = JSON.stringify(rejected);
    const visible = programError(rejected);
    assert.equal(visible, 'patch_size: Input should be greater than or equal to 16');
    assert.doesNotMatch(visible, /private-|\[object Object\]/);
    assert.equal(JSON.stringify(rejected), before, 'formatting leaves the rejection intact');
  } finally { global.fetch = previous; }
});

test('ordinary workbench Error and string messages remain readable', () => {
  const {programError} = program();
  assert.equal(programError(new Error('Source unavailable')), 'Source unavailable');
  assert.equal(programError('작업이 거절됐습니다.'), '작업이 거절됐습니다.');
});

test('structured and unrecognized workbench errors never collapse to object strings', () => {
  const {programError} = program();
  assert.equal(programError({detail: [{loc: ['query', 'stride'], msg: 'Choose a valid stride'}]}),
    'stride: Choose a valid stride');
  assert.equal(programError({status: 422, input: 'private-input-marker'}), '요청을 처리하지 못했습니다.');
  assert.equal(programError([]), '요청을 처리하지 못했습니다.');
});
