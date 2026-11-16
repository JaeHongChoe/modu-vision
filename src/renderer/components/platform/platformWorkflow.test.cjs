require('../../../../scripts/test_ts_dependencies.cjs');
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
function load(relative, mocks = {}) {
  const file = path.resolve(__dirname, relative), m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = ref => Object.hasOwn(mocks, ref) ? mocks[ref] : original(ref);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {fileName:file,
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText, file);
  return m.exports;
}
const receipt = {id:'transfer-1',revision:'frozen-revision',project_id:'project-1',geti_project_id:'geti-1',task:'classification',dataset_version_id:'version-1',manifest_sha256:'abc',dataset_fingerprint:'fingerprint',labelset_id:'labels',split_sha256:'split',counts:{training:4,validation:2,testing:2,total:8},source_mapping:[{relative_path:'test.png',sha256:'digest',media_id:'media-1',subset:'testing'}],class_mapping:{},created_at:'2026-11-17'};
function status(overrides={}) { return {available:true,runtime:{state:'running'},project:{name:'표면 검사용 · 긴 프로젝트',task:'classification',supported:true,linked:true},dataset:receipt,jobs:[],models:[],architectures:[{id:'efficientnet',name:'EfficientNet',task:'classification',weights_ready:true,supported:true}],native_tasks:['classification','segmentation','detection','anomaly','ocr','rotated_detection','rotation','enhancement','defect_gan','super_resolution'],permissions:{can_prepare:true,can_train:true,can_adopt:true},...overrides}; }
function fixture(handler=async path=>path.endsWith('/status')?status():{}) {
  const calls=[];let scope='account-1/project-1/transport-1';
  const context={workspace_id:'workspace',project_id:'project-1',actor_id:'account-1',mode:'local'};
  const api={getProjectContext:()=>context,getApiDiagnosticScope:()=>scope,
    request:async(path,options={})=>{calls.push({path,...options});return handler(path,options);}};
  const exports=load('../../services/getiPlatform.ts',{'./api':api});
  const controller=new exports.GetiPlatformController({project:{id:'project-1',name:'표면 검사',task:'classification',source_dataset_dir:'/source',active_labelset_id:'labels'},inputRecipe:null,isCurrent:()=>scope==='account-1/project-1/transport-1'});
  return {controller,calls,exports,switchScope:()=>{scope='account-2/project-2/transport-2';}};
}
test('next action comes from actual prerequisites, permissions and uncertainty',()=>{
  const {exports:{platformNextAction}}=fixture();
  assert.equal(platformNextAction(null,{hasSource:false}).kind,'refresh');
  assert.equal(platformNextAction(status(),{hasSource:false}).kind,'import');
  assert.equal(platformNextAction(status({runtime:{state:'stopped'}}),{hasSource:true}).kind,'start');
  assert.equal(platformNextAction(status({dataset:null}),{hasSource:true}).kind,'prepare');
  assert.equal(platformNextAction(status({architectures:[{id:'m',name:'M',task:'classification',supported:true,weights_ready:false}]}),{hasSource:true,architectureId:'m'}).kind,'weights');
  assert.equal(platformNextAction(status(),{hasSource:true,architectureId:'efficientnet'}).kind,'train');
  assert.equal(platformNextAction(status(),{hasSource:true,uncertain:true}).kind,'refresh');
  assert.equal(platformNextAction(status({project:{name:'마스크',task:'segmentation',supported:false,linked:false}}),{hasSource:true}).kind,'native');
  assert.equal(platformNextAction(status({permissions:{can_prepare:false,can_train:false,can_adopt:false}}),{hasSource:true}).disabled,true);
});
test('mount/read never starts runtime or submits training, and carries the selected authority',async()=>{
  const f=fixture();assert.equal(f.calls.length,0);await f.controller.refresh();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/platform/geti/status');
  assert.equal(f.calls[0].projectContext.actor_id,'account-1');assert.ok(f.calls[0].signal);
  assert.equal(f.controller.getSnapshot().status.project.name,'표면 검사용 · 긴 프로젝트');
});
test('training double activation dispatches once and a lost response requires reconciliation',async()=>{
  let rejectTrain;
  const f=fixture(async path=>path.endsWith('/status')?status():new Promise((_,reject)=>{rejectTrain=reject;}));
  await f.controller.refresh();const first=f.controller.train();const second=f.controller.train();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/train')).length,1);
  rejectTrain(new Error('connection lost'));await Promise.all([first,second]);
  assert.equal(f.controller.getSnapshot().uncertain,true);
  await f.controller.refresh();await f.controller.train();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/train')).length,1,'an empty fresh list cannot prove a lost submission did not happen');
  assert.match(f.controller.getSnapshot().error,/결과|확인/);
});
test('project/account/transport switch aborts old work and ignores late results',async()=>{
  let resolve;
  const f=fixture(()=>new Promise(done=>{resolve=done;}));const pending=f.controller.refresh();
  f.switchScope();f.controller.dispose();assert.equal(f.calls[0].signal.aborted,true);
  resolve(status());await pending;assert.equal(f.controller.getSnapshot().status,null);
  await f.controller.start();assert.equal(f.calls.length,1);
});
test('cancel remains a request until worker exit is confirmed',async()=>{
  const active={id:'job-1',status:'running',progress:13};
  const f=fixture(async path=>path.endsWith('/status')?status({jobs:[active]}):{...active,status:'cancel_requested',worker_exited:false});
  await f.controller.refresh();await f.controller.cancel('job-1');
  const job=f.controller.getSnapshot().status.jobs[0];assert.equal(job.status,'cancel_requested');assert.equal(job.worker_exited,false);
  assert.match(f.controller.getSnapshot().notice,/종료.*확인|중단.*요청/);
});
test('prediction admits only exact frozen source images and never forwards a typed URL',async()=>{
  const model={id:'model-1',name:'표면 검사 AI',architecture:'efficientnet',task:'classification',variants:[{id:'v1',format:'openvino',precision:'fp32',weights_size:4,evaluations:[]}]};
  const f=fixture(async path=>path.endsWith('/status')?status({models:[model]}):path.startsWith('/api/platform/geti/images')?{items:[{image_uuid:'media-1',relative_path:'test.png',sha256:'digest',source_path:'/source/test.png',file_name:'test.png',transferred:true},{image_uuid:'foreign',relative_path:'other.png',sha256:'other',source_path:'https://external.invalid/x',file_name:'x',transferred:true}],pagination:{offset:0,limit:100,total:2}}:{});
  await f.controller.refresh();await f.controller.loadImages();
  assert.deepEqual(f.controller.getSnapshot().images.map(row=>row.image_uuid),['media-1']);
  f.controller.selectImage('foreign');await f.controller.predict();assert.equal(f.calls.filter(c=>c.path.endsWith('/predict')).length,0);
  f.controller.selectImage('media-1');await f.controller.predict();
  const call=f.calls.find(c=>c.path.endsWith('/predict'));assert.equal(JSON.parse(call.body).image_path,'/source/test.png');
});
test('truth adoption needs deliberate review and frozen revision; permissions cannot be bypassed',async()=>{
  const f=fixture(async path=>path.endsWith('/status')?status():{dataset_version_id:'new-version',labelset_id:'new-labelset'});
  await f.controller.refresh();await f.controller.adoptLabels();assert.equal(f.calls.length,1);
  f.controller.reviewAdoption(true);await f.controller.adoptLabels();
  assert.equal(JSON.parse(f.calls.at(-1).body).expected_revision,'frozen-revision');
  const denied=fixture(async()=>status({permissions:{can_prepare:true,can_train:true,can_adopt:false}}));await denied.controller.refresh();denied.controller.reviewAdoption(true);await denied.controller.adoptLabels();assert.equal(denied.calls.length,1);
});
test('view keeps one primary, readable names, progress and unsupported native route',async()=>{
  const f=fixture();await f.controller.refresh();
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  let html=renderToStaticMarkup(React.createElement(PlatformWorkspaceView,{state:f.controller.getSnapshot(),controller:f.controller,hasSource:true,projectName:'표면 검사',task:'classification',onNavigate:()=>{}}));
  assert.equal((html.match(/workspace-button--primary/g)||[]).length,1);assert.match(html,/사진 준비/);assert.match(html,/AI 만들기/);assert.match(html,/결과 확인/);assert.match(html,/검사 사용/);assert.match(html,/학습 시작/);assert.match(html,/<details/);
  const g=fixture(async()=>status({project:{name:'마스크',task:'segmentation',supported:false,linked:false},jobs:[{id:'job',status:'cancel_requested',progress:null,worker_exited:false}]}));await g.controller.refresh();
  html=renderToStaticMarkup(React.createElement(PlatformWorkspaceView,{state:g.controller.getSnapshot(),controller:g.controller,hasSource:true,projectName:'마스크',task:'segmentation',onNavigate:()=>{}}));
  assert.match(html,/기존 모델/);assert.match(html,/종료.*확인/);assert.match(html,/semantic|시맨틱/);assert.doesNotMatch(html,/value="100"/);
});

