// 원클릭 자동 진행 (product direction 5.2): it follows the footer's readiness, takes only the actions a machine may take,
// stops where a person decides, never repeats a finished step, and resumes from where it stopped.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load() {
  const filename = path.join(__dirname, 'autoProgress.ts'), m = new Module(filename, module);
  m.filename = filename;
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  return m.exports;
}
const ctx = (stage, label, step, extra = {}) => ({scope: 's', stage, status: 'ready', blocker: null, nextAction: {label, step, ...(extra.target ? {target: extra.target} : {})}, progress: null, ...extra});

test('each readiness picture maps to one move: advance, act, wait or a stop for a person', () => {
  const {decide} = load();
  assert.deepEqual(decide(ctx(1, '프로젝트 만들기·열기', 1, {status: 'blocked'})).kind, 'stop');
  assert.match(decide(ctx(1, '데이터 가져오기 (1단계)', 1, {status: 'blocked'})).reason, /사람이 고릅니다/);
  assert.deepEqual(decide(ctx(1, '라벨 확인하기 (2단계)', 2)), {kind: 'advance', step: 2});
  // Step 2: drafts and missing labels are the person's; a ready step 2 moves on even with unlabelled images left.
  const drafts = decide(ctx(2, '초안 검토', 2, {target: 'draft-review-queue', blocker: '초안 3장 검토 대기'}));
  assert.equal(drafts.kind, 'stop'); assert.match(drafts.reason, /초안 채택은 사람이/);
  assert.match(decide(ctx(2, '라벨 작업 열기 (2단계)', 2, {status: 'blocked', blocker: '승인 이미지 부족'})).reason, /정답 라벨은 사람이/);
  assert.deepEqual(decide(ctx(2, '초안 만들기', 2, {target: 'draft-create'})), {kind: 'advance', step: 3});
  // Step 3: the split is saved by itself, class problems stop, training starts or is waited for.
  assert.deepEqual(decide(ctx(3, '분할 다시 적용 (1단계)', 1, {status: 'blocked'})), {kind: 'act', action: 'split'});
  assert.deepEqual(decide(ctx(3, '데이터 다시 확인 (1단계)', 1, {status: 'blocked', blocker: '분할이 저장되지 않았습니다 (자동 분할로는 4단계 평가를 할 수 없습니다)'})), {kind: 'act', action: 'split'});
  assert.equal(decide(ctx(3, '데이터 다시 확인 (1단계)', 1, {status: 'blocked', blocker: '분류에는 이미지가 있는 클래스가 2개 이상 필요합니다 (지금 1개)'})).kind, 'stop');
  assert.deepEqual(decide(ctx(3, '학습 시작 버튼으로 이동 (3단계)', 3)), {kind: 'act', action: 'train'});
  assert.deepEqual(decide(ctx(3, '학습 진행 상태 보기', 3, {status: 'checking'})), {kind: 'wait', training: true});
  const finished = {epoch: 10, totalEpochs: 10, etaSeconds: null, completed: true};
  assert.deepEqual(decide(ctx(3, '학습 결과 평가 (4단계)', 4, {progress: finished})), {kind: 'advance', step: 4});
  assert.deepEqual(decide(ctx(3, '학습 결과 평가 (4단계)', 4, {progress: {...finished, completed: false}})), {kind: 'act', action: 'train'}, 'a run on older data is trained again');
  // Steps 4–6.
  assert.deepEqual(decide(ctx(4, '시험 이미지로 평가하기 (4단계)', 4, {status: 'unknown'})), {kind: 'act', action: 'evaluate'});
  assert.deepEqual(decide(ctx(4, '바뀐 내용과 되돌리기', 4, {status: 'blocked'})), {kind: 'advance', step: 3});
  // Found in the real app: the active flow still names the model trained before the data changed. Step 4 already
  // evaluated a current model, so the flow is saved again around it instead of training once more (a 3→4→5→3 circle).
  assert.deepEqual(decide(ctx(5, '바뀐 내용과 되돌리기', 5, {status: 'blocked'})), {kind: 'act', action: 'saveFlow'});
  assert.deepEqual(decide(ctx(6, '바뀐 내용과 되돌리기', 6, {status: 'blocked'})), {kind: 'advance', step: 5});
  assert.deepEqual(decide(ctx(4, '검사 흐름 만들기 (5단계)', 5)), {kind: 'advance', step: 5});
  assert.deepEqual(decide(ctx(5, '검사 흐름 저장·평가 (5단계)', 5, {status: 'blocked'})), {kind: 'act', action: 'saveFlow'});
  assert.deepEqual(decide(ctx(6, '검사 흐름 저장·평가 (5단계)', 5, {status: 'blocked'})), {kind: 'advance', step: 5});
  assert.deepEqual(decide(ctx(5, '검사 흐름 평가 확인 (5단계)', 5, {status: 'blocked'})), {kind: 'act', action: 'evaluateFlow'});
  assert.deepEqual(decide(ctx(5, '검사 결과 확인 (6단계)', 6)), {kind: 'advance', step: 6});
  assert.deepEqual(decide(ctx(6, '승인·패키지·대상 확인', 6, {status: 'unknown'})), {kind: 'act', action: 'inspect'});
  const inspected = decide(ctx(6, '승인·패키지·대상 확인', 6, {status: 'unknown', progress: {inspected: 5, total: 5, ng: 1}}));
  assert.equal(inspected.kind, 'done'); assert.match(inspected.reason, /승인·패키지·대상 확인을 직접/);
  assert.deepEqual(decide(ctx(3, '준비도 다시 확인', 3, {status: 'unknown'})), {kind: 'wait', refresh: true});
});

