// One assistant conversation against a scripted bridge: read tools run and are logged, a write waits for the person and
// runs only on "확인", a declined or forbidden call is answered and never executed, the confirmed write carries its run id
// through beginRun/endRun, a question keeps one frozen context, and an image question is one call without tools.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>{if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,cache);}return original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const cache=new Map();
const {AssistantConversation}=load(path.join(__dirname,'assistantSession.ts'),cache);
const {createAssistantTools}=load(path.join(__dirname,'assistantTools.ts'),cache);
const {AssistantReadWaitError}=load(path.join(__dirname,'assistantReadWait.ts'),cache);

function harness(replies,{contexts,toolDeps={},readTimeoutMs,log}={}){
 const sent=[],logged=[],events=[],runs=[],executed=[];let contextCount=0;const authority={scope:'project-a',project:'project-a',write:'image-a:revision-1'};
 const deps={snapshot:()=>({stage:3,status:'ready',blocker:null,nextAction:{label:'학습 시작',step:3},progress:null,errors:[]}),
  facts:()=>({task:'classification',view:'simple',shared:false,images:20,unlabeled:0,classes:{OK:10,NG:10},split:{train:14,val:4,test:2},splitState:'saved',trainingStatus:'idle',currentImage:null}),
  setStep:async()=>{},focusTarget:()=>{},setTrainRatio:()=>{},splitSupport:()=>({supported:true,reason:null}),
  applySplit:async(...args)=>{executed.push(['applySplit',...args]);return {ok:true};},findImage:()=>[],setImageClass:async()=>({}),draftModels:async()=>[],
  startDraftBatch:async()=>({}),draftStats:async()=>({models:[]}),impact:async()=>({}),evaluation:()=>null,
  training:()=>({isTraining:false,status:'idle',preset:'balanced',computeOnServer:false,folderReady:true}),startTraining:async()=>{executed.push(['startTraining']);return {jobId:'job_1'};}};
 const bridge={send:async input=>{sent.push(JSON.parse(JSON.stringify(input)));const reply=replies.shift();return typeof reply==='function'?reply(input):(reply??answer('끝'));},
  cancel:async()=>true,beginRun:async(runId,tool)=>{runs.push(['begin',runId,tool]);return true;},endRun:async runId=>{runs.push(['end',runId]);}};
 Object.assign(deps,toolDeps);
 const tools=createAssistantTools(deps);const conversation=new AssistantConversation({bridge,tools,context:()=>contexts?contexts[contextCount++]:{step:3},
  scope:()=>authority.scope,projectScope:()=>authority.project,writeBinding:()=>authority.write,log:async entry=>{logged.push(entry);await log?.(entry);},provider:()=>'anthropic',readTimeoutMs},event=>events.push(event));
 return {conversation,sent,logged,events,runs,executed,authority,bridge,deps,tools};
}
const call=(id,name,args={})=>({id,name,arguments:args});
const answer=(text,toolCalls=[])=>({ok:true,text,toolCalls,stop:toolCalls.length?'tool':'end'});

test('diagnosis passes the observed error and saved job to the provider before a replacement draft, with zero app effects',async()=>{
 let proposed=0;
 const toolDeps={diagnostics:async()=>({section:'requests',partial:true,errors:[{code:'invalid_json',message:'Unexpected end of JSON input',status:200}]}),tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:async()=>({fresh:true,revision:9,status:'waiting_human',job_id:'job_existing'}),propose:async()=>{proposed++;return{status:'draft',execution_requires_person:true};}}};
 const h=harness([answer('',[call('d1','read_app_diagnostics',{section:'requests'}),call('d2','read_task_status')]),input=>{
   const results=input.messages.filter(m=>m.role==='tool').map(m=>JSON.parse(m.content));
   assert.equal(results[0].errors[0].code,'invalid_json');assert.equal(results[1].job_id,'job_existing');
   return answer('기존 작업 결과를 먼저 확인했습니다.',[call('d3','propose_task',{goal:'상태를 확인합니다',steps:[{action:'open_step',args:{step:5}}]})]);
 },answer('수정 계획을 준비했습니다. 확인 후 진행합니다.')],{toolDeps});
 assert.equal(h.sent.length,0);await h.conversation.ask('왜 실패했어? 확인하고 계획을 고쳐줘');
 assert.equal(h.sent.length,3);assert.equal(proposed,1);assert.deepEqual(h.executed,[]);assert.deepEqual(h.runs,[]);
 assert.ok(h.events.some(row=>row.kind==='assistant'&&row.text==='수정 계획을 준비했습니다. 확인 후 진행합니다.'));assert.ok(!h.events.some(row=>row.kind==='error'));
});