test('a refused write preserves settings and requires a fresh successful read before another write',async()=>{
  const f=fixture(async path=>{if(path.endsWith('/status'))return status();throw Object.assign(new Error('원본 기준이 바뀌었습니다.'),{status:409});});
  await f.controller.refresh();f.controller.setEpochs('8');await f.controller.train();await f.controller.train();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/train')).length,1);
  assert.equal(f.controller.getSnapshot().epochs,'8');assert.equal(f.controller.getSnapshot().uncertain,false);
  await f.controller.refresh();await f.controller.train();assert.equal(f.calls.filter(c=>c.path.endsWith('/train')).length,2);
});

test('a degraded status retains useful records but blocks mutation until fresh read',async()=>{
  const f=fixture(async()=>status({runtime:{state:'running',degraded:true,message:'일부 기록을 새로 읽지 못했습니다.'}}));await f.controller.refresh();
  const {platformNextAction}=f.exports;assert.equal(platformNextAction(f.controller.getSnapshot().status,{hasSource:true}).kind,'refresh');
  await f.controller.train();assert.equal(f.calls.length,1);
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  const html=renderToStaticMarkup(React.createElement(PlatformWorkspaceView,{state:f.controller.getSnapshot(),controller:f.controller,hasSource:true,projectName:'표면 검사',task:'classification'}));
  assert.match(html,/일부 기록을 새로 읽지 못했습니다/);assert.match(html,/4장/);
});

