const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const {create}=require('zustand');
function harness(){
 const project=create(()=>({project:{id:'a',source_dataset_dir:'/a',active_labelset_id:'labels'},projectDir:'/a',task:'detection'}));
 const annotation=create(()=>({currentImage:{file_path:'/a/a.png',file_name:'a.png',image_id:'a'},metadata:{image_uuid:'a',content_hash:'abc',revision:1},annotationLoadStatus:'ready',isDirty:false,annotations:[]}));
 const compute=create(()=>({transportRevision:0,selectedProfileId:null})),dataset=create(()=>({folderPath:'/a',split:{train:7,val:2,test:1}})),training=create(()=>({preset:'fast'}));
 let generation=1,contextListeners=[],imageReads=0,delayedRead=null,delayedIntent=null;const sent=[],cancelled=[],executed=[];const tools={specs:()=>[],tier:name=>name==='write'?'write':null,preview:async()=>({title:'테스트 쓰기',lines:[],undo:'거절'}),execute:async()=>{executed.push('write');return {ok:true,summary:'쓰기',content:'쓰기'};}};
 const config={active:'openai-compatible',providers:[{provider:'openai-compatible',baseUrl:'http://localhost:123/v1',model:'m',format:'chat',supportsImages:true}]};
 const bridge={getState:async()=>config,saveProvider:async input=>({...config,providers:[input]}),imageIntent:async input=>delayedIntent?delayedIntent(input):({ok:true,grantId:'g',message:'확인',providerLabel:'local'}),
  send:async input=>{sent.push(input);return {ok:true,text:'답',toolCalls:[]};},cancel:async id=>{cancelled.push(id);return true;},beginRun:async()=>true,endRun:async()=>{}};
 const stubs={zustand:{create},'./useAnnotationStore':{useAnnotationStore:annotation},'./useProjectStore':{useProjectStore:project},'./useComputeStore':{useComputeStore:compute},'./useDatasetStore':{useDatasetStore:dataset},'./useTrainingStore':{useTrainingStore:training},
  '../stores/useAnnotationStore':{useAnnotationStore:annotation},'../stores/useProjectStore':{useProjectStore:project},'../stores/useComputeStore':{useComputeStore:compute},'../stores/useDatasetStore':{useDatasetStore:dataset},'../stores/useTrainingStore':{useTrainingStore:training},
  '../services/api':{getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>generation,subscribeProjectContext:fn=>{contextListeners.push(fn);return()=>{};}},
  './api':{getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>generation,subscribeProjectContext:fn=>{contextListeners.push(fn);return()=>{};}},
  '../services/hostAdapter':{host:{assistant:()=>bridge}},'../services/assistantLog':{assistantLog:{append:async()=>{},list:async()=>({runs:[]})}},
  '../services/assistantRuntime':{assistantContext:()=>({step:2}),createRuntimeTools:()=>tools,currentImageForQuestion:async()=>{imageReads++;if(delayedRead)return delayedRead();return {mediaType:'image/png',base64:'AAAA'};}},
  '../stores/workflowEvidence':{workflowEvidenceRevision:()=>0},'./workflowEvidence':{workflowEvidenceRevision:()=>0}};
 const cache=new Map();function load(file){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const orig=m.require.bind(m);m.require=n=>{if(stubs[n])return stubs[n];if(n.startsWith('.')){const target=path.resolve(path.dirname(file),n)+'.ts';if(fs.existsSync(target))return load(target);}return orig(n);};m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
 const store=load(path.join(__dirname,'useAssistantStore.ts')).useAssistantStore;
 return {store,project,annotation,compute,dataset,training,sent,cancelled,executed,bridge,reads:()=>imageReads,setRead:fn=>delayedRead=fn,setIntent:fn=>delayedIntent=fn,changeAuthority:()=>{generation++;contextListeners.forEach(fn=>fn());}};
}
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:v=>resolve(v)};};
test('changing the selected image invalidates the shown offer before any pixels are read',async()=>{
 const h=harness();await h.store.getState().loadConfig();await h.store.getState().offerImage('확인');
 h.annotation.setState({currentImage:{file_path:'/a/b.png',file_name:'b.png',image_id:'b'}});
 await h.store.getState().sendImage();assert.equal(h.reads(),0);assert.equal(h.sent.length,0);assert.equal(h.store.getState().imageOffer,null);
});
test('image or project changes during the local image read cannot send a replacement image',async()=>{
 for(const kind of ['image','project']){
  const h=harness();await h.store.getState().loadConfig();await h.store.getState().offerImage('확인');
  const read=deferred();h.setRead(()=>read.promise);const sending=h.store.getState().sendImage();
  if(kind==='image')h.annotation.setState({metadata:{image_uuid:'a',content_hash:'new',revision:2}});else h.changeAuthority();
  read.resolve({mediaType:'image/png',base64:'BBBB'});await sending;assert.equal(h.sent.length,0);
 }
});
test('a late offer cannot replace a newer selection or revive a cancelled confirmation',async()=>{
 for(const cancel of [false,true]){
  const h=harness();await h.store.getState().loadConfig();const intent=deferred();h.setIntent(()=>intent.promise);
  const offering=h.store.getState().offerImage('이 이미지');
  if(cancel)h.store.getState().dropImage();else h.annotation.setState({currentImage:null});
  intent.resolve({ok:true,grantId:'old',message:'old',providerLabel:'old'});await offering;
  assert.equal(h.store.getState().imageOffer,null);
 }
});
test('one unchanged image confirmation sends exactly its opaque identity without local paths',async()=>{
 const h=harness();await h.store.getState().loadConfig();const intents=[];h.bridge.imageIntent=async input=>{intents.push(input);return {ok:true,grantId:'fresh',message:'전송 확인',providerLabel:'local'};};
 await h.store.getState().offerImage('질문');assert.equal(h.reads(),0);await h.store.getState().sendImage();await h.store.getState().sendImage();
 assert.equal(h.reads(),1);assert.equal(h.sent.length,1);assert.match(intents[0].imageIdentity,/^[a-f0-9]{64}$/);
 assert.equal(h.sent[0].image.imageIdentity,intents[0].imageIdentity);assert.ok(!JSON.stringify(h.sent).includes('/a/'));
});
test('project or transport change clears history and cancels only the old request',async()=>{
 const h=harness();await h.store.getState().loadConfig();const reply=deferred();h.bridge.send=async input=>{h.sent.push(input);return reply.promise;};
 const asking=h.store.getState().ask('A');h.changeAuthority();
 assert.equal(h.store.getState().busy,false);assert.equal(h.cancelled.length,1);reply.resolve({ok:true,text:'늦은 A',toolCalls:[]});await asking;
 assert.ok(!h.store.getState().items.some(item=>item.text==='늦은 A'));
});
test('settings in flight cannot send against a receiver not yet shown by the panel',async()=>{
 const h=harness();await h.store.getState().loadConfig();const saving=deferred();h.bridge.saveProvider=()=>saving.promise;
 const changed=h.store.getState().saveProvider({provider:'openai-compatible',baseUrl:'http://localhost:456/v1',model:'m',supportsImages:true});
 await h.store.getState().ask('보내기');await h.store.getState().offerImage('이미지');
 assert.equal(h.sent.length,0);assert.equal(h.store.getState().imageOffer,null);
 saving.resolve({active:'openai-compatible',providers:[{provider:'openai-compatible',baseUrl:'http://localhost:456/v1',model:'m',supportsImages:true}]});await changed;
 await h.store.getState().ask('새 설정');assert.equal(h.sent.length,1);
});
test('image A-B-A while a confirmed local read is pending still consumes the old offer',async()=>{
 const h=harness();await h.store.getState().loadConfig();await h.store.getState().offerImage('A');const read=deferred();h.setRead(()=>read.promise);
 const original=h.annotation.getState().currentImage,sending=h.store.getState().sendImage();
 h.annotation.setState({currentImage:{...original,file_path:'/a/b.png'}});h.annotation.setState({currentImage:original});
 read.resolve({mediaType:'image/png',base64:'AAAA'});await sending;assert.equal(h.sent.length,0);
});