test('structured errors retain code/status/message and oversized JSON becomes a valid explicit omission',async()=>{
 const h=harness([answer('',[call('d1','read_app_diagnostics')]),input=>{
   const row=JSON.parse(input.messages.find(m=>m.role==='tool').content);
   assert.equal(row.error.code,'stale_revision');assert.equal(row.error.status,409);assert.match(row.error.message,/저장 버전/);
   assert.ok(!JSON.stringify(row).includes('[object Object]'));return answer('저장 버전을 확인하세요.');
 }],{toolDeps:{diagnostics:async()=>{throw {code:'stale_revision',status:409,message:'저장 버전이 바뀌었습니다.'};}}});
 await h.conversation.ask('이 오류는 뭐야?');
 assert.ok(h.events.some(row=>row.kind==='assistant'&&row.text==='저장 버전을 확인하세요.'));assert.ok(!h.events.some(row=>row.kind==='error'));
 const large=harness([answer('',[call('long','read_task_status')]),input=>{
   const text=input.messages.find(m=>m.role==='tool').content,body=JSON.parse(text);
   assert.ok(text.length<=4000);assert.equal(body.truncated,true);assert.equal(body.tool,'read_task_status');return answer('더 작은 범위를 확인해야 합니다.');
 }],{toolDeps:{tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:async()=>({steps:Array.from({length:24},()=>({error:'x'.repeat(500)}))}),propose:async()=>({})}}});
 await large.conversation.ask('작업 결과 확인');
 assert.ok(large.events.some(row=>row.kind==='assistant'&&row.text==='더 작은 범위를 확인해야 합니다.'));assert.ok(!large.events.some(row=>row.kind==='error'));
});

test('read tools run at once, every call is logged, and the answer finishes the turn',async()=>{
 const h=harness([answer('',[call('c1','read_status'),call('c2','read_dataset_summary')]),answer('3단계에서 학습을 시작하면 됩니다.')]);
 await h.conversation.ask('왜 막혔나요?');
 assert.equal(h.sent.length,2);
 assert.deepEqual(h.sent[1].messages.map(message=>message.role),['user','assistant','tool','tool']);
 assert.equal(JSON.parse(h.sent[1].messages[3].content).images,20);
 assert.deepEqual(h.logged.map(entry=>[entry.tool,entry.tier,entry.result,entry.confirmed]),[['conversation','conversation','ok',false],['read_status','read','ok',false],['read_dataset_summary','read','ok',false],['conversation','conversation','ok',false]]);
 assert.ok(h.logged.every(entry=>/^run_[a-f0-9]{24}$/.test(entry.run_id)&&/^[a-f0-9]{64}$/.test(entry.args_sha256)&&entry.provider==='anthropic'));
 assert.equal(h.conversation.messages.length,5);assert.equal(h.conversation.busy,false);
 assert.ok(h.events.some(event=>event.kind==='assistant'&&event.text.includes('3단계')));
});

test('a write waits for the person; declining answers the model and runs nothing',async()=>{
 const h=harness([answer('분할을 저장할까요?',[call('w1','save_split',{train_percent:70,val_percent:20,test_percent:10})]),answer('알겠습니다.')]);
 await h.conversation.ask('분할을 저장해 줘');
 assert.equal(h.sent.length,1,'nothing more is sent while the preview waits');
 assert.equal(h.conversation.pending.preview.title,'분할 저장');assert.equal(h.conversation.busy,true);
 assert.deepEqual(h.executed,[]);
 await h.conversation.decide(false);
 assert.deepEqual(h.executed,[]);assert.deepEqual(h.runs,[],'no run header is armed for a declined write');
 const result=h.sent[1].messages.at(-1);
 assert.equal(result.role,'tool');assert.equal(result.isError,true);assert.match(result.content,/실행하지 않기로/);
 assert.deepEqual(h.logged.filter(entry=>entry.tool==='save_split').map(entry=>[entry.result,entry.confirmed,entry.error_code]),[['refused',false,'declined_by_person']]);
});

