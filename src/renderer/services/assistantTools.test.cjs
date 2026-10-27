// The assistant's tool tiers (§4.5): read and navigate run at once, a write never runs without the person's confirmation
// and a run id, a forbidden tool never runs and answers "직접 하세요" with the place to do it.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>{if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,cache);}return original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const {createAssistantTools,ToolRefusal}=load(path.join(__dirname,'assistantTools.ts'));
const RUN='run_'+'cd'.repeat(12);
const DRAFT_PREVIEW={title:'라벨 초안 만들기',lines:[],undo:'거절',bound:{model_job_id:'job_new',data_state:'current',threshold:0.5}};

function fakeDeps(overrides={}){
 const calls=[];const record=(name,value)=>(...args)=>{calls.push([name,...args]);return typeof value==='function'?value(...args):value;};
 const image={path:'/data/OK/ok_01.png',name:'ok_01.png',currentClass:'OK',folderClass:'OK'};
 const deps={
  snapshot:()=>({scope:'["/secret/project"]',stage:3,status:'ready',blocker:null,nextAction:{label:'학습 시작',step:3,target:'workflow-train'},progress:null,errors:[]}),
  facts:()=>({task:'classification',view:'simple',shared:false,images:20,unlabeled:4,classes:{OK:10,NG:10},split:{train:14,val:4,test:2},splitState:'saved',trainingStatus:'idle',currentImage:'/data/OK/ok_01.png'}),
  setStep:record('setStep',Promise.resolve()),focusTarget:record('focusTarget'),setTrainRatio:record('setTrainRatio'),
  splitSupport:()=>({supported:true,reason:null}),applySplit:record('applySplit',Promise.resolve({ok:true})),
  findImage:name=>name==='current'||name==='ok_01.png'?[image]:name==='dup.png'?[image,{...image,path:'/data/NG/dup.png'}]:[],
  setImageClass:record('setImageClass',async(_image,className)=>({className,changed:true})),
  draftModels:async()=>[{jobId:'job_old',task:'classification',dataState:'changed',createdAt:null},{jobId:'job_new',task:'classification',dataState:'current',createdAt:null}],
  startDraftBatch:record('startDraftBatch',async()=>({id:'batch_1',total:4})),draftStats:async()=>({models:[]}),impact:async()=>({models:[{data_state:'current'},{data_state:'changed'}],model_evaluations:[],flows:[],required_actions:['evaluate_model']}),
  evaluation:()=>null,training:()=>({isTraining:false,status:'idle',preset:'balanced',computeOnServer:false,folderReady:true}),
  startTraining:record('startTraining',async()=>({jobId:'job_9'})),...overrides};
 return {deps,calls};
}

test('every tool has exactly one tier and the forbidden list covers §4.5',()=>{
 const tools=createAssistantTools(fakeDeps().deps);
 const tiers=Object.fromEntries(tools.specs().map(spec=>[spec.name,tools.tier(spec.name)]));
 assert.deepEqual(Object.keys(tiers).filter(name=>tiers[name]==='write').sort(),['assign_class','generate_label_drafts','save_split','start_training']);
 assert.deepEqual(Object.keys(tiers).filter(name=>tiers[name]==='forbidden').sort(),['adopt_drafts','approve_flow','approve_model','change_account_or_permissions','confirm_ground_truth','delete_label_files','delete_original_images','deploy_or_register_service','send_files_outside']);
 assert.ok(tools.specs().every(spec=>/^[a-z][a-z0-9_]{1,63}$/.test(spec.name)&&spec.description&&spec.parameters.type==='object'));
 assert.ok(JSON.stringify(tools.specs()).length<24000,'fits the main process bound');
});