test('image or setting A-B-A while beginRun awaits cannot revive an old write confirmation',async()=>{
 for(const kind of ['image','settings']){
  const h=harness();await h.store.getState().loadConfig();h.bridge.send=async()=>({ok:true,text:'',toolCalls:[{id:'w1',name:'write',arguments:{}}]});
  await h.store.getState().ask('쓰기');assert.ok(h.store.getState().pending);
  const begin=deferred();h.bridge.beginRun=()=>begin.promise;const confirming=h.store.getState().decide(true);
  if(kind==='image'){const original=h.annotation.getState().currentImage;h.annotation.setState({currentImage:{...original,file_path:'/a/b.png'}});h.annotation.setState({currentImage:original});}
  else {h.training.setState({preset:'precision'});h.training.setState({preset:'fast'});}
  begin.resolve(true);await confirming;assert.deepEqual(h.executed,[]);
 }
});
test('editing a selected compute profile under the same id invalidates the write preview',async()=>{
 const h=harness();await h.store.getState().loadConfig();h.compute.setState({selectedProfileId:'server',profiles:[{id:'server',name:'A',base_url:'https://server-a.invalid'}]});
 h.bridge.send=async()=>({ok:true,text:'',toolCalls:[{id:'w1',name:'write',arguments:{}}]});await h.store.getState().ask('쓰기');assert.ok(h.store.getState().pending);
 h.compute.setState({profiles:[{id:'server',name:'B',base_url:'https://server-b.invalid'}]});await h.store.getState().decide(true);
 assert.equal(h.store.getState().pending,null);assert.deepEqual(h.executed,[]);
});
test('failed image intent IPC leaves no offer and shows an action without reading pixels',async()=>{
 const h=harness();await h.store.getState().loadConfig();h.bridge.imageIntent=async()=>{throw Error('IPC down');};
 await h.store.getState().offerImage('확인');assert.equal(h.store.getState().imageOffer,null);assert.equal(h.reads(),0);assert.equal(h.sent.length,0);
 assert.ok(h.store.getState().items.some(item=>item.kind==='error'&&/다시/.test(item.detail)));
});