test('a confirmed write runs once with its run id armed around it, and is logged as confirmed',async()=>{
 const h=harness([answer('',[call('w1','start_training')]),answer('학습을 시작했습니다.')]);
 await h.conversation.ask('학습 시작해 줘');
 const runId=h.conversation.pending.runId;
 await h.conversation.decide(true);
 assert.deepEqual(h.executed,[['startTraining']]);
 assert.deepEqual(h.runs,[['begin',runId,'start_training'],['end',runId]]);
 const entry=h.logged.find(item=>item.tool==='start_training');
 assert.deepEqual([entry.run_id,entry.confirmed,entry.result,entry.link],[runId,true,'ok','job:job_1']);
 await h.conversation.decide(true);
 assert.deepEqual(h.executed,[['startTraining']],'a second "확인" has nothing to run');
});

test('forbidden and unknown tools are answered, never run, and the loop goes on',async()=>{
 const h=harness([answer('',[call('f1','approve_model'),call('u1','format_disk'),{id:'b1',name:'open_step',arguments:{},argumentsError:true}]),answer('승인은 직접 하셔야 합니다.')]);
 await h.conversation.ask('모델 승인해 줘');
 const results=h.sent[1].messages.filter(message=>message.role==='tool');
 assert.match(results[0].content,/^직접 하세요\. 4단계/);assert.match(results[1].content,/그런 도구는 없습니다/);assert.match(results[2].content,/JSON 객체/);
 assert.ok(results.every(message=>message.isError));
 assert.deepEqual(h.logged.filter(entry=>entry.tier!=='conversation').map(entry=>[entry.tool,entry.result,entry.error_code]),
  [['approve_model','refused','forbidden'],['format_disk','refused','unknown_tool'],['open_step','refused','invalid_arguments']]);
 assert.deepEqual(h.executed,[]);
});

test('a question freezes its context; an image question sends the image once and no tools',async()=>{
 const h=harness([answer('',[call('c1','read_status')]),answer('끝'),answer('이미지의 긁힘으로 보입니다.')],{contexts:[{step:3,n:1},{step:3,n:2}]});
 await h.conversation.ask('상태는?');
 assert.deepEqual(h.sent.map(input=>input.context),[{step:3,n:1},{step:3,n:1}],'one context for every call of a question');
 const image={grantId:'img_'+'0'.repeat(24),mediaType:'image/png',base64:'iVBORw0KGgo='};
 await h.conversation.ask('이 이미지 불량인가요?',image);
 const imageCall=h.sent[2];
 assert.deepEqual(imageCall.tools,[]);assert.deepEqual(imageCall.image,image);assert.deepEqual(imageCall.context,{step:3,n:2});
 assert.equal(h.logged.at(-1).confirmed,true,'the image call is logged as confirmed by the person');
 assert.match(h.conversation.messages.at(-2).text,/이미지 1장을 함께 보냈습니다/);
 assert.ok(!JSON.stringify(h.conversation.messages).includes('iVBORw0KGgo'),'pixels are never kept in the history');
});

test('a provider error is shown with its Korean action and the unanswered question is not kept',async()=>{
 const h=harness([{ok:false,error:{code:'rate_limited',reason:'사용 한도나 요청 빈도 제한에 걸렸습니다.',action:'잠시 뒤 다시 보내세요.',requestId:'req-1'}}]);
 await h.conversation.ask('질문');
 assert.ok(h.events.some(event=>event.kind==='error'&&event.error.code==='rate_limited'));
 assert.deepEqual(h.conversation.messages,[]);assert.equal(h.conversation.busy,false);
 assert.deepEqual(h.logged.map(entry=>[entry.result,entry.error_code]),[['error','rate_limited']]);
});

