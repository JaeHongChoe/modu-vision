// The one readable picture of the step (state, blocker, next action, progress) plus the panels' current errors.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load() {
  const filename = path.join(__dirname, 'assistContext.ts');
  const m = new Module(filename, module);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  return m.exports;
}
const snapshot = (overrides = {}) => ({scope: 's1', stage: 2, status: 'ready', blocker: '초안 3장 검토 대기', nextAction: {label: '초안 검토', step: 2, target: 'draft-review-queue'},
  progress: {confirmed: 10, drafts: 3, remaining: 2}, ...overrides});

test('the published picture is readable back, and an unchanged publish is silent', () => {
  const {publishAssistContext, currentAssistContext, subscribeAssistContext} = load();
  let notified = 0;
  const unsubscribe = subscribeAssistContext(() => { notified += 1; });
  publishAssistContext(snapshot());
  assert.equal(notified, 1);
  assert.deepEqual(currentAssistContext(), {...snapshot(), errors: []});
  publishAssistContext(snapshot());
  assert.equal(notified, 1, 'the same picture again does not wake listeners');
  publishAssistContext(snapshot({status: 'blocked', blocker: '검토자 이름 필요'}));
  assert.equal(notified, 2);
  assert.equal(currentAssistContext().blocker, '검토자 이름 필요');
  unsubscribe();
  publishAssistContext(snapshot({stage: 3}));
  assert.equal(notified, 2);
});

test('panels report one error each, replace it, and clear it with null or blank text', () => {
  const {reportPanelError, assistPanelErrors, currentAssistContext, subscribeAssistContext} = load();
  let notified = 0;
  subscribeAssistContext(() => { notified += 1; });
  reportPanelError('초안 검토', '검토자 이름을 입력하세요.');
  reportPanelError('초안 만들기', '  모델 파일이 없습니다 ');
  assert.deepEqual(assistPanelErrors(), [{source: '초안 검토', text: '검토자 이름을 입력하세요.'}, {source: '초안 만들기', text: '모델 파일이 없습니다'}]);
  assert.equal(notified, 2);
  reportPanelError('초안 검토', '검토자 이름을 입력하세요.');
  assert.equal(notified, 2, 'the same error again is silent');
  reportPanelError('초안 검토', '다른 오류');
  assert.equal(assistPanelErrors()[0].text, '다른 오류');
  reportPanelError('초안 검토', null);
  reportPanelError('초안 만들기', '   ');
  assert.deepEqual(assistPanelErrors(), []);
  assert.equal(notified, 5);
  reportPanelError('초안 검토', undefined);
  assert.equal(notified, 5, 'clearing an absent error is silent');
  assert.equal(currentAssistContext(), null, 'no picture until the footer publishes one');
});

test('an error registration clears only its own panel instance', () => {
  const context = load();
  assert.equal(typeof context.registerPanelError, 'function', 'panels need an owned error registration');
  const first = context.registerPanelError('흐름 평가', '첫 프로젝트 오류');
  const second = context.registerPanelError('흐름 평가', '다른 프로젝트 오류');
  first();
  assert.deepEqual(context.assistPanelErrors(), [{source: '흐름 평가', text: '다른 프로젝트 오류'}]);
  first();
  assert.equal(context.assistPanelErrors().length, 1);
  second();
  assert.deepEqual(context.assistPanelErrors(), []);
});