// A small app: the readiness picture is derived from a state the fake actions change, like the real evidence would.
function app(start) {
  const world = {stage: 1, split: false, labels: 30, drafts: 0, model: false, training: false, evaluated: false, flow: false, flowEvaluated: false, inspected: false, truth: true, ...start};
  const calls = [];
  const picture = () => {
    const w = world;
    if (w.stage === 1) return ctx(1, '라벨 확인하기 (2단계)', 2);
    if (w.stage === 2) return w.drafts ? ctx(2, '초안 검토', 2, {target: 'draft-review-queue', blocker: `초안 ${w.drafts}장 검토 대기`}) : w.labels < 20 ? ctx(2, '라벨 작업 열기 (2단계)', 2, {status: 'blocked', blocker: '승인 이미지 부족'}) : ctx(2, '모델 학습 시작 (3단계)', 3);
    if (w.stage === 3) {
      if (!w.split) return ctx(3, '분할 다시 적용 (1단계)', 1, {status: 'blocked'});
      if (w.training) { w.training = false; w.model = true; return ctx(3, '학습 진행 상태 보기', 3, {status: 'checking'}); }
      return w.model ? ctx(3, '학습 결과 평가 (4단계)', 4, {progress: {epoch: 5, totalEpochs: 5, etaSeconds: null, completed: true}}) : ctx(3, '학습 시작 버튼으로 이동 (3단계)', 3);
    }
    if (w.stage === 4) return w.evaluated ? ctx(4, '검사 흐름 만들기 (5단계)', 5) : ctx(4, '시험 이미지로 평가하기 (4단계)', 4, {status: 'unknown'});
    if (w.stage === 5) return !w.flow ? ctx(5, '검사 흐름 저장·평가 (5단계)', 5, {status: 'blocked'}) : !w.flowEvaluated ? ctx(5, '검사 흐름 평가 확인 (5단계)', 5, {status: 'blocked'}) : ctx(5, '검사 결과 확인 (6단계)', 6);
    return ctx(6, '승인·패키지·대상 확인', 6, {status: 'unknown', progress: w.inspected ? {inspected: 5, total: 5, ng: 0} : null});
  };
  let clock = 0;
  const deps = {
    snapshot: () => picture(),
    setStep: async step => { calls.push(`step ${step}`); world.stage = step; },
    refresh: () => {},
    act: async action => {
      calls.push(action);
      if (action === 'split') world.split = true;
      if (action === 'train') world.training = true;
      if (action === 'evaluate') world.evaluated = true;
      if (action === 'saveFlow') world.flow = true;
      if (action === 'evaluateFlow') { if (!world.truth) return {person: '정답 확인 필요', step: 5, target: 'flow-area-evaluate'}; world.flowEvaluated = true; }
      if (action === 'inspect') world.inspected = true;
      return null;
    },
    sleep: async ms => { clock += ms; },
    cancelled: () => false,
    now: () => clock,
  };
  return {world, calls, deps};
}