const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
test('a stalled read ends the unfinished turn locally and leaves another question usable without a provider retry',async()=>{
 const release=deferred(),entered=deferred(),h=harness([answer('',[call('r1','read_app_diagnostics'),call('w1','start_training')]),answer('새 질문의 답')],
  {readTimeoutMs:5,toolDeps:{diagnostics:async()=>{entered.resolve();return release.promise;}}});
 const asking=h.conversation.ask('왜 대기 중인가요?');await entered.promise;
 try{
  await delay(30);assert.equal(h.conversation.busy,false,'a timed out read must release the composer');await asking;
  assert.equal(h.conversation.pending,null);assert.deepEqual(h.conversation.messages,[]);assert.equal(h.sent.length,1,'timeout must not start another provider round');
  const notice=h.events.find(event=>event.kind==='notice');assert.match(notice?.text??'',/상태|진단|읽/);assert.match(notice.text,/다시|새 질문/);
  assert.ok(!h.events.some(event=>event.kind==='error'),'a local read timeout has its own recovery notice');
  const entry=h.logged.find(row=>row.tool==='read_app_diagnostics');assert.equal(entry?.error_code,'read_timeout');
  await h.conversation.ask('새 질문');assert.deepEqual(h.conversation.messages.map(row=>row.text),['새 질문','새 질문의 답']);
  release.resolve({stale:'never display'});await delay(1);
  assert.ok(!h.events.some(event=>event.kind==='tool'&&event.name==='read_app_diagnostics'&&event.ok));
  assert.equal(h.sent.length,2);assert.deepEqual(h.executed,[]);assert.deepEqual(h.runs,[]);
 }finally{release.resolve({});await asking;}
});

test('a stalled timeout audit cannot keep the composer busy or later alter another turn or account',async()=>{
 for(const settleLog of ['resolve','reject'])for(const changeAccount of [false,true]){
  const read=deferred(),readEntered=deferred(),audit=deferred(),auditEntered=deferred(),fresh=deferred(),freshEntered=deferred(),auditScopes=[];
  const h=harness([answer('이전 답'),answer('',[call('r1','read_app_diagnostics'),call('w1','start_training')]),()=>{freshEntered.resolve();return fresh.promise;}],
   {readTimeoutMs:5,toolDeps:{diagnostics:async()=>{readEntered.resolve();return read.promise;}},log:entry=>{
    if(entry.error_code==='read_timeout'){auditScopes.push({scope:h.authority.scope,project:h.authority.project,provider:entry.provider});auditEntered.resolve();return audit.promise;}
   }});
  await h.conversation.ask('이전 완료 질문');let finished=false;const asking=h.conversation.ask('상태 읽기');asking.then(()=>finished=true);await readEntered.promise;await auditEntered.promise;
  let current;
  try{
   await delay(10);assert.equal(h.conversation.busy,false,'an unfinished audit POST cannot hold the read composer');assert.equal(finished,true,'ask settles without waiting for the audit POST');await asking;
   assert.deepEqual(h.conversation.messages.map(row=>row.text),['이전 완료 질문','이전 답']);assert.equal(h.sent.length,2,'timeout does not request another provider round');
   assert.ok(h.events.some(event=>event.kind==='notice'&&/진단.*멈췄/.test(event.text)));assert.deepEqual(h.executed,[]);assert.deepEqual(h.runs,[]);
   assert.deepEqual(auditScopes,[{scope:'project-a',project:'project-a',provider:'anthropic'}],'the append starts under the old account and project');
   if(changeAccount){h.authority.scope=h.authority.project='account-b:project-b';h.conversation.invalidate();}
   current=h.conversation.ask('현재 질문');await freshEntered.promise;const eventCount=h.events.length;
   read.resolve({stale:'old read'});if(settleLog==='resolve')audit.resolve({saved:true});else audit.reject(new Error('old audit failed'));
   await delay(5);assert.equal(h.conversation.busy,true,'an old audit reply cannot release the new provider wait');assert.equal(h.events.length,eventCount,'old GET/audit replies emit nothing into a newer turn');
   fresh.resolve(answer('현재 답'));await current;
   assert.deepEqual(h.conversation.messages.map(row=>row.text),changeAccount?['현재 질문','현재 답']:['이전 완료 질문','이전 답','현재 질문','현재 답']);
   assert.equal(h.sent.length,3);assert.ok(!h.logged.some(row=>row.tool==='read_app_diagnostics'&&row.result==='ok'));
  }finally{read.resolve({});audit.resolve({});fresh.resolve(answer('cleanup'));await asking;if(current)await current;}
 }
});

