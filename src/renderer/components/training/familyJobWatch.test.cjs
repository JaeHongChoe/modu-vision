const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-09: the family workbench hooks reopen a job whose state may still change before an older finished one, keep reading
// it while its connection is down, and reconnect a server job through the server job API.
// prelude: source placed before the module's own, used to give only the module under test recording timers
function load(file,mocks={},prelude=''){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
  m.require=ref=>ref in mocks?mocks[ref]:ref==='./jobProgress'||ref==='./scopedTrainingJob'||ref==='./taskHandoff'?load(ref+'.ts'):require(ref);
  m._compile(prelude+ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
function hookHarness(){let cursor=0;const slots=[],effects=[];
  const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},
    useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
    useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=fn();});}},useCallback:fn=>fn};
  return{react,render:fn=>{cursor=0;const result=fn();effects.splice(0).forEach(run=>run());return result;}};}
const state={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'default',models_dir:'/project/models'}};
const projectStore=Object.assign(sel=>sel(state),{getState:()=>state});
const computeState={selectedProfileId:null,transportRevision:0,profiles:[]};
const computeStore=Object.assign(sel=>sel?sel(computeState):computeState,{getState:()=>computeState});
// The hook's timers are recorded, never run: the delay each read is scheduled with is what is checked. Only the module
// under test sees them (global timers are left alone for the test runner).
const delays=[],scheduled=[];globalThis.__s209Timers={set:(fn,ms)=>{delays.push(ms);scheduled.push(fn);return delays.length;},clear:()=>{}};
const TIMERS='const setTimeout=(...args)=>globalThis.__s209Timers.set(...args),clearTimeout=(...args)=>globalThis.__s209Timers.clear(...args);';
const row=(job_id,status,extra={})=>({job_id,task:'ocr',status,epoch:1,epochs:4,source_dataset_path:'/source',training_provenance:{labelset_id:'default',dataset_version_id:'v1'},...extra});

test('S2-09: the OCR and defect-generation hook reopens a disconnected server job first, keeps reading it and reconnects it',async()=>{
  const h=hookHarness(),reconnects=[];
  const jobs=[row('done','completed'),row('lost','disconnected',{execution_job_id:'exec-lost',compute_profile_id:'gpu-a'})];
  const execution={controlModelTraining:async job=>job,submitModelTraining:async()=>null,
    reconnectModelTraining:async job=>{reconnects.push([job.job_id,job.execution_job_id]);return {...job,status:'running',epoch:2};}};
  const m=load('useSpecializedTraining.ts',{react:h.react,'../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeStore},
    '../../services/specializedApi':{specializedApi:{trainingJobs:async()=>({jobs}),trainingJob:async()=>null}},'../../services/modelExecution':execution,
    '../../services/api':{getApiPersistenceIdentity:()=>'local'},'./useTaskHandoff':{useTaskHandoff:()=>null}},TIMERS);
  delays.length=0;
  {
    h.render(()=>m.useSpecializedTraining('ocr',()=>{}));await new Promise(setImmediate);
    const restored=h.render(()=>m.useSpecializedTraining('ocr',()=>{}));
    assert.equal(restored.job.job_id,'lost','a job whose state may still change is reopened before an older finished one');
    assert.deepEqual(delays,[3000],'a disconnected job keeps being read, more slowly than an active one');
    await restored.reconnect();
    const after=h.render(()=>m.useSpecializedTraining('ocr',()=>{}));
    assert.deepEqual(reconnects,[['lost','exec-lost']]);assert.equal(after.job.status,'running');
    assert.equal(delays.at(-1),600,'a reconnected job is read at the active pace');
  }
});

test('S2-09: the patch and rotation hook reopens a watched job over the latest finished one and reconnects it',async()=>{
  const h=hookHarness(),reconnects=[];
  const jobs=[row('lost','disconnected',{task:'rotation',execution_job_id:'exec-lost',compute_profile_id:'gpu-a'}),row('done','completed',{task:'rotation'})];
  const program={rotation:{datasets:async()=>({datasets:[]}),jobs:async()=>({jobs}),models:async()=>({models:[]}),status:async()=>null,cancel:async()=>null}};
  const execution={controlModelTraining:async job=>job,reconnectModelTraining:async job=>{reconnects.push([job.job_id,job.execution_job_id]);return {...job,status:'running'};}};
  const m=load('useProgramWorkbench.ts',{react:h.react,'../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeStore},
    '../../services/modelTrainingProgram':{...load('../../services/modelTrainingProgram.ts',{'./api':{request:async()=>null},'./modelExecution':{}}),modelTrainingProgram:program},
    '../../services/modelExecution':execution,'../../services/api':{getApiPersistenceIdentity:()=>'local'},'./useTaskHandoff':{useTaskHandoff:()=>null}},TIMERS);
  delays.length=0;
  {
    h.render(()=>m.useProgramWorkbench('rotation'));await new Promise(setImmediate);await new Promise(setImmediate);
    const restored=h.render(()=>m.useProgramWorkbench('rotation'));
    assert.equal(restored.job.job_id,'lost','the older fallback (the last job) loses to a job whose state may still change');
    await new Promise(setImmediate);
    assert.ok(delays.includes(3000),`a disconnected job keeps being read (${delays})`);
    await restored.reconnect();
    const after=h.render(()=>m.useProgramWorkbench('rotation'));
    assert.deepEqual(reconnects,[['lost','exec-lost']]);assert.equal(after.job.status,'running');
  }
});