test('prepare sends the scoped saved recipe and explicit retry reuses an unresolved operation id',async()=>{
  let attempts=0;const f=fixture(async path=>{if(path.endsWith('/status'))return status({dataset:null});if(++attempts===1)throw new Error('network loss');return receipt;});
  const recipe={version:1,operations:[{op:'code',language:'modu-numeric-v1',code:'clip(image, 0, 1)'}]};
  const controller=new f.exports.GetiPlatformController({project:{id:'project-1',name:'표면 검사',task:'classification',source_dataset_dir:'/source',active_labelset_id:'labels'},inputRecipe:recipe,isCurrent:()=>true});
  await controller.refresh();await controller.prepare();await controller.refresh();await controller.prepare();
  const writes=f.calls.filter(c=>c.path.endsWith('/prepare')).map(c=>JSON.parse(c.body));
  assert.equal(writes.length,2);assert.deepEqual(writes[0].input_recipe,recipe);assert.equal(writes[0].operation_id,writes[1].operation_id);
  controller.dispose();
});

test('prediction-disabled model formats cannot be executed through direct handler calls',async()=>{
  const model={id:'model',name:'저장된 AI',architecture:'e',task:'classification',variants:[{id:'pt',format:'pytorch',precision:'fp32',weights_size:1,evaluations:[],prediction_supported:false}]};
  const f=fixture(async path=>path.endsWith('/status')?status({models:[model]}):path.startsWith('/api/platform/geti/images')?{items:[{image_uuid:'media-1',relative_path:'test.png',sha256:'digest',source_path:'/source/test.png',transferred:true}],pagination:{offset:0,limit:100,total:1}}:{});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.predict();assert.equal(f.calls.filter(c=>c.path.endsWith('/predict')).length,0);
});

test('newly adopted labels are selected only by a second deliberate action through the existing activation owner',async()=>{
  const f=fixture(async path=>path.endsWith('/status')?status():{dataset_version_id:'new-version',labelset_id:'new-labelset'}), selected=[];
  await f.controller.refresh();await f.controller.useAdoptedLabels(async id=>{selected.push(id);return true;});assert.deepEqual(selected,[]);
  f.controller.reviewAdoption(true);await f.controller.adoptLabels();assert.deepEqual(selected,[]);
  await f.controller.useAdoptedLabels(async id=>{selected.push(id);return false;});
  assert.deepEqual(selected,['new-labelset']);assert.equal(f.controller.getSnapshot().adopted.labelset_id,'new-labelset');assert.match(f.controller.getSnapshot().error,/라벨.*선택|전환/);
});