test('task tools propose plans and read app capabilities without approving or running actions',async()=>{
 const calls=[],{deps}=fakeDeps({tasks:{isExecuting:()=>false,features:action=>({action}),screen:()=>({step:1}),status:()=>({status:'draft'}),propose:async input=>{calls.push(input);return {status:'draft',execution_requires_person:true};}}});
 const tools=createAssistantTools(deps);
 assert.equal(tools.tier('propose_task'),'navigate');assert.equal(tools.tier('approve_task'),null);
 const answer=await tools.runAutomatic('propose_task',{goal:'자료 나누기',steps:[{action:'save_split',args:{train_percent:80,val_percent:10,test_percent:10},label:'자료 나누기'}]});
 assert.equal(answer.ok,true);assert.equal(calls.length,1);assert.equal(calls[0].goal,'자료 나누기');
 await assert.rejects(tools.runAutomatic('propose_task',{goal:'x',binding:'model-controlled',steps:[]}),/계획/);
 assert.equal(calls.length,1);
 assert.ok(JSON.stringify(tools.specs()).length<24000,'real task tool schemas stay within provider bound');
});
test('an active task fences legacy writes and settings preparation before their side effects',async()=>{
 const {deps,calls}=fakeDeps({tasks:{isExecuting:()=>true,features:()=>({}),screen:()=>({}),status:()=>({}),propose:async()=>({})}});
 const tools=createAssistantTools(deps);
 await assert.rejects(tools.preview('save_split',{train_percent:80,val_percent:10,test_percent:10}),/계획|작업/);
 await assert.rejects(tools.execute('start_training',{},{confirmed:true,runId:RUN}),/계획|작업/);
 await assert.rejects(tools.runAutomatic('prefill_split',{train_percent:80}),/계획|작업/);
 assert.deepEqual(calls,[]);
});

test('diagnostics are read-only, awaited, and accept only a registered section',async()=>{
 const sections=[];const {deps,calls}=fakeDeps({diagnostics:async section=>{sections.push(section);return {observed_at:'now',section,errors:[{code:'invalid_json',message:'Unexpected end of JSON input'}]};}});
 const tools=createAssistantTools(deps);
 assert.equal(tools.tier('read_app_diagnostics'),'read');
 const result=await tools.runAutomatic('read_app_diagnostics',{section:'requests'});
 assert.equal(JSON.parse(result.content).errors[0].code,'invalid_json');
 assert.deepEqual(sections,['requests']);assert.deepEqual(calls,[]);
 await assert.rejects(tools.runAutomatic('read_app_diagnostics',{section:'shell'}),/section|진단/);
 await assert.rejects(tools.runAutomatic('read_app_diagnostics',{path:'/private/log'}),/진단|인자/);
 deps.diagnostics=async(section,offset)=>({section,offset});
 assert.equal(JSON.parse((await tools.runAutomatic('read_app_diagnostics',{section:'requests',offset:8})).content).offset,8);
 for(const offset of [-1,1.5,10001,'8'])await assert.rejects(tools.runAutomatic('read_app_diagnostics',{section:'requests',offset}),/offset|진단|인자/);
});

test('task status awaits the GET-only observation rather than serializing a Promise',async()=>{
 const {deps}=fakeDeps({tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:async()=>({revision:7,fresh:true,current_step_id:'step_2',status:'waiting_human'}),propose:async()=>({})}});
 const value=JSON.parse((await createAssistantTools(deps).runAutomatic('read_task_status',{})).content);
 assert.equal(value.revision,7);assert.equal(value.fresh,true);assert.equal(value.current_step_id,'step_2');
});

test('a write tool never runs without the person\'s confirmation and a run id',async()=>{
 const {deps,calls}=fakeDeps();const tools=createAssistantTools(deps);
 for(const confirmation of [undefined,null,{confirmed:false,runId:RUN},{confirmed:true,runId:'run_short'},{confirmed:'yes',runId:RUN}])
  await assert.rejects(tools.execute('save_split',{train_percent:70,val_percent:20,test_percent:10},confirmation),error=>error instanceof ToolRefusal&&error.code==='confirmation_required');
 await assert.rejects(tools.runAutomatic('save_split',{train_percent:70,val_percent:20,test_percent:10}),error=>error.code==='not_automatic');
 assert.deepEqual(calls,[],'nothing was applied');
 const preview=await tools.preview('save_split',{train_percent:70,val_percent:20,test_percent:10});
 assert.equal(preview.title,'분할 저장');assert.match(preview.lines[0],/학습 70% · 검증 20% · 시험 10%/);assert.match(preview.undo,/되돌립니다/);
 assert.deepEqual(calls,[],'a preview changes nothing');
 const done=await tools.execute('save_split',{train_percent:70,val_percent:20,test_percent:10},{confirmed:true,runId:RUN});
 assert.equal(done.ok,true);assert.deepEqual(calls,[['applySplit',0.7,0.2,0.1]]);
 await assert.rejects(tools.preview('save_split',{train_percent:70,val_percent:20,test_percent:20}),/합은 100/);
});

