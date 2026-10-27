const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const {renderToStaticMarkup} = require('react-dom/server');

function load(file, mocks = {}) {
  const m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = name => name in mocks ? mocks[name] : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.React, esModuleInterop: true,
  }}).outputText, file);
  return m.exports;
}
const file = path.join(__dirname, 'AssistantTaskCard.tsx');
const catalog = load(path.join(__dirname, '../../services/assistantTasks/catalog.ts'));
// A missing feature is an empty render, so RED fails on the public behavior, not a module-loader error.
const Card = fs.existsSync(file) ? load(file, {'../../services/assistantTasks/catalog': catalog}).AssistantTaskCard : () => null;
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...[].concat(tree.props?.children || []).flat(Infinity).flatMap(nodes)];
const text = tree => tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'string' || typeof tree === 'number' ? String(tree) : [].concat(tree.props?.children || []).flat(Infinity).map(text).join('');
const step = (extra = {}) => ({id: 's1', action: 'save_split', args: {train_percent: 70, val_percent: 20, test_percent: 10}, label: '균열 자료 나누기', status: 'pending', attempt_id: null, receipt: null, error: null, ...extra});
const task = (extra = {}) => ({schema_version: 1, id: 'task_1', revision: 7, status: 'draft', goal: '균열 검사 만들기', plan_sha256: 'PRIVATE_HASH', binding: 'PRIVATE_BINDING', steps: [step()], approval: null, current_step_id: null, message: null, created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z', ...extra});
const render = (extra = {}) => Card({task: task(), busy: false, loading: false, error: null, ready: true, onApprove() {}, onPause() {}, onResume() {}, onRefresh() {}, onRestore() {}, onNavigate() {}, ...extra});
const buttons = tree => nodes(tree).filter(node => node.type === 'button');
const primary = tree => buttons(tree).filter(node => /\bworkspace-button--primary\b/.test(node.props.className || ''));
const button = (tree, label) => buttons(tree).find(node => text(node) === label);

test('a draft presents an ordered canonical plan and the settings being approved', () => {
  const tree = render();
  assert.ok(nodes(tree).some(node => node.type === 'section' && node.props['aria-label'] === '도우미 작업'));
  assert.ok(nodes(tree).some(node => node.type === 'ol' && node.props['aria-label'] === '실행 계획'));
  assert.match(text(tree), /자료 나누기/);
  assert.match(text(tree), /학습 70% · 검증 20% · 시험 10%/);
  assert.match(text(tree), /균열 자료 나누기/);
  assert.ok(!/PRIVATE_HASH|PRIVATE_BINDING|plan_sha256|binding/.test(text(tree)));
  assert.equal(primary(tree).length, 1);
  assert.match(text(primary(tree)[0]), /계획.*확인.*실행/);
});

test('approval captures the displayed identity and revision rather than reading a changed task object', () => {
  const shown = task(), calls = [];
  const tree = render({task: shown, onApprove: (...args) => calls.push(args)});
  shown.id = 'replacement'; shown.revision = 99;
  primary(tree)[0].props.onClick();
  assert.deepEqual(calls, [['task_1', 7]]);
});

test('unavailable approval is disabled and its retained callback does nothing', () => {
  for (const lock of [{ready: false}, {blocked: true}, {busy: true}, {loading: true}]) {
    const calls = [], tree = render({...lock, onApprove: (...args) => calls.push(args)});
    assert.equal(primary(tree)[0].props.disabled, true, JSON.stringify(lock));
    primary(tree)[0].props.onClick();
    assert.deepEqual(calls, []);
    assert.ok(nodes(tree).some(node => node.props?.role === 'status'));
  }
});

test('every task state has one primary action and all controls use shared workspace buttons', () => {
  for (const status of ['draft', 'ready', 'running', 'waiting_human', 'waiting_job', 'paused', 'needs_reconciliation', 'failed', 'completed']) {
    const tree = render({task: task({status, current_step_id: 's1'})});
    assert.equal(primary(tree).length, 1, status);
    assert.ok(buttons(tree).every(node => /(^|\s)workspace-button(\s|$)/.test(node.props.className || '')), status);
    assert.ok(nodes(tree).some(node => node.props?.role === 'status'), status);
  }
});

test('running work exposes a usable pause while busy and says training continues', () => {
  let pauses = 0;
  const tree = render({task: task({status: 'running', current_step_id: 's1'}), busy: true, onPause: () => pauses++});
  assert.match(text(primary(tree)[0]), /일시정지/);
  assert.equal(primary(tree)[0].props.disabled, false);
  primary(tree)[0].props.onClick(); assert.equal(pauses, 1);
  assert.match(text(tree), /이미 시작한 학습은.*계속/);
  assert.ok(!buttons(tree).some(node => /학습.*(취소|중단)/.test(text(node))));
  assert.match(text(tree), /현재 단계 1\/1/);
  assert.ok(nodes(tree).some(node => node.type === 'li' && node.props['aria-current'] === 'step'));
  assert.ok(!nodes(tree).some(node => node.type === 'progress'), 'no invented completion percentage');
});

test('human waiting opens the real review stage and resumes only through a separate explicit check', () => {
  const navigated = [], resumed = [];
  const tree = render({task: task({status: 'waiting_human', current_step_id: 's1', steps: [step({action: 'generate_label_drafts', args: {mode: 'phrase', prompt: '균열'}, status: 'waiting_human'})]}), onNavigate: value => navigated.push(value), onResume: () => resumed.push(true)});
  assert.match(text(tree), /사람 검토 대기/);
  assert.match(text(tree), /채택.*정답 확정.*직접/);
  primary(tree)[0].props.onClick(); assert.deepEqual(navigated, [2]); assert.deepEqual(resumed, []);
  button(tree, '검토 결과 확인하고 이어하기').props.onClick(); assert.deepEqual(resumed, [true]);
});

test('job waiting refreshes saved results and cannot imply the job is finished', () => {
  let reads = 0;
  const tree = render({task: task({status: 'waiting_job', steps: [step({action: 'start_training', args: {}, status: 'waiting_job', receipt: {job_id: 'job_24'}})]}), onRefresh: () => reads++});
  assert.match(text(tree), /작업 완료 대기/);
  assert.match(text(tree), /job_24/);
  primary(tree)[0].props.onClick(); assert.equal(reads, 1);
  assert.ok(!/100%|모든 작업을 완료/.test(text(tree)));
});

test('paused and uncertain tasks resume with a saved-result reconciliation explanation', () => {
  for (const status of ['paused', 'needs_reconciliation']) {
    let resumes = 0;
    const tree = render({task: task({status}), onResume: () => resumes++});
    assert.match(text(tree), /저장.*결과.*확인/);
    primary(tree)[0].props.onClick(); assert.equal(resumes, 1);
  }
});

test('failed work keeps the error and offers a read before any retry', () => {
  let reads = 0, resumes = 0;
  const tree = render({task: task({status: 'failed', steps: [step({status: 'failed', error: '정답을 먼저 확인하세요.'})]}), onRefresh: () => reads++, onResume: () => resumes++});
  assert.match(text(tree), /정답을 먼저 확인하세요/);
  assert.match(text(tree), /실패/);
  primary(tree)[0].props.onClick(); assert.equal(reads, 1); assert.equal(resumes, 0);
});

test('completed work shows bounded saved identifiers and counts without paths, arrays or false quality approval', () => {
  const navigated = [];
  const tree = render({task: task({status: 'completed', steps: [step({action: 'evaluate_model', args: {}, status: 'completed', receipt: {job_id: 'job_24', project_id: 'project_9', version_id: 'flow_v3', samples: 18, unknown: 2, report_path: '/private/report.html', source_dataset_path: '/private/images', images: Array.from({length: 100}, () => 'PRIVATE_IMAGE'), predictions: ['PRIVATE_PREDICTION']}})]}), onNavigate: value => navigated.push(value)});
  assert.match(text(tree), /job_24/); assert.match(text(tree), /project_9/); assert.match(text(tree), /flow_v3/);
  assert.match(text(tree), /평가 자료 수.*18/); assert.match(text(tree), /정답 미확정.*2/);
  assert.ok(!/\/private|PRIVATE_IMAGE|PRIVATE_PREDICTION/.test(text(tree)));
  assert.match(text(tree), /최종 승인.*직접/);
  primary(tree)[0].props.onClick(); assert.deepEqual(navigated, [4]);
});

test('empty, loading and error states give an accessible restore action with connection guards', () => {
  for (const props of [{}, {loading: true}, {error: '작업 기록을 읽지 못했습니다.'}, {ready: false}]) {
    let restored = 0;
    const tree = render({task: null, ...props, onRestore: () => restored++});
    assert.equal(primary(tree).length, 0); // The composer's send action stays primary until a plan exists.
    const restore=nodes(tree).find(node=>node.type==='button'&&text(node)==='저장된 작업 불러오기');
    assert.ok(restore);
    const disabled = Boolean(props.loading || props.ready === false);
    assert.equal(restore.props.disabled, disabled);
    restore.props.onClick(); assert.equal(restored, disabled ? 0 : 1);
    if (props.error) assert.ok(nodes(tree).some(node => node.props?.role === 'alert' && text(node).includes(props.error)));
  }
});

test('the plan stays bounded and the current step remains visible if incoming data exceeds the contract', () => {
  const steps = Array.from({length: 30}, (_, index) => step({id: `s${index + 1}`, label: `단계 ${index + 1}`}));
  const tree = render({task: task({status: 'running', steps, current_step_id: 's30'})});
  assert.equal(nodes(tree).filter(node => node.type === 'li').length, 24);
  assert.match(text(tree), /6개.*표시.*생략/);
  assert.match(text(tree), /현재 단계 30\/30/);
});

test('a partially displayed oversized plan cannot be approved', () => {
  let approvals = 0;
  const steps = Array.from({length: 25}, (_, index) => step({id: `s${index}`}));
  const tree = render({task: task({steps}), onApprove: () => approvals++});
  assert.equal(primary(tree)[0].props.disabled, true);
  primary(tree)[0].props.onClick(); assert.equal(approvals, 0);
});

test('an oversized human review plan cannot continue through a secondary action', () => {
  let resumed = 0;
  const steps = Array.from({length: 25}, (_, index) => step({id: `s${index}`}));
  const tree = render({task: task({status: 'waiting_human', steps}), onResume: () => resumed++});
  const resume = button(tree, '검토 결과 확인하고 이어하기');
  assert.equal(resume.props.disabled, true);
  resume.props.onClick(); assert.equal(resumed, 0);
});

test('connection, loading and conflict guards protect every visible task callback', () => {
  for (const status of ['ready', 'running', 'waiting_human', 'waiting_job', 'paused', 'needs_reconciliation', 'failed', 'completed']) {
    for (const lock of [{ready: false}, {blocked: true}, {loading: true}]) {
      const calls = [];
      const tree = render({task: task({status}), ...lock, onPause: () => calls.push('pause'), onResume: () => calls.push('resume'), onRefresh: () => calls.push('refresh'), onNavigate: () => calls.push('navigate')});
      for (const node of buttons(tree)) {assert.equal(node.props.disabled, true, `${status}: ${JSON.stringify(lock)}`); node.props.onClick();}
      assert.deepEqual(calls, []);
    }
  }
});

test('untrusted goal and labels render as text while unrecognized arguments stay hidden', () => {
  const attack = '<img src=x onerror=alert(1)>';
  const tree = render({task: task({goal: attack, steps: [step({label: '<script>attack()</script>', args: {train_percent: 70, val_percent: 20, test_percent: 10, binding: 'PRIVATE_ARG', html: attack}})]})});
  const html = renderToStaticMarkup(tree);
  assert.match(html, /&lt;img/); assert.match(html, /&lt;script&gt;/);
  assert.ok(!/<img|<script|PRIVATE_ARG/.test(html));
  assert.ok(!nodes(tree).some(node => node.props?.dangerouslySetInnerHTML));
});
test('mismatched recovery offers explicit local settings restoration without approving or resuming',()=>{
 let restored=0,approved=0,resumed=0;
 const tree=render({contextMatches:false,settingsRecoveryLabel:'저장 설정 복원 · CPU · 배치 8',onRestoreSettings:()=>restored++,onApprove:()=>approved++,onResume:()=>resumed++});
 assert.equal(primary(tree).length,1);assert.match(text(primary(tree)[0]),/저장 설정 복원/);primary(tree)[0].props.onClick();
 assert.equal(restored,1);assert.equal(approved,0);assert.equal(resumed,0);assert.match(text(tree),/학습을 시작하지 않습니다/);
 const mismatched=render({contextMatches:false});assert.equal(primary(mismatched)[0].props.disabled,true);
});