test('native navigation failure is visible and never becomes an unhandled rejected promise',async()=>{
  const f=fixture();await f.controller.refresh();const steps=[];
  await f.controller.navigate(2,async step=>{steps.push(step);return false;});assert.deepEqual(steps,[2]);assert.match(f.controller.getSnapshot().error,/화면.*열|저장/);
  await f.controller.navigate(7,async step=>{steps.push(step);return true;});assert.deepEqual(steps,[2]);
});

test('upstream running status with a saved cancel-request flag remains cancellation uncertainty',async()=>{
  const job={id:'real-job-shape',status:'running',progress:20,cancel_requested:true,worker_exited:false};
  const f=fixture(async()=>status({jobs:[job]}));await f.controller.refresh();
  assert.match(f.exports.platformJobLabel(job),/중단 요청.*종료 확인/);
  await f.controller.cancel(job.id);assert.equal(f.calls.length,1,'never resubmit an already recorded cancel request');
});

test('acknowledged unconfirmed submission clears only after the same operation has a proven terminal record',async()=>{
  let operation,reads=0;
  const f=fixture(async(path,options)=>{if(path.endsWith('/status'))return ++reads===1?status():status({jobs:[{id:'job',operation_id:operation,status:'interrupted',progress:0,worker_exited:true}]});operation=JSON.parse(options.body).operation_id;return{id:'job',operation_id:operation,status:'submission_unconfirmed',progress:0,worker_exited:false};});
  await f.controller.refresh();await f.controller.train();assert.equal(f.controller.getSnapshot().uncertain,true);
  await f.controller.refresh();assert.equal(f.controller.getSnapshot().uncertain,false);assert.equal(f.exports.platformJobActive(f.controller.getSnapshot().status.jobs[0]),false);
});

test('view-only members can read models but cannot submit a prediction',async()=>{
  const model={id:'model',name:'AI',architecture:'e',task:'classification',variants:[{id:'ov',format:'openvino',precision:'fp32',weights_size:1,evaluations:[]}]};
  const f=fixture(async path=>path.endsWith('/status')?status({models:[model],permissions:{can_prepare:false,can_train:false,can_adopt:false}}):path.startsWith('/api/platform/geti/images')?{items:[{image_uuid:'media-1',relative_path:'test.png',sha256:'digest',source_path:'/source/test.png',transferred:true}],pagination:{offset:0,limit:100,total:1}}:{});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.predict();assert.equal(f.calls.filter(c=>c.path.endsWith('/predict')).length,0);
});

test('uncertain work has an explicit owned-runtime recovery action without a repeated training submission',async()=>{
  const uncertain={id:'unknown-job',status:'submission_unconfirmed',progress:0,worker_exited:false};
  const f=fixture(async path=>path.endsWith('/recover')?status({runtime:{state:'stopped'},jobs:[{...uncertain,status:'interrupted',worker_exited:true}]}):status({jobs:[uncertain]}));
  await f.controller.refresh();await f.controller.recover();
  assert.deepEqual(f.calls.map(c=>c.path),['/api/platform/geti/status','/api/platform/geti/recover']);
  assert.equal(f.controller.getSnapshot().status.jobs[0].status,'interrupted');assert.equal(f.controller.getSnapshot().uncertain,false);assert.match(f.controller.getSnapshot().notice,/종료|정리/);
});

test('semantic projects keep native default and require explicit instance target before any write',async()=>{
  const semantic=status({project:{name:'semantic',task:'segmentation',supported:false,linked:false,target_task_options:['instance_segmentation']},runtime:{state:'stopped'},dataset:null,architectures:[]});
  const f=fixture(async path=>path.endsWith('/start')?{...semantic,runtime:{state:'running'}}:path.endsWith('/prepare')?{...receipt,source_task:'segmentation',task:'instance_segmentation'}:semantic);
  await f.controller.refresh();assert.equal(f.exports.platformNextAction(semantic,{hasSource:true}).kind,'native');
  f.controller.selectTargetTask('instance_segmentation');
  assert.equal(f.exports.platformNextAction(semantic,{hasSource:true,targetTask:f.controller.getSnapshot().targetTask}).kind,'start');
  assert.equal(f.calls.length,1,'choosing a target is presentation only');
  await f.controller.start();await f.controller.prepare();
  const body=JSON.parse(f.calls.find(call=>call.path.endsWith('/prepare')).body);assert.equal(body.target_task,'instance_segmentation');
});