test('forbidden tools never run and say where the person does it',async()=>{
 const {deps,calls}=fakeDeps();const tools=createAssistantTools(deps);
 for(const name of ['approve_model','adopt_drafts','delete_label_files','confirm_ground_truth','deploy_or_register_service']){
  const answer=tools.refusal(name);
  assert.equal(answer.ok,false);assert.match(answer.content,/^직접 하세요\. /);assert.equal(answer.errorCode,'forbidden');
  await assert.rejects(tools.runAutomatic(name,{}),error=>error.code==='not_automatic');
  await assert.rejects(tools.preview(name,{}),error=>error.code==='not_write');
  await assert.rejects(tools.execute(name,{},{confirmed:true,runId:RUN}),error=>error.code==='not_write');
 }
 assert.match(tools.refusal('adopt_drafts').content,/2단계 초안 검토/);
 assert.match(tools.refusal('no_such_tool').content,/목록에 없습니다/);
 assert.deepEqual(calls,[]);
});

test('read tools are path-free and navigate tools only open screens',async()=>{
 const {deps,calls}=fakeDeps();const tools=createAssistantTools(deps);
 const status=await tools.runAutomatic('read_status',{});
 assert.ok(!status.content.includes('/secret')&&!status.content.includes('/data/'),status.content);
 assert.deepEqual(JSON.parse((await tools.runAutomatic('read_data_changes',{})).content),{models:{current:1,changed:1},evaluations:{},flows:0,required_actions:['evaluate_model']});
 assert.match((await tools.runAutomatic('read_evaluation_summary',{})).content,/4단계/);
 await tools.runAutomatic('open_next_action',{});
 await tools.runAutomatic('open_step',{step:2,target:'Bad Target!'});
 assert.deepEqual(calls,[['setStep',3],['focusTarget','workflow-train'],['setStep',2],['focusTarget',undefined]]);
 await assert.rejects(tools.runAutomatic('open_step',{step:9}),/1~6/);
});

test('class, drafts and training previews check their preconditions and name the way back',async()=>{
 const {deps,calls}=fakeDeps();const tools=createAssistantTools(deps);
 const klass=await tools.preview('assign_class',{image:'current',class_name:'NG'});
 assert.match(klass.lines[0],/ok_01\.png: OK → NG/);assert.ok(!JSON.stringify(klass).includes('/data/'));assert.match(klass.undo,/class_changed/);
 await assert.rejects(tools.preview('assign_class',{image:'dup.png',class_name:'NG'}),error=>error.code==='ambiguous_image');
 await assert.rejects(tools.preview('assign_class',{image:'missing.png',class_name:'NG'}),error=>error.code==='unknown_image');
 const assigned=await tools.execute('assign_class',{image:'ok_01.png',class_name:null},{confirmed:true,runId:RUN});
 assert.equal(assigned.link,'class_changed');assert.deepEqual(calls.at(-1)[0],'setImageClass');
 const drafts=await tools.preview('generate_label_drafts',{});
 assert.match(drafts.lines[0],/job_new/,'a model trained on the current data is preferred');assert.match(drafts.lines[2],/정답이 아니며/);
 const changed=await tools.preview('generate_label_drafts',{model_job_id:'job_old',threshold:0.3});
 assert.match(changed.lines[0],/평가 근거 아님/);assert.match(changed.lines[1],/0\.30/);
 await assert.rejects(tools.preview('generate_label_drafts',{model_job_id:'job_unknown'}),error=>error.code==='unknown_model');
 const started=await tools.execute('generate_label_drafts',{threshold:0.4},{confirmed:true,runId:RUN,preview:await tools.preview('generate_label_drafts',{threshold:0.4})});
 assert.equal(started.link,'batch:batch_1');assert.deepEqual(calls.at(-1),['startDraftBatch','job_new',0.4]);
 assert.match((await tools.preview('start_training',{})).undo,/학습 중지/);
 const notReady=createAssistantTools(fakeDeps({snapshot:()=>({stage:2,status:'ready',blocker:null,nextAction:{label:'x',step:2},progress:null,errors:[]})}).deps);
 await assert.rejects(notReady.preview('start_training',{}),error=>error.code==='training_not_ready');
 await assert.rejects(notReady.execute('start_training',{},{confirmed:true,runId:RUN}),error=>error.code==='training_not_ready');
 const notClassification=createAssistantTools(fakeDeps({facts:()=>({...fakeDeps().deps.facts(),task:'detection'})}).deps);
 await assert.rejects(notClassification.preview('assign_class',{image:'current',class_name:'NG'}),error=>error.code==='not_classification');
});