test('from step 1 it runs every machine action once, in order, up to the inspected test images', async () => {
  const {runAutoProgress} = load();
  const {calls, deps} = app();
  const states = [];
  const result = await runAutoProgress(deps, state => states.push(state.state), {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'done');
  assert.deepEqual(calls, ['step 2', 'step 3', 'split', 'train', 'step 4', 'evaluate', 'step 5', 'saveFlow', 'evaluateFlow', 'step 6', 'inspect']);
  assert.match(result.log.at(-1).text, /승인·패키지·대상 확인을 직접/);
  assert.equal(states[0], 'running');
});

test('it stops for a person (drafts, labels, truth) and, pressed again, resumes there without repeating finished steps', async () => {
  const {runAutoProgress} = load();
  const {world, calls, deps} = app({drafts: 2});
  let result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped');
  assert.match(result.stop.reason, /초안 채택은 사람이/);
  assert.equal(result.stop.step, 2);
  assert.deepEqual(calls, ['step 2']);
  world.drafts = 0; world.truth = false;
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped');
  assert.deepEqual(result.stop, {reason: '정답 확인 필요', step: 5, target: 'flow-area-evaluate'});
  world.truth = true;
  calls.length = 0;
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'done');
  assert.deepEqual(calls, ['evaluateFlow', 'step 6', 'inspect'], 'the split, the training, the evaluation and the saved flow are not done again');
});

test('a refusal fails with the action named, an action the readiness keeps asking for stops after two tries, and waits are bounded', async () => {
  const {runAutoProgress} = load();
  let {deps} = app({stage: 4});
  deps.act = async () => '모델 파일을 읽지 못했습니다';
  let result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'failed');
  assert.match(result.stop.reason, /시험 이미지로 모델 평가 \(4단계\): 모델 파일을 읽지 못했습니다/);
  ({deps} = app({stage: 4}));
  const tried = [];
  deps.act = async action => { tried.push(action); return null; };
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped');
  assert.deepEqual(tried, ['evaluate', 'evaluate']);
  assert.match(result.stop.reason, /했지만 아직/);
  ({deps} = app({stage: 3}));
  deps.snapshot = () => ctx(3, '데이터 변경 상태 보기', 1, {status: 'checking'});
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 1000, settleMs: 5000});
  assert.equal(result.state, 'failed');
  assert.match(result.stop.reason, /확인이 끝나지 않았습니다/);
});

test('right after an action it waits for the readiness to catch up instead of taking the same action again', async () => {
  const {runAutoProgress} = load();
  const {world, calls, deps} = app({stage: 3, split: true});
  // The training store reports the run a moment late: two more pictures still name the start button.
  let late = 2;
  const real = deps.snapshot;
  deps.snapshot = () => world.training && late-- > 0 ? ctx(3, '학습 시작 버튼으로 이동 (3단계)', 3) : real();
  const result = await runAutoProgress(deps, () => {}, {pollMs: 1000, afterActMs: 20_000});
  assert.equal(result.state, 'done');
  assert.equal(calls.filter(call => call === 'train').length, 1);
});

test('stopping by the person ends the run at the next pass, and the dialog\'s defaults line names what will run', async () => {
  const {runAutoProgress} = load();
  const {deps} = app();
  let stop = false;
  deps.cancelled = () => stop;
  const original = deps.setStep;
  deps.setStep = async step => { await original(step); stop = true; };
  const result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'cancelled');
  const source = fs.readFileSync(path.join(__dirname, 'AutoProgressDialog.tsx'), 'utf8');
  assert.match(source, /학습은 3단계에 보이는 설정/);
  assert.match(source, /항상 멈추는 곳 \(사람이 합니다\)/);
  assert.match(source, /정답 확인은 사람이 합니다/, 'flow truth is never confirmed by the run');
  assert.doesNotMatch(source, /approve|승인 요청|deploy/i, 'no approval or deployment call');
  const footer = fs.readFileSync(path.join(__dirname, 'WizardFooter.tsx'), 'utf8');
  assert.equal(footer.match(/workspace-button--primary/g).length, 1, 'the footer keeps one primary action');
  assert.match(footer, /aria-haspopup="dialog" onClick=\{\(\)=>setAutoOpen\(true\)\}/);
});

