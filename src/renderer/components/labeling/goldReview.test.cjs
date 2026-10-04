// E05: how a gold-sample label review reads: each conflict with its place, the counts, and why an old report no longer
// backs an approval.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

const name = path.resolve(__dirname, 'goldReview.ts');
const m = new Module(name, module);
m.filename = name;
m.paths = Module._nodeModulePaths(__dirname);
m._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, name);
const { countsLine, describeConflict, eligibilityLine, limitationText, reviewTask } = m.exports;

test('each conflict names the objects and where they are', () => {
  assert.equal(describeConflict({ conflict_id: 'a', kind: 'class', reference_object: 'r2', candidate_object: 'c2', reference_label: 'dent',
    candidate_label: 'scratch', overlap: 1, bbox: [60, 10, 90, 40], candidate_bbox: [61, 11, 90, 40] }),
    '클래스 불일치: 기준 dent → 작업 scratch (겹침 1) · 위치 [60, 10, 90, 40] · 작업 위치 [61, 11, 90, 40]');
  assert.equal(describeConflict({ conflict_id: 'b', kind: 'geometry', reference_object: 'r3', candidate_object: 'c3', label: 'scratch',
    overlap: 0.31, bbox: [10.4, 60, 50, 90] }), '모양 불일치: scratch 겹침 0.31 · 위치 [10, 60, 50, 90]');
  assert.equal(describeConflict({ conflict_id: 'c', kind: 'missing', reference_object: 'r4', label: 'scratch', bbox: [] }),
    '누락: 작업 라벨에 없는 기준 객체 r4 (scratch)');
  assert.equal(describeConflict({ conflict_id: 'd', kind: 'extra', candidate_object: 'c4', label: 'dent', bbox: [95, 70, 115, 95] }),
    '추가: 기준에 없는 작업 객체 c4 (dent) · 위치 [95, 70, 115, 95]');
  assert.equal(countsLine({ missing: 1, extra: 0, class: 2, geometry: 3 }), '누락 1 · 추가 0 · 클래스 불일치 2 · 모양 불일치 3');
  assert.equal(countsLine(null), '결과 파일이 바뀌어 읽지 않음');
  assert.equal(describeConflict({ conflict_id: 'e', kind: 'extra', candidate_object: 'c5', label: 'dent', bbox: [1, 2, 3, 4], candidate_bbox: [1, 2, 3, 4] }),
    '추가: 기준에 없는 작업 객체 c5 (dent) · 위치 [1, 2, 3, 4]', 'an extra object is placed once');
});

test('a report supports an approval only when current, unchanged since and passing, and says which one it is not', () => {
  const base = { current: true, stale_reasons: [], candidate_changes: [], passes: true, approval_eligible: true };
  assert.equal(eligibilityLine(base), '현재 정답 기준과 모두 일치: 승인 근거로 쓸 수 있습니다.');
  assert.equal(eligibilityLine({ ...base, passes: false, approval_eligible: false }),
    '현재 정답 기준으로 계산한 결과: 불일치, 라벨 없는 이미지 또는 비교할 수 없는 이미지가 있어 승인 근거로 쓸 수 없습니다.', 'conflicts are not agreement (review P2-1)');
  assert.equal(eligibilityLine({ ...base, candidate_changes: ['candidate_changed:train/a.png'], approval_eligible: false }),
    '현재 정답 기준으로 계산했지만 그 뒤 작업 라벨이 바뀌었습니다(1장): 다시 실행해야 승인 근거가 됩니다.');
  assert.equal(eligibilityLine({ ...base, current: false, approval_eligible: false,
    stale_reasons: ['gold_label_changed:train/b.png', 'guideline_changed', 'gold_unapproved:train/a.png', 'profile_retired'] }),
    '이전 기준의 결과: 승인 근거로 쓸 수 없습니다 (정답 라벨이 바뀌었습니다: train/b.png, 라벨 기준서가 바뀌었습니다, 정답 이미지가 더 이상 승인 상태가 아닙니다: train/a.png, 검수 기준을 그만 쓰기로 했습니다). 새 검수 기준을 만들어 다시 실행하세요.');
  assert.deepEqual(['detection', 'segmentation', 'classification', 'anomaly', undefined].map(reviewTask), ['detection', 'segmentation', null, null, null],
    'only the project task is compared, and only detection and segmentation (review P1-2)');
  assert.match(limitationText('classes are compared by exact name; a renamed class is a class conflict'), /이름이 정확히 같아야/);
  assert.equal(limitationText('a new limitation'), 'a new limitation', 'an unknown note is shown as given');
});
test('freeze 3: an image the review could not compare is counted and named, and the new limitations read in Korean', () => {
  const review = m.exports;
  assert.equal(review.countsLine({ missing: 1, extra: 0, class: 0, geometry: 0, not_comparable: 2 }), '누락 1 · 추가 0 · 클래스 불일치 0 · 모양 불일치 0 · 비교 불가 2');
  assert.equal(review.countsLine({ missing: 0, extra: 0, class: 0, geometry: 0, not_comparable: 0 }), '누락 0 · 추가 0 · 클래스 불일치 0 · 모양 불일치 0',
    'none not comparable: the line as before');
  assert.equal(review.countsLine({ missing: 0, extra: 0, class: 0, geometry: 0 }), '누락 0 · 추가 0 · 클래스 불일치 0 · 모양 불일치 0', 'a report saved before freeze 3');
  for (const limitation of ["a normal mark as an image's only label is an image without objects; other image tags (classification or anomaly labels) are not compared, and a labeler image with one is reported as not comparable",
    'only labels saved in the app are compared: labels that exist only in the source folder (imported COCO, YOLO or mask files) are not read']) {
    assert.match(review.limitationText(limitation), /[가-힣]/, limitation);
  }
  assert.match(review.notComparableText("train/c.png (candidate): the image tag 'NG' (a classification or anomaly label) is not compared"),
    /비교할 수 없습니다.*train\/c\.png \(candidate\)/);
});
