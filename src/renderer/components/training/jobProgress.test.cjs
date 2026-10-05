const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-09: every model family's job is read the same way: label, progress, location, uncertainty, failure and next action.
function load(file,mocks={}){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
  m.require=ref=>ref in mocks?mocks[ref]:ref.startsWith('.')?load(ref+'.ts'):require(ref);
  m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{fileName:name,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
const model=()=>load('jobProgress.ts');
test('S2-09: recorded pending cancel suppresses another cancel and permits observation',()=>{
 const {jobProgress}=model();
 for(const status of ['disconnected','stopping']){
  const v=jobProgress({job_id:'x',status,compute_profile_id:'server',observation:{cause:'cancel_unconfirmed',next_action:'종료 확인이 필요합니다.',cancel:{requested_at:1,stage:'requested',complete:false}}});
  assert.equal(v.stopping,true);assert.equal(v.canCancel,false);assert.equal(v.canReconnect,true);assert.equal(v.nextAction,'종료 확인이 필요합니다.');
 }
});
test('S2-09: the shared view renders evidence steps, queue order and can omit duplicate controls',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const useComputeStore=selector=>selector({profiles:[]});
 const {JobProgressView}=load('JobProgressView.tsx',{'../../stores/useComputeStore':{useComputeStore},'./jobProgress':model()});
 const html=job=>renderToStaticMarkup(React.createElement(JobProgressView,{job,busy:false,onCancel(){},showActions:false}));
 const done=html({job_id:'x',status:'aborted',observation:{cause:'time_limit',next_action:'時間 제한을 늘리세요.',cancel:{stage:'released',requested_at:1,acknowledged_at:2,signals:['cooperative'],exit_confirmed:true,reservation_released:true}}});
 assert.match(done,/취소 확인 단계/);assert.match(done,/✓.*예약 반환/);assert.match(done,/다음 행동:/);
 const queue=html({job_id:'x',status:'queued',queue_position:2,wait_reason:'device_reserved'});assert.match(queue,/이 프로젝트 대기 순서: 2/);assert.match(queue,/장치 예약이 반환되면/);assert.doesNotMatch(queue,/<button/);
 const unknown=html({job_id:'x',status:'aborted',observation:{cancel:{stage:'exited',requested_at:1,exit_confirmed:true,reservation_released:null}}});assert.match(unknown,/○ 예약 반환/);assert.doesNotMatch(unknown,/✓ 예약 반환/);
});
test('S2-09: the four record shapes of the ten families read as the same progress',()=>{const {jobProgress}=model();
 const core=jobProgress({job_id:'a',status:'running',current_epoch:3,total_epochs:10,current_train_loss:0.25});
 const program=jobProgress({job_id:'b',status:'running',epoch:3,epochs:10,loss:0.25});
 const rotated=jobProgress({job_id:'c',status:'running',epochs_completed:3,total_epochs:10});
 const specialized=jobProgress({job_id:'d',status:'running',epoch:3,epochs:10,batch:4,batches:8,loss:0.25});
 for(const view of [core,program,rotated,specialized]){assert.equal(view.epoch,3);assert.equal(view.totalEpochs,10);assert.equal(view.label,'실행 중');assert.equal(view.tone,'active');assert.equal(view.watch,true);}
 assert.equal(core.loss,0.25);assert.equal(rotated.loss,null);assert.deepEqual([specialized.batch,specialized.batches],[4,8]);});
test('S2-09: every state a family reports has a Korean label, and an unknown one is shown as recorded',()=>{const {jobProgress,JOB_STATUS_LABELS}=model();
 for(const status of ['queued','preparing','transferring','running','stopping','cancelling','syncing','unverified','disconnected','interrupted','completed','aborted','cancelled','stopped','failed'])
  assert.ok(JOB_STATUS_LABELS[status],status);
 assert.equal(jobProgress({job_id:'x',status:'mystery'}).label,'mystery');});
test('S2-09: a disconnected server job stays watched, can reconnect, and says what happens to its reservation',()=>{const {jobProgress}=model();
 const view=jobProgress({job_id:'m',execution_job_id:'e',compute_profile_id:'gpu-a',status:'disconnected',epoch:2,epochs:5});
 assert.equal(view.tone,'uncertain');assert.equal(view.watch,true);assert.equal(view.canReconnect,true);assert.equal(view.server,'gpu-a');
 assert.match(view.nextAction,/연결을 복구하면 같은 작업을 다시 관찰합니다/);assert.match(view.nextAction,/예약은 유지/);
 const local=jobProgress({job_id:'m',status:'disconnected'});assert.equal(local.canReconnect,false,'only a server job reconnects from a workbench');assert.equal(local.server,null);});
test('S2-09: an interrupted job is not watched, cannot be cancelled or reconnected, and says to run it again',()=>{const {jobProgress}=model();
 const view=jobProgress({job_id:'i',status:'interrupted',epoch:1,epochs:5});
 assert.deepEqual([view.tone,view.watch,view.canCancel,view.canReconnect],['uncertain',false,false,false]);
 assert.match(view.nextAction,/다시 학습/);});
test('S2-09: a failure shows its recorded cause and the backend next action before any default',()=>{const {jobProgress}=model();
 const observed=jobProgress({job_id:'f',status:'failed',error:{message:'CUDA error: out of memory'},observation:{cause:'out_of_memory',next_action:'메모리가 부족했습니다. 배치 크기를 줄이세요.'}});
 assert.equal(observed.tone,'failed');assert.equal(observed.failure,'CUDA error: out of memory');assert.equal(observed.nextAction,'메모리가 부족했습니다. 배치 크기를 줄이세요.');
 const plain=jobProgress({job_id:'f',status:'failed',error:'Training process ended before completion'});
 assert.equal(plain.failure,'Training process ended before completion');assert.match(plain.nextAction,/오류 내용을 확인/);
 assert.equal(jobProgress({job_id:'ok',status:'completed'}).nextAction,null);
 assert.equal(jobProgress({job_id:'ok',status:'completed'}).failure,null);});
test('S2-09: cancel follows one rule for workbenches and the task center',()=>{const {jobProgress,cancellable}=model();
 const can=status=>jobProgress({job_id:'c',status}).canCancel;
 for(const status of ['queued','preparing','transferring','running','syncing','disconnected','unverified'])assert.equal(can(status),true,status);
 for(const status of ['stopping','cancelling','interrupted','completed','aborted','cancelled','stopped','failed'])assert.equal(can(status),false,status);
 assert.equal(jobProgress({job_id:'c',status:'running',cancel_supported:false}).canCancel,false,'a job that says it cannot be cancelled');
 assert.equal(cancellable({status:'stopping'}),false);assert.equal(jobProgress({job_id:'c',status:'stopping'}).stopping,true,'shown as a pending stop');});
test('S2-09: watching continues through transfers, result sync and an unverified record, and stops at a terminal state',()=>{const {watchJob}=model();
 for(const status of ['queued','preparing','transferring','running','stopping','cancelling','syncing','disconnected','unverified'])assert.equal(watchJob(status),true,status);
 for(const status of ['interrupted','completed','aborted','cancelled','stopped','failed'])assert.equal(watchJob(status),false,status);});
test('S2-09: one job view renders the same states for every workbench',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const store={profiles:[{id:'gpu-a',name:'연구실 GPU'}]};
 const useComputeStore=selector=>selector?selector(store):store;
 const {JobProgressView}=load('JobProgressView.tsx',{'../../stores/useComputeStore':{useComputeStore},'./jobProgress':model()});
 const html=job=>renderToStaticMarkup(React.createElement(JobProgressView,{job,busy:false,onCancel(){},onReconnect(){}}));
 const disconnected=html({job_id:'m',execution_job_id:'e',compute_profile_id:'gpu-a',status:'disconnected',epoch:2,epochs:5});
 assert.match(disconnected,/연결 끊김 · 상태 미확인/);assert.match(disconnected,/연구실 GPU/);assert.match(disconnected,/같은 서버 작업 재연결/);assert.match(disconnected,/epoch 2\/5/);
 const failed=html({job_id:'f',status:'failed',error:'boom'});
 assert.match(failed,/role="alert"[^>]*>[^<]*boom/);assert.doesNotMatch(failed,/취소 요청/);assert.match(failed,/이 컴퓨터/);
 const running=html({job_id:'r',status:'running',epoch:1,epochs:4,loss:0.5});
 assert.match(running,/취소 요청/);assert.match(running,/손실 0\.5000/);assert.match(running,/aria-valuenow="1"/);
 const stopping=html({job_id:'s',status:'stopping'});assert.match(stopping,/disabled=""[^>]*>.*종료 확인 중/);
 const reconnectless=renderToStaticMarkup(React.createElement(JobProgressView,{job:{job_id:'m',compute_profile_id:'gpu-a',status:'disconnected'},busy:false,onCancel(){}}));
 assert.doesNotMatch(reconnectless,/재연결/,'no reconnect button without a reconnect handler');});