test('instance target refuses saved geometry/FFT preprocessing before sending a prepare request',async()=>{
  const semantic=status({project:{name:'semantic',task:'segmentation',supported:false,linked:false,target_task_options:['instance_segmentation']},dataset:null});
  const f=fixture(async()=>semantic),controller=new f.exports.GetiPlatformController({project:{id:'project-1',name:'mask',task:'segmentation',source_dataset_dir:'/source',active_labelset_id:'labels'},inputRecipe:{version:1,operations:[{op:'brightness',factor:1.1}]},isCurrent:()=>true});
  await controller.refresh();controller.selectTargetTask('instance_segmentation');await controller.prepare();
  assert.equal(f.calls.filter(call=>call.path.endsWith('/prepare')).length,0);assert.match(controller.getSnapshot().error,/개체별.*전처리|전처리.*개체별/);controller.dispose();
});

test('public client submission correlation reconciles a child operation with a different internal id',async()=>{
  let operation,reads=0;
  const f=fixture(async(path,options)=>{if(path.endsWith('/status'))return ++reads===1?status():status({jobs:[{id:'job',operation_id:'op_internal',submission_id:operation,status:'interrupted',progress:0,worker_exited:true}]});operation=JSON.parse(options.body).operation_id;throw new Error('lost response');});
  await f.controller.refresh();await f.controller.train();assert.equal(f.controller.getSnapshot().uncertain,true);await f.controller.refresh();assert.equal(f.controller.getSnapshot().uncertain,false);
});

test('actual structured architecture license displays a readable name rather than a JavaScript object',async()=>{
  const f=fixture(async()=>status({architectures:[{id:'efficientnet',name:'EfficientNet',task:'classification',weights_ready:false,supported:true,license:{name:'Apache 2.0',url:'https://www.apache.org/licenses/LICENSE-2.0.txt'}}]}));await f.controller.refresh();
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  const html=renderToStaticMarkup(React.createElement(PlatformWorkspaceView,{state:f.controller.getSnapshot(),controller:f.controller,hasSource:true,projectName:'표면 검사',task:'classification'}));
  assert.match(html,/가중치 라이선스: Apache 2.0/);assert.doesNotMatch(html,/\[object Object\]/);
});

const storedModel = (id='model-A',variantId='variant-A') => ({id,name:id,architecture:'efficientnet',task:'classification',variants:[{id:variantId,format:'openvino',precision:'fp32',weights_size:4,evaluations:[]}]});
const admittedImage = (digest='digest') => ({image_uuid:'media-1',relative_path:'test.png',sha256:digest,source_path:'/source/test.png',file_name:'test.png',transferred:true});
const otherReceipt = (overrides={}) => ({...receipt,id:'transfer-B',revision:'revision-B',dataset_version_id:'version-B',...overrides});

test('a changed receipt retires checked review, image pagination and prediction before adoption can run',async()=>{
  let current=status({models:[storedModel()]});
  const f=fixture(async path=>path.endsWith('/status')?current:path.startsWith('/api/platform/geti/images')?{items:[admittedImage()],pagination:{offset:0,limit:100,total:200}}:path.endsWith('/predict')?{model_id:'model-A',predictions:[{label:'A'}]}:{});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.predict();f.controller.reviewAdoption(true);
  assert.equal(f.controller.getSnapshot().adoptionReview,true);assert.ok(f.controller.getSnapshot().prediction);
  current=status({dataset:otherReceipt(),models:[storedModel('model-B','variant-B')]});await f.controller.refresh();
  const state=f.controller.getSnapshot();assert.equal(state.adoptionReview,false);assert.equal(state.adopted,null);assert.deepEqual(state.images,[]);assert.equal(state.imageId,'');assert.equal(state.imagesLoaded,false);assert.equal(state.nextImageOffset,null);assert.equal(state.prediction,null);
  assert.equal(state.modelId,'model-B');assert.equal(state.variantId,'variant-B');
  await f.controller.adoptLabels();assert.equal(f.calls.filter(call=>call.path.endsWith('/adopt-labels')).length,0,'review of A never authorizes adoption of B');
});

