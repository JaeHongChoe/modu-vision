const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function loadHandoff() {
  const filename = path.resolve(__dirname, '../src/renderer/components/flowchart/flowHandoff.ts');
  if (!fs.existsSync(filename)) return {};
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(compiled, filename);
  return loaded.exports;
}

function loadIdentityCard() {
  const filename = path.resolve(__dirname, '../src/renderer/components/flowchart/SavedFlowIdentityCard.tsx');
  if (!fs.existsSync(filename)) return {};
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(compiled, filename);
  return loaded.exports;
}

const versions = [
  { version_id: 'newer', is_latest: true, is_active: false },
  { version_id: 'active', is_latest: false, is_active: true },
];
const pipeline = {
  name: '검사', id: 'line', edges: [],
  nodes: [
    { id: 'a', data: { model_job_id: 'job_a', node_type: 'inspection', label: 'A' }, position: { y: 0, x: 0 } },
    { id: 'b', data: { model_job_id: 'job_b', node_type: 'detection_crop', label: 'B' }, position: { y: 1, x: 1 } },
    { id: 'c', data: { model_job_id: 'job_a', node_type: 'inspection', label: 'C' }, position: { y: 2, x: 2 } },
  ],
};

test('active saved flow wins over a newer inactive revision', () => {
  const { activeSavedVersion } = loadHandoff();
  assert.equal(typeof activeSavedVersion, 'function');
  assert.equal(activeSavedVersion(versions).version_id, 'active');
  assert.equal(activeSavedVersion([{ version_id: 'newer', is_latest: true, is_active: false }]), null);
});

test('saved flow identity includes the exact graph hash and model jobs', async () => {
  const { savedFlowIdentity } = loadHandoff();
  assert.equal(typeof savedFlowIdentity, 'function');
  const identity = await savedFlowIdentity(versions[1], pipeline);
  assert.equal(identity.versionId, 'active');
  assert.equal(identity.pipelineId, 'line');
  assert.deepEqual(identity.modelJobIds, ['job_a', 'job_b']);
  assert.match(identity.pipelineHash, /^[a-f0-9]{64}$/);
  assert.equal(identity.pipelineHash, 'f85b13c3acd63151ec97b73e1e84a96bf1bb20dab61f99471773810aea8b7730');
});

test('saved flow identity prefers the server graph hash for exponent-formatted numbers', async () => {
  const { savedFlowIdentity } = loadHandoff();
  const exponentGraph = structuredClone(pipeline);
  exponentGraph.nodes[0].position = { x: 1e-7, y: 1e-6 };
  const serverHash = 'a1'.repeat(32);
  const local = await savedFlowIdentity(versions[1], exponentGraph);
  assert.notEqual(local.pipelineHash, serverHash);
  const identity = await savedFlowIdentity({ ...versions[1], pipeline_hash: serverHash }, exponentGraph);
  assert.equal(identity.pipelineHash, serverHash);
  assert.deepEqual(identity.modelJobIds, ['job_a', 'job_b']);
});

test('invalid server graph hash falls back to the existing browser hash', async () => {
  const { savedFlowIdentity } = loadHandoff();
  for (const value of ['bad', 'g'.repeat(64), 'a'.repeat(63), 42]) {
    const identity = await savedFlowIdentity({ ...versions[1], pipeline_hash: value }, pipeline);
    assert.equal(identity.pipelineHash, 'f85b13c3acd63151ec97b73e1e84a96bf1bb20dab61f99471773810aea8b7730');
  }
});

test('a detector feeding segmentation is labeled by its graph, not its recipe task', () => {
  const { flowRecipeLabel } = loadHandoff();
  assert.equal(typeof flowRecipeLabel, 'function');
  assert.equal(flowRecipeLabel({ ...pipeline, nodes: [
    { ...pipeline.nodes[0], data: { ...pipeline.nodes[0].data, node_type: 'detection_crop', task: 'detection' } },
    { ...pipeline.nodes[1], data: { ...pipeline.nodes[1].data, node_type: 'inspection', task: 'segmentation' } },
  ] }, 'segmentation'), '검출+분할');
});

test('inspection result source distinguishes a draft preview from an active saved version', () => {
  const { flowRunSourceLabel } = loadHandoff();
  assert.equal(typeof flowRunSourceLabel, 'function');
  assert.match(flowRunSourceLabel({ kind: 'draft', versionId: null }), /미저장 초안/);
  assert.match(flowRunSourceLabel({ kind: 'saved', versionId: 'revision_a' }), /revision_a/);
});

test('saved flow handoff card shows exact version, hash and model jobs before action', () => {
  const { SavedFlowIdentityCard } = loadIdentityCard();
  assert.equal(typeof SavedFlowIdentityCard, 'function');
  const html = renderToStaticMarkup(React.createElement(SavedFlowIdentityCard, {
    identity: {
      versionId: 'version_42', pipelineId: 'line', pipelineName: '검사', recipeTask: 'segmentation',
      pipelineHash: 'abcdef0123456789', modelJobIds: ['job_detect', 'job_segment'],
    },
    isActive: true,
  }));
  assert.match(html, /version_42/);
  assert.match(html, /abcdef0123456789/);
  assert.match(html, /job_detect/);
  assert.match(html, /job_segment/);
  assert.match(html, /활성 저장 버전/);
});