test('an audit failure is shown only while its retired timeout still owns the current scope',async()=>{
 const read=deferred(),audit=deferred(),auditEntered=deferred(),h=harness([answer('',[call('r1','read_task_status')])],
  {readTimeoutMs:5,toolDeps:{tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:()=>read.promise,propose:async()=>({})}},log:entry=>{
   if(entry.error_code==='read_timeout'){auditEntered.resolve();return audit.promise;}
  }});
 let finished=false;const asking=h.conversation.ask('작업 상태');asking.then(()=>finished=true);await auditEntered.promise;
 try{
  await delay(10);assert.equal(finished,true);assert.equal(h.conversation.busy,false);
  audit.reject(new Error('audit unavailable'));await delay(5);
  assert.ok(h.events.some(event=>event.kind==='notice'&&/기록.*남기지 못/.test(event.text)&&/audit unavailable/.test(event.text)));
  assert.equal(h.sent.length,1);assert.deepEqual(h.conversation.messages,[]);assert.equal(h.conversation.busy,false);
 }finally{read.resolve({});audit.resolve({});await asking;}
});

test('stopping a read retires its wait immediately and its late result cannot overwrite a new request',async()=>{
 const release=deferred(),entered=deferred(),h=harness([answer('',[call('r1','read_task_status')]),answer('현재 요청 답')],
  {readTimeoutMs:1000,toolDeps:{tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:async()=>{entered.resolve();return release.promise;},propose:async()=>({})}}});
 let finished=false;const asking=h.conversation.ask('이전 작업');asking.then(()=>finished=true);await entered.promise;
 try{
  await h.conversation.stop();await delay(10);assert.equal(finished,true,'stop must settle even when the GET ignores abort');
  assert.equal(h.conversation.busy,false);assert.equal(h.sent.length,1);await h.conversation.ask('현재 질문');
  release.resolve({goal:'late old task'});await asking;assert.deepEqual(h.conversation.messages.map(row=>row.text),['현재 질문','현재 요청 답']);
  assert.ok(!h.logged.some(row=>row.tool==='read_task_status'));assert.ok(!h.events.some(event=>event.kind==='tool'&&event.name==='read_task_status'));
 }finally{release.resolve({});await asking;}
});

test('a disconnected project or account cannot revive an older read after returning to the same scope',async()=>{
 const release=deferred(),entered=deferred(),h=harness([answer('',[call('r1','read_status')]),answer('복귀 후 답')],{readTimeoutMs:1000});let ownsRead;
 h.tools.runAutomatic=(_name,_args,isCurrent)=>{ownsRead=isCurrent;entered.resolve();return release.promise;};
 const asking=h.conversation.ask('이전 계정 상태');await entered.promise;
 try{
  h.authority.scope='other-account:project-b';h.conversation.invalidate();
  h.authority.scope='project-a';h.conversation.invalidate();await asking;
  assert.equal(ownsRead(),false,'returning to the same binding cannot renew an aborted read');
  await h.conversation.ask('복귀 후 질문');release.resolve({ok:true,summary:'이전 계정 상태',content:'old account evidence'});await delay(1);
  assert.deepEqual(h.conversation.messages.map(row=>row.text),['복귀 후 질문','복귀 후 답']);
  assert.ok(!h.logged.some(row=>row.tool==='read_status'));assert.ok(!h.events.some(event=>event.kind==='tool'));
 }finally{release.resolve({ok:true,summary:'late',content:'late'});await asking;}
});

test('the read deadline does not retire an in-flight proposal mutation or a confirmed write',async()=>{
 for(const kind of ['proposal','write']){
  const entered=deferred(),release=deferred();
  const taskDeps={isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:async()=>({}),propose:async()=>{entered.resolve();await release.promise;return {status:'draft'};}};
  const h=harness([answer('',[kind==='proposal'?call('p1','propose_task',{goal:'화면 열기',steps:[{action:'open_step',args:{step:3}}]}):call('w1','start_training')]),answer('완료 결과')],{readTimeoutMs:5,toolDeps:{tasks:taskDeps}});
  h.deps.startTraining=async()=>{h.executed.push(['startTraining']);entered.resolve();await release.promise;return {jobId:'job_1'};};
  const asking=h.conversation.ask('작업');let finishing=asking;if(kind==='write'){await asking;finishing=h.conversation.decide(true);}await entered.promise;
  try{
   await delay(25);assert.equal(h.conversation.busy,true,kind);assert.equal(h.sent.length,1);assert.equal(h.conversation.writing,kind==='write');
   assert.ok(!h.logged.some(row=>row.error_code==='read_timeout'));assert.ok(!h.events.some(event=>event.kind==='notice'));
   release.resolve();await finishing;assert.equal(h.sent.length,2);assert.equal(h.conversation.busy,false);
   if(kind==='write')assert.equal(h.logged.find(row=>row.tool==='start_training')?.confirmed,true);
  }finally{release.resolve();await finishing;}
 }
});