test('same receipt id with a new revision retires adopted state and the unresolved adoption operation id',async()=>{
  let current=status(),attempt=0;const ids=[];
  const f=fixture(async(path,options)=>{if(path.endsWith('/status'))return current;if(path.endsWith('/adopt-labels')){ids.push(JSON.parse(options.body));if(++attempt===1)throw new Error('lost adoption response');return{labelset_id:'labels-B',dataset_version_id:'adopted-B'};}return{};});
  await f.controller.refresh();f.controller.reviewAdoption(true);await f.controller.adoptLabels();
  current=status({dataset:otherReceipt({id:receipt.id})});await f.controller.refresh();assert.equal(f.controller.getSnapshot().adoptionReview,false);
  f.controller.reviewAdoption(true);await f.controller.adoptLabels();assert.equal(ids.length,2);assert.notEqual(ids[0].operation_id,ids[1].operation_id);assert.equal(ids[1].expected_revision,'revision-B');
  assert.equal(f.controller.getSnapshot().adopted.labelset_id,'labels-B');
  current=status({dataset:otherReceipt({id:receipt.id,revision:'revision-C'})});await f.controller.refresh();
  let selected=0;await f.controller.useAdoptedLabels(async()=>{selected++;return true;});assert.equal(selected,0);assert.equal(f.controller.getSnapshot().adopted,null);
});

test('local prepare accepts the new receipt through the same transition and clears old model metadata',async()=>{
  const f=fixture(async path=>path.endsWith('/status')?status({models:[storedModel()]}):path.endsWith('/prepare')?otherReceipt():path.startsWith('/api/platform/geti/images')?{items:[admittedImage()],pagination:{offset:0,limit:100,total:200}}:path.endsWith('/predict')?{predictions:[{label:'old'}]}:{});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.predict();f.controller.reviewAdoption(true);await f.controller.prepare();
  const state=f.controller.getSnapshot();assert.equal(state.status.dataset.id,'transfer-B');assert.deepEqual(state.status.models,[]);assert.equal(state.modelId,'');assert.equal(state.variantId,'');assert.equal(state.adoptionReview,false);assert.deepEqual(state.images,[]);assert.equal(state.nextImageOffset,null);assert.equal(state.prediction,null);
});

for(const settles of ['resolve','reject'])test(`late receipt-A image ${settles} cannot change receipt B`,async()=>{
  let current=status(),resolveImages,rejectImages;
  const f=fixture(async path=>path.endsWith('/status')?current:new Promise((resolve,reject)=>{resolveImages=resolve;rejectImages=reject;}));
  await f.controller.refresh();const pending=f.controller.loadImages();const request=f.calls.at(-1);
  current=status({dataset:otherReceipt()});await f.controller.refresh();assert.equal(request.signal.aborted,true);
  if(settles==='resolve')resolveImages({items:[admittedImage()],pagination:{offset:0,limit:100,total:200}});else rejectImages(new Error('late A image error'));
  await pending;const state=f.controller.getSnapshot();assert.deepEqual(state.images,[]);assert.equal(state.imagesLoaded,false);assert.equal(state.nextImageOffset,null);assert.equal(state.imageError,'');assert.equal(state.imageLoading,false);
});

test('a late prediction response cannot publish after its receipt was retired',async()=>{
  let resolvePrediction;
  const f=fixture(async path=>path.endsWith('/status')?status({models:[storedModel()]}):path.startsWith('/api/platform/geti/images')?{items:[admittedImage()],pagination:{offset:0,limit:100,total:1}}:new Promise(resolve=>{resolvePrediction=resolve;}));
  await f.controller.refresh();await f.controller.loadImages();const pending=f.controller.predict();const request=f.calls.at(-1);
  // Exercise the central acceptance boundary while the inference request is in flight.
  f.controller.applyStatus(status({dataset:otherReceipt(),models:[storedModel('model-B','variant-B')]}));
  assert.equal(request.signal.aborted,true);resolvePrediction({model_id:'model-A',predictions:[{label:'old-A'}]});await pending;
  assert.equal(f.controller.getSnapshot().prediction,null);assert.equal(f.controller.getSnapshot().modelId,'model-B');assert.equal(f.controller.getSnapshot().error,'');
});

