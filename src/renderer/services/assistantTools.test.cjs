// The assistant's tool tiers (§4.5): read and navigate run at once, a write never runs without the person's confirmation
// and a run id, a forbidden tool never runs and answers "직접 하세요" with the place to do it.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>{if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,cache);}return original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const {createAssistantTools,ToolRefusal}=load(path.join(__dirname,'assistantTools.ts'));
const RUN='run_'+'cd'.repeat(12);

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
 const started=await tools.execute('generate_label_drafts',{threshold:0.4},{confirmed:true,runId:RUN});
 assert.equal(started.link,'batch:batch_1');assert.deepEqual(calls.at(-1),['startDraftBatch','job_new',0.4]);
 assert.match((await tools.preview('start_training',{})).undo,/학습 중지/);
 const notReady=createAssistantTools(fakeDeps({snapshot:()=>({stage:2,status:'ready',blocker:null,nextAction:{label:'x',step:2},progress:null,errors:[]})}).deps);
 await assert.rejects(notReady.preview('start_training',{}),error=>error.code==='training_not_ready');
 await assert.rejects(notReady.execute('start_training',{},{confirmed:true,runId:RUN}),error=>error.code==='training_not_ready');
 const notClassification=createAssistantTools(fakeDeps({facts:()=>({...fakeDeps().deps.facts(),task:'detection'})}).deps);
 await assert.rejects(notClassification.preview('assign_class',{image:'current',class_name:'NG'}),error=>error.code==='not_classification');
});