test('preparation reads backend evidence while training settings only prefill existing controls',async()=>{
 const {deps,calls}=fakeDeps({trainingPreparation:async()=>({ready:false,weights:{state:'missing'},input:{split_counts:{train:14,val:4,test:2}}}),
  prefillTraining:async settings=>{calls.push(['prefillTraining',settings]);return settings;}});
 const tools=createAssistantTools(deps);
 assert.equal(tools.tier('read_training_preparation'),'read');assert.equal(tools.tier('prefill_training'),'navigate');
 const checked=JSON.parse((await tools.runAutomatic('read_training_preparation',{})).content);
 assert.equal(checked.ready,false);assert.equal(checked.weights.state,'missing');assert.deepEqual(calls,[]);
 const prepared=await tools.runAutomatic('prefill_training',{preset:'precision',device:'cpu',batch_size:8});
 assert.equal(prepared.ok,true);assert.deepEqual(calls,[['prefillTraining',{preset:'precision',device:'cpu',batch_size:8}],['setStep',3],['focusTarget','workflow-training']]);
 assert.match(prepared.summary,/설정/);assert.ok(!calls.some(row=>row[0]==='startTraining'));
 for(const args of [{preset:'turbo'},{device:'cuda:7'},{batch_size:0},{batch_size:129},{batch_size:1.5},{}])
  await assert.rejects(tools.runAutomatic('prefill_training',args),error=>error.code==='invalid_arguments');
 const busy=createAssistantTools(fakeDeps({training:()=>({...deps.training(),isTraining:true})}).deps);
 await assert.rejects(busy.runAutomatic('prefill_training',{preset:'fast'}),error=>error.code==='already_training');
});

test('the assistant can read and open pending drafts in the requested queue order without adopting them',async()=>{
 const {deps,calls}=fakeDeps({pendingDrafts:async(order,limit)=>({order,total:2,drafts:[{id:'d2',image:'b.png',candidate_count:1,confidence:.3}].slice(0,limit)}),
  openPendingDraft:async(id,order)=>{calls.push(['openPendingDraft',id,order]);return{id:id||'d2',image:'b.png',order};}});
 const tools=createAssistantTools(deps);
 const listed=JSON.parse((await tools.runAutomatic('read_pending_drafts',{order:'uncertain',limit:12})).content);
 assert.equal(listed.drafts[0].id,'d2');assert.equal(listed.order,'uncertain');assert.deepEqual(calls,[]);
 assert.equal(tools.tier('open_pending_draft'),'navigate');
 assert.equal((await tools.runAutomatic('open_pending_draft',{order:'confident',proposal_id:'d1'})).ok,true);
 assert.deepEqual(calls,[['openPendingDraft','d1','confident']]);
 for(const args of [{order:'random'},{limit:0},{limit:25}])await assert.rejects(tools.runAutomatic('read_pending_drafts',args),error=>error.code==='invalid_arguments');
 assert.equal(tools.tier('adopt_drafts'),'forbidden');
});