test('a step entered again and again stops the run instead of circling, and a step back says why', async () => {
  const {runAutoProgress} = load();
  // A readiness that keeps sending step 4 back to step 3 while step 3 says its model is done: the run circles 3↔4.
  const {world, deps} = app({stage: 3});
  deps.snapshot = () => world.stage === 3
    ? ctx(3, '학습 결과 평가 (4단계)', 4, {progress: {epoch: 5, totalEpochs: 5, etaSeconds: null, completed: true}})
    : ctx(4, '학습 모델 확인 (3단계)', 3, {status: 'blocked', blocker: '선택 모델의 현재 체크포인트·데이터 근거 확인 필요'});
  const result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10, settleMs: 200});
  assert.equal(result.state, 'stopped');
  assert.match(result.stop.reason, /여러 번 다시 거쳤지만/);
  assert.match(result.log.map(row => row.text).join('\n'), /4단계에서 3단계로 돌아감: 선택 모델의 현재 체크포인트·데이터 근거 확인 필요/);
});

test('a truth stop carries what the dialog needs to let the person confirm in place, and the dialog keeps one primary button', async () => {
  const {runAutoProgress} = load();
  const {deps} = app({stage: 5, flow: true});
  deps.act = async action => action === 'evaluateFlow' ? {person: '정답 확인 필요', step: 5, target: 'flow-area-evaluate', confirm: {kind: 'truth', cohortId: 'cohort_1', versionId: 'v1', images: 3}} : null;
  const result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.deepEqual(result.stop.confirm, {kind: 'truth', cohortId: 'cohort_1', versionId: 'v1', images: 3});
  const source = fs.readFileSync(path.join(__dirname, 'AutoProgressDialog.tsx'), 'utf8');
  assert.match(source, /flowEvaluation\.confirmTruthFromLabels\(confirm\.cohortId, confirm\.versionId, reviewer\.trim\(\)\)/, 'the same call as step 5, with the reviewer the person typed');
  assert.match(source, /disabled=\{busy \|\| !reviewer\.trim\(\)\}/, 'no truth without a named reviewer');
  assert.match(source, /className=\{run\?\.stop\?\.confirm && !running \? 'workspace-button' : 'workspace-button workspace-button--primary'\}/, 'the confirm button is the one primary while it shows');
});

test('a flow save with no model on the current data goes back to step 3 by itself, then saves the flow around the new model', async () => {
  const {runAutoProgress} = load();
  // The data changed after training: step 5 asks for the flow save, but there is no current model to put in it.
  const {world, calls, deps} = app({stage: 5, split: true, model: false});
  const act = deps.act;
  deps.act = async action => action === 'saveFlow' && !world.model ? {back: 3, reason: '검사 흐름에 넣을 현재 데이터의 모델이 없습니다'} : act(action);
  const result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'done');
  assert.deepEqual(calls.slice(0, 7), ['step 3', 'train', 'step 4', 'evaluate', 'step 5', 'saveFlow', 'evaluateFlow']);
  assert.match(result.log.map(row => row.text).join('\n'), /5단계에서 3단계로 돌아감: 검사 흐름에 넣을 현재 데이터의 모델이 없습니다/);
  // A save that keeps sending the run back still ends at the circling stop.
  const again = app({stage: 5, split: true, model: true});
  const againAct = again.deps.act;
  again.deps.act = async action => action === 'saveFlow' ? (again.calls.push(action), {back: 3, reason: '현재 모델 없음'}) : againAct(action);
  const circled = await runAutoProgress(again.deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(circled.state, 'stopped');
  assert.match(circled.stop.reason, /검사 흐름 저장 \(5단계\)을\(를\) 했지만 아직/);
  assert.deepEqual(again.calls.filter(call => call === 'saveFlow').length, 2);
});

