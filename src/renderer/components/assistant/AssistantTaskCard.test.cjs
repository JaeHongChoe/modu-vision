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
  m.require = name => name in mocks ? mocks[name] : name.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(file),name)+'.ts')?load(path.resolve(path.dirname(file),name)+'.ts',mocks):original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.React, esModuleInterop: true,
  }}).outputText, file);
  return m.exports;
}
const file = path.join(__dirname, 'AssistantTaskCard.tsx');
const catalog = load(path.join(__dirname, '../../services/assistantTasks/catalog.ts'));
// A missing feature is an empty render, so RED fails on the public behavior, not a module-loader error.
const presentationFile = path.join(__dirname, '../../services/assistantTasks/taskRecoveryPresentation.ts');
const presentation = fs.existsSync(presentationFile) ? load(presentationFile) : {};
const Card = fs.existsSync(file) ? load(file, {'../../services/assistantTasks/catalog': catalog, '../../services/assistantTasks/taskRecoveryPresentation': presentation}).AssistantTaskCard : () => null;
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
  assert.match(text(tree), /초안 검토/);
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
  assert.match(text(tree), /품질 기준 충족과 운영 배포 상태.*저장된 근거/);
  primary(tree)[0].props.onClick(); assert.deepEqual(navigated, [4]);
});
test('machine input and resource waits name their actual reason without a routine human review gate',()=>{
 for(const [code,label] of [['waiting_input','입력 필요'],['waiting_resource','실행 자원 대기'],['failed','작업 오류']]){
  const row=step({status:'waiting_human',receipt:{not_started:true,reason_code:code}});
  const tree=render({task:task({status:'waiting_human',current_step_id:row.id,steps:[row]})});
  assert.match(text(tree),new RegExp(label));assert.doesNotMatch(text(tree),/계획 확인하고 실행/);
 }
});
test('persisted policy assessments show quality status and reasons separately from completed evaluation',()=>{
 for(const [status,label] of [['not_qualified','품질 기준 미달'],['input_needed','품질 판정 입력 필요'],['qualified','품질 기준 충족'],['pending','품질 판정 결과 확인 중']]){
  const row=step({action:'evaluate_model',status:'completed',receipt:{evaluation_id:'evaluation_a',quality_assessment:{status,decision_source:'policy',reasons:['recall below configured bound']}}});
  const tree=render({task:task({status:'completed',authorization:{source:'user_request'},steps:[row]})});
  assert.match(text(tree),new RegExp(label));assert.match(text(tree),/recall below configured bound/);
  if(status==='pending')assert.match(text(primary(tree)[0]),/품질 판정/);
 }
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
  const resume = button(tree, '설정 확인하고 이어하기');
  assert.equal(resume.props.disabled, true);
  resume.props.onClick(); assert.equal(resumed, 0);
});

test('cancelled folder choice offers folder selection through explicit resume and gives its message first', () => {
  const calls = [], message = '자료 폴더 선택을 취소했습니다. 사용할 폴더를 선택하세요.';
  const tree = render({task: task({status: 'waiting_human', current_step_id: 's1', message, steps: [step({action: 'import_dataset', status: 'waiting_human', args: {}, receipt: {not_started: true, reason_code: 'waiting_input'}})]}), onResume: () => calls.push('resume'), onNavigate: () => calls.push('navigate')});
  assert.equal(text(primary(tree)[0]), '폴더 선택하고 이어하기');
  const status = nodes(tree).find(node => node.props?.role === 'status');
  assert.equal(text([].concat(status.props.children).flat(Infinity)[0]), message);
  assert.ok(!/정답 확정|초안 채택/.test(text(tree)));
  primary(tree)[0].props.onClick(); assert.deepEqual(calls, ['resume']);
});

test('truth review and setup waits name their actual prerequisite instead of label adoption', () => {
  for (const [action, receipt, message, label] of [
    ['evaluate_flow', {truth_review_required: true}, '5단계에서 정답을 확정하세요.', '정답 확인 화면 열기'],
    ['configure_training', {not_started: true}, '3단계에서 실행 위치를 선택하세요.', '설정 화면 열기'],
  ]) {
    const tree = render({task: task({status: 'waiting_human', message, steps: [step({action, receipt, status: 'waiting_human'})]})});
    assert.equal(text(primary(tree)[0]), label);
    assert.ok(!text(tree).includes('라벨 초안 채택'));
  }
});

test('failed or stopped started work is a saved-result check rather than draft review or setup', async t => {
  for (const [action, jobStatus, receipt, label, stage] of [
    ['generate_label_drafts', 'failed', {batch_id: 'batch_failed', generated: 0}, '초안 작업 기록 열기', 2],
    ['generate_label_drafts', 'stopped', {batch_id: 'batch_stopped', generated: 0}, '초안 작업 기록 열기', 2],
    ['start_training', 'failed', {job_id: 'job_failed'}, '학습 작업 기록 열기', 3],
    ['start_training', 'stopped', {job_id: 'job_stopped'}, '학습 작업 기록 열기', 3],
    ['start_training', 'aborted', {job_id: 'job_aborted'}, '학습 작업 기록 열기', 3],
    ['start_training', 'interrupted', {job_id: 'job_interrupted'}, '학습 작업 기록 열기', 3],
  ]) {
    await t.test(`${action} ${jobStatus}`, () => {
    const calls = [], message = '작업 오류: 필요한 모델 파일을 읽지 못했습니다.';
    const shown = task({status: 'waiting_human', message, current_step_id: 's1', steps: [step({action, status: 'waiting_human', args: {}, receipt: {not_started: false, status: jobStatus, diagnostics: {source: action, errors: [{message: '필요한 모델 파일을 읽지 못했습니다.'}]}, ...receipt}})]});
    const guidance = presentation.taskWaitingGuidance(shown, shown.steps[0]);
    assert.equal(guidance.label, label); assert.equal(guidance.action, 'navigate'); assert.equal(guidance.secondary, null);
    assert.match(guidance.status, /결과 확인/); assert.ok(!/검토|채택|정답|설정 확인/.test(guidance.status + guidance.description));
    const tree = render({task: shown, onNavigate: step => calls.push(['navigate', step]), onResume: () => calls.push('resume'), onApprove: () => calls.push('approve')});
    assert.equal(text(primary(tree)[0]), label);
    assert.match(text(tree), /필요한 모델 파일을 읽지 못했습니다/);
    assert.ok(!buttons(tree).some(node => /검토.*이어하기|설정 확인.*이어하기/.test(text(node))));
    primary(tree)[0].props.onClick(); assert.deepEqual(calls, [['navigate', stage]], `${action} ${jobStatus}`);
    });
  }
});