test('same-model ChatGPT account authority changes cancel an old reply and clear image/write confirmation',async()=>{
 const h=harness();const config=authority=>({active:'chatgpt',encryptionAvailable:true,providers:[{provider:'chatgpt',baseUrl:'https://chatgpt.com',model:'chosen',hasKey:false,supportsImages:false,ready:true,connectionAuthority:authority}],chatgpt:{active:'a',accounts:[{recordId:'a',label:'계정 1',model:'chosen',signedIn:true,planUsageEnabled:true}],status:'connected',authority}});
 h.store.setState({config:config('first')});const reply=deferred();h.bridge.send=async input=>{h.sent.push(input);return reply.promise;};
 const asking=h.store.getState().ask('이 계정으로 질문');h.store.setState({config:config('second')});
 assert.equal(h.store.getState().busy,false);assert.equal(h.cancelled.length,1);reply.resolve({ok:true,text:'옛 계정 응답',toolCalls:[]});await asking;
 assert.ok(!h.store.getState().items.some(i=>i.text==='옛 계정 응답'));
});
test('ChatGPT connection mutation invalidates the session before IPC and cannot send while configuring',async()=>{
 const h=harness();await h.store.getState().loadConfig();const changed=deferred(),calls=[];h.bridge.chatgpt={begin:input=>{calls.push(input);return changed.promise;}};
 assert.equal(typeof h.store.getState().beginChatgpt,'function');
 const changing=h.store.getState().beginChatgpt({termsAccepted:true});await h.store.getState().ask('전환중 질문');assert.equal(h.sent.length,0);assert.deepEqual(calls,[{termsAccepted:true}]);
 changed.resolve({active:'chatgpt',encryptionAvailable:true,providers:[],chatgpt:{accounts:[],active:null,encryptionAvailable:true,status:'waiting'}});await changing;assert.equal(h.store.getState().config.chatgpt.status,'waiting');
});
test('ChatGPT image requests cannot even obtain an intent or read local pixels',async()=>{
 const h=harness();await h.store.getState().loadConfig();h.store.setState({config:{active:'chatgpt',providers:[],chatgpt:{active:null,accounts:[],status:'idle'}}});let intents=0;h.bridge.imageIntent=async()=>{intents++;return {ok:true};};
 await h.store.getState().offerImage('이 이미지');assert.equal(intents,0);assert.equal(h.reads(),0);assert.equal(h.store.getState().imageOffer,null);assert.match(h.store.getState().items.at(-1).text,/텍스트/);
});