test('a flow saved by the run is what step 5 shows: its versions and its model check are read again', () => {
  const studio = fs.readFileSync(path.join(__dirname, '..', 'flowchart', 'FlowchartStudio.tsx'), 'utf8');
  const dialog = fs.readFileSync(path.join(__dirname, 'AutoProgressDialog.tsx'), 'utf8');
  assert.match(dialog, /await api\.flowchart\.savePipeline\(pipeline, task, folder\);[\s\S]*announceFlowSaved\(\);/);
  assert.match(studio, /useEffect\(\(\) => subscribeFlowSaved\(\(\) => setExternalSave\(\(value\) => value \+ 1\)\), \[\]\);/);
  assert.match(studio, /\}, \[folderPath, hasSelectedFolder, contextRevision, verificationRetry, externalSave\]\);/, 'the saved versions');
  assert.match(studio, /verificationRetry, task, isProjectBusy, externalSave\]\);/, 'the model check, so the old model\'s warning does not stay up');
});

test('an empty project stops at step 2 for its first labels instead of going to step 3 to be sent back', () => {
  const {decide} = load();
  const empty = decide(ctx(2, '직접 라벨 달기', 2, {target: 'labeling-canvas', progress: {confirmed: 0, drafts: 0, remaining: 24}}));
  assert.equal(empty.kind, 'stop');
  assert.equal(empty.step, 2); assert.equal(empty.target, 'draft-review-queue');
  assert.match(empty.reason, /아직 라벨이 한 장도 없습니다/);
  assert.deepEqual(decide(ctx(2, '직접 라벨 달기', 2, {target: 'labeling-canvas', progress: {confirmed: 20, drafts: 0, remaining: 4}})), {kind: 'advance', step: 3},
    'with labels, the unlabeled rest stays out of training and the run goes on');
});

// Real-app QA (2026-10-24): 24 drafts adopted, 30 still waiting. The run stopped at the drafts with only '2단계 그 화면으로',
// so a person who wanted to train on the 24 confirmed labels first had no way on. The stop now offers that choice; the
// run continues only after the person presses it, and drafts stay out of training (they are not labels).
test('waiting drafts stop the run but, with confirmed labels, offer to leave them and go on to training', () => {
  const {decide} = load();
  const progress = {confirmed: 24, drafts: 30, remaining: 0};
  const waiting = ctx(2, '초안 검토', 2, {target: 'draft-review-queue', blocker: '초안 30장 검토 대기 · 확정 24장', progress});
  const offered = decide(waiting);
  assert.equal(offered.kind, 'stop'); assert.match(offered.reason, /초안 채택은 사람이/); assert.equal(offered.deferDrafts, 24);
  assert.deepEqual(decide(waiting, {deferDrafts: true}), {kind: 'advance', step: 3}, 'pressed: on to step 3');
  const none = ctx(2, '초안 검토', 2, {target: 'draft-review-queue', blocker: '초안 54장 검토 대기', progress: {confirmed: 0, drafts: 54, remaining: 0}});
  assert.equal(decide(none).deferDrafts, undefined, 'nothing confirmed: nothing to train on');
  assert.equal(decide(none, {deferDrafts: true}).kind, 'stop');
  const blocked = {...waiting, status: 'blocked'};
  assert.equal(decide(blocked).deferDrafts, undefined, 'a blocked review policy is not offered a way round');
  assert.equal(decide(blocked, {deferDrafts: true}).kind, 'stop');
});

