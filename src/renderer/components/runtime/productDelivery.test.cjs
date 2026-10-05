const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file='deliveryContracts.ts',mocks={}){const name=path.resolve(__dirname,file);assert.ok(fs.existsSync(name),'Delivery form contracts are missing');const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));const original=mod.require.bind(mod);mod.require=ref=>mocks[ref]??original(ref);mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return mod.exports;}
test('protocol forms preserve advanced mappings on an unchanged round trip',()=>{
 const {adapterFormFromConfig,adapterConfigFromForm}=load();const config={enabled:true,modbus:{host:'localhost',port:1502,unit_id:2,result_register:42,ack_register:43,sequence_register:44,timeout:1,ack_timeout:3,verdict_values:{OK:8,NG:9,REVIEW:10}},mes:{url:'http://localhost:9000',timeout:4,token:null,field_mapping:{receipt:'job_id',decision:'model_verdict'},ack_field:'answer.ok',ack_value:'yes',ack_job_field:'answer.receipt'},clear_mes_token:false};
 assert.deepEqual(adapterConfigFromForm(adapterFormFromConfig(config),config),config);
});
test('protocol form rejects malformed numeric register before submission',()=>{
 const {adapterFormFromConfig,adapterConfigFromForm}=load();const form=adapterFormFromConfig({enabled:false,modbus:null,mes:null});form.modbusEnabled=true;form.resultRegister='wrong';assert.throws(()=>adapterConfigFromForm(form,{enabled:false,modbus:null,mes:null}),/레지스터|숫자/);
});
test('package handoff requires verified source scope and retains saved identity',()=>{
 const {packageHandoff}=load();assert.throws(()=>packageHandoff({integrity:'verified',scope_matches:false}),/소스/);
 assert.throws(()=>packageHandoff({integrity:'failed',scope_matches:true}),/무결성/);
 assert.deepEqual(packageHandoff({integrity:'verified',scope_matches:true,package_id:'id',package_path:'/package',manifest_sha256:'hash'}),{packageId:'id',packagePath:'/package',manifestSHA:'hash'});
});
test('device badges do not turn configured hardware into actual execution or approval',()=>{
 const {deviceStateLabel}=load();assert.equal(deviceStateLabel({configured:true,live_verified:false,approved:false}),'설정 가능 · 실행 미확인');assert.equal(deviceStateLabel({configured:true,live_verified:true,approved:false}),'실제 실행 확인 · 운영 승인 전');assert.equal(deviceStateLabel({configured:true,live_verified:true,approved:true}),'실행 확인 · 승인 적용');
});
test('operator viewer can read live status but cannot start, inspect or approve',()=>{
 const file=path.resolve(__dirname,'OperatorWorkspace.tsx'),react=require('react');let index=0;
 const state={project:{name:'Cell',task:'segmentation'},permissions:{can_control:false,can_inspect:false,can_review:false,can_configure:false},runtime_matches_active:true,input_health:{source_exists:true,manual_input:'available',adapters:{},configuration:{mode:'manual',folder:null,camera:null}},service:{runtime:{status:'ready',manifest_sha256:'hash'},active:{deployment_id:'active',release:{manifest_sha256:'hash'}}},results:[]};
 // Keep the real operator selection hook; this viewer remains unable to execute any action.
 const injected={0:state};
 const hookApi={api:{},getProjectContext:()=>null,getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=> 'local',subscribeProjectContext:()=>()=>{}};
 const realHook=load('useOperatorImageChoice.ts',{'../../services/api':hookApi});
 const mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(path.dirname(file));const original=mod.require.bind(mod);mod.require=ref=>ref==='./InspectionQueuePanel'?{InspectionQueuePanel:()=>null}:ref==='./operatorCopy'?load('operatorCopy.ts'):ref==='../../stores/useProjectStore'?{useProjectStore:select=>select({language:'ko'})}:ref==='./useOperatorImageChoice'?realHook:ref==='react'?{...react,useState:initial=>react.useState(Object.hasOwn(injected,index)?injected[index++]:(index++,initial))}:ref==='../../services/api'?{api:{},request:async()=>({}),getProjectContext:()=>null,getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=> 'local',subscribeProjectContext:()=>()=>{}}:ref==='./useDeliveryScope'?{useDeliveryScope:()=>({scope:{current:{}},key:'scope',projectDir:'/project',project:{id:'p',name:'Cell',task:'segmentation',source_dataset_dir:'/source'}})}:ref==='../common/ImageSearchSelect'?{ImageSearchSelect:()=>null}:original(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 const html=require('react-dom/server').renderToStaticMarkup(react.createElement(mod.exports.OperatorWorkspace));
 for(const label of ['검사 시작','검사 중지','검사 입력','입력 설정 검증·저장'])assert.match(html.match(new RegExp('<button[^>]*>'+label+'<\\/button>'))?.[0]||'',/disabled=""/,label);
});
test('protocol round trip preserves an intentionally absent sequence register',()=>{
 const {adapterFormFromConfig,adapterConfigFromForm}=load();const value={enabled:false,modbus:{host:'localhost',port:502,unit_id:1,result_register:10,ack_register:11,sequence_register:null,timeout:2,ack_timeout:5},mes:null,clear_mes_token:false};assert.deepEqual(adapterConfigFromForm(adapterFormFromConfig(value),value),value);
});
