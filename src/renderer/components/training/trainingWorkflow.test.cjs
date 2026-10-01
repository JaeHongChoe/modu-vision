const assert = require('node:assert/strict');
const fs = require('node:fs'); const path = require('node:path'); const Module = require('node:module'); const ts = require('typescript'); const test = require('node:test');
function load(file, mocks = {}) { const name = path.resolve(__dirname, file); if (!fs.existsSync(name)) return {}; const mod = new Module(name, module); mod.filename = name; mod.paths = Module._nodeModulePaths(path.dirname(name)); const req = mod.require.bind(mod); mod.require = ref => mocks[ref] ?? req(ref); mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText,name); return mod.exports; }
const models = load('modelTrainingOptions.ts');
test('region anomaly purpose is submitted as segmentation evaluation profile', () => {
  assert.equal(models.trainingModelOverrides('anomaly','padim','',{},'region').anomaly_mode, 'segmentation');
  assert.equal(models.trainingModelOverrides('anomaly','dino_synthetic','',{},'image').anomaly_mode, 'classification');
});
test('a batch remediation changes the actual batch size and leaves the preset intact', async () => {
  const {performErrorAction} = load('../common/errorActions.ts'); assert.equal(typeof performErrorAction,'function');
  const settings = {batchSize:32,preset:'precision',device:'mps'};
  const result = await performErrorAction('reduce_batch_size',{settings,setSettings:next=>Object.assign(settings,next)});
  assert.equal(settings.batchSize,16); assert.equal(settings.preset,'precision'); assert.equal(result.effect,'applied');
});
test('CPU remediation selects local transport and requires readback before resolution', async () => {
  const {performErrorAction} = load('../common/errorActions.ts'); assert.equal(typeof performErrorAction,'function');
  let selected='remote'; const settings={batchSize:8,device:'auto'};
  const result=await performErrorAction('continue_on_cpu',{settings,setSettings:next=>Object.assign(settings,next),selectLocal:async()=>{selected=null;},readTarget:()=>selected});
  assert.equal(selected,null); assert.equal(settings.device,'cpu'); assert.equal(result.effect,'applied');
  await assert.rejects(()=>performErrorAction('continue_on_cpu',{settings,setSettings:()=>{},selectLocal:async()=>{},readTarget:()=> 'remote'}));
});
test('class rebalance opens data review and unsupported errors stay unresolved', async () => {
  const {performErrorAction} = load('../common/errorActions.ts'); assert.equal(typeof performErrorAction,'function');
  let stage=3; const result=await performErrorAction('rebalance_classes',{openData:async()=>{stage=1;}});
  assert.equal(stage,1); assert.equal(result.effect,'navigation');
  assert.equal((await performErrorAction('unknown',{})).effect,'unavailable');
});
test('budget gate rejects fractional, infinite and over-epoch submissions before request', () => {
  const {validateTrainingBudget} = load('trainingWorkflow.ts'); assert.equal(typeof validateTrainingBudget,'function');
  assert.equal(validateTrainingBudget({max_trials:4,max_total_epochs:8,max_seconds:600},2),null);
  for (const [budget,epochs] of [[{max_trials:1.5,max_total_epochs:8,max_seconds:600},2],[{max_trials:4,max_total_epochs:1,max_seconds:600},2],[{max_trials:4,max_total_epochs:8,max_seconds:Infinity},2]]) assert.ok(validateTrainingBudget(budget,epochs));
});
test('task identity retains transport and source and cancellation never implies release', () => {
  const {normalizeTask,taskLifecycle} = load('taskCenterModel.ts'); assert.equal(typeof normalizeTask,'function');
  const row=normalizeTask('training',{job_id:'same',status:'stopping',task:'classification',source_dataset_path:'/source',training_provenance:{labelset_id:'labels'},compute_profile_id:'server'});
  assert.equal(row.key,'training:server:same'); assert.equal(row.source,'/source'); assert.equal(row.labelset,'labels');
  assert.deepEqual(taskLifecycle(row,[{job_id:'same',remote:true,uncertain:true}]),{cancellation:'requested',termination:'pending',resource:'reserved_uncertain'});
  assert.deepEqual(taskLifecycle({...row,status:'aborted'},[]),{cancellation:'acknowledged',termination:'confirmed',resource:'released'});
  assert.equal(taskLifecycle({...row,status:'disconnected'},[]).resource,'unconfirmed');
});
test('tasks from another source or labelset are filtered and reopened identity persists', () => {
  const {tasksForScope,taskSelection} = load('taskCenterModel.ts'); assert.equal(typeof tasksForScope,'function');
  const rows=[{key:'a',source:'/a',labelset:'one'},{key:'b',source:'/b',labelset:'one'},{key:'c',source:'/a',labelset:'two'}];
  assert.deepEqual(tasksForScope(rows,'/a','one').map(row=>row.key),['a']);
  const values=new Map();const storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
  taskSelection(storage,'project/source/transport','a'); assert.equal(taskSelection(storage,'project/source/transport'),'a');assert.equal(taskSelection(storage,'other'),null);
});
test('project sample rows enforce source boundaries and preserve explicit Korean text without guessing labels', () => {
  const {projectSampleRow} = load('preparedSampleRows.ts');assert.equal(typeof projectSampleRow,'function');
  assert.equal(projectSampleRow('/images','/images/한글.png','검사 12','test'), '한글.png\t검사 12\ttest');
  assert.throws(()=>projectSampleRow('/images','/images2/a.png','text','train'));
  assert.throws(()=>projectSampleRow('/images','/images/a.png','','train'));
  assert.throws(()=>projectSampleRow('/images','/images/../outside.png','text','train'));
  assert.throws(()=>projectSampleRow('/images','/images/a.png','text\tbad','train'));
});
test('project artifact tasks keep recorded scope and never invent cancellation support', () => {
  const {normalizeTask,tasksForScope} = load('taskCenterModel.ts');
  const row=normalizeTask('inspection',{job_id:'run',status:'completed',source_dataset_path:'/source',scope_kind:'project',cancel_supported:false});
  assert.equal(row.labelset,'');assert.equal(row.raw.cancel_supported,false);
  assert.equal(tasksForScope([row],'/source','new-labelset').length,1);
});
test('a stale project remediation cannot apply a device after local selection resolves', async () => {
  const {performErrorAction}=load('../common/errorActions.ts');let scope='old',device='mps';
  await assert.rejects(()=>performErrorAction('continue_on_cpu',{settings:{batchSize:8,device},setSettings:next=>{device=next.device;},selectLocal:async()=>{scope='new';},readTarget:()=>null,isCurrent:()=>scope==='old'}));
  assert.equal(device,'mps');
});
test('GAN preparation reads explicit project bbox labels and preserves original annotation coordinates', () => {
  const {annotationCrops}=load('preparedSampleRows.ts');assert.equal(typeof annotationCrops,'function');
  const input=[{type:'bbox',label:'긁힘',bbox:[1,2,20,24]}];const before=JSON.stringify(input);
  assert.deepEqual(annotationCrops(input,32,32),[{label:'긁힘',bbox:[1,2,20,24]}]);assert.equal(JSON.stringify(input),before);
  assert.throws(()=>annotationCrops([{type:'bbox',label:'bad',bbox:[0,0,100,100]}],32,32));
});
test('remediation settings reach the real next training submission', async () => {
  let submitted;
  const {useTrainingStore}=load('../../stores/useTrainingStore.ts',{
    '../services/api':{api:{training:{start:async body=>{submitted=body;return {job_id:'job_fixture',status:'started'};}}}},
    './useComputeStore':{useComputeStore:{getState:()=>({isLoaded:true,loadError:null,selectedProfileId:null})}},
    './useDatasetStore':{useDatasetStore:{getState:()=>({isSplitting:false})}},
    '../utils/trainingComputeReadiness':{trainingComputeReadiness:()=>({ready:true,reason:''})},
  });
  useTrainingStore.getState().setPreset('precision');useTrainingStore.getState().setNextSettings({batchSize:16,device:'cpu'});
  await useTrainingStore.getState().startTraining('/fixture/source','classification',undefined,{backbone:'dinov3_vits16'});
  assert.equal(submitted.config_overrides.batch_size,16);assert.equal(submitted.config_overrides.backbone,'dinov3_vits16');assert.equal(submitted.preset,'precision');assert.equal(submitted.device,'cpu');
});
test('error action preview starts from the actual fast and precision preset batch sizes', () => {
  const {trainingPresetBatchSize}=load('../common/errorActions.ts');assert.equal(typeof trainingPresetBatchSize,'function');
  assert.equal(trainingPresetBatchSize('fast'),16);assert.equal(trainingPresetBatchSize('precision'),8);
});
test('project and transport switches hide the old task snapshot before asynchronous refresh', () => {
  const {taskSnapshotForScope}=load('taskCenterModel.ts');assert.equal(typeof taskSnapshotForScope,'function');
  const rows=[{key:'training:server:owned'}];
  assert.deepEqual(taskSnapshotForScope('project-a/server','project-b/server',rows),[]);
  assert.deepEqual(taskSnapshotForScope('project-a/server','project-a/local',rows),[]);
  assert.equal(taskSnapshotForScope('project-a/server','project-a/server',rows),rows);
});
