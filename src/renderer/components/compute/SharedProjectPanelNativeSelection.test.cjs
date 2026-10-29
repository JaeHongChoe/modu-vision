const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const root=path.resolve(__dirname,'../..'),tick=()=>new Promise(setImmediate);
function nodes(tree){return tree&&typeof tree==='object'?[tree,...[].concat(tree.props?.children||[]).flatMap(nodes)]:[];}
function store(state){const hook=selector=>selector(state);return Object.assign(hook,{getState:()=>state,setState:update=>Object.assign(state,typeof update==='function'?update(state):update),subscribe:()=>()=>{}});}
async function fixture(run,{selectedKey='local_restored_membership',catalogChange,refuseCurrent=false,currentOriginal=false,refuseCatalog=false,preseedCatalog=false,admissionFailureAfterCurrent=false,administrator=true}={}){
 const storageDescriptor=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{value:undefined,configurable:true});
 const previous={window:global.window,fetch:global.fetch},origin='https://localhost:8443',actor='owner-id',logical='same-manifest',calls=[];let tabs=0,catalogFailure=refuseCatalog,failAdmission=false,holdCatalog=false,releaseCatalog;
 global.window={location:{origin,search:''}};
 const context={workspace_id:'restored-workspace',project_id:logical,actor_id:actor,mode:'team'};
 const project={id:logical,name:'Restored same-ID copy',task:'classification',project_dir:'/fixture/restored',dataset_dir:'/fixture/restored/dataset',models_dir:'/fixture/restored/models',source_dataset_dir:null,registration_id:'restored-registration',workspace_id:context.workspace_id,context};
 const original={...project,name:'Original same-ID project',project_dir:'/fixture/original',registration_id:'original-registration',workspace_id:'default-workspace',context:{...context,workspace_id:'default-workspace'}};
 const catalog=[{...original,permission_key:logical,project_id:logical,path:original.project_dir},{...project,permission_key:'local_restored_membership',project_id:logical,path:project.project_dir}];if(catalogChange)catalogChange(catalog[1]);
 const annotation=store({isDirty:false,setImages:async()=>true,setTask(){},setReviewerName(){}}),dataset=store({isLoading:false,isSplitting:false,isGenerating:false,folderPath:'',setFolderPath(value){this.folderPath=value;},staleDatasetKeys:[]}),flow=store({pipelineDirty:false,isRunning:false,isLoading:false,isSaving:false,selectedImage:null}),idle=store({});
 const overrides=new Map([[path.join(root,'stores/useAnnotationStore.ts'),{useAnnotationStore:annotation}],[path.join(root,'stores/useDatasetStore.ts'),{useDatasetStore:dataset}],[path.join(root,'stores/useFlowchartStore.ts'),{useFlowchartStore:flow}],[path.join(root,'stores/useTrainingStore.ts'),{useTrainingStore:idle}],[path.join(root,'stores/useInspectionRunStore.ts'),{useInspectionRunStore:idle}],[path.join(root,'stores/useModelAssistRunStore.ts'),{useModelAssistRunStore:store({activeOperations:0})}],[path.join(root,'services/datasetWorkflow.ts'),{}],[path.join(root,'stores/useComputeStore.ts'),{resetComputeTransportCache(){}}],[path.join(root,'services/websocket.ts'),{telemetryService:{disconnect(){},connect:async()=>{}}}]]),cache=new Map();
 function load(file,local={}){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const originalRequire=m.require.bind(m);m.require=ref=>{if(Object.hasOwn(local,ref))return local[ref];if(ref.startsWith('.')){for(const ext of ['.ts','.tsx']){const target=path.resolve(path.dirname(file),ref)+ext;if(overrides.has(target))return overrides.get(target);if(fs.existsSync(target))return load(target);}}return originalRequire(ref);};m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;}
 global.fetch=async(url,options={})=>{const route=new URL(url).pathname;calls.push({route,options});if(route==='/api/accounts/login')return Response.json({expires_at:Date.now()/1000+3600,csrf_token:'fixture_csrf_abcdefghijklmnopqrstuvwxyz',user:{id:actor,username:'owner',administrator}});if(route==='/api/local-host/capabilities')return Response.json({capabilities:{projects:true,pickPaths:true}});if(route==='/api/local-host/session'&&failAdmission)return Response.json({detail:'Native admission unavailable'},{status:503});if(route==='/api/local-host/session')return Response.json({tab_id:'native-tab-'+ ++tabs});if(route==='/api/local-host/session/dispose')return Response.json({disposed:true});if(route==='/api/accounts/me')return Response.json({user:{id:actor,username:'owner',administrator},browser_project_mode:'local_host',workspace_id:'default-workspace',projects:[{id:logical,path:original.project_dir,role:'owner'},{id:'local_restored_membership',path:project.project_dir,role:'owner'}],selected_project_id:selectedKey});if(route==='/api/accounts/users')return Response.json({users:[]});if(route==='/api/accounts/logout')return Response.json({ok:true});if(route==='/api/local-host/approve')return Response.json({approved:true});if(route==='/api/local-host/projects/location-recovery')return catalogFailure?Response.json({detail:'Registration catalog unavailable'},{status:500}):Response.json({registrations:catalog.map(row=>({...row,revision:1}))});if(route==='/api/local-host/projects/select')return refuseCurrent?Response.json({detail:'Project membership required'},{status:403}):Response.json(currentOriginal?original:project,{headers:{'X-Vision-Context':JSON.stringify(currentOriginal?original.context:context)}});if(route==='/api/accounts/select-project'){assert.deepEqual(JSON.parse(options.body),{project_id:selectedKey});return Response.json({selected_project_id:selectedKey});}if(route==='/api/local-host/projects/current'){if(refuseCurrent)return Response.json({detail:'Project membership required'},{status:403});if(admissionFailureAfterCurrent)failAdmission=true;return Response.json(currentOriginal?original:project,{headers:{'X-Vision-Context':JSON.stringify(currentOriginal?original.context:context)}});}if(route==='/api/project/list')return Response.json({projects:[original]});if(route==='/api/local-host/projects/list'&&holdCatalog){holdCatalog=false;return new Promise(resolve=>{releaseCatalog=()=>resolve(Response.json({projects:catalog}));});}if(route==='/api/local-host/projects/list')return catalogFailure?Response.json({detail:'Registration catalog unavailable'},{status:500}):Response.json({projects:catalog});assert.fail('Unexpected request '+route);};
 const api=load(path.join(root,'services/api.ts'));load(path.join(root,'services/browserHost.ts'));const host=load(path.join(root,'services/hostAdapter.ts')).host,realStore=load(path.join(root,'stores/useProjectStore.ts')),projectStore=realStore.useProjectStore;if(preseedCatalog)projectStore.setState({recentProjects:catalog});
 const values=[null,origin,'owner','fixture password',[],'',[],'','','','viewer','',{value:'',scope:null},false,''];let cursor=0;
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect(){},useSyncExternalStore:(_subscribe,getSnapshot)=>getSnapshot(),useState:initial=>{const index=cursor++;if(values[index]===undefined)values[index]=initial;return[values[index],value=>values[index]=typeof value==='function'?value(values[index]):value];}};
 const hook=Object.assign(selector=>selector(projectStore.getState()),{getState:projectStore.getState,setState:projectStore.setState});
 const Panel=load(path.join(root,'components/compute/SharedProjectPanel.tsx'),{react:{__esModule:true,default:react,...react},'lucide-react':{Users:'icon',LogIn:'icon',LogOut:'icon'},'../../stores/useProjectStore':{...realStore,useProjectStore:hook}}).SharedProjectPanel;
 let authenticated=0;const render=(localAuthenticationOnly=true)=>{cursor=0;return Panel({localAuthenticationOnly,onAuthenticated:()=>authenticated++});};
 try{nodes(render()).find(node=>node.type==='button'&&node.props.children.includes('연결·로그인')).props.onClick();for(let i=0;i<100&&(i===0||values[13]);i++)await tick();assert.equal(authenticated,0,'login alone never accepts a saved project');assert.equal(calls.filter(row=>row.route==='/api/accounts/select-project').length,0,'the account default is not automatically selected');const gate=nodes(render()).find(node=>typeof node.props?.onSelect==='function'&&typeof node.props?.onReconnect==='function');assert(gate,'authenticated local project gate is present');try{await gate.props.onSelect(selectedKey);}catch(error){values[14]=error.message||String(error);}await run({api,host,projectStore,flow,calls,context,project,original,values,render,failCatalog:()=>catalogFailure=true,mutateCatalog:change=>change(catalog[1]),holdCatalog:()=>holdCatalog=true,releaseCatalog:()=>{assert.ok(releaseCatalog);releaseCatalog();},activate:async()=>{nodes(render(false)).find(node=>node.props?.['aria-label']==='공동 작업 프로젝트').props.onChange({target:{value:selectedKey}});for(let i=0;i<100&&(i===0||values[13]);i++)await tick();},authenticated:()=>authenticated,disconnectGate:async()=>{await host.shared.disconnect();values[0]=null;}});}finally{global.window=previous.window;global.fetch=previous.fetch;if(storageDescriptor)Object.defineProperty(global,'localStorage',storageDescriptor);else delete global.localStorage;}
}
test('ordinary native browser login accepts the selected membership alias only in its registered restored workspace',()=>fixture(async f=>{assert.equal(f.projectStore.getState().projectError,null);assert.equal(f.projectStore.getState().project.registration_id,'restored-registration');assert.deepEqual(f.api.getProjectContext(),f.context);assert.equal(f.authenticated(),1,'a verified restored registration must complete the login gate');assert.equal(f.values[14],'');assert.equal((await f.host.shared.connection()).project_id,'local_restored_membership','account membership selection remains its own exact key');assert.equal(f.calls.filter(row=>row.route==='/api/accounts/select-project').length,1);}));