test('S2-09: reconnect asks the server job API with the execution id, and a local job is never reconnected',async()=>{
 const calls=[];const request=async(path,init)=>{calls.push([path,init?.method]);return {job_id:'exec-1',model_id:'model-1',compute_profile_id:'gpu-a',status:'running',current_epoch:3,total_epochs:5};};
 const store={getState:()=>({})};
 const {reconnectModelTraining,normalizeExecution}=load('../../services/modelExecution.ts',{'./api':{request},'../stores/useComputeStore':{useComputeStore:store},'../stores/useProjectStore':{useProjectStore:store}});
 assert.equal(normalizeExecution({job_id:'x',status:'failed',error:{error_code:'OOM',details:'Controlled allocation failed'}}).error,'Controlled allocation failed');
 const row=await reconnectModelTraining({job_id:'model-1',execution_job_id:'exec-1',compute_profile_id:'gpu-a',status:'disconnected'});
 assert.deepEqual(calls,[['/api/compute/jobs/exec-1/reconnect','POST']]);
 assert.deepEqual([row.job_id,row.execution_job_id,row.status,row.epoch,row.epochs],['model-1','exec-1','running',3,5],'the reply keeps the model identity and its progress');
 await assert.rejects(reconnectModelTraining({job_id:'local',status:'disconnected'}),/다시 연결하지 않습니다/);assert.equal(calls.length,1);});
