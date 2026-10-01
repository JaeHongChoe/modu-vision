const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../../stores/useFlowchartStore.ts');
const item = new Module(filename, module);
item.filename = filename;
item.paths = Module._nodeModulePaths(path.dirname(filename));
const originalRequire = item.require.bind(item);
const initial = { id: 'draft', name: 'Incomplete', nodes: [{ id: 'inspect', position: { x: 0, y: 0 },
  data: { node_type: 'inspection', task: 'segmentation', label: 'Inspect', model_job_id: null } }], edges: [] };
let project, drafts, beforeSave, failSave, runCalls;
const context = () => ({ project_id: project.id, source_dataset_path: project.source_dataset_dir,
  labelset_id: project.active_labelset_id });
const key = () => JSON.stringify(context());
const flowDraft = {
  get: async () => {
    if (!drafts.has(key())) throw Object.assign(new Error('No draft'), { status: 404 });
    return structuredClone(drafts.get(key()));
  },
  save: async (pipeline, owner) => {
    if (beforeSave) await beforeSave();
    if (failSave) throw new Error('Disk unavailable');
    const saved = { context: owner, pipeline: structuredClone(pipeline), draft_sha256: 'a'.repeat(64), active_version_id: null };
    drafts.set(JSON.stringify(owner), saved);
    return saved;
  },
};
item.require = name => {
  if (name === '../services/api') return { api: { flowchart: {
    getPipeline: async () => structuredClone(initial), getActivePipeline: async () => structuredClone(initial),
    run: async () => { runCalls++; return {}; }, savePipeline: async () => ({}),
  } } };
  if (name === '../services/flowDraft') return { flowDraft };
  if (name === './useProjectStore') return { useProjectStore: { getState: () => ({ project }) } };
  if (name === '../components/flowchart/flowchartStartup') return { getFlowchartModelTask: node => node.data.task || null };
  return originalRequire(name);
};
item._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, filename);
const store = item.exports.useFlowchartStore;

test.beforeEach(async () => {
  project = { id: 'project-a', source_dataset_dir: '/source-a', active_labelset_id: 'default' };
  drafts = new Map(); beforeSave = null; failSave = false; runCalls = 0;
  store.getState().invalidateForDataChange();
  await store.getState().loadPipeline(true, 'segmentation', '/source-a');
});

test('incomplete draft survives labelset switch and restart while execution stays blocked', async () => {
  store.getState().updateNodeData('inspect', { label: 'Preserved incomplete graph' });
  assert.equal(await store.getState().saveDraft(), true);
  assert.equal(store.getState().pipelineDirty, false);
  assert.equal(store.getState().pipelineIsDraft, true);
  project.active_labelset_id = 'other';
  store.getState().invalidateForDataChange();
  await store.getState().loadPipeline(true, 'segmentation', '/source-a');
  assert.equal(store.getState().pipeline.nodes[0].data.label, 'Inspect');
  project.active_labelset_id = 'default';
  store.getState().invalidateForDataChange();
  await store.getState().loadPipeline(true, 'segmentation', '/source-a');
  assert.equal(store.getState().pipeline.nodes[0].data.label, 'Preserved incomplete graph');
  assert.equal(store.getState().pipeline.nodes[0].data.model_job_id, null);
  assert.equal(await store.getState().runPipeline('/source-a/a.png', 'a', { savedVersionId: 'old-active' }), false);
  assert.equal(runCalls, 0);
  project.source_dataset_dir = '/source-b';
  assert.equal(await store.getState().loadDraft(), null);
});

test('failed or stale draft save keeps edits dirty and does not claim durability', async () => {
  store.getState().updateNodeData('inspect', { label: 'Changed' });
  failSave = true;
  assert.equal(await store.getState().saveDraft(), false);
  assert.equal(store.getState().pipelineDirty, true);
  failSave = false;
  let finish;
  beforeSave = () => new Promise(resolve => { finish = resolve; });
  const pending = store.getState().saveDraft();
  store.getState().updateNodeData('inspect', { label: 'Newer edit' });
  finish();
  assert.equal(await pending, false);
  assert.equal(store.getState().pipelineDirty, true);
  assert.equal(store.getState().pipeline.nodes[0].data.label, 'Newer edit');
});

test('initial default is not a persisted draft, but save/readback and reopen record durability',async()=>{
  assert.equal(store.getState().persistedDraftHash,null);
  store.getState().updateNodeData('inspect',{label:'Saved ROI draft'});
  assert.equal(await store.getState().saveDraft(),true);
  assert.match(store.getState().persistedDraftHash,/^[a-f0-9]{64}$/);
  store.getState().invalidateForDataChange();
  assert.equal(store.getState().persistedDraftHash,null);
  await store.getState().loadPipeline(true,'segmentation','/source-a');
  assert.match(store.getState().persistedDraftHash,/^[a-f0-9]{64}$/);
  assert.equal(store.getState().pipeline.nodes[0].data.label,'Saved ROI draft');
});

test('a draft save whose transport owner changes cannot claim readback durability',async()=>{
  store.getState().updateNodeData('inspect',{label:'Unsaved ROI draft'});
  let finish;beforeSave=()=>new Promise(resolve=>{finish=resolve;});let ownerCurrent=true;
  const pending=store.getState().saveDraft(()=>ownerCurrent);ownerCurrent=false;finish();
  assert.equal(await pending,false);
  assert.equal(store.getState().persistedDraftHash,null);
  assert.equal(store.getState().pipelineDirty,true);
});