test('nonadministrator local compute accepts the registered restored membership instead of conflating its logical project ID',()=>fixture(async f=>{
 assert.equal(f.authenticated(),1);assert.equal(f.values[14],'');
 assert.equal((await f.host.shared.connection()).project_id,'local_restored_membership');
 assert.equal(f.api.getProjectContext().project_id,'same-manifest');
 assert.equal(await f.api.localBrowserComputeOnlyReady(),true);
 assert.equal(f.calls.some(row=>row.route.startsWith('/api/compute/')),false,'native readiness does not inspect administrator SSH settings');
},{administrator:false}));

for(const [name,change] of [
 ['membership key',row=>row.permission_key='foreign-membership'],
 ['registration',row=>row.registration_id='foreign-registration'],
 ['workspace',row=>row.workspace_id='foreign-workspace'],
 ['path',row=>row.path='/fixture/foreign'],
 ['actor',row=>row.context={...row.context,actor_id:'foreign-actor'}],
])test('nonadministrator local compute refuses a changed accepted native '+name,()=>fixture(async f=>{
 assert.equal(f.authenticated(),1);f.mutateCatalog(change);
 assert.equal(await f.api.localBrowserComputeOnlyReady(),false);
 assert.equal(f.projectStore.getState().project.project_dir,'/fixture/restored','discovery never changes the accepted project');
},{administrator:false}));