test('S2-09: a server phase is named while the status stays running, and an old error does not follow a job that is active again',()=>{const {jobProgress}=model();
 assert.equal(jobProgress({job_id:'p',status:'running',phase:'transferring'}).label,'전송 중');
 assert.equal(jobProgress({job_id:'p',status:'running',phase:'syncing'}).label,'결과 동기화 중');
 assert.equal(jobProgress({job_id:'p',status:'running',phase:'reconnecting'}).label,'재연결 중');
 assert.equal(jobProgress({job_id:'p',status:'running',phase:'running'}).label,'실행 중');
 assert.equal(jobProgress({job_id:'p',status:'failed',phase:'transferring'}).label,'실패','a phase never hides a terminal status');
 assert.equal(jobProgress({job_id:'p',status:'running',error:{message:'connection lost'}}).failure,null);
 assert.equal(jobProgress({job_id:'p',status:'disconnected',error:{message:'connection lost'}}).failure,'connection lost');});
test('S2-09: the bar takes the job tone, and a stopping job that cannot be cancelled shows no pending-stop button',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const store={profiles:[]};const useComputeStore=selector=>selector?selector(store):store;
 const {JobProgressView}=load('JobProgressView.tsx',{'../../stores/useComputeStore':{useComputeStore},'./jobProgress':model()});
 const html=job=>renderToStaticMarkup(React.createElement(JobProgressView,{job,busy:false,onCancel(){}}));
 assert.match(html({job_id:'f',status:'failed',epoch:2,epochs:4}),/h-full bg-rose-500/);
 assert.match(html({job_id:'i',status:'interrupted',epoch:2,epochs:4}),/h-full bg-amber-500/);
 assert.match(html({job_id:'r',status:'running',epoch:2,epochs:4}),/h-full bg-cyan-500/);
 assert.doesNotMatch(html({job_id:'s',status:'stopping',cancel_supported:false}),/종료 확인 중<\/button>/);
 assert.match(html({job_id:'s',status:'stopping'}),/종료 확인 중<\/button>/);});

test('S2-09: only local patch worker observation offers local reconnection before terminal state',()=>{
 const {jobProgress}=model();const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const {JobProgressView}=load('JobProgressView.tsx',{'../../stores/useComputeStore':{useComputeStore:selector=>selector({profiles:[]})},'./jobProgress':model()});
 for(const status of ['disconnected','stopping']){
  const job={job_id:'patch-owned',task:'patch_classification',status};assert.equal(jobProgress(job).canReconnect,true);
  const html=renderToStaticMarkup(React.createElement(JobProgressView,{job,busy:false,onCancel(){},onReconnect(){}}));
  assert.match(html,/같은 로컬 작업 재연결/);assert.doesNotMatch(html,/같은 서버 작업 재연결/);
 }
 for(const task of ['rotation','ocr','enhancement'])assert.equal(jobProgress({job_id:'x',task,status:'disconnected'}).canReconnect,false);
 for(const status of ['completed','failed','interrupted','aborted','running'])assert.equal(jobProgress({job_id:'x',task:'patch_classification',status}).canReconnect,false);
});
test('S2-09: patch local reconnect posts the original owned job id to the core endpoint',async()=>{
 const calls=[];const request=async(path,options)=>{calls.push({path,method:options?.method,body:JSON.parse(options.body)});return {job_id:'patch-owned',status:'running',optimizer_resume:false};};
 const {modelTrainingProgram}=load('../../services/modelTrainingProgram.ts',{'./api':{request},'./modelExecution':{}});
 const row=await modelTrainingProgram.patch.reconnect('patch-owned');assert.equal(row.job_id,'patch-owned');assert.equal(row.optimizer_resume,false);
 assert.deepEqual(calls,[{path:'/api/training/reconnect',method:'POST',body:{job_id:'patch-owned'}}]);
});

test('native terminal rows keep observing until their final resource facts settle',()=>{
 const {watchJob,jobProgress}=model();
 for(const status of ['completed','failed','stopped','aborted','interrupted']){
  assert.equal(watchJob(status,{pending_finalization:true}),true,status);
  assert.equal(jobProgress({status,observation:{pending_finalization:true}}).watch,true,status);
  assert.equal(watchJob(status,{pending_finalization:false}),false,status);
  assert.equal(watchJob(status),false,'legacy terminal records keep their previous contract');
 }
});
