const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
function load(file) {
  const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = name => name.startsWith('.') ? load(path.resolve(path.dirname(file), name) + '.ts') : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, file);
  return m.exports;
}
const file = path.join(__dirname, 'learningTaskProgress.ts');
const api = fs.existsSync(file) ? load(file) : {selectLearningTask: () => null, learningTaskProgress: () => null};
const step = extra => ({id:'s1',action:'start_training',label:'첫 학습',status:'pending',receipt:null,error:null,...extra});
const task = extra => ({id:'t1',status:'running',message:null,current_step_id:'s1',updated_at:'2026-11-14T00:00:00Z',steps:[step()],...extra});
const state = extra => ({task:task(),contextMatches:true,error:null,...extra});
test('one progress surface prefers current running work over a later completed record', () => {
  const active = state(), old = state({task:task({id:'other',status:'completed',updated_at:'2026-11-14T01:00:00Z'})});
  assert.equal(api.selectLearningTask(active,old)?.owner,'oneclick');
});
test('another project or a hidden old plan cannot appear as the current work', () => {
  assert.equal(api.selectLearningTask(state({contextMatches:false}),state({planningNew:true})),null);
});
test('waiting for inputs points to the recorded prerequisite without inventing a human approval', () => {
  const view = api.learningTaskProgress(task({status:'waiting_human',message:'확정 라벨이 필요합니다.',steps:[step({receipt:{reason_code:'waiting_input'}})]}));
  assert.equal(view?.label,'입력이 필요해요');
  assert.equal(view?.message,'확정 라벨이 필요합니다.');
  assert.match(view?.detail || '',/라벨/);
});
test('resource wait, explicit pause and uncertain execution stay distinct', () => {
  assert.equal(api.learningTaskProgress(task({status:'waiting_human',steps:[step({receipt:{reason_code:'waiting_resource'}})]}))?.label,'사용 가능한 장치를 기다려요');
  assert.equal(api.learningTaskProgress(task({status:'paused'}))?.label,'자동 진행을 쉬고 있어요');
  assert.match(api.learningTaskProgress(task({status:'needs_reconciliation'}))?.detail || '',/다시 실행하지/);
});
test('completed execution never claims field quality or approval', () => {
  const view = api.learningTaskProgress(task({status:'completed',steps:[step({status:'completed',receipt:{quality_status:'input_needed'}})]}));
  assert.equal(view?.completed,1); assert.equal(view?.total,1);
  assert.equal(view?.label,'요청한 실행이 끝났어요');
  assert.match(view?.detail || '',/현장/);
  assert.match(view?.detail || '',/별도/);
});
test('controller error overrides a stale completed label and keeps the recovery visible', () => {
  const view = api.learningTaskProgress(task({status:'completed'}),'연결을 확인해 주세요.');
  assert.equal(view?.label,'작업 상태를 확인해 주세요');
  assert.equal(view?.message,'연결을 확인해 주세요.');
});
test('unresolved input or explicit pause remains visible beside a newer completed task',()=>{
 for (const status of ['waiting_human','paused','failed']) {
  const pending=state({task:task({status})}), done=state({task:task({id:'done',status:'completed',updated_at:'2026-11-14T01:00:00Z'})});
  assert.equal(api.selectLearningTask(pending,done)?.owner,'oneclick',status);
 }
});
test('uncertain result is a needed check, not a claim that a request is running',()=>{
 assert.equal(api.learningTaskProgress(task({status:'needs_reconciliation'}))?.label,'실행 결과 확인이 필요해요');
});
test('failed and stopped work uses the recorded recovery category rather than asking for inputs',()=>{
 const failed=api.learningTaskProgress(task({status:'waiting_human',steps:[step({receipt:{reason_code:'failed',status:'failed',not_started:false}})]}));
 assert.equal(failed?.label,'작업 오류');
 assert.match(failed?.detail||'',/오류/);
 const stopped=api.learningTaskProgress(task({status:'waiting_human',steps:[step({receipt:{status:'stopped',not_started:false}})]}));
 assert.equal(stopped?.label,'학습 결과 확인');
});
