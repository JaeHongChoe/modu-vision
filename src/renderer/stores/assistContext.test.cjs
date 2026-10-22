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
