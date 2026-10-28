const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function fixture({delayed=false}={}){
 const cache=new Map(),captured=[],calls=[];let runtime='A',release;const listed=delayed?new Promise(r=>release=r):Promise.resolve();
 const plain={projectDir:'/A',isProjectBusy:false,splitState:'saved',isCurrentData:true,status:'completed',jobId:'j'};
 const store={getState:()=>plain,subscribe:()=>()=>{}};const connection=JSON.stringify(['oneclick','w','a','local']);
 let saved={schema_version:1,id:'task_'+'a'.repeat(32),revision:2,status:'waiting_job',goal:'원클릭',plan_sha256:'a'.repeat(64),updated_at:'now',created_at:'now',approval:null,current_step_id:'step_a',message:null,
  binding:JSON.stringify(['A',connection]),steps:[{id:'step_a',action:'open_step',args:{step:3},label:'화면',status:'waiting_job',attempt_id:'attempt_a',receipt:{job_id:'j'},error:null}]};
 function load(file){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const req=m.require.bind(m);m.require=name=>{
  if(name.endsWith('/controller')){const actual=load(path.resolve(__dirname,'controller.ts')).AssistantTaskController;return{AssistantTaskController:class extends actual{constructor(deps){super({...deps,runnerFactory:()=>({run:async task=>{calls.push('dispatch');return{...task,status:'completed'};},pause:async()=>null})});captured.push(this);}}};}
  for(const namePart of ['Training','Project','Dataset','Compute'])if(name.includes('use'+namePart+'Store'))return{['use'+namePart+'Store']:store};
  if(name.endsWith('/api'))return{getProjectContext:()=>({workspace_id:'w',actor_id:'a',mode:'local'}),getApiPersistenceIdentity:()=>runtime};
  if(name.endsWith('/runtime'))return{taskRuntimeBinding:()=>runtime,taskRuntimeGeneration:()=>0,executeTaskAction:async()=>{},reconcileTaskAction:async()=>{},taskActionCatalog:()=>[]};
  if(name.endsWith('/connection'))return{taskConnectionKey:()=>'',taskConnectionGeneration:()=>0,taskConnectionReady:()=>false};
  if(name.endsWith('/workflowEvidence'))return{subscribeWorkflowEvidence:()=>()=>{}};
  if(name.endsWith('/qualification'))return{qualifyCompletedTaskStep:async()=>{}};
  if(name.endsWith('/settingsRecovery'))return{trainingSettingsRecovery:()=>null};
  if(name.endsWith('/assistantContext'))return{reducePaths:x=>x};
  if(name.endsWith('/client'))return{assistantTaskClient:{list:async()=>{await listed;return[saved];},get:async()=>structuredClone(saved),
   create:async plan=>{calls.push('create');saved={...saved,...plan,id:'task_'+'b'.repeat(32),revision:1,status:'draft',plan_sha256:'b'.repeat(64),current_step_id:null,authorization:null,steps:plan.steps.map((row,index)=>({...row,id:'step_'+index,status:'pending',attempt_id:null,receipt:null,error:null}))};return structuredClone(saved);},
   event:async(_id,event)=>{calls.push(event.type);saved.revision++;if(event.type==='authorize'){saved.status='ready';saved.authorization={source:event.authorization_source,scope:{workspace_id:'w',actor_id:'a'},binding:saved.binding,plan_sha256:saved.plan_sha256,allowed_steps:saved.steps.map(row=>row.id),policy_version:'continuous-execution-v1',authorized_at:'now'};}else if(event.type==='pause')saved.status='paused';return structuredClone(saved);}}};
  if(name.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(file),name)+'.ts'))return load(path.resolve(path.dirname(file),name)+'.ts');return req(name);
 };m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
 const api=load(path.resolve(__dirname,'../../stores/useAssistantTaskStore.ts'));return{api,c:captured[1],calls,get saved(){return saved;},release:()=>release(),setRuntime:value=>{runtime=value;plain.projectDir='/'+value;}};
}
test('oneclick cancel during saved-plan lookup never resumes polls authorizes or dispatches afterward',async()=>{
 const f=fixture({delayed:true}),pending=f.api.startOneclickTask(()=>{});await tick();await f.api.pauseOneclickTask();f.release();await pending;
 assert.deepEqual(f.calls,[]);assert.notEqual(f.c.state.task?.status,'completed');
});
test('a safe historical paused plan in A does not block a new current project B oneclick request',async()=>{
 const f=fixture();Object.assign(f.saved,{status:'paused',current_step_id:null,steps:[{...f.saved.steps[0],status:'pending',attempt_id:null,receipt:null}]});f.c.state.task=structuredClone(f.saved);f.setRuntime('B');
 await f.api.startOneclickTask(()=>{});assert.deepEqual(f.calls,['create','authorize','dispatch']);assert.equal(f.c.state.task.binding,JSON.stringify(['B',JSON.stringify(['oneclick','w','a','local'])]));
});
