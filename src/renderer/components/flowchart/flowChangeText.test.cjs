// E04: how a recorded or previewed inspection-rule change reads, when a reason is required, and what the runtime line
// says while a new release is being applied.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

const name = path.resolve(__dirname, 'flowChangeText.ts');
const m = new Module(name, module);
m.filename = name;
m.paths = Module._nodeModulePaths(__dirname);
m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, name);
const { describeChange, reasonRequired, runtimeLabel, summarizeDelta } = m.exports;

const delta = (changes, layout_only = false) => ({ changes, layout_only, semantic_sha256_before: 'a', semantic_sha256_after: 'b' });

test('each kind of rule difference reads as what changed', () => {
  assert.equal(describeChange({ kind: 'node_changed', node_id: 'node_inspect', field: 'threshold', before: 0.5, after: 0.7 }),
    'node_inspect · threshold: 0.5 → 0.7');
  assert.equal(describeChange({ kind: 'node_changed', node_id: 'n', field: 'params.roi', before: null, after: [1, 2] }), 'n · params.roi: 없음 → [1,2]');
  assert.equal(describeChange({ kind: 'node_added', node_id: 'node_fixed_roi', node_type: 'fixed_roi' }), '노드 추가: node_fixed_roi (고정 ROI)');
  assert.equal(describeChange({ kind: 'edge_added', source: 'a', target: 'b',
    predicate: { kind: 'class', operator: 'absent', class_name: 'dent', min_confidence: 0.4 } }), '연결 추가: a → b (dent 없음 ≥ 0.4)');
  assert.equal(describeChange({ kind: 'edge_removed', source: 'a', target: 'b', isBranch: 'fail' }), '연결 삭제: a → b (불합격)');
});

test('a reason is required only for a rule change against an active version', () => {
  const preview = (parent, changes, layout_only = false) => ({ parent_revision: parent, stale: false, semantic_delta: delta(changes, layout_only), layout_only });
  const change = [{ kind: 'node_changed', node_id: 'n', field: 'threshold', before: 1, after: 2 }];
  assert.equal(reasonRequired(preview('1'.repeat(32), change)), true);
  assert.equal(reasonRequired(preview(null, change)), false, 'the first flow');
  assert.equal(reasonRequired(preview('1'.repeat(32), [], true)), false, 'a layout move');
  assert.equal(reasonRequired(preview('1'.repeat(32), [])), false, 'nothing changed');
  assert.equal(reasonRequired(null), false);
  assert.equal(summarizeDelta(delta([], true)), '배치·이름만 변경 (검사 규칙과 평가 근거 유지)');
  assert.equal(summarizeDelta(delta(change)), '검사 규칙 변경 1건');
  assert.equal(summarizeDelta(delta(change), true), '새 검사 플로우 (규칙 1개)');
});

test('the running release stays named while a new one is applied and not yet acknowledged', () => {
  assert.equal(runtimeLabel({ active: null, pending: null }), '운영 런타임: 적용된 릴리스 없음');
  const label = runtimeLabel({ active: { deployment_id: 'd1', manifest_sha256: 'a'.repeat(64), acknowledged: true },
    pending: { operation_id: 'op1', status: 'applying', manifest_sha256: 'b'.repeat(64) } });
  assert.equal(label, `운영 런타임: 릴리스 ${'a'.repeat(12)} · 확인(ACK) 완료 · 적용 중 ${'b'.repeat(12)} (applying, 확인 전에는 위 릴리스가 계속 실행)`);
  assert.equal(runtimeLabel({ active: null, pending: null, unreadable: true }), '운영 런타임 배포 기록을 읽을 수 없습니다.');
});
