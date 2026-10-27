const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
function load(file, cache = new Map()) {
  if (cache.has(file)) return cache.get(file).exports;
  const loaded = new Module(file, module); loaded.filename = file; loaded.paths = Module._nodeModulePaths(path.dirname(file)); cache.set(file, loaded);
  const original = loaded.require.bind(loaded);
  loaded.require = name => name.startsWith('.') && fs.existsSync(path.resolve(path.dirname(file),name) + '.ts') ? load(path.resolve(path.dirname(file),name) + '.ts',cache) : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
  return loaded.exports;
}
const filename = path.join(__dirname,'runner.ts');
const {AssistantTaskRunner} = fs.existsSync(filename) ? load(filename) : {};
const copy = value => structuredClone(value);
const deferred = () => {let resolve; const promise = new Promise(r => {resolve=r;}); return {promise,resolve};};
function task(actions=['start_training']) {
  return {schema_version:1,id:'task-one',revision:1,status:'ready',goal:'검사 준비',plan_sha256:'a'.repeat(64),binding:'context-A',
    steps:actions.map((action,i) => ({id:`step-${i}`,action,args:action==='open_step'?{step:3}:{},label:'다음 작업',status:'pending',attempt_id:null,receipt:null,error:null})),
    approval:{plan_sha256:'a'.repeat(64),binding:'context-A',confirmed_at:'2026-10-27T00:00:00Z'},current_step_id:null,message:null,created_at:'2026-10-27T00:00:00Z',updated_at:'2026-10-27T00:00:00Z'};
}
/** In-memory durable boundary, with CAS and persisted transitions; action effects are outside this ledger. */
function harness(initial=task()) {
  let saved=copy(initial),binding=saved.binding;
  const calls=[],changes=[],effects=[];
  const deps={read:async id=>{assert.equal(id,saved.id);return copy(saved);},binding:()=>binding,
    event:async (id,event)=>{
      assert.equal(id,saved.id); assert.equal(event.expected_revision,saved.revision); assert.equal(event.binding,saved.binding); calls.push(copy(event));
      const step=saved.steps.find(row=>row.id===event.step_id);
      if(event.type==='begin_step'){assert.equal(step.status,'pending');step.status='running';step.attempt_id=event.attempt_id;saved.status='running';saved.current_step_id=step.id;}
      if(event.type==='complete_step')assert.equal(step.status,'running','interrupted steps must reconcile their completion');
      if(event.type==='complete_step'||event.type==='wait_step'||event.type==='reconcile_step'){
        const paused=saved.status==='paused';
        assert.equal(step.attempt_id,event.attempt_id);
        if(event.type==='reconcile_step'&&!Object.hasOwn(event,'receipt')){step.status='uncertain';saved.status=paused?'paused':'needs_reconciliation';}
        else {step.receipt=copy(event.receipt);step.status=event.wait_kind?`waiting_${event.wait_kind}`:'completed';saved.status=saved.steps.every(row=>row.status==='completed')?'completed':paused?'paused':event.wait_kind?step.status:'ready';if(!event.wait_kind)saved.current_step_id=null;}
        if(event.binding_after)saved.binding=event.binding_after;
      }
      if(event.type==='record_evidence'){
        assert.equal(step.attempt_id,event.attempt_id);assert.ok(event.receipt&&Object.keys(event.receipt).length);
        step.receipt={...(step.receipt||{}),...copy(event.receipt)};step.status='uncertain';saved.status=saved.status==='paused'?'paused':'needs_reconciliation';
      }
      if(event.type==='pause'){if(saved.status!=='completed'){saved.status='paused';const active=saved.steps.find(row=>row.status==='running');if(active)active.status='uncertain';}}
      if(event.type==='resume'){
        const active=saved.steps.find(row=>row.id===saved.current_step_id);if(active?.status==='waiting_human')assert.equal(event.confirmed,true);
        saved.status=active?'needs_reconciliation':'ready';
      }
      if(event.type==='fail_step'){step.status='failed';saved.status='failed';}
      saved.message=event.message??null;saved.revision++;return copy(saved);
    },
    execute:async step=>{effects.push(step.id);return {kind:'completed',receipt:{job_id:'job-one'}};},
    onChange:value=>changes.push(copy(value)),
  };
  return {deps,calls,changes,effects,get saved(){return saved;},set saved(v){saved=copy(v);},setBinding:v=>{binding=v;}};
}