test('S2-09: after a failed read the OCR and defect-generation hook keeps watching, slowly, and returns to the active pace',async()=>{
  const h=hookHarness();let fail=true;
  const jobs=[row('live','running')];
  const execution={controlModelTraining:async(job,action,local)=>local(),submitModelTraining:async()=>null,reconnectModelTraining:async job=>job};
  const m=load('useSpecializedTraining.ts',{react:h.react,'../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeStore},
    '../../services/specializedApi':{specializedApi:{trainingJobs:async()=>({jobs}),trainingJob:async()=>{if(fail)throw new Error('server busy');return row('live','running',{epoch:2});}}},
    '../../services/modelExecution':execution,'../../services/api':{getApiPersistenceIdentity:()=>'local'},'./useTaskHandoff':{useTaskHandoff:()=>null}},TIMERS);
  delays.length=0;scheduled.length=0;
  {
    h.render(()=>m.useSpecializedTraining('ocr',()=>{}));await new Promise(setImmediate);
    h.render(()=>m.useSpecializedTraining('ocr',()=>{}));
    assert.deepEqual(delays,[600],'an active job is read at its pace');
    scheduled.at(-1)();await new Promise(setImmediate);
    const failed=h.render(()=>m.useSpecializedTraining('ocr',()=>{}));
    assert.match(failed.error,/server busy/);assert.deepEqual(delays,[600,3000],'a failed read schedules the next one, slowly');
    fail=false;scheduled.at(-1)();await new Promise(setImmediate);
    h.render(()=>m.useSpecializedTraining('ocr',()=>{}));
    assert.equal(delays.at(-1),600,'a successful read returns to the active pace');
  }
});

test('S2-09: local patch reconnect observes the same owned job and reads canonical status, including refusal',async()=>{
 const h=hookHarness(),calls=[];let refuse=false;
 const local=row('patch-owned','disconnected',{task:'patch_classification',output_dir:'/project/models/patch-owned'});
 const active={...local,status:'running',epoch:3,observation:{cause:null,next_action:null}};
 const program={patch:{datasets:async()=>({datasets:[]}),jobs:async()=>({jobs:[local]}),models:async()=>({models:[]}),
  reconnect:async id=>{calls.push(['reconnect',id]);if(refuse)throw new Error('This job cannot be reconnected');return {job_id:id,status:'running',optimizer_resume:false};},
  status:async id=>{calls.push(['status',id]);return active;}}};
 const execution={controlModelTraining:async job=>job,reconnectModelTraining:async()=>assert.fail('local patch must not call the server reconnect helper')};
 const m=load('useProgramWorkbench.ts',{react:h.react,'../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeStore},
  '../../services/modelTrainingProgram':{...load('../../services/modelTrainingProgram.ts',{'./api':{request:async()=>null},'./modelExecution':{}}),modelTrainingProgram:program},
  '../../services/modelExecution':execution,'../../services/api':{getApiPersistenceIdentity:()=>'local'},'./useTaskHandoff':{useTaskHandoff:()=>null}},TIMERS);
 h.render(()=>m.useProgramWorkbench('patch'));await new Promise(setImmediate);await new Promise(setImmediate);
 const restored=h.render(()=>m.useProgramWorkbench('patch'));await new Promise(setImmediate);calls.length=0;
 await restored.reconnect();let after=h.render(()=>m.useProgramWorkbench('patch'));assert.deepEqual(calls,[['reconnect','patch-owned'],['status','patch-owned']]);assert.equal(after.job.epoch,3);assert.deepEqual(after.job.observation,active.observation);
 after.setJob(local);refuse=true;calls.length=0;after=h.render(()=>m.useProgramWorkbench('patch'));await new Promise(setImmediate);calls.length=0;await after.reconnect();after=h.render(()=>m.useProgramWorkbench('patch'));
 assert.deepEqual(calls,[['reconnect','patch-owned']]);assert.equal(after.job.status,'disconnected');assert.match(after.error,/cannot be reconnected/);
});
