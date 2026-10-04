const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-09: the task center reads each job the way its workbench does, and gives a model family's next action only to a
// model family's job.
function load(file,mocks={}){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
  m.require=ref=>ref in mocks?mocks[ref]:['./jobProgress','./taskCenterModel','./taskHandoff'].includes(ref)?load(ref+'.ts'):require(ref);
  m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
const memory=()=>{const values=new Map();return{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};};
const requests=[];const requestCalls=[];
const rows=[
  {kind:'dataset_import',job_id:'import',task:'dataset_import',status:'interrupted',resumable:true,scope_kind:'project',source_dataset_path:'/source',data_operation:{progress_unit:'image',attempt:1}},
  {kind:'project_restore',job_id:'restored',task:'project_restore',status:'interrupted',resumable:true,target_dir:'/new-owned',autoactivated:false,scope_kind:'project',source_dataset_path:'/source',data_operation:{progress_unit:'file',attempt:1,result_ref:{count:4,sha256:'def'}}},
  {kind:'project_backup',job_id:'verified',task:'project_backup',status:'completed',downloadable:true,capabilities:{restore:true},scope_kind:'project',source_dataset_path:'/source',data_operation:{progress_unit:'archive',result_ref:{count:4,sha256:'a'.repeat(64)}}},
  {kind:'project_backup',job_id:'backup',task:'project_backup',status:'completed',downloadable:false,scope_kind:'project',source_dataset_path:'/source',data_operation:{progress_unit:'archive',attempt:1,expires_at:1,result_ref:{count:3,sha256:'abc'}}},
  {kind:'labeling-batch',job_id:'batch',task:'labeling',status:'interrupted',error:'proposals stopped; partial proposals remain reviewable',source_dataset_path:'/source',training_provenance:{labelset_id:'default'}},
  {kind:'ocr',job_id:'ocr-lost',task:'ocr',status:'interrupted',epoch:1,epochs:4,error:'Application stopped before training completed',source_dataset_path:'/source',training_provenance:{labelset_id:'default'}},
  {kind:'ocr',job_id:'ocr-stuck',task:'ocr',status:'stopping',cancel_supported:false,source_dataset_path:'/source',training_provenance:{labelset_id:'default'}},
];
const jobEventListeners=[];let taskReads=0;
async function renderCenter(){
  let cursor=0;const slots=[],effects=[];const state={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'default'},task:'detection',isProjectBusy:false,setStep:async()=>{},setTask:async()=>({ok:true})};
  const useProjectStore=Object.assign(sel=>sel?sel(state):state,{getState:()=>state});
  const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},
    useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
    useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i][j])){slots[i]=deps;effects.push(fn);}}};
  const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(child=>child!==undefined&&child!==null&&child!==false)});
  const m=load('TaskCenter.tsx',{react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{},
    '../../services/jobEventFeed':{onJobEventChanges:listener=>{jobEventListeners.push(listener);return()=>{jobEventListeners.splice(jobEventListeners.indexOf(listener),1);};}},
    '../../services/api':{getApiPersistenceIdentity:()=>'local',request:async(path,options)=>{requests.push(path);requestCalls.push({path,options});taskReads+=1;return {tasks:rows,reservations:[],errors:[],source_dataset_path:'/source',labelset_id:'default'};}},
    '../../stores/useProjectStore':{useProjectStore},'../../stores/useComputeStore':{useComputeStore:Object.assign(sel=>sel({transportRevision:0,profiles:[],selectedProfileId:null}),{getState:()=>({transportRevision:0,selectedProfileId:null})})},
    './ProgramWorkbenchControls':{programInput:'',programButton:''}});
  const render=()=>{cursor=0;const tree=m.TaskCenter({initialOpen:true});effects.splice(0).forEach(fn=>fn());return tree;};
  const nodesOf=tree=>{const nodes=[];const walk=n=>{if(n&&typeof n==='object'){nodes.push(n);n.children?.flat().forEach(walk);}};walk(tree);return nodes;};
  const text=tree=>{const parts=[];const walk=n=>{if(typeof n==='string'||typeof n==='number')parts.push(String(n));else if(n&&typeof n==='object')n.children?.flat().forEach(walk);};walk(tree);return parts.join('');};
  render();await new Promise(setImmediate);
  const open=key=>{nodesOf(render()).find(n=>n.props['aria-label']==='저장 작업 다시 열기').props.onChange({target:{value:key}});const tree=render();return {text:text(tree),nodes:nodesOf(tree)};};
  return open;
}