test('only a matching human grant executes and begin intent is durable before a side effect',async()=>{
  assert.equal(typeof AssistantTaskRunner,'function');
  for(const state of ['draft','missing','changed']){
    const input=task();if(state==='draft')input.status='draft';if(state==='missing')input.approval=null;if(state==='changed')input.approval.plan_sha256='b'.repeat(64);
    const h=harness(input);await new AssistantTaskRunner(h.deps).run(input);assert.deepEqual(h.effects,[]);assert.ok(!h.calls.some(e=>e.type==='approve'));
  }
  const h=harness();h.deps.execute=async step=>{assert.equal(h.saved.steps[0].status,'running');assert.equal(h.saved.steps[0].attempt_id,step.attempt_id);h.effects.push(step.id);return {kind:'completed',receipt:{job_id:'job-one'}};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);
  assert.equal(result.status,'completed');assert.deepEqual(h.effects,['step-0']);assert.deepEqual(h.calls.map(e=>e.type),['begin_step','complete_step']);
});

test('latest state and immutable approved args are checked before dispatch',async()=>{
  for(const change of ['binding','args','approval','hash']){
    const h=harness(),input=copy(h.saved),latest=copy(h.saved);
    if(change==='binding')h.setBinding('context-B');if(change==='args')latest.steps[0].args={device:'cpu'};if(change==='approval')latest.approval=null;if(change==='hash')latest.plan_sha256='b'.repeat(64);h.saved=latest;
    await new AssistantTaskRunner(h.deps).run(input);assert.deepEqual(h.effects,[],change);
  }
});

test('a fresh read after persisted intent fences context changes before the side effect',async()=>{
  const h=harness(),save=h.deps.event;h.deps.event=async(...args)=>{const result=await save(...args);if(args[1].type==='begin_step')h.setBinding('context-B');return result;};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.deepEqual(h.effects,[]);assert.equal(result.status,'paused');
});

test('pause during an await keeps the committed receipt and never dispatches the next step',async()=>{
  const h=harness(task(['start_training','open_step'])),entered=deferred(),done=deferred();let control;
  h.deps.execute=async(step,c)=>{control=c;h.effects.push(step.id);entered.resolve();await done.promise;return {kind:'completed',receipt:{job_id:'job-one'}};};
  const runner=new AssistantTaskRunner(h.deps),running=runner.run(h.saved);await entered.promise;await runner.pause();assert.equal(control.isPaused(),true);assert.equal(h.saved.status,'paused');
  done.resolve();const result=await running;
  assert.equal(result.steps[0].status,'completed');assert.deepEqual(result.steps[0].receipt,{job_id:'job-one'});assert.equal(result.status,'paused');assert.deepEqual(h.effects,['step-0']);
});

test('a context change after a started side effect preserves its receipt in the original task',async()=>{
  const h=harness(task(['start_training','open_step']));
  h.deps.execute=async step=>{h.effects.push(step.id);h.setBinding('context-B');return {kind:'completed',receipt:{job_id:'job-one'}};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);
  assert.deepEqual(result.steps[0].receipt,{job_id:'job-one'});assert.equal(result.status,'paused');assert.deepEqual(h.effects,['step-0']);
});

test('a lost action response persists uncertainty and the next run reconciles without repeating mutation',async()=>{
  const h=harness();h.deps.execute=async step=>{h.effects.push(step.id);throw Error('response lost after job started');};
  const first=await new AssistantTaskRunner(h.deps).run(h.saved);
  assert.equal(first.status,'needs_reconciliation');assert.equal(first.steps[0].status,'uncertain');assert.deepEqual(h.effects,['step-0']);
  h.deps.reconcile=async step=>{assert.ok(step.attempt_id);return {kind:'completed',receipt:{job_id:'job-one'}};};
  const second=await new AssistantTaskRunner(h.deps).run(first);
  assert.equal(second.status,'completed');assert.deepEqual(second.steps[0].receipt,{job_id:'job-one'});assert.deepEqual(h.effects,['step-0']);
});