test('local compute does not reuse a cached native catalog when fresh discovery fails',()=>fixture(async f=>{
 assert.equal(f.authenticated(),1);f.failCatalog();
 await assert.rejects(f.api.localBrowserComputeOnlyReady(),/Registration catalog unavailable/);
},{administrator:false}));

test('a delayed native registration list cannot confirm local compute after the accepted actor changes',()=>fixture(async f=>{
 assert.equal(f.authenticated(),1);f.holdCatalog();
 const pending=f.api.localBrowserComputeOnlyReady();await tick();
 f.api.setProjectContext({...f.context,actor_id:'changed-actor'});f.releaseCatalog();
 await assert.rejects(pending,/계정|프로젝트/);
},{administrator:false}));

for(const [name,change] of [
 ['membership key',row=>row.permission_key='foreign-membership'],['registration',row=>row.registration_id='other-registration'],['workspace',row=>row.workspace_id='other-workspace'],['logical ID',row=>row.project_id='other-manifest'],['manifest ID',row=>row.id='other-manifest'],['registered path',row=>row.path='/fixture/original'],['project path',row=>row.project_dir='/fixture/original'],['context actor',row=>row.context={...row.context,actor_id:'foreign-actor'}],['context workspace',row=>row.context={...row.context,workspace_id:'default-workspace'}],['context project',row=>row.context={...row.context,project_id:'other-manifest'}],['context mode',row=>row.context={...row.context,mode:'local'}]
])test('native alias gate refuses a registration catalog mismatch in '+name,()=>fixture(async f=>{assert.equal(f.authenticated(),0);assert.match(f.values[14],/공유 프로젝트 선택/);assert.equal((await f.host.shared.connection()).project_id,'local_restored_membership');},{catalogChange:change}));
test('same manifest ID alone cannot complete the native login in the other restored registration',()=>fixture(async f=>{assert.equal(f.authenticated(),0,'the original membership key cannot accept the other same-ID restored workspace');assert.match(f.values[14],/공유 프로젝트 선택/);},{selectedKey:'same-manifest'}));
test('revoked selected membership cannot fall back to the other same-ID project',()=>fixture(async f=>{assert.equal(f.authenticated(),0);assert.match(f.values[14],/Project membership required/);assert.equal(f.api.getProjectContext(),null);assert.equal(f.projectStore.getState().project,null);assert.equal(f.calls.filter(row=>row.route==='/api/local-host/projects/select').length,1,'explicit selection is refused once');assert.equal(f.calls.filter(row=>row.route==='/api/local-host/projects/current').length,0,'a refused selection never falls back to current');},{refuseCurrent:true}));