test('a late adoption response cannot populate the next receipt with the previous adopted labelset',async()=>{
  let resolveAdoption;
  const f=fixture(async path=>path.endsWith('/status')?status():new Promise(resolve=>{resolveAdoption=resolve;}));
  await f.controller.refresh();f.controller.reviewAdoption(true);const pending=f.controller.adoptLabels();
  f.controller.applyStatus(status({dataset:otherReceipt()}));resolveAdoption({labelset_id:'labels-A',dataset_version_id:'adopted-A'});await pending;
  assert.equal(f.controller.getSnapshot().adopted,null);assert.equal(f.controller.getSnapshot().adoptionReview,false);
});

test('refreshing a different model or variant clears the cached prediction while the receipt remains current',async()=>{
  let current=status({models:[storedModel()]});
  const f=fixture(async path=>path.endsWith('/status')?current:path.startsWith('/api/platform/geti/images')?{items:[admittedImage()],pagination:{offset:0,limit:100,total:1}}:{predictions:[{label:'old'}]});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.predict();
  current=status({models:[storedModel('model-B','variant-B')]});await f.controller.refresh();assert.equal(f.controller.getSnapshot().prediction,null);assert.equal(f.controller.getSnapshot().modelId,'model-B');
  await f.controller.predict();assert.ok(f.controller.getSnapshot().prediction);
  current=status({models:[storedModel('model-B','variant-C')]});await f.controller.refresh();assert.equal(f.controller.getSnapshot().variantId,'variant-C');assert.equal(f.controller.getSnapshot().prediction,null);
});

test('same immutable receipt and selection preserve a deliberate review and confirmed prediction on refresh',async()=>{
  const f=fixture(async path=>path.endsWith('/status')?status({dataset:{...receipt},models:[storedModel()]}):path.startsWith('/api/platform/geti/images')?{items:[admittedImage()],pagination:{offset:0,limit:100,total:200}}:{predictions:[{label:'current'}]});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.predict();f.controller.reviewAdoption(true);await f.controller.refresh();
  const state=f.controller.getSnapshot();assert.equal(state.adoptionReview,true);assert.equal(state.images.length,1);assert.equal(state.nextImageOffset,100);assert.ok(state.prediction);
});

test('a stale parent binding explains recovery outside closed details',async()=>{
  const message='현재 라벨셋이 달라졌습니다. 학습용 데이터를 새로 준비하세요.';
  const f=fixture(async()=>status({dataset:null,models:[],project:{name:'검사',task:'classification',supported:true,linked:false,binding_state:'stale',binding_message:message}}));await f.controller.refresh();
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  const tree=PlatformWorkspaceView({state:f.controller.getSnapshot(),controller:f.controller,hasSource:true,projectName:'검사',task:'classification'}), messages=[];
  function visit(node,ancestors=[]){if(!node||typeof node!=='object')return;if(node.props?.children===message)messages.push({node,ancestors});React.Children.forEach(node.props?.children,child=>visit(child,[...ancestors,node.type]));}visit(tree);
  assert.ok(messages.some(({node,ancestors})=>node.props.role==='status'&&!ancestors.includes('details')),'the current binding warning must stay visible without opening technical details');
});

test('model photos use admitted platform sources without an active Library revision',async()=>{
  const frozen={image_uuid:'media-1',relative_path:'test.png',file_name:'test.png',source_path:'/source/test.png',sha256:'digest',transferred:true};
  const fresh={image_uuid:'inspection',relative_path:'new.png',file_name:'new.png',source_path:'/source/new.png',sha256:'new-digest',transferred:false};
  const f=fixture(async path=>{
    if(path.endsWith('/status'))return status({models:[storedModel()]});
    if(path.startsWith('/api/dataset/library/'))throw Object.assign(new Error('No validated dataset revision is active'),{status:409});
    if(path.startsWith('/api/platform/geti/images'))return {items:[frozen,fresh,{...frozen,image_uuid:'wrong-identity'},{...frozen,image_uuid:'changed',sha256:'wrong'},{...fresh,image_uuid:'url',source_path:'https://foreign.invalid/private.png'},{...fresh,image_uuid:'foreign-root',source_path:'/other/new.png'}],pagination:{offset:0,limit:100,total:6}};
    return {predictions:[{label:'blue'}]};
  });
  await f.controller.refresh();await f.controller.loadImages();
  assert.deepEqual(f.controller.getSnapshot().images.map(row=>row.image_uuid),['media-1','inspection']);
  assert.equal(f.controller.getSnapshot().imageError,'');
  f.controller.selectImage('inspection');await f.controller.predict();
  assert.equal(JSON.parse(f.calls.find(row=>row.path.endsWith('/predict')).body).image_path,'/source/new.png');
  assert.equal(f.controller.getSnapshot().status.dataset.counts.total,8,'new inspection photos do not join the frozen training cohort');
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  const html=renderToStaticMarkup(React.createElement(PlatformWorkspaceView,{state:f.controller.getSnapshot(),controller:f.controller,hasSource:true,projectName:'검사',task:'classification'}));
  assert.match(html,/new\.png · 새 검사 사진/);assert.match(html,/test\.png · 시험 사진/);
});

