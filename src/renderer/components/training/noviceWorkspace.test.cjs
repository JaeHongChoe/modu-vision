const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const training = fs.readFileSync(path.join(__dirname, 'TrainingController.tsx'), 'utf8');
const evaluation = fs.readFileSync(path.join(__dirname, '../evaluation/EvaluationStudio.tsx'), 'utf8');
test('training editors have one persistent render path across viewing modes', () => {
  for (const label of ['training-model-settings', 'training-scheduling-settings']) {
    assert.equal(training.split(`id="${label}"`).length - 1, 1, label);
  }
  assert.match(training, /<details[^>]+className="modu-stage-disclosure"[^>]*open=\{!simpleView\}/);
  assert.match(fs.readFileSync(path.join(__dirname, 'ModelFamilyCatalog.tsx'), 'utf8'), /모델 종류 선택 · 10종/);
});
test('evaluation keeps model, cohort, cancellation and errors in a single visible toolbar', () => {
  for (const label of ['평가 모델', '평가 데이터 버전', '원격 평가 취소']) {
    assert.equal(evaluation.split(`aria-label="${label}"`).length - 1, 1, label);
  }
  assert.equal(evaluation.split('<ModelComparisonPanel').length - 1, 1);
  assert.match(evaluation, /aria-label="평가 대상과 실행"/);
  assert.match(evaluation, /testPredictions.length > 0 && \(\(\) =>/);
});