test('native login still accepts the original exact-key registration with its own default workspace',()=>fixture(async f=>{assert.equal(f.authenticated(),1);assert.equal(f.values[14],'');assert.equal(f.projectStore.getState().project.registration_id,'original-registration');assert.deepEqual(f.api.getProjectContext(),f.original.context);},{selectedKey:'same-manifest',currentOriginal:true}));

test('failed fresh catalog discovery cannot reuse the previous session registration list to open the native gate',()=>fixture(async f=>{assert.equal(f.authenticated(),0);assert.match(f.values[14],/Registration catalog unavailable/);assert.deepEqual(f.projectStore.getState().recentProjects,[]);},{selectedKey:'same-manifest',currentOriginal:true,refuseCatalog:true,preseedCatalog:true}));

test('native activation refuses a failed fresh catalog instead of preserving prior registration readiness',()=>fixture(async f=>{assert.equal(f.authenticated(),1);f.failCatalog();await f.activate();assert.match(f.values[14],/Registration catalog unavailable/);assert.deepEqual(f.projectStore.getState().recentProjects,[]);},{selectedKey:'same-manifest',currentOriginal:true}));

test('native registration cannot use direct-ID legacy readiness after new-tab admission fails during catalog hydration',()=>fixture(async f=>{assert.equal(f.authenticated(),0);assert.match(f.values[14],/공유 프로젝트 선택/);assert.equal(f.host.localBrowser(),false);},{selectedKey:'same-manifest',currentOriginal:true,admissionFailureAfterCurrent:true}));

for(const phase of ['isLoading','isSaving']){
 test(`shared project controls wait while the current flow ${phase} and release after it settles`,()=>fixture(async f=>{
  assert.equal(f.authenticated(),1);
  const input=nodes(f.render(false)).find(node=>node.props?.['aria-label']==='공유 프로젝트 이름');
  input.props.onChange({target:{value:'Next inspection project'}});
  const controls=()=>{
   const tree=nodes(f.render(false));
   return [tree.find(node=>node.props?.['aria-label']==='공동 작업 프로젝트'),
    tree.find(node=>node.props?.['aria-label']==='공동 작업 서버 연결 해제'),
    tree.find(node=>node.type==='button'&&node.props.children.includes('만들기'))];
  };
  for(const control of controls())assert.equal(control.props.disabled,false,'the accepted idle project can change');
  f.flow.setState({[phase]:true});
  for(const control of controls())assert.equal(control.props.disabled,true,'do not offer a change that saveOpenEdits must refuse');
  f.flow.setState({[phase]:false});
  for(const control of controls())assert.equal(control.props.disabled,false,'settled flow reads release the same controls');
  const before=f.calls.filter(call=>call.route==='/api/accounts/select-project').length;
  await f.activate();
  assert.equal(f.values[14],'');
  assert.equal(f.calls.filter(call=>call.route==='/api/accounts/select-project').length,before+1);
  assert.deepEqual(f.api.getProjectContext(),f.context);
 }));
 test(`the real save guard still refuses a forced project change during flow ${phase} before any selection request`,()=>fixture(async f=>{
  f.flow.setState({[phase]:true});
  const before=f.calls.length,context=f.api.getProjectContext(),project=f.projectStore.getState().project;
  await f.activate();
  assert.match(f.values[14],/플로우를 불러오거나 저장하는 중/);
  assert.equal(f.calls.length,before,'refusal precedes every network mutation and discovery');
  assert.deepEqual(f.api.getProjectContext(),context);
  assert.equal(f.projectStore.getState().project,project);
 }));
 test(`the refresh login form remains available with stale flow ${phase} state`,()=>fixture(async f=>{
  await f.disconnectGate();
  f.flow.setState({[phase]:true});
  let tree=nodes(f.render(true));
  tree.find(node=>node.props?.['aria-label']==='공유 계정 비밀번호').props.onChange({target:{value:'fixture password'}});
  tree=nodes(f.render(true));
  assert.equal(tree.find(node=>node.type==='button'&&node.props.children.includes('연결·로그인')).props.disabled,false);
 }));
}
