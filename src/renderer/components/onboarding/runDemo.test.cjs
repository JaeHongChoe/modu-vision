const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-01: the example runs its steps in the user's order through the injected app calls; a failure names its step,
// leaves the later steps pending and never leaves the example 'running'.
function compile(file,mocks={}){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 const original=m.require.bind(m);m.require=ref=>ref in mocks?mocks[ref]:original(ref);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
function load(){return compile('runDemo.ts',{'./firstRun':compile('firstRun.ts')});}
function deps(overrides={}){const calls=[];let clock=0;const statuses=['running','running','completed'];
 const base={exampleDataset:async()=>{calls.push('dataset');return {folder:'/ud/examples/demo-v1',images:64,task:'classification'};},serverReason:()=>null,
  createProject:async data=>{calls.push(['project',data]);return true;},projectError:()=>null,projectBusy:()=>false,
  importFolder:async folder=>{calls.push(['import',folder]);return {error:null,folder:'/private/ud/examples/demo-v1'};},markExample:async()=>{calls.push('mark');},
  startTraining:async folder=>{calls.push(['train',folder]);return 'job_1';},trainingError:()=>null,
  jobStatus:async id=>{calls.push(['status',id]);return {status:statuses.shift()||'completed'};},
  trainingEnded:async id=>{calls.push(['ended',id]);},
  saveFlow:async(job,folder)=>{calls.push(['flow',job,folder]);return {version_id:'abcdef1234567890'};},
  inspect:async folder=>{calls.push(['inspect',folder]);return {run_id:'run_1',counts:{OK:5,NG:7},images:12};},
  projectId:()=>'project_1',sleep:async()=>{clock+=1000;},now:()=>clock};
 return {deps:{...base,...overrides},calls};}
test('the example runs dataset, project, import (then marks it), training until completed, flow and inspection in order',async()=>{const {runDemo,DEMO_PROJECT_NAME}=load();
 const {deps:d,calls}=deps();const updates=[];const final=await runDemo(d,state=>updates.push(state));
 assert.equal(final.state,'ready');
 assert.deepEqual(final.result,{project_id:'project_1',project_name:DEMO_PROJECT_NAME,job_id:'job_1',flow_version_id:'abcdef1234567890',run_id:'run_1',counts:{OK:5,NG:7},images:12});
 assert.deepEqual(calls.map(c=>Array.isArray(c)?c[0]:c),['dataset','project','import','mark','train','status','status','status','ended','flow','inspect']);
 assert.deepEqual(calls[1][1],{name:DEMO_PROJECT_NAME,task:'classification'});
 assert.equal(calls.find(c=>c[0]==='train')[1],'/private/ud/examples/demo-v1','training uses the folder the import settled on');
 assert.deepEqual(Object.values(final.steps).map(s=>s.status),['done','done','done','done','done','done']);
 assert.equal(final.steps.train.detail,'작업 job_1');assert.equal(final.steps.flow.detail,'저장 버전 abcdef12');
 assert.ok(updates.some(state=>state.steps.train?.status==='running'),'each step is shown running before done');});
test('a failed training names its step, keeps the later steps pending and ends failed',async()=>{const {runDemo}=load();
 const {deps:d,calls}=deps({jobStatus:async()=>({status:'failed',error:{message:'out of memory'}})});
 const final=await runDemo(d,()=>{});
 assert.equal(final.state,'failed');assert.match(final.error,/failed 상태로 끝났습니다: out of memory/);
 assert.equal(final.steps.train.status,'failed');assert.equal(final.steps.flow,undefined);assert.ok(!calls.some(c=>c[0]==='flow'));});
test('a selected server, a refused project, an import error and a training timeout each stop the example with their reason',async()=>{const {runDemo}=load();
 const {deps:serverDeps,calls:serverCalls}=deps({serverReason:()=>'팀 서버 연결을 끊고 다시 시작하세요.'});const server=await runDemo(serverDeps,()=>{});
 assert.equal(server.state,'failed');assert.match(server.error,/팀 서버 연결을 끊고/);assert.deepEqual(server.steps,{},'a refusal before the first step marks no step');assert.deepEqual(serverCalls,[]);
 const project=await runDemo(deps({createProject:async()=>false,projectError:()=>'이미 같은 폴더가 있습니다'}).deps,()=>{});assert.equal(project.steps.project.status,'failed');assert.match(project.error,/이미 같은 폴더/);
 const {deps:importDeps,calls}=deps({importFolder:async()=>({error:'이미지를 읽지 못했습니다',folder:null})});const imported=await runDemo(importDeps,()=>{});
 assert.equal(imported.steps.import.status,'failed');assert.ok(!calls.includes('mark'),'a failed import is never marked as the example');
 const slow=await runDemo(deps({jobStatus:async()=>({status:'running'})}).deps,()=>{},{trainingTimeoutMs:3000});assert.match(slow.error,/제한 시간 안에 끝나지 않았습니다/);});
test('a name an earlier example left behind is skipped, and the created name is the one reported',async()=>{const {runDemo,DEMO_PROJECT_NAME}=load();
 const taken=new Set([DEMO_PROJECT_NAME,`${DEMO_PROJECT_NAME} 2`]);let last=null;
 const {deps:d,calls}=deps({createProject:async data=>{calls.push(['project',data]);last=taken.has(data.name)?`Project already exists at /p/${data.name}. Open it instead.`:null;return !last;},projectError:()=>last});
 const final=await runDemo(d,()=>{});
 assert.equal(final.state,'ready');assert.equal(final.result.project_name,`${DEMO_PROJECT_NAME} 3`);
 assert.deepEqual(calls.filter(c=>c[0]==='project').map(c=>c[1].name),[DEMO_PROJECT_NAME,`${DEMO_PROJECT_NAME} 2`,`${DEMO_PROJECT_NAME} 3`]);
 assert.equal(final.steps.project.detail,`${DEMO_PROJECT_NAME} 3`);
 let always='Project already exists at /p/x. Open it instead.';const stuck=await runDemo(deps({createProject:async()=>false,projectError:()=>always}).deps,()=>{});
 assert.equal(stuck.state,'failed','the retries end');});
test('a few failed status reads are retried; a run of them, or any end other than completed, stops the example',async()=>{const {runDemo}=load();
 let reads=0;const flaky=await runDemo(deps({jobStatus:async()=>{reads+=1;if(reads<4)throw new Error('connection refused');return {status:'completed'};}}).deps,()=>{});
 assert.equal(flaky.state,'ready');
 const down=await runDemo(deps({jobStatus:async()=>{throw new Error('connection refused');}}).deps,()=>{});
 assert.equal(down.state,'failed');assert.match(down.error,/상태를 확인하지 못했습니다\(connection refused\).*job_1/);
 for(const status of ['cancelled','interrupted','aborted','stopped','disconnected']){
  const ended=await runDemo(deps({jobStatus:async()=>({status})}).deps,()=>{});assert.equal(ended.state,'failed',status);assert.match(ended.error,new RegExp(`${status} 상태로 끝났습니다`));}});

test('other project work still finishing is waited for; only this run\'s name collisions count toward the budget',async()=>{const {runDemo,DEMO_PROJECT_NAME}=load();
 // Busy at first (a data load), then a refusal while the training store has not yet seen the last job end: both wait.
 let busy=2,refusals=['학습이 진행 중입니다. 작업이 끝난 후 프로젝트를 전환하세요.','데이터 작업이 진행 중입니다. 완료 후 프로젝트를 전환하세요.'],last=null;
 const {deps:d,calls}=deps({projectBusy:()=>busy-->0,createProject:async data=>{calls.push(['project',data]);last=refusals.shift()||null;return !last;},projectError:()=>last});
 const waited=await runDemo(d,()=>{});
 assert.equal(waited.state,'ready');assert.equal(calls.filter(c=>c[0]==='project').length,3,'the same name, tried again');
 assert.equal(waited.result.project_name,DEMO_PROJECT_NAME);
 // Twenty-five examples already listed and one collision: the next free name is used (the listed names spend nothing).
 const listed=Array.from({length:25},(_,i)=>({name:i?`${DEMO_PROJECT_NAME} ${i+1}`:DEMO_PROJECT_NAME}));
 let first=true;const many=await runDemo(deps({createProject:async()=>{if(first){first=false;last='Project already exists at /p/x. Open it instead.';return false;}last=null;return true;},projectError:()=>last}).deps,()=>{},
  {projectName:`${DEMO_PROJECT_NAME} 26`,takenNames:listed.map(row=>row.name)});
 assert.equal(many.state,'ready');assert.equal(many.result.project_name,`${DEMO_PROJECT_NAME} 27`);
 // Work that never finishes stops the example with that reason.
 const stuck=await runDemo(deps({projectBusy:()=>true}).deps,()=>{});
 assert.equal(stuck.state,'failed');assert.match(stuck.error,/다른 프로젝트 작업이 끝나지 않아/);});
test('an ended example training is read again by the training store, so a project switch is not refused (s201s3 review P2)',async()=>{const {runDemo}=load();
 const done=deps();await runDemo(done.deps,()=>{});
 assert.deepEqual(done.calls.filter(c=>c[0]==='ended'),[['ended','job_1']],'completed: once, before the flow step');
 const failed=deps({jobStatus:async()=>({status:'failed',error:'out of memory'})});await runDemo(failed.deps,()=>{});
 assert.deepEqual(failed.calls.filter(c=>c[0]==='ended'),[['ended','job_1']],'a failed job ends too');
 const running=deps({jobStatus:async()=>({status:'running'})});await runDemo(running.deps,()=>{},{trainingTimeoutMs:2000});
 assert.equal(running.calls.filter(c=>c[0]==='ended').length,0,'a job still running is not settled');
 const broken=await runDemo(deps({trainingEnded:async()=>{throw new Error('status read failed');}}).deps,()=>{});
 assert.equal(broken.state,'ready','a failed re-read does not fail the example');});