test('a proposal failure is recorded as navigation even if its cause has a read-wait code',async()=>{
 const h=harness([answer('',[call('p1','propose_task',{goal:'학습 화면 열기',steps:[{action:'open_step',args:{step:3}}]})]),answer('계획을 저장하지 못했습니다.')],
  {readTimeoutMs:5,toolDeps:{tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:async()=>({}),propose:async()=>{throw new AssistantReadWaitError('read_timeout');}}}});
 await h.conversation.ask('화면 열기 계획');const row=h.logged.find(entry=>entry.tool==='propose_task');
 assert.equal(row?.tier,'navigate');assert.equal(row?.error_code,'tool_error');assert.equal(h.sent.length,2);
 assert.equal(h.conversation.busy,false);assert.ok(!h.events.some(event=>event.kind==='notice'));
});

test('a write preview cannot authorize another project or changed image/settings',async()=>{
 for(const kind of ['scope','write']){
  const h=harness([answer('',[call('w1','start_training')])]);await h.conversation.ask('학습 시작');
  h.authority[kind]='changed';await h.conversation.decide(true);
  assert.deepEqual(h.executed,[]);assert.deepEqual(h.runs,[]);assert.equal(h.sent.length,1);
  assert.equal(h.conversation.pending,null);assert.equal(h.conversation.busy,false);
  assert.ok(h.events.some(e=>e.kind==='notice'&&/바뀌/.test(e.text)));
 }
});
test('a delayed provider reply from another project is dropped before logging, tools, or history',async()=>{
 const reply=deferred(),h=harness([()=>reply.promise]);const asking=h.conversation.ask('A 상태');
 h.authority.scope='project-b';reply.resolve(answer('A 응답',[call('w1','start_training')]));await asking;
 assert.deepEqual(h.logged,[]);assert.deepEqual(h.executed,[]);assert.deepEqual(h.conversation.messages,[]);
 assert.equal(h.conversation.pending,null);assert.ok(!h.events.some(e=>e.kind==='assistant'));
});
test('a context change during async preview does not create a confirmation',async()=>{
 const ready=deferred(),h=harness([answer('',[call('w1','generate_label_drafts')])]);
 h.tools.preview=()=>ready.promise;
 const asking=h.conversation.ask('초안');await new Promise(r=>setImmediate(r));
 h.authority.scope='project-b';ready.resolve({title:'초안',lines:[],undo:'거절'});await asking;
 assert.equal(h.conversation.pending,null);assert.deepEqual(h.executed,[]);
 assert.ok(!h.events.some(e=>e.kind==='tool'));
});
test('a failed or stale run grant never executes; an acquired stale grant is released',async()=>{
 for(const stale of [false,true]){
  const h=harness([answer('',[call('w1','start_training')])]);await h.conversation.ask('학습');
  const grant=deferred();h.bridge.beginRun=async()=>grant.promise;
  const deciding=h.conversation.decide(true);if(stale)h.authority.scope='project-b';
  grant.resolve(stale);await deciding;
  assert.deepEqual(h.executed,[]);assert.equal(h.sent.length,1);
  assert.equal(h.runs.filter(x=>x[0]==='end').length,stale?1:0);
 }
});
test('invalidation cancels the owned request and old completion cannot overwrite a new question',async()=>{
 const old=deferred(),fresh=deferred(),h=harness([()=>old.promise,()=>fresh.promise]);
 const first=h.conversation.ask('A');const cancelled=[];h.bridge.cancel=async id=>{cancelled.push(id);return true;};
 h.conversation.invalidate();h.authority.scope='project-b';const second=h.conversation.ask('B');
 old.resolve(answer('늦은 A'));await first;assert.equal(h.conversation.busy,true);assert.equal(cancelled.length,1);
 fresh.resolve(answer('B 응답'));await second;
 assert.deepEqual(h.conversation.messages.map(m=>m.text),['B','B 응답']);assert.equal(h.conversation.busy,false);
 assert.deepEqual(h.logged.map(e=>e.tool),['conversation']);
});
test('IPC failures are shown without executing an unarmed write or losing an executed outcome',async()=>{
 const denied=harness([answer('',[call('w1','start_training')])]);await denied.conversation.ask('학습');
 denied.bridge.beginRun=async()=>{throw Error('IPC unavailable');};await denied.conversation.decide(true);
 assert.deepEqual(denied.executed,[]);assert.equal(denied.conversation.busy,false);assert.ok(denied.events.some(e=>e.kind==='notice'&&/실행하지 않았/.test(e.text)));
 const done=harness([answer('',[call('w1','start_training')]),answer('학습 시작됨')]);await done.conversation.ask('학습');
 done.bridge.endRun=async()=>{throw Error('IPC unavailable');};await done.conversation.decide(true);
 assert.deepEqual(done.executed,[['startTraining']]);assert.equal(done.logged.find(x=>x.tool==='start_training').result,'ok');
 assert.ok(done.events.some(e=>e.kind==='notice'&&/실행 결과/.test(e.text)));
});
test('an async read of settings cannot return evidence after the captured settings change',async()=>{
 const read=deferred(),entered=deferred(),h=harness([answer('',[call('r1','read_status')])]);h.tools.runAutomatic=()=>{entered.resolve();return read.promise;};
 const asking=h.conversation.ask('준비 상태');await entered.promise;h.authority.write='new-settings';
 read.resolve({ok:true,summary:'옛 준비 상태',content:'old-ready'});await asking;
 assert.ok(!h.events.some(e=>e.kind==='tool'));assert.equal(h.sent.length,1);assert.deepEqual(h.conversation.messages,[]);
});
test('an authorized write may update its own state and is logged as executed, not context-changed',async()=>{
 const h=harness([answer('',[call('w1','start_training')]),answer('학습 시작됨')]);
 h.deps.startTraining=async()=>{h.executed.push(['startTraining']);h.authority.write='training-is-now-running';return {jobId:'job_1'};};
 await h.conversation.ask('학습');await h.conversation.decide(true);
 assert.deepEqual(h.executed,[['startTraining']]);const result=h.logged.find(e=>e.tool==='start_training');
 assert.equal(result.result,'ok');assert.equal(result.error_code,undefined);assert.equal(result.link,'job:job_1');
});
test('an image answer cannot invoke unsolicited tools or start a second provider request',async()=>{
 const h=harness([answer('이미지 설명',[call('r1','read_dataset_summary'),call('w1','start_training')]),answer('unexpected second request')]);
 await h.conversation.ask('이 이미지는?',{grantId:'g',imageIdentity:'a'.repeat(64),mediaType:'image/png',base64:'AAAA'});
 assert.equal(h.sent.length,1);assert.deepEqual(h.executed,[]);assert.equal(h.conversation.pending,null);
 assert.ok(!h.events.some(e=>e.kind==='tool'));assert.equal(h.conversation.messages.at(-1).toolCalls,undefined);
 assert.ok(h.events.some(e=>e.kind==='notice'&&/이미지.*도구/.test(e.text)));
});
// Defect: stop or a provider/model change during a confirmed write let the backend write finish, then dropped its run-log
// record and panel line. The outcome is recorded whenever the project is unchanged; only a project change withholds it.
test('stop or a settings change while a confirmed write runs still records and shows the outcome',async()=>{
 for(const change of ['stop','settings']){
  const h=harness([answer('',[call('w1','start_training')]),answer('학습 시작됨')]),entered=deferred(),release=deferred();let writing;
  h.deps.startTraining=async()=>{h.executed.push(['startTraining']);entered.resolve();await release.promise;return {jobId:'job_1'};};
  await h.conversation.ask('학습');const deciding=h.conversation.decide(true);await entered.promise;writing=h.conversation.writing;
  if(change==='stop')await h.conversation.stop();else {h.authority.scope='project-a:other-model';h.conversation.invalidate();}
  release.resolve();await deciding;
  const entry=h.logged.find(e=>e.tool==='start_training');
  assert.deepEqual([entry?.result,entry?.confirmed,entry?.link,entry?.provider],['ok',true,'job:job_1','anthropic'],change);
  assert.ok(h.events.some(e=>e.kind==='tool'&&e.name==='start_training'&&e.ok),change);
  assert.deepEqual(h.executed,[['startTraining']],'the write ran exactly once');
  assert.equal(writing,true,'the panel can tell a confirmed write is running');assert.equal(h.conversation.writing,false);
  assert.equal(h.sent.length,change==='stop'?2:1,'stop is not offered mid-write; a settings change ends the turn after the record');
  assert.equal(h.conversation.busy,false);
 }
});
test('a project change while a confirmed write runs keeps the record out of the other project and says so',async()=>{
 const h=harness([answer('',[call('w1','start_training')])]),entered=deferred(),release=deferred();
 h.deps.startTraining=async()=>{entered.resolve();await release.promise;return {jobId:'job_1'};};
 await h.conversation.ask('학습');const deciding=h.conversation.decide(true);await entered.promise;
 h.authority.scope=h.authority.project='project-b';h.conversation.invalidate();release.resolve();await deciding;
 assert.ok(!h.logged.some(e=>e.tool==='start_training'));assert.ok(h.events.some(e=>e.kind==='notice'&&/프로젝트/.test(e.text)&&/기록/.test(e.text)));
});
test('the confirmed draft write carries the model its preview showed',async()=>{
 const h=harness([answer('',[call('w1','generate_label_drafts')]),answer('초안 시작')]),batches=[];let models=[{jobId:'job_a',task:'classification',dataState:'current',createdAt:null}];
 h.deps.draftModels=async()=>models;h.deps.startDraftBatch=async(jobId,threshold)=>{batches.push([jobId,threshold]);return {id:'b1',total:3};};
 await h.conversation.ask('초안 만들어 줘');assert.match(h.conversation.pending.preview.lines[0],/job_a/);
 models=[{jobId:'job_b',task:'classification',dataState:'current',createdAt:null},...models];await h.conversation.decide(true);
 assert.deepEqual(batches,[['job_a',0.5]]);assert.equal(h.logged.find(e=>e.tool==='generate_label_drafts').result,'ok');
});