test('provider rate-limit errors retain a machine code for the visible usage-management action',async()=>{
 const h=harness();await h.store.getState().loadConfig();h.bridge.send=async()=>({ok:false,error:{code:'rate_limited',reason:'한도에 도달했습니다.',action:'사용량을 확인하세요.'}});await h.store.getState().ask('질문');
 assert.equal(h.store.getState().items.find(item=>item.kind==='error').code,'rate_limited');
});
test('a ChatGPT usage error refreshes paused public state without retrying or losing the visible explanation',async()=>{
 const h=harness(),config=paused=>({active:'chatgpt',encryptionAvailable:true,providers:[{provider:'chatgpt',baseUrl:'https://api.openai.com/v1',model:'chosen',format:'responses',hasKey:false,supportsImages:false,ready:!paused,connectionAuthority:'same'}],chatgpt:{active:'a',accounts:[{recordId:'a',label:'계정',model:'chosen',signedIn:true,planUsageEnabled:true,usagePaused:paused}],status:'connected',authority:'same'}});h.store.setState({config:config(false)});let sent=0;
 h.bridge.getState=async()=>config(true);h.bridge.send=async()=>{sent++;return{ok:false,error:{code:'rate_limited',reason:'한도 확인 필요',action:'사용량을 확인하세요.',status:200,diagnostics:{bodyShape:'response_error',providerCode:'subscription_sharing_usage_limit_exceeded'}}};};await h.store.getState().ask('질문');await new Promise(r=>setImmediate(r));
 assert.equal(h.store.getState().config.chatgpt.accounts[0].usagePaused,true);assert.equal(sent,1);assert.ok(h.store.getState().items.some(i=>i.kind==='error'&&i.text==='한도 확인 필요'&&/HTTP 200/.test(i.detail)));
});
test('each ChatGPT mutation invalidates an existing reply before its IPC result is accepted',async()=>{
 for(const [method,bridgeMethod,args] of [['cancelChatgpt','cancel',[]],['selectChatgptAccount','selectAccount',['opaque-b']],['selectChatgptModel','selectModel',['opaque-b','visible-model']],['signOutChatgpt','signOut',['opaque-b']]]){
  const h=harness();await h.store.getState().loadConfig();const reply=deferred(),change=deferred(),calls=[];h.bridge.send=()=>reply.promise;h.bridge.chatgpt={[bridgeMethod]:(...input)=>{calls.push(input);return change.promise;}};
  const asking=h.store.getState().ask('기존 질문');assert.equal(h.store.getState().busy,true);
  const changing=h.store.getState()[method](...args);assert.equal(h.store.getState().busy,false);assert.equal(h.cancelled.length,1);assert.deepEqual(calls,[args]);
  reply.resolve({ok:true,text:'폐기될 응답',toolCalls:[]});await asking;assert.ok(!h.store.getState().items.some(item=>item.text==='폐기될 응답'));
  change.resolve({active:null,providers:[],encryptionAvailable:true});assert.equal(await changing,true);
 }
});