test('a reloaded running step without reconciliation support never executes again',async()=>{
  const input=task();input.status='running';input.current_step_id='step-0';Object.assign(input.steps[0],{status:'running',attempt_id:'attempt-prior'});
  const h=harness(input),result=await new AssistantTaskRunner(h.deps).run(input);
  assert.equal(result.status,'needs_reconciliation');assert.deepEqual(h.effects,[]);assert.ok(!h.calls.some(e=>e.type==='begin_step'));
});

test('human waits require explicit UI resume and job waits reconcile at most once per run',async()=>{
  for(const kind of ['waiting_human','waiting_job']){
    const h=harness(task(['start_training','open_step']));h.deps.execute=async step=>{h.effects.push(step.id);return {kind,receipt:{job_id:'job-one'}};};
    const first=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(first.status,kind);assert.deepEqual(h.effects,['step-0']);
    let reads=0;h.deps.reconcile=async()=>{reads++;return {kind,receipt:{job_id:'job-one'}};};
    const second=await new AssistantTaskRunner(h.deps).run(first);assert.equal(second.status,kind);assert.equal(reads,kind==='waiting_job'?1:0);assert.deepEqual(h.effects,['step-0']);
  }
});

test('only an explicit verified permitted binding transition authorizes subsequent steps',async()=>{
  for(const action of ['create_project','evaluate_model']){
    const input=task([action,'open_step']);if(action==='create_project')input.steps[0].args={name:'검사',task:'classification'};
    const h=harness(input);h.deps.execute=async step=>{h.effects.push(step.id);if(step.id==='step-0'){h.setBinding('context-B');return {kind:'completed',receipt:{project_id:'project-B'},bindingAfter:'context-B'};}return {kind:'completed',receipt:{step:3}};};
    const result=await new AssistantTaskRunner(h.deps).run(input);
    assert.deepEqual(h.effects,action==='create_project'?['step-0','step-1']:['step-0']);
    assert.equal(result.status,action==='create_project'?'completed':'paused');
  }
});

test('concurrent run calls share one supervisor and cannot dispatch twice',async()=>{
  const h=harness(),entered=deferred(),release=deferred();h.deps.execute=async step=>{h.effects.push(step.id);entered.resolve();await release.promise;return {kind:'completed',receipt:{job_id:'job-one'}};};
  const runner=new AssistantTaskRunner(h.deps),first=runner.run(h.saved);await entered.promise;const second=runner.run(h.saved);release.resolve();await Promise.all([first,second]);assert.deepEqual(h.effects,['step-0']);
});

test('a lost completion event response is read back and never causes a duplicate action',async()=>{
  const h=harness(),write=h.deps.event;h.deps.event=async(...args)=>{const value=await write(...args);if(args[1].type==='complete_step')throw Error('ledger response lost');return value;};
  const first=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(first.status,'completed');await new AssistantTaskRunner(h.deps).run(first);assert.deepEqual(h.effects,['step-0']);
});

test('losing the begin-intent response never dispatches an action with an uncertain lease',async()=>{
  const h=harness(),write=h.deps.event;h.deps.event=async(...args)=>{const value=await write(...args);if(args[1].type==='begin_step')throw Error('intent response lost');return value;};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(result.status,'needs_reconciliation');assert.deepEqual(h.effects,[]);
});