test('steps 3 to 6 publish their own number line, and a changed number wakes listeners', () => {
  const {publishAssistContext, currentAssistContext, subscribeAssistContext} = load();
  let notified = 0;
  subscribeAssistContext(() => { notified += 1; });
  const training = snapshot({stage: 3, status: 'checking', blocker: '작업 완료 응답 확인 중', nextAction: {label: '학습 진행 상태 보기', step: 3, target: 'workflow-training'},
    progress: {epoch: 3, totalEpochs: 10, etaSeconds: 150, completed: false}});
  publishAssistContext(training);
  assert.deepEqual(currentAssistContext().progress, {epoch: 3, totalEpochs: 10, etaSeconds: 150, completed: false});
  publishAssistContext(training);
  assert.equal(notified, 1);
  publishAssistContext({...training, progress: {...training.progress, epoch: 4}});
  assert.equal(notified, 2, 'the next epoch is a new picture');
  publishAssistContext(snapshot({stage: 4, status: 'ready', blocker: null, nextAction: {label: '검사 흐름 만들기 (5단계)', step: 5}, progress: {samples: 50, escapes: 2, overkills: 3, accuracy: 90, low: 79, high: 96}}));
  assert.deepEqual(currentAssistContext().progress, {samples: 50, escapes: 2, overkills: 3, accuracy: 90, low: 79, high: 96});
  publishAssistContext(snapshot({stage: 6, status: 'unknown', blocker: '검사 결과까지 확인했습니다.', nextAction: {label: '승인·패키지·대상 확인', step: 6, target: 'workflow-package'}, progress: null}));
  assert.equal(currentAssistContext().progress, null);
  assert.equal(notified, 4);
});

test('step 4 mirrors the accuracy range, an evaluation without answers and counts from before a data change; step 5 an evaluation to redo', () => {
  const {publishAssistContext, currentAssistContext, subscribeAssistContext} = load();
  let notified = 0;
  subscribeAssistContext(() => { notified += 1; });
  const evaluated = snapshot({stage: 4, status: 'ready', blocker: null, nextAction: {label: '검사 흐름 만들기 (5단계)', step: 5},
    progress: {samples: 50, escapes: 2, overkills: 3, accuracy: 90, low: 79, high: 96}});
  publishAssistContext(evaluated);
  publishAssistContext({...evaluated, progress: {samples: 5, escapes: 0, overkills: 0, accuracy: null, low: null, high: null}});
  assert.deepEqual(currentAssistContext().progress, {samples: 5, escapes: 0, overkills: 0, accuracy: null, low: null, high: null}, 'no answers: no accuracy, the image count stays');
  publishAssistContext({...evaluated, status: 'blocked', blocker: '모델 m을(를) 학습한 뒤 데이터가 바뀌었습니다', nextAction: {label: '바뀐 내용과 되돌리기', step: 4},
    progress: {...evaluated.progress, dataChanged: true}});
  assert.equal(currentAssistContext().progress.dataChanged, true, 'the counts say they come from before the change');
  publishAssistContext(snapshot({stage: 5, status: 'blocked', blocker: '저장 버전과 현재 정답에 일치하는 전체 플로우 평가 필요', nextAction: {label: '검사 흐름 평가 확인 (5단계)', step: 5, target: 'flow-area-evaluate'},
    progress: {versionId: 'v1', evaluated: false, unsaved: false, reevaluate: true}}));
  assert.deepEqual(currentAssistContext().progress, {versionId: 'v1', evaluated: false, unsaved: false, reevaluate: true});
  assert.equal(notified, 4);
});

test('step 6 mirrors the images the run could not judge, and a new failure wakes listeners', () => {
  const {publishAssistContext, currentAssistContext, subscribeAssistContext} = load();
  let notified = 0;
  subscribeAssistContext(() => { notified += 1; });
  const inspected = snapshot({stage: 6, status: 'unknown', blocker: '검사 결과까지 확인했습니다.', nextAction: {label: '승인·패키지·대상 확인', step: 6, target: 'workflow-package'},
    progress: {inspected: 100, total: 100, ng: 3, failed: 5}});
  publishAssistContext(inspected);
  assert.deepEqual(currentAssistContext().progress, {inspected: 100, total: 100, ng: 3, failed: 5});
  publishAssistContext({...inspected, progress: {...inspected.progress, failed: 6}});
  assert.equal(currentAssistContext().progress.failed, 6);
  assert.equal(notified, 2);
});
