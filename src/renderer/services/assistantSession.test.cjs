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

function harness(replies,{contexts}={}){
 const sent=[],logged=[],events=[],runs=[],executed=[];let contextCount=0;
 const deps={snapshot:()=>({stage:3,status:'ready',blocker:null,nextAction:{label:'학습 시작',step:3},progress:null,errors:[]}),
  facts:()=>({task:'classification',view:'simple',shared:false,images:20,unlabeled:0,classes:{OK:10,NG:10},split:{train:14,val:4,test:2},splitState:'saved',trainingStatus:'idle',currentImage:null}),
  setStep:async()=>{},focusTarget:()=>{},setTrainRatio:()=>{},splitSupport:()=>({supported:true,reason:null}),
  applySplit:async(...args)=>{executed.push(['applySplit',...args]);return {ok:true};},findImage:()=>[],setImageClass:async()=>({}),draftModels:async()=>[],
  startDraftBatch:async()=>({}),draftStats:async()=>({models:[]}),impact:async()=>({}),evaluation:()=>null,
  training:()=>({isTraining:false,status:'idle',preset:'balanced',computeOnServer:false,folderReady:true}),startTraining:async()=>{executed.push(['startTraining']);return {jobId:'job_1'};}};
 const bridge={send:async input=>{sent.push(JSON.parse(JSON.stringify(input)));const reply=replies.shift();return typeof reply==='function'?reply(input):reply;},
  cancel:async()=>true,beginRun:async(runId,tool)=>{runs.push(['begin',runId,tool]);return true;},endRun:async runId=>{runs.push(['end',runId]);}};
 const conversation=new AssistantConversation({bridge,tools:createAssistantTools(deps),context:()=>contexts?contexts[contextCount++]:{step:3},
  log:async entry=>{logged.push(entry);},provider:()=>'anthropic'},event=>events.push(event));
 return {conversation,sent,logged,events,runs,executed};
}
const call=(id,name,args={})=>({id,name,arguments:args});
const answer=(text,toolCalls=[])=>({ok:true,text,toolCalls,stop:toolCalls.length?'tool':'end'});

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