test('a claimed completed action without a saved-result receipt remains uncertain',async()=>{
  const h=harness();h.deps.execute=async step=>{h.effects.push(step.id);return {kind:'completed',message:'끝났습니다'};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(result.status,'needs_reconciliation');assert.equal(result.steps[0].receipt,null);assert.deepEqual(h.effects,['step-0']);
});

test('a resumed human gate reconciles real human state before another step can run',async()=>{
  const input=task(['generate_label_drafts','open_step']);input.status='needs_reconciliation';input.current_step_id='step-0';Object.assign(input.steps[0],{status:'waiting_human',attempt_id:'attempt-prior',receipt:{batch_id:'batch-one'}});
  const h=harness(input);h.deps.reconcile=async step=>{assert.equal(step.receipt.batch_id,'batch-one');return {kind:'waiting_human',receipt:{batch_id:'batch-one',adopted:false}};};
  const result=await new AssistantTaskRunner(h.deps).run(input);assert.equal(result.status,'waiting_human');assert.deepEqual(h.effects,[]);
});

test('another supervisor winning begin CAS never lends its attempt to this supervisor',async()=>{
  const h=harness(),write=h.deps.event;h.deps.event=async(id,event)=>{if(event.type==='begin_step'){await write(id,{...event,attempt_id:'attempt-other'});throw Error('stale revision');}return write(id,event);};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.deepEqual(h.effects,[]);assert.equal(result.steps[0].attempt_id,'attempt-other');
});

test('a persisted job wait that completes is reconciled before the next action',async()=>{
  const input=task(['start_training','open_step']);input.status='waiting_job';input.current_step_id='step-0';Object.assign(input.steps[0],{status:'waiting_job',attempt_id:'attempt-prior',receipt:{job_id:'job-one'}});
  const h=harness(input);h.deps.reconcile=async step=>({kind:'completed',receipt:{job_id:step.receipt.job_id,status:'completed'}});
  const result=await new AssistantTaskRunner(h.deps).run(input);assert.equal(result.status,'completed');assert.deepEqual(h.effects,['step-1']);assert.equal(result.steps[0].receipt.status,'completed');
});

test('the executable action cannot mutate the immutable approved arguments',async()=>{
  const h=harness(task(['open_step']));h.deps.execute=async step=>{Reflect.set(step.args,'step',6);return {kind:'completed',receipt:{opened_step:step.args.step}};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.deepEqual(result.steps[0].receipt,{opened_step:3});assert.equal(result.steps[0].args.step,3);
});

test('unapproved, out-of-order completed steps cannot skip the pending prefix',async()=>{
  const input=task(['open_step','start_training']);input.status='ready';Object.assign(input.steps[1],{status:'completed',receipt:{job_id:'unrelated'},attempt_id:'attempt-other'});
  const h=harness(input);await new AssistantTaskRunner(h.deps).run(input);assert.deepEqual(h.effects,[]);
});

test('run-local generation fences A to B to A while durable intent is awaiting',async()=>{
  const h=harness(),write=h.deps.event;let generation=1;h.deps.generation=()=>generation;
  h.deps.event=async(...args)=>{const result=await write(...args);if(args[1].type==='begin_step'){h.setBinding('context-B');generation++;h.setBinding('context-A');generation++;}return result;};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.deepEqual(h.effects,[]);assert.equal(result.status,'paused');
});

test('an adapter-verified owned transition can advance the run-local generation without invalidating resume bindings',async()=>{
  const input=task(['create_project','open_step']);input.steps[0].args={name:'검사',task:'classification'};
  const h=harness(input);let generation=1;h.deps.generation=()=>generation;
  h.deps.execute=async step=>{h.effects.push(step.id);if(step.id==='step-0'){generation++;h.setBinding('context-B');return {kind:'completed',receipt:{project_id:'project-B'},bindingAfter:'context-B'};}return {kind:'completed',receipt:{step:3}};};
  const result=await new AssistantTaskRunner(h.deps).run(input);assert.equal(result.status,'completed');assert.deepEqual(h.effects,['step-0','step-1']);assert.equal(result.approval.binding,'context-A');
});

test('verified training consumption can advance the fence with its exact settings and job receipt',async()=>{
  const h=harness(task(['start_training','open_step']));
  h.deps.execute=async step=>{h.effects.push(step.id);if(step.id==='step-0'){h.setBinding('training-device-consumed');return {kind:'completed',receipt:{job_id:'job-one',settings:{device:'cpu',batch_size:32}},bindingAfter:'training-device-consumed'};}return {kind:'completed',receipt:{step:3}};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(result.status,'completed');assert.deepEqual(h.effects,['step-0','step-1']);
});

test('unknown task states and a forged ready state cannot resume a human waiting step',async()=>{
  for(const kind of ['unknown','forged-human-ready']){
    const input=task();input.status=kind==='unknown'?'made_up':'ready';
    if(kind==='forged-human-ready'){input.current_step_id='step-0';Object.assign(input.steps[0],{status:'waiting_human',attempt_id:'attempt-prior',receipt:{batch_id:'batch-one'}});}
    const h=harness(input);let reconciled=0;h.deps.reconcile=async()=>{reconciled++;return {kind:'completed',receipt:{adopted:true}};};
    await new AssistantTaskRunner(h.deps).run(input);assert.deepEqual(h.effects,[]);assert.equal(reconciled,0);
  }
});

test('a verified training start records its owned transition before returning a durable job wait',async()=>{
  const h=harness(task(['start_training','open_step']));let generation=1;h.deps.generation=()=>generation;
  h.deps.execute=async step=>{h.effects.push(step.id);h.setBinding('training-device-consumed');generation++;return {kind:'waiting_job',receipt:{job_id:'job-one',settings:{device:'cpu',batch_size:32}},bindingAfter:'training-device-consumed'};};
  const first=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(first.status,'waiting_job');assert.equal(first.binding,'training-device-consumed');assert.equal(first.approval.binding,'context-A');assert.deepEqual(h.effects,['step-0']);
  h.deps.reconcile=async()=>({kind:'waiting_job',receipt:{job_id:'job-one'}});const second=await new AssistantTaskRunner(h.deps).run(first);assert.equal(second.status,'waiting_job');assert.deepEqual(h.effects,['step-0']);
});

test('a human wait cannot advance authority even when a binding-changing action supplied bindingAfter',async()=>{
  const h=harness(task(['import_dataset','open_step']));
  h.deps.execute=async step=>{h.effects.push(step.id);h.setBinding('context-B');return {kind:'waiting_human',receipt:{selected:false},bindingAfter:'context-B'};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(result.status,'paused');assert.equal(result.binding,'context-A');assert.deepEqual(result.steps[0].receipt,{selected:false});assert.deepEqual(h.effects,['step-0']);
});

test('known-started partial evidence survives uncertainty and reload without replaying the mutation',async()=>{
  const h=harness();h.deps.execute=async step=>{h.effects.push(step.id);return {kind:'needs_reconciliation',receipt:{job_id:'job-known',run_id:'run-known',settings:{device:'cpu'}},message:'작업은 시작했지만 상태를 읽지 못했습니다.'};};
  const first=await new AssistantTaskRunner(h.deps).run(h.saved);
  assert.equal(first.status,'needs_reconciliation');assert.equal(first.steps[0].status,'uncertain');assert.deepEqual(first.steps[0].receipt,{job_id:'job-known',run_id:'run-known',settings:{device:'cpu'}});
  assert.ok(h.calls.some(event=>event.type==='record_evidence'));
  h.deps.reconcile=async step=>{assert.equal(step.receipt.job_id,'job-known');assert.equal(step.receipt.run_id,'run-known');return {kind:'completed',receipt:{...step.receipt,status:'completed'}};};
  const second=await new AssistantTaskRunner(h.deps).run(first);assert.equal(second.status,'completed');assert.deepEqual(h.effects,['step-0']);
});

test('partial reconciliation evidence extends existing identifiers without claiming completion',async()=>{
  const input=task();input.status='needs_reconciliation';input.current_step_id='step-0';Object.assign(input.steps[0],{status:'uncertain',attempt_id:'attempt-prior',receipt:{job_id:'job-known',run_id:'run-known'}});
  const h=harness(input);h.deps.reconcile=async()=>({kind:'needs_reconciliation',receipt:{last_status:'syncing'}});
  const result=await new AssistantTaskRunner(h.deps).run(input);assert.equal(result.steps[0].status,'uncertain');assert.deepEqual(result.steps[0].receipt,{job_id:'job-known',run_id:'run-known',last_status:'syncing'});assert.deepEqual(h.effects,[]);
});

test('a pause queued after result read selects reconciliation from fresh serialized state and retains the receipt',async()=>{
  const h=harness(task(['start_training','open_step'])),read=h.deps.read;let queued=false,pause;
  const runner=new AssistantTaskRunner(h.deps);
  h.deps.read=async id=>{const snapshot=await read(id);if(h.effects.length===1&&!queued&&snapshot.steps[0].status==='running'){queued=true;pause=runner.pause();}return snapshot;};
  const result=await runner.run(h.saved);await pause;
  assert.equal(result.status,'paused');assert.equal(result.steps[0].status,'completed');assert.deepEqual(result.steps[0].receipt,{job_id:'job-one'});assert.deepEqual(h.effects,['step-0']);
  assert.ok(h.calls.some(event=>event.type==='reconcile_step'&&event.receipt?.job_id==='job-one'));
  assert.ok(!h.calls.some(event=>event.type==='complete_step'));
});

test('a real not-started human prerequisite resumes explicitly and rechecks before its first side effect',async()=>{
  const h=harness();let prepared=false;
  h.deps.execute=async step=>{if(!prepared)return {kind:'waiting_human',receipt:{not_started:true},message:'정답을 먼저 확인하세요.'};h.effects.push(step.id);return {kind:'completed',receipt:{job_id:'job-first'}};};
  const first=await new AssistantTaskRunner(h.deps).run(h.saved);assert.equal(first.status,'waiting_human');assert.deepEqual(first.steps[0].receipt,{not_started:true});assert.deepEqual(h.effects,[]);
  let reconciliations=0;h.deps.reconcile=async(step,control)=>{reconciliations++;assert.equal(step.receipt.not_started,true);return h.deps.execute(step,control);};
  await new AssistantTaskRunner(h.deps).run(first);assert.equal(reconciliations,0);assert.deepEqual(h.effects,[]);
  prepared=true;const resumed=await h.deps.event(first.id,{type:'resume',confirmed:true,binding:first.binding,expected_revision:first.revision});
  const result=await new AssistantTaskRunner(h.deps).run(resumed);assert.equal(reconciliations,1);assert.equal(result.status,'completed');assert.deepEqual(result.steps[0].receipt,{job_id:'job-first'});assert.deepEqual(h.effects,['step-0']);
  assert.equal(h.calls.filter(event=>event.type==='begin_step').length,1,'the same existing attempt is reconciled; no duplicate intent is created');
});

test('an ambiguous resumed write revokes the old not-started proof with or without known identifiers',async()=>{
  for(const identified of [false,true]){
    const input=task();input.status='needs_reconciliation';input.current_step_id='step-0';Object.assign(input.steps[0],{status:'waiting_human',attempt_id:'attempt-prior',receipt:{not_started:true}});
    const h=harness(input);h.deps.reconcile=async()=>({kind:'needs_reconciliation',...(identified?{receipt:{job_id:'job-known'}}:{})});
    const first=await new AssistantTaskRunner(h.deps).run(input);assert.equal(first.status,'needs_reconciliation');assert.equal(first.steps[0].receipt.not_started,false);
    if(identified)assert.equal(first.steps[0].receipt.job_id,'job-known');assert.deepEqual(h.effects,[]);
  }
});

test('each action receives the latest completed upstream model receipt as a local baton without changing approved args',async()=>{
  const h=harness(task(['start_training','evaluate_model','open_step','save_flow'])),observed=[];
  h.deps.execute=async(step,control)=>{observed.push([step.action,control.priorModelJobId]);assert.deepEqual(step.args,step.action==='open_step'?{step:3}:{});
    return{kind:'completed',receipt:step.action==='start_training'?{job_id:'job_A'}:step.action==='evaluate_model'?{job_id:'job_B'}:step.action==='open_step'?{job_id:'unrelated_navigation_id',opened:true}:{version_id:'flow'}};};
  const result=await new AssistantTaskRunner(h.deps).run(h.saved);
  assert.equal(result.status,'completed');assert.deepEqual(observed,[['start_training',undefined],['evaluate_model','job_A'],['open_step','job_B'],['save_flow','job_B']]);
  assert.deepEqual(result.steps.map(step=>step.args),[{},{},{step:3},{}]);
});
test('reloaded in-flight reconciliation uses only the completed prefix and its own receipt does not replace the upstream baton',async()=>{
  const initial=task(['start_training','evaluate_model','save_flow']);initial.steps[0]={...initial.steps[0],status:'completed',attempt_id:'attempt_A',receipt:{job_id:'job_A'}};
  initial.steps[1]={...initial.steps[1],status:'waiting_job',attempt_id:'attempt_B',receipt:{job_id:'job_B'}};initial.status='waiting_job';initial.current_step_id=initial.steps[1].id;
  const h=harness(initial),observed=[];
  h.deps.reconcile=async(step,control)=>{observed.push(['reconcile',control.priorModelJobId,step.receipt.job_id]);return{kind:'completed',receipt:{job_id:'job_B'}};};
  h.deps.execute=async(step,control)=>{observed.push([step.action,control.priorModelJobId]);return{kind:'completed',receipt:{version_id:'flow'}};};
  const result=await new AssistantTaskRunner(h.deps).run(initial);assert.equal(result.status,'completed');assert.deepEqual(observed,[['reconcile','job_A','job_B'],['save_flow','job_B']]);
});