test('evaluation guidance is read-only and carries the measured threshold tradeoff instead of applying it',async()=>{
 const evidence={loaded:true,recommendation:{threshold:.35,escapes:1,overkills:2},conditions:{evaluated_split:'val',selection_overlap:true}};
 const {deps,calls}=fakeDeps({evaluationGuidance:()=>evidence});const tools=createAssistantTools(deps);
 assert.equal(tools.tier('read_evaluation_guidance'),'read');
 assert.deepEqual(JSON.parse((await tools.runAutomatic('read_evaluation_guidance',{})).content),evidence);
 assert.deepEqual(calls,[]);assert.equal(tools.tier('approve_model'),'forbidden');
});

test('a scope change while choosing a draft model refuses the write before batch mutation',async()=>{
 let current=true,release;const {deps,calls}=fakeDeps({draftModels:()=>new Promise(resolve=>{release=resolve;})});const tools=createAssistantTools(deps);
 const pending=tools.execute('generate_label_drafts',{}, {confirmed:true,runId:RUN,preview:DRAFT_PREVIEW,isCurrent:()=>current});
 current=false;release([{jobId:'job_new',task:'classification',dataState:'current'}]);
 await assert.rejects(pending,error=>error.code==='context_changed');assert.deepEqual(calls,[]);
 await assert.rejects(tools.runAutomatic('open_step',{step:2},()=>false),error=>error.code==='context_changed');
 await assert.rejects(tools.preview('save_split',{train_percent:70,val_percent:20,test_percent:10},()=>false),error=>error.code==='context_changed');
});
test('completed write outcomes are refused after a scope change and mutation deps receive the same guard',async()=>{
 for(const [name,args,dep,result] of [
  ['save_split',{train_percent:70,val_percent:20,test_percent:10},'applySplit',{ok:true}],
  ['assign_class',{image:'current',class_name:'NG'},'setImageClass',{className:'NG',changed:true}],
  ['generate_label_drafts',{},'startDraftBatch',{id:'A_batch',total:4}],
  ['start_training',{},'startTraining',{jobId:'A_job'}],
 ]){
  let current=true,release,guard;const check=()=>current;
  const {deps}=fakeDeps({[dep]:(...values)=>{guard=values.at(-1);return new Promise(resolve=>{release=resolve;});}});
  const pending=createAssistantTools(deps).execute(name,args,{confirmed:true,runId:RUN,preview:DRAFT_PREVIEW,isCurrent:check});
  // Draft model selection yields once before the write is dispatched.
  while(!release)await Promise.resolve();
  assert.equal(guard,check);current=false;release(result);await assert.rejects(pending,error=>error.code==='context_changed');
 }
});
// Defect: preview and execute each picked the default draft model, so the confirm card could run a model it never showed.
test('the draft model and threshold shown in the preview are the ones executed, or the write is refused',async()=>{
 let models=[{jobId:'job_a',task:'classification',dataState:'current',createdAt:null}];
 const {deps,calls}=fakeDeps({draftModels:async()=>models});const tools=createAssistantTools(deps);
 const preview=await tools.preview('generate_label_drafts',{threshold:0.42});assert.match(preview.lines[0],/job_a/);
 models=[{jobId:'job_b',task:'classification',dataState:'current',createdAt:null},...models];
 await tools.execute('generate_label_drafts',{threshold:0.42},{confirmed:true,runId:RUN,preview});
 assert.deepEqual(calls.at(-1),['startDraftBatch','job_a',0.42],'a newer default does not replace the previewed model');
 models=[{jobId:'job_b',task:'classification',dataState:'current',createdAt:null}];calls.length=0;
 await assert.rejects(tools.execute('generate_label_drafts',{threshold:0.42},{confirmed:true,runId:RUN,preview}),
  error=>error.code==='model_unavailable'&&/job_a/.test(error.message)&&/다시 요청/.test(error.message));
 models=[{jobId:'job_a',task:'classification',dataState:'changed',createdAt:null}];
 await assert.rejects(tools.execute('generate_label_drafts',{threshold:0.42},{confirmed:true,runId:RUN,preview}),error=>error.code==='model_unavailable');
 await assert.rejects(tools.execute('generate_label_drafts',{threshold:0.42},{confirmed:true,runId:RUN}),error=>error.code==='preview_required');
 assert.deepEqual(calls,[],'no other model is picked');
});
