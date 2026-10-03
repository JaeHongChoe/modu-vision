// QA finding (app-flow QA): after saving a flow built in the editor (a recipe, palette nodes), the server stores it
// with its defaults filled in, so the editor never equalled its saved version and readiness reported "편집 화면과 활성
// 저장 버전의 규칙이 다릅니다". The editor now adopts the stored version when nothing was edited during the save.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks) {
  const name = path.resolve(__dirname, file);
  const m = new Module(name, module);
  m.filename = name;
  m.paths = Module._nodeModulePaths(__dirname);
  const req = m.require.bind(m);
  m.require = key => (Object.hasOwn(mocks, key) ? mocks[key] : req(key));
  m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, name);
  return m.exports;
}

const project = { id: 'p', project_dir: '/workspace', source_dataset_dir: '/source', active_labelset_id: 'default' };
const built = () => ({ id: 'recipe_fixed', name: '고정 ROI 검사', nodes: [
  { id: 'node_input', position: { x: 0, y: 0 }, data: { node_type: 'input', label: '검사 이미지' } },
  { id: 'node_decision', position: { x: 200, y: 0 }, data: { node_type: 'decision', label: '최종 판정', rule: 'any_defect_is_ng' } }],
  edges: [{ id: 'e1', source: 'node_input', target: 'node_decision', payload_type: 'result' }] });
/** What the server returns for the saved version: the same rules with its defaults filled in. */
const stored = () => ({ ...built(), execution_config: { max_workers: 1, device_slots: 1 },
  nodes: built().nodes.map(node => ({ ...node, type: 'custom', data: { ...node.data, model_job_id: null, threshold: 0.5, score_spec: null, crop_padding: 10, params: {} } })),
  edges: built().edges.map(edge => ({ ...edge, label: null, isBranch: null, predicate: null })) });

function flowStore({ save = async () => ({ version_id: 'v1' }), version = async () => stored() } = {}) {
  const m = load('./useFlowchartStore.ts', {
    '../services/api': { api: { flowchart: { run: async () => ({ final_verdict: 'NG', is_ok: false, roi_count: 0, crops: [], execution_steps: [] }),
      savePipeline: save, getPipelineVersion: version } } },
    '../services/flowDraft': { flowDraft: {} },
    './useProjectStore': { useProjectStore: { getState: () => ({ project }) } },
    '../components/flowchart/flowchartStartup': { getFlowchartModelTask: () => null },
  });
  const store = m.useFlowchartStore;
  store.setState({ pipeline: built(), cleanPipeline: null, pipelineDirty: true, pipelineIsDraft: true,
    selectedImage: { source: 'dataset', imagePath: '/source/a.png', fileName: 'a.png' } });
  return { m, store };
}

test('after a save the editor holds the stored version, so it equals what was saved', async () => {
  const { m, store } = flowStore();
  assert.notEqual(m.flowSemanticKey(built()), m.flowSemanticKey(stored()), 'the server defaults change the semantic key');
  await store.getState().savePipeline(undefined, 'anomaly', '/source');
  const state = store.getState();
  assert.equal(m.flowSemanticKey(state.pipeline), m.flowSemanticKey(stored()));
  assert.equal(state.cleanPipeline, state.pipeline);
  assert.equal(state.pipelineDirty, false);
  assert.equal(state.pipelineIsDraft, false);
  assert.equal(state.errorMessage, null);
});

test('a run made with the saved rules stays current after the stored version is adopted', async () => {
  const { m, store } = flowStore();
  assert.equal(await store.getState().runPipeline(), true);
  assert.equal(m.isExecutionResultCurrent(store.getState()), true);
  await store.getState().savePipeline(undefined, 'anomaly', '/source');
  assert.equal(m.isExecutionResultCurrent(store.getState()), true, 'only server defaults were added');
  assert.equal(store.getState().executionResult?.final_verdict, 'NG');
});

test('an edit made while the version is read back is kept, and a failed read keeps the saved graph as before', async () => {
  let edit;
  const { store } = flowStore({ version: async () => { edit(); return stored(); } });
  edit = () => store.getState().updateNodeData('node_decision', { rule: 'all_defects_are_ng' });
  await store.getState().savePipeline(undefined, 'anomaly', '/source');
  assert.equal(store.getState().pipeline.nodes.find(node => node.id === 'node_decision').data.rule, 'all_defects_are_ng', 'never replaced');
  assert.equal(store.getState().pipelineDirty, true, 'the edit still needs saving');
  const failed = flowStore({ version: async () => { throw new Error('version read failed'); } });
  const target = failed.store.getState().pipeline;
  await failed.store.getState().savePipeline(undefined, 'anomaly', '/source');
  assert.equal(failed.store.getState().pipeline, target);
  assert.equal(failed.store.getState().cleanPipeline, target);
  assert.equal(failed.store.getState().pipelineDirty, false);
});

test('a save whose editor was replaced while the version was read back changes nothing there', async () => {
  let replace;
  const { store } = flowStore({ version: async () => { replace(); return stored(); } });
  replace = () => store.getState().invalidateForDataChange();
  await store.getState().savePipeline(undefined, 'anomaly', '/source');
  assert.equal(store.getState().pipeline, null, 'the replaced editor is not given the saved flow');
  assert.equal(store.getState().saveMessage, null, 'and no message about that save');
});

test('a run made before an edit stays out of date after the edited flow is saved', async () => {
  const { m, store } = flowStore();
  assert.equal(await store.getState().runPipeline(), true);
  store.getState().updateNodeData('node_decision', { rule: 'all_defects_are_ng' });
  assert.equal(m.isExecutionResultCurrent(store.getState()), false);
  await store.getState().savePipeline(undefined, 'anomaly', '/source');
  assert.equal(m.isExecutionResultCurrent(store.getState()), false, 'adopting the stored version never revives an old run');
});