test('S2-09: the task center gives the family next action only to a model family job, never to a labeling batch',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    const open=await renderCenter();
    const batch=open('labeling-batch:local:batch');
    assert.match(batch.text,/proposals stopped; partial proposals remain reviewable/,'its own recorded cause');
    assert.doesNotMatch(batch.text,/다시 학습하세요/,'a labeling batch is not told to train again');
    assert.doesNotMatch(batch.text,/다음 행동:/);
    const family=open('ocr:local:ocr-lost');
    assert.match(family.text,/실행 주체 없음 · 재개 확인 필요/);assert.match(family.text,/문자 인식/);assert.match(family.text,/Epoch 1\/4/);
    assert.match(family.text,/다음 행동: .*다시 학습하세요/);
    assert.equal(family.nodes.filter(n=>n.type==='button'&&n.children.flat().some(c=>typeof c==='string'&&c.includes('취소 요청'))).length,0,'an interrupted job has nothing to cancel');
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});

test('S2-09: a stopping job that says it cannot be cancelled shows no pending-stop button in the task center',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    const open=await renderCenter();
    const stuck=open('ocr:local:ocr-stuck');
    assert.match(stuck.text,/취소 요청 · 종료 확인 중/);
    assert.equal(stuck.nodes.filter(n=>n.type==='button'&&n.children.flat().some(c=>typeof c==='string'&&c.includes('취소 요청'))).length,0);
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});
test('S1-10 slice 4: an open task center reads its list again as soon as a reconnection catch-up reports changed jobs',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    jobEventListeners.length=0;await renderCenter();
    assert.equal(jobEventListeners.length,1,'the open center listens for catch-ups');
    const before=taskReads;jobEventListeners[0]({jobIds:['job_1'],reset:false});await new Promise(setImmediate);
    assert.equal(taskReads,before+1,'a reported change reads the list at once, not at the next poll');
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});

 test('E08: interrupted import resumes its own ID and expired backup stays disabled',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    const open=await renderCenter();const pending=open('dataset_import:local:import');
    assert.match(pending.text,/같은 작업 재개/);assert.match(pending.text,/데이터 화면/);
    const resume=pending.nodes.find(n=>n.type==='button'&&n.children.includes('같은 작업 재개'));
    resume.props.onClick();await new Promise(setImmediate);
    assert.ok(requests.includes('/api/dataset/imports/import/resume'));
    const expired=open('project_backup:local:backup');
    assert.match(expired.text,/검증 3개/);assert.match(expired.text,/결과 만료/);
    const download=expired.nodes.find(n=>n.type==='button'&&n.children.includes('백업 결과 사용 불가'));
    assert.equal(download.props.disabled,true);
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});

 test('E08: explicit fresh restore binds verified archive and resumed restore keeps its identity',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    const open=await renderCenter();let verified=open('project_backup:local:verified');
    const input=verified.nodes.find(n=>n.type==='input'&&n.props['aria-label']==='새 복원 폴더');
    assert.ok(input,'explicit destination required');input.props.onChange({target:{value:'/new-owned'}});
    verified=open('project_backup:local:verified');
    const restore=verified.nodes.find(n=>n.type==='button'&&n.children.includes('새 폴더에 복원'));
    assert.ok(restore&&!restore.props.disabled);restore.props.onClick();await new Promise(setImmediate);
    const call=requestCalls.find(c=>c.path==='/api/dataset/operations/backups/verified/restore');
    assert.deepEqual(JSON.parse(call.options.body),{target_dir:'/new-owned',expected_archive_sha256:'a'.repeat(64)});
    assert.ok(call.options.headers['Idempotency-Key']);
    const restored=open('project_restore:local:restored');assert.match(restored.text,/\/new-owned/);assert.match(restored.text,/자동 활성화하지 않습니다/);
    restored.nodes.find(n=>n.type==='button'&&n.children.includes('같은 작업 재개')).props.onClick();await new Promise(setImmediate);
    assert.ok(requests.includes('/api/dataset/operations/restores/restored/resume'));
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});