test('an image task renews one-shot grants for stateless tool followups and freezes reasoning/context',async()=>{
 const h=harness([answer('',[call('r1','read_status')]),answer('확인했습니다.')]);const intents=[];h.bridge.imageIntent=async input=>{intents.push(input);return{ok:true,grantId:'followup',providerLabel:'ChatGPT',expiresAt:999,message:'same image'};};
 const image={grantId:'grant',imageIdentity:'a'.repeat(64),mediaType:'image/png',base64:'AAAA'};
 await h.conversation.ask('이 이미지로 작업을 진행해줘',image,{reasoningEffort:'high',allowImageTools:true,context:{step:2,selected:'frozen'}});
 assert.equal(h.sent.length,2);
 assert.equal(h.sent[0].image.base64,'AAAA');assert.equal(h.sent[1].image.base64,'AAAA');assert.equal(h.sent[1].image.grantId,'followup');assert.equal(intents.length,1);assert.equal(intents[0].imageIdentity,image.imageIdentity);assert.deepEqual(intents[0].context,{step:2,selected:'frozen'});
 assert.ok(h.sent[0].tools.some(t=>t.name==='read_status'));
 assert.ok(h.sent.every(row=>row.reasoningEffort==='high'));
 assert.ok(h.sent.every(row=>row.context.selected==='frozen'));
 assert.ok(h.conversation.messages.some(row=>row.role==='tool'));
 assert.ok(!JSON.stringify(h.conversation.messages).includes('AAAA'));
});

test('cancelling while a followup image grant is pending sends no late pixels',async()=>{
 const h=harness([answer('',[call('r1','read_status')]),answer('late')]);let release,entered;const begun=new Promise(r=>entered=r);h.bridge.imageIntent=()=>{entered();return new Promise(r=>release=r);};
 const asking=h.conversation.ask('현재 이미지를 확인해줘',{grantId:'initial',imageIdentity:'a'.repeat(64),mediaType:'image/png',base64:'AAAA'},{allowImageTools:true});
 await begun;h.conversation.invalidate();release({ok:true,grantId:'late',providerLabel:'ChatGPT',message:'late'});await asking;assert.equal(h.sent.length,1);assert.ok(!h.events.some(e=>e.kind==='assistant'&&e.text==='late'));
});