test('the choice reaches the run, is kept for that run only, and the dialog offers it at the stop', async () => {
  const {runAutoProgress} = load();
  const {world, calls, deps} = app({drafts: 30});
  const picture = deps.snapshot;
  deps.snapshot = () => { const value = picture(); return value.stage === 2 && world.drafts ? {...value, progress: {confirmed: 24, drafts: world.drafts, remaining: 0}} : value; };
  let result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped'); assert.equal(result.stop.deferDrafts, 24); assert.deepEqual(calls, ['step 2']);
  world.stage = 2; calls.length = 0;
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10, choices: {deferDrafts: true}});
  assert.equal(result.state, 'done'); assert.equal(world.drafts, 30, 'the drafts are left as they were');
  assert.deepEqual(calls.slice(0, 3), ['step 3', 'split', 'train']);
  world.stage = 2; calls.length = 0;
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped', 'a new run asks again');
  const dialog = fs.readFileSync(path.join(__dirname, 'AutoProgressDialog.tsx'), 'utf8');
  assert.match(dialog, /\{run\.stop\.deferDrafts && <button type="button" className="workspace-button" onClick=\{\(\) => start\(\{ deferDrafts: true \}\)\}>남은 초안은 두고 확정한 라벨 \{run\.stop\.deferDrafts\}장으로 학습까지 이어 가기<\/button>\}/);
  assert.match(dialog, /void runAutoProgress\(appDeps\(\(\) => get\(\)\.cancelRequested\), \(run\) => set\(\{ run \}\), \{ choices \}\);/);
});

// 5.3-4: a model that missed test defects is looked at before step 5. The footer sends the person to the wrong images;
// an automatic run stops there too and, like the draft stop, offers going on for this run only.
test('a model that missed test defects stops the run with a one-press go-on for this run only', async () => {
  const {decide, runAutoProgress, PERSON_STOPS} = load();
  const missed = ctx(4, '틀린 이미지 보기', 4, {status: 'blocked', target: 'workflow-missed-defects', blocker: '모델이 시험 이미지의 불량을 놓쳤습니다', progress: {samples: 3, escapes: 3, overkills: 0, accuracy: 0, low: 0, high: 56}});
  const stopped = decide(missed);
  assert.deepEqual([stopped.kind, stopped.step, stopped.target, stopped.missed], ['stop', 4, 'workflow-missed-defects', {escapes: 3, samples: 3}]);
  assert.equal(stopped.reason, '모델이 시험 이미지의 불량을 놓쳤습니다 · 놓친 이미지를 보고 고칠지, 이대로 갈지는 사람이 정합니다.');
  assert.deepEqual(decide(missed, {acceptMissed: {escapes: 3, samples: 3}}), {kind: 'advance', step: 5});
  // The go-on is for the result the person saw: more misses or other test images stop the run again with the new count.
  const worse = ctx(4, '틀린 이미지 보기', 4, {status: 'blocked', target: 'workflow-missed-defects', progress: {samples: 3, escapes: 3, overkills: 0, accuracy: 0, low: 0, high: 56}});
  assert.equal(decide(worse, {acceptMissed: {escapes: 2, samples: 3}}).kind, 'stop');
  assert.deepEqual(decide(worse, {acceptMissed: {escapes: 3, samples: 5}}).missed, {escapes: 3, samples: 3});
  assert.deepEqual(decide(worse, {acceptMissed: {escapes: 4, samples: 3}}), {kind: 'advance', step: 5}, 'fewer misses than accepted on the same images');
  // Results recorded but not on the screen: wait for them (the run's bounded wait ends in a stop), never go on past them.
  assert.deepEqual(decide(ctx(4, '새로고침 버튼으로 이동 (4단계)', 4, {status: 'unknown', target: 'evaluation-refresh'})), {kind: 'wait', refresh: true});
  const failedRead = decide(ctx(4, '새로고침 버튼으로 이동 (4단계)', 4, {status: 'blocked', target: 'evaluation-refresh', blocker: '시험 이미지 평가 결과를 읽지 못했습니다: 연결이 끊겼습니다'}));
  assert.deepEqual([failedRead.kind, failedRead.step, failedRead.target], ['stop', 4, 'evaluation-refresh'], 'a failed read stops at once for the person');
  assert.ok(PERSON_STOPS.includes('불량을 놓친 모델 (틀린 이미지를 보고 그대로 이어 갈 수 있음)'), 'said before the run starts');
  // From step 3: step 4's picture is first the loading one (its counts not read yet), then the missed one.
  const {world, calls, deps} = app({stage: 3, split: true, model: true, evaluated: true});
  let loading = 2;
  const picture = deps.snapshot;
  deps.snapshot = () => { const value = picture(); if (value.stage !== 4) return value; if (loading > 0) { loading--; return ctx(4, '준비도 다시 확인', 4, {status: 'checking'}); } return missed; };
  let result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped'); assert.deepEqual(result.stop.missed, {escapes: 3, samples: 3}); assert.equal(result.stop.target, 'workflow-missed-defects');
  assert.ok(!calls.includes('step 5'), 'never on to step 5 before the misses are known, nor by itself after');
  world.stage = 4; calls.length = 0;
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10, choices: {acceptMissed: result.stop.missed}});
  assert.equal(result.state, 'done'); assert.deepEqual(calls, ['step 5', 'saveFlow', 'evaluateFlow', 'step 6', 'inspect']);
  Object.assign(world, {stage: 4, flow: false, flowEvaluated: false, inspected: false}); calls.length = 0;
  result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10});
  assert.equal(result.state, 'stopped', 'a new run asks again');
  const dialog = fs.readFileSync(path.join(__dirname, 'AutoProgressDialog.tsx'), 'utf8');
  assert.match(dialog, /\{run\.stop\.missed && missedShown && <button type="button" className="workspace-button" onClick=\{\(\) => start\(\{ acceptMissed: run\.stop\?\.missed \}\)\}>놓친 불량 \{run\.stop\.missed\.escapes\}장을 그대로 두고 검사 흐름까지 이어 가기<\/button>\}/);
});

