const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function render(relative, exported, props, viewMode = 'simple', technicalDetails = false) {
  const file = path.resolve(__dirname, relative), loaded = new Module(file, module);
  loaded.filename = file; loaded.paths = Module._nodeModulePaths(path.dirname(file));
  loaded.require = name => name === '../../stores/useProjectStore'
    ? {useProjectStore: select => select({viewMode, technicalDetails})} : require(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX},
  }).outputText, file);
  return loaded.exports[exported](props);
}
const walk = (node, parents = []) => Array.isArray(node) ? node.flatMap(child => walk(child, parents))
  : node && typeof node === 'object' && node.props ? [{node, parents}, ...walk(node.props.children, [...parents, node])] : [];
const text = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(text).join('')
  : typeof node === 'object' ? [].concat(node.props?.children || []).map(text).join('') : String(node);
const identity = Object.freeze({pipelineId: 'flow-current', pipelineName: '표면 흠집 검사', versionId: 'version-current-12345678',
  pipelineHash: 'a'.repeat(64), modelJobIds: Object.freeze(['model-current-A', 'model-current-B']), recipeTask: 'mixed'});

test('simple saved-flow summary shows the name and model count while exact evidence stays available in a closed disclosure', () => {
  for (const technical of [false, true]) {
    const tree = render('SavedFlowIdentityCard.tsx', 'SavedFlowIdentityCard', {identity, isActive: true}, 'simple', technical);
    const rows = walk(tree), details = rows.find(row => row.node.type === 'details');
    assert.ok(details, 'the evidence has its own disclosure');
    assert.ok(!details.node.props.open, 'technical preference must not overwhelm simple view');
    const visible = rows.filter(row => !row.parents.some(parent => parent.type === 'details'));
    assert.ok(visible.some(row => row.node.type === 'strong' && text(row.node) === identity.pipelineName));
    assert.ok(text(tree).includes('모델 2개 연결'));
    for (const value of [identity.versionId, identity.pipelineId, identity.pipelineHash, ...identity.modelJobIds]) {
      assert.ok(text(details.node).includes(value), `exact ${value} retained`);
    }
    assert.ok(rows.some(row => row.node.type === 'summary' && text(row.node).includes('저장 버전·모델 근거')));
  }
});

test('full technical view exposes the evidence and an inactive saved selection is never called active', () => {
  const tree = render('SavedFlowIdentityCard.tsx', 'SavedFlowIdentityCard', {identity, isActive: false}, 'full', true);
  assert.equal(walk(tree).find(row => row.node.type === 'details').node.props.open, true);
  assert.ok(text(tree).includes('선택한 저장 버전'));
  assert.ok(!text(tree).includes('활성 저장 버전'));
  assert.deepEqual(identity.modelJobIds, ['model-current-A', 'model-current-B']);
});

const report = Object.freeze({run_id: 'run-history-12345678', pipeline_id: 'flow-history', pipeline_name: '어제 검사 흐름',
  saved_version_id: 'version-history-87654321', pipeline_hash: 'b'.repeat(64),
  model_sha256: Object.freeze({'model-history': 'c'.repeat(64)}),
  execution_config: Object.freeze({execution_target: 'local', device: 'cpu'})});

test('inspection record uses the selected execution snapshot, with exact run and checkpoint identities in its own evidence', () => {
  const tree = render('../inference/InspectionRunIdentityCard.tsx', 'InspectionRunIdentityCard', {report}, 'simple', true);
  const rows = walk(tree), details = rows.find(row => row.node.type === 'details');
  assert.ok(details && !details.node.props.open);
  const outside = rows.filter(row => !row.parents.some(parent => parent.type === 'details')).map(row => text(row.node));
  assert.ok(outside.some(value => value === report.pipeline_name));
  assert.ok(text(tree).includes('검사 당시 저장본'));
  assert.ok(text(tree).includes('이 컴퓨터 · CPU'));
  for (const value of [report.run_id, report.pipeline_id, report.saved_version_id, report.pipeline_hash, 'model-history', 'c'.repeat(64)]) {
    assert.ok(text(details.node).includes(value), `recorded ${value} retained`);
  }
  assert.ok(!text(tree).includes(identity.pipelineName));
  assert.ok(!text(tree).includes('model-current'));
});

test('legacy inspection evidence says what is missing instead of presenting the current model as historical proof', () => {
  const legacy = {...report, saved_version_id: null, pipeline_hash: '', model_sha256: undefined, execution_config: undefined};
  const tree = render('../inference/InspectionRunIdentityCard.tsx', 'InspectionRunIdentityCard', {report: legacy});
  const visibleStatus = walk(tree).find(row => row.node.props.role === 'status' && !row.parents.some(parent => parent.type === 'details'));
  assert.ok(visibleStatus);
  assert.match(text(visibleStatus.node), /미기록/);
  assert.ok(!text(tree).includes('현재 모델'));
});
