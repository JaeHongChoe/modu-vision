// E07: how a deployment preflight reads: the status, a stale report, the requirements grouped by node in flow order and
// what to do for each failing kind.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

const name = path.resolve(__dirname, 'flowPreflightText.ts');
const m = new Module(name, module);
m.filename = name;
m.paths = Module._nodeModulePaths(__dirname);
m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, name);
const { byNode, PREFLIGHT_TARGETS, remedyText, sameTarget, staleLine, statusLine } = m.exports;

const row = (node_id, kind, state, artifact_ref = 'x', version_range = null) =>
  ({ node_id, kind, state, artifact_ref, version_range, platform: null, license_ref: null, evidence_ref: null, remedy: 'detail' });

test('the status says what blocks and never offers a fallback', () => {
  const counts = { ready: 3, missing: 1, mismatch: 0, unavailable: 0, unverified: 0 };
  assert.equal(statusLine({ status: 'blocked', blocked_nodes: { a: [], b: [] }, decision_blocked: true, counts }),
    '차단됨: 노드 2개가 막혔습니다 (최종 판정 포함). 대체 실행 없이 이 대상에는 배포할 수 없습니다.');
  assert.equal(statusLine({ status: 'unverified', blocked_nodes: {}, decision_blocked: false, counts: { ...counts, unverified: 4 } }),
    '대상 장비에서 확인할 항목 4개: 패키지를 설치한 뒤 run_flow.py --preflight로 확인하세요.');
  assert.equal(staleLine(['target_changed', 'environment_changed']),
    '이전 점검 결과입니다: 선택한 대상과 다릅니다, 이 컴퓨터의 런타임 구성(패키지·버전·장치)이 바뀌었습니다. 다시 점검하세요.');
});

test('requirements group by node in flow order with the whole flow first, and each failing kind says what to do', () => {
  const groups = byNode([row('n2', 'device', 'ready'), row(null, 'runtime', 'ready'), row('n1', 'model', 'missing'), row('n2', 'model', 'ready')], ['n1', 'n2']);
  assert.deepEqual(groups.map(([id, rows]) => [id, rows.length]), [[null, 1], ['n1', 1], ['n2', 2]]);
  assert.equal(remedyText(row('n1', 'model', 'missing')), '3단계에서 모델을 학습하거나 이 노드에 완료 모델을 연결하세요.');
  assert.match(remedyText(row('n1', 'runtime', 'unavailable', 'ultralytics', '>=8.4.41')), /^ultralytics >=8\.4\.41을\(를\) 실행 환경에 설치하세요/);
  assert.match(remedyText(row('n1', 'device', 'unavailable', 'cuda:0')), /다른 장치로 대신 실행하지 않습니다/);
  assert.match(remedyText(row('n1', 'runtime', 'unverified')), /run_flow\.py --preflight/);
  assert.equal(remedyText(row('n1', 'model', 'ready')), null);
});

test('targets compare by every field', () => {
  const [cpu, cuda] = PREFLIGHT_TARGETS.map(item => item.target);
  assert.equal(sameTarget(cpu, { device: 'cpu', kind: 'this_computer' }), true, 'key order does not matter');
  assert.equal(sameTarget(cpu, cuda), false);
  assert.equal(new Set(PREFLIGHT_TARGETS.map(item => item.key)).size, PREFLIGHT_TARGETS.length);
});

function renderReport(report) {
  const filename = path.resolve(__dirname, 'FlowPreflightPanel.tsx');
  const panel = new Module(filename, module);
  panel.filename = filename;
  panel.paths = Module._nodeModulePaths(__dirname);
  let state = 0;
  const jsx = (type, props) => ({ type, props });
  const mocks = {
    react: { useEffect: () => {}, useRef: initial => ({ current: initial }), useState: initial => [++state === 2 ? report : initial, () => {}] },
    'react/jsx-runtime': { jsx, jsxs: jsx },
    './flowPreflightText': m.exports,
    '../../services/flowPreflight': { flowPreflight: new Proxy({}, { get: () => () => { throw new Error('render must not run a preflight'); } }) },
  };
  const original = panel.require.bind(panel);
  panel.require = name => name in mocks ? mocks[name] : original(name);
  panel._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  return panel.exports.FlowPreflightPanel({ versionId: 'a'.repeat(32), recipeTask: 'detection',
    sourceDatasetPath: '/owned-fixture', pipeline: null, onSelectNode: () => {} });
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...nodes(tree.props?.children)];
}

function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join('');
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  return tree && typeof tree === 'object' ? text(tree.props?.children) : '';
}

const reportFixture = (status, stale = false) => ({ status, stale, stale_reasons: stale ? ['artifacts_changed'] : [],
  report_id: 'b'.repeat(32), checked_at: '2026-10-04T00:00:00Z', requirements: [], blocked_nodes: {}, decision_blocked: false,
  counts: { ready: 1, missing: 0, mismatch: 0, unavailable: 0, unverified: status === 'unverified' ? 2 : 0 } });

test('a stale ready report warns to check again instead of claiming current readiness', () => {
  const report = reportFixture('ready', true);
  const before = structuredClone(report);
  const tree = renderReport(report);
  const status = nodes(tree).find(node => node.props?.role === 'status');
  assert.equal(text(status), '이전 점검 결과입니다. 현재 준비 상태를 확인하려면 다시 점검하세요.');
  assert.match(status.props.className, /text-amber-/);
  assert.doesNotMatch(status.props.className, /emerald/);
  const alert = nodes(tree).find(node => node.props?.role === 'alert');
  assert.equal(text(alert), '이전 점검 결과입니다: 모델 또는 교정 파일이 바뀌었습니다. 다시 점검하세요.');
  assert.doesNotMatch(text(tree), /모든 의존성이 이 대상에서 준비됐습니다|artifacts_changed/);
  assert.match(text(tree), /보고서 bbbbbbbb/);
  assert.deepEqual(report, before, 'the historical report and identity stay intact');
});

test('current ready, blocked and unverified reports retain their wording and status style', () => {
  for (const [state, wording, style] of [
    ['ready', '모든 의존성이 이 대상에서 준비됐습니다.', /text-emerald-/],
    ['blocked', '차단됨: 노드 0개가 막혔습니다.', /text-rose-/],
    ['unverified', '대상 장비에서 확인할 항목 2개: 패키지를 설치한 뒤 run_flow.py --preflight로 확인하세요.', /text-sky-/],
  ]) {
    const report = reportFixture(state);
    const tree = renderReport(report);
    const status = nodes(tree).find(node => node.props?.role === 'status');
    assert.equal(text(status), wording);
    assert.match(status.props.className, style);
    assert.equal(nodes(tree).some(node => node.props?.role === 'alert'), false);
    assert.match(text(tree), /보고서 bbbbbbbb/);
  }
});
