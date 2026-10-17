// Exercise the original store and API serializer; only the HTTP transport is a supplier.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks = {}) {
  const name = path.resolve(__dirname, file);
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(path.dirname(name));
  const original = m.require.bind(m);
  m.require = key => Object.hasOwn(mocks, key) ? mocks[key] : original(key);
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, name);
  return m.exports;
}

const project = { id: 'seg-project', project_dir: '/workspace/seg', source_dataset_dir: '/original/source', active_labelset_id: 'default' };
const pipeline = { id: 'flow', name: 'flow', nodes: [
  { id: 'input', position: { x: 0, y: 0 }, data: { node_type: 'input', label: 'Input' } },
], edges: [] };
const execution = { executionTarget: 'selected_compute', device: 'cuda', computeProfileId: 'server42-l40s-2' };

for (const [kind, selectedImage, expectedId] of [
  ['local file', { source: 'file', imagePath: '/workspace/seg/dataset/retained-required-input.png', fileName: 'retained-required-input.png' }, null],
  ['dataset image', { source: 'dataset', imagePath: '/original/source/dataset-image.png', imageId: 'dataset-image-42', fileName: 'dataset-image.png' }, 'dataset-image-42'],
]) {
  test(`${kind} reaches the original API JSON boundary with its explicit image id and unchanged request fields`, async () => {
    const apiModule = load('../services/api.ts');
    const store = load('./useFlowchartStore.ts', {
      '../services/api': apiModule,
      '../services/flowDraft': { flowDraft: {} },
      './useProjectStore': { useProjectStore: { getState: () => ({ project }) } },
      '../components/flowchart/flowchartStartup': { getFlowchartModelTask: () => null },
    }).useFlowchartStore;
    store.setState({ pipeline, selectedImage });
    const previous = global.fetch;
    const calls = [];
    global.fetch = async (url, options) => {
      calls.push({ url, options });
      return { ok: true, headers: new Headers(), json: async () => ({
        final_verdict: 'OK', is_ok: true, roi_count: 0, crops: [], execution_steps: [],
        execution_target: 'selected_compute', execution_device: 'cuda:0', compute_profile_id: 'server42-l40s-2',
      }) };
    };
    try {
      assert.equal(await store.getState().runPipeline(undefined, undefined, execution), true);
      assert.equal(calls.length, 1);
      assert.equal(new URL(calls[0].url).pathname, '/api/flowchart/run');
      assert.equal(calls[0].options.method, 'POST');
      assert.deepEqual(JSON.parse(calls[0].options.body), {
        image_path: selectedImage.imagePath,
        image_id: expectedId,
        pipeline,
        project_id: project.id,
        execution_target: 'selected_compute',
        device: 'cuda',
        compute_profile_id: 'server42-l40s-2',
      }, 'the serialized request has exactly the original seven fields; dataset image/project identity is preserved');
    } finally {
      global.fetch = previous;
    }
  });
}