test('completed available drafts stay reviewable and inputs not dispatched stay setup checks', () => {
  const completedDraft = task({status: 'waiting_human', message: '초안을 만들었습니다. 검토한 뒤 이어 가세요.', steps: [step({action: 'generate_label_drafts', status: 'waiting_human', receipt: {not_started: false, status: 'completed', batch_id: 'batch_ready', generated: 3}})]});
  const review = presentation.taskWaitingGuidance(completedDraft, completedDraft.steps[0]);
  assert.equal(review.status, '초안 검토 대기'); assert.equal(review.label, '검토 화면 열기'); assert.equal(review.secondary, '검토 결과 확인하고 이어하기');
  for (const action of ['generate_label_drafts', 'start_training']) {
    const shown = task({status: 'waiting_human', message: '필요한 모델과 입력 자료를 먼저 준비하세요.', steps: [step({action, status: 'waiting_human', receipt: {not_started: true}})]});
    const setup = presentation.taskWaitingGuidance(shown, shown.steps[0]);
    assert.equal(setup.status, '설정 확인 필요'); assert.equal(setup.label, '설정 화면 열기');
    assert.equal(text(primary(render({task: shown}))[0]), '설정 화면 열기');
  }
});

test('new request captures the shown plan without approving resuming or calling a provider', () => {
  const shown = task({status: 'waiting_human'}), calls = [];
  const tree = render({task: shown, onPrepareNewRequest: (...args) => calls.push(['prepare', ...args]), onApprove: () => calls.push('approve'), onResume: () => calls.push('resume')});
  const prepare = button(tree, '이 계획 멈추고 새 요청'); assert.ok(prepare);
  shown.id = 'changed'; shown.revision = 80;
  prepare.props.onClick(); assert.deepEqual(calls, [['prepare', 'task_1', 7]]);
  assert.match(text(tree), /이미 시작한 학습.*계속/);
});

test('unresolved steps and busy writes keep new request disabled while explaining what is pending', () => {
  for (const extra of [{busy: true}, {loading: true}, {blocked: true}, ...['running', 'uncertain', 'waiting_job'].map(status => ({task: task({status: 'paused', steps: [step({status})]})}))]) {
    let prepared = 0;
    const tree = render({...extra, onPrepareNewRequest: () => prepared++});
    const prepare = button(tree, '이 계획 멈추고 새 요청'); assert.ok(prepare);
    assert.equal(prepare.props.disabled, true); prepare.props.onClick(); assert.equal(prepared, 0);
    assert.match(text(tree), /기록|확인|진행|완료/);
  }
});

test('prepared mode keeps the earlier plan as history and removes old execution controls', () => {
  const calls = [];
  const tree = render({planningNew: true, task: task({status: 'draft'}), onApprove: () => calls.push('approve'), onResume: () => calls.push('resume'), onRefresh: () => calls.push('refresh')});
  assert.match(text(tree), /새 요청을 입력하세요/); assert.match(text(tree), /이전 계획 기록/);
  assert.equal(primary(tree).length, 0);
  assert.ok(!buttons(tree).some(node => /실행|이어하기|일시정지/.test(text(node))));
  assert.deepEqual(calls, []);
});

test('history selection reads a chosen saved record and a pending read can be stopped without execution', () => {
  const calls = [];
  const tree = render({readPending: true, loading: true, history: [{id: 'old', goal: '다른 프로젝트 계획', status: 'completed', updated_at: '2026-10-28T01:02:00Z', contextMatches: false}], onSelectHistory: id => calls.push(['history', id]), onStopRead: () => calls.push('stop')});
  const stop = button(tree, '상태 확인 대기 중지'); assert.ok(stop); assert.equal(stop.props.disabled, false); stop.props.onClick();
  assert.deepEqual(calls, ['stop']); assert.match(text(tree), /다른 프로젝트/);
  const ready = render({history: [{id: 'old', goal: '이전 검사', status: 'completed', updated_at: '2026-10-28T01:02:00Z', contextMatches: true}], onSelectHistory: id => calls.push(['history', id])});
  const select = buttons(ready).find(node => text(node).includes('이전 검사')); assert.ok(select); select.props.onClick();
  assert.deepEqual(calls, ['stop', ['history', 'old']]);
});

test('job refresh honestly says it can continue the plan', () => {
  const tree = render({task: task({status: 'waiting_job'})});
  assert.equal(text(primary(tree)[0]), '저장 결과 확인하고 계획 계속');
});

test('a read stop never exposes an escape from an executing write', () => {
  let stops = 0;
  const tree = render({busy: true, readPending: true, onStopRead: () => stops++});
  const stop = button(tree, '상태 확인 대기 중지'); assert.ok(stop); assert.equal(stop.props.disabled, true);
  stop.props.onClick(); assert.equal(stops, 0);
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