test('admitted platform photo pagination appends by server offset and preserves selection',async()=>{
  const image=(id,relative)=>({image_uuid:id,relative_path:relative,file_name:relative,source_path:'/source/'+relative,sha256:'new-'+id,transferred:false});
  const f=fixture(async route=>{
    if(route.endsWith('/status'))return status({models:[storedModel()]});
    if(route.startsWith('/api/platform/geti/images'))return new URL(route,'https://fixture.test').searchParams.get('offset')==='2'
      ? {items:[image('three','three.png')],pagination:{offset:2,limit:2,total:3}}
      : {items:[image('one','one.png'),image('two','two.png')],pagination:{offset:0,limit:2,total:3}};
    throw Error('Unexpected Library lookup');
  });
  await f.controller.refresh();await f.controller.loadImages();f.controller.selectImage('two');await f.controller.loadImages(true);
  assert.deepEqual(f.controller.getSnapshot().images.map(row=>row.image_uuid),['one','two','three']);
  assert.equal(f.controller.getSnapshot().imageId,'two');assert.equal(f.controller.getSnapshot().nextImageOffset,null);
});

test('a mismatched platform image page preserves the prior selection and requests a fresh list',async()=>{
  const row={image_uuid:'media-1',relative_path:'test.png',file_name:'test.png',source_path:'/source/test.png',sha256:'digest',transferred:true};let pages=0;
  const f=fixture(async route=>route.endsWith('/status')?status({models:[storedModel()]}):{items:[row],pagination:++pages===1?{offset:0,limit:2,total:3}:{offset:0,limit:0,total:3}});
  await f.controller.refresh();await f.controller.loadImages();await f.controller.loadImages(true);
  const state=f.controller.getSnapshot();assert.equal(state.imageId,'media-1');assert.equal(state.nextImageOffset,2);assert.equal(state.images.length,1);assert.match(state.imageError,/사진 목록.*다시/);
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  const tree=PlatformWorkspaceView({state,controller:f.controller,hasSource:true,projectName:'검사',task:'classification'});let retry;
  function visit(node){if(!node||typeof node!=='object')return;if(node.type==='button'&&node.props.children==='사진 목록 다시 확인')retry=node;React.Children.forEach(node.props?.children,visit);}visit(tree);
  assert.ok(retry);retry.props.onClick();
  assert.equal(new URL(f.calls.at(-1).path,'https://fixture.test').searchParams.get('offset'),'0','the visible retry restarts listing instead of repeating an invalid old offset');
});

test('a reconnected browser shows frozen model preprocessing separately from its empty next-training draft',async()=>{
  const f=fixture(async()=>status({dataset:{...receipt,input_recipe:{version:1,operations:[{op:'resize',width:80,height:64}]}},models:[storedModel()]}));await f.controller.refresh();
  const {PlatformWorkspaceView}=load('./PlatformWorkspaceView.tsx',{'../../services/getiPlatform':f.exports});
  const html=renderToStaticMarkup(React.createElement(PlatformWorkspaceView,{state:f.controller.getSnapshot(),controller:f.controller,hasSource:true,projectName:'검사',task:'classification',preparationCount:0}));
  assert.match(html,/고정된 학습·예측 규칙/);assert.match(html,/크기 맞추기 80 × 64/);
  assert.match(html,/새 학습 기준에 사용할 편집 규칙/);assert.match(html,/현재 브라우저에 저장한 편집 규칙이 없습니다/);
  assert.doesNotMatch(html,/저장한 고정 입력 전처리가 없습니다/);
});