test('results recorded but never shown at step 4 end the run in a stop, not in step 5', async () => {
  const {runAutoProgress} = load();
  const {calls, deps} = app({stage: 4, split: true, model: true, evaluated: true});
  const picture = deps.snapshot;
  deps.snapshot = () => { const value = picture(); return value.stage === 4 ? ctx(4, '새로고침 버튼으로 이동 (4단계)', 4, {status: 'unknown', target: 'evaluation-refresh', blocker: '시험 이미지 평가 결과가 화면에 없습니다'}) : value; };
  const result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10, settleMs: 100});
  assert.equal(result.state, 'failed'); assert.match(result.stop.reason, /4단계 확인이 끝나지 않았습니다: 시험 이미지 평가 결과가 화면에 없습니다/);
  assert.equal(result.stop.target, 'evaluation-refresh', '4단계 그 화면으로 reaches the refresh button');
  assert.ok(!calls.includes('step 5'));
});

// Found in re-review: going on was said of the model the person saw, and a stop's button is offered only while the
// screen still shows that result.
test('going on with missed defects ends when the run trains another model, and a stale stop offers no go-on', async () => {
  const {runAutoProgress, missedStillShown} = load();
  const {world, calls, deps} = app({stage: 4, split: true, model: true, evaluated: true});
  const missed = (escapes) => ctx(4, '틀린 이미지 보기', 4, {status: 'blocked', target: 'workflow-missed-defects', blocker: '모델이 시험 이미지의 불량을 놓쳤습니다', progress: {samples: 3, escapes, overkills: 0, accuracy: 0, low: 0, high: 56}});
  let trained = false;
  const picture = deps.snapshot;
  // Step 4 first shows the accepted result and sends the run back to train (as a data change would); the new model misses 2.
  deps.snapshot = () => { const value = picture(); if (value.stage !== 4) return value; return !trained ? ctx(4, '바뀐 내용과 되돌리기', 4, {status: 'blocked'}) : missed(2); };
  const act = deps.act; deps.act = async (action) => { if (action === 'train') trained = true; return act(action); };
  world.model = false;
  const result = await runAutoProgress(deps, () => {}, {afterActMs: 0, pollMs: 10, choices: {acceptMissed: {escapes: 3, samples: 3}}});
  assert.equal(result.state, 'stopped', 'the retrained model is shown to the person'); assert.deepEqual(result.stop.missed, {escapes: 2, samples: 3});
  assert.ok(calls.includes('train') && !calls.includes('step 5'));
  assert.equal(missedStillShown({escapes: 2, samples: 3}, missed(2)), true);
  assert.equal(missedStillShown({escapes: 3, samples: 3}, missed(2)), false, 'relabelled or retrained: another count');
  assert.equal(missedStillShown({escapes: 2, samples: 3}, ctx(4, '검사 흐름 만들기 (5단계)', 5)), false, 'no misses any more');
  assert.equal(missedStillShown({escapes: 2, samples: 3}, null), false);
});
