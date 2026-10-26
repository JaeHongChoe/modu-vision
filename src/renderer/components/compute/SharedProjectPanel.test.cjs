const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');const ts=require('typescript');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function hostAdapterModule(name='hostAdapter.ts'){const file=path.join(__dirname,'..','..','services',name);const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=key=>key==='./browserSession'?hostAdapterModule('browserSession.ts'):original(key);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function load(filename,mocks){const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:name==='../../services/hostAdapter'?hostAdapterModule():original(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,filename);return m.exports;}
function nodes(tree){if(!tree||typeof tree!=='object')return [];return [tree,...[].concat(tree.props?.children||[]).flatMap(nodes)];}
function datasetHook(state={isLoading:false,isSplitting:false,setFolderPath(){}}){return Object.assign(selector=>selector(state),{getState:()=>state});}
for(const operation of ['activate','login','disconnect','login_inventory_failure'])test(`${operation} switches the transport before remount loads and discards late old-server profiles`,async()=>{
  const isLogin=operation.startsWith('login');
  let server='old';const pending=[];let commit;
  const api={compute:{listProfiles:()=>new Promise(resolve=>pending.push({server,resolve})),getSelection:async()=>({compute_profile_id:`${server}-gpu`})}};
  // This transport-only fixture already supplies accepted team context to the panel.
  // Null-context automatic loading is exercised through the real API in computeContextReadiness.
  const compute=load(path.resolve(__dirname,'../../stores/useComputeStore.ts'),{'../services/api':{api,getApiPersistenceIdentity:()=>`shared:https://${server}.example`,getProjectContext:()=>({workspace_id:'fixture',project_id:`${server}-project`,actor_id:'user',mode:'team'})}});
  const store=compute.useComputeStore;
  const oldLoad=store.getState().load();
  const unsubscribe=store.subscribe((state,previous)=>{if(state.transportRevision!==previous.transportRevision)void state.load();});
  const connected={server_url:'https://old.example',project_id:'old-project',user:{id:'user',username:'tester',administrator:false},expires_at:1};
  const values=[isLogin?null:connected,'https://new.example','tester','private-test-password',[],isLogin?'':'old-project',[], '', '', '', 'viewer', '', {value:'',scope:null}, false, ''];let cursor=0;
  const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:()=>{},useState:initial=>{const index=cursor++;if(values[index]===undefined)values[index]=initial;return [values[index],value=>{values[index]=typeof value==='function'?value(values[index]):value;}];}};
  const switchCommit=()=>new Promise(resolve=>{commit=()=>{server='new';resolve({...connected,server_url:'https://new.example',project_id:'new-project'});};});
  const project={source_dataset_dir:'/fixture',id:'new-project'};
  const projectState={project,isProjectBusy:false,syncCurrentProject:async()=>{}};
  const projectStore=Object.assign(selector=>selector(projectState),{getState:()=>projectState,setState:()=>{}});
  const request=async endpoint=>{if(endpoint==='/api/accounts/me'&&operation==='login_inventory_failure')throw new Error('New server inventory is unavailable');return endpoint==='/api/accounts/me'?{projects:[{id:'new-project',path:'/fixture',role:'trainer'}],selected_project_id:'new-project'}:{};};
  global.window={api:{loginSharedServer:switchCommit,selectSharedProject:isLogin?async()=>({...connected,server_url:'https://new.example',project_id:'new-project'}):switchCommit,disconnectSharedServer:switchCommit}};
  const panel=load(path.resolve(__dirname,'SharedProjectPanel.tsx'),{
    react:{__esModule:true,default:react,...react},
    'lucide-react':{Users:'icon',LogIn:'icon',LogOut:'icon'},
    '../../services/api':{api,request,setSharedApiBase:()=>{},setProjectContext:()=>{},getProjectContext:()=>({workspace_id:'fixture',project_id:project.id,actor_id:'user',mode:'team'}),getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=>`shared:https://${server}.example`},
    '../../stores/useProjectStore':{useProjectStore:projectStore,saveOpenEdits:async()=>{}},
    '../../stores/useComputeStore':compute,
    '../../stores/useDatasetStore':{useDatasetStore:datasetHook()},
    '../../stores/useAnnotationStore':{useAnnotationStore:{getState:()=>({setReviewerName:()=>{},setImages:async()=>{}})}},
    '../../services/websocket':{telemetryService:{disconnect:()=>{},connect:async()=>{}}},
  }).SharedProjectPanel;
  const tree=panel();const list=nodes(tree);
  if(operation==='activate')list.find(n=>n.props?.['aria-label']==='공동 작업 프로젝트').props.onChange({target:{value:'new-project'}});
  else if(operation==='disconnect')list.find(n=>n.props?.['aria-label']==='공동 작업 서버 연결 해제').props.onClick();
  else list.find(n=>n.type==='button'&&n.props.children.includes('연결·로그인')).props.onClick();
  await tick();
  assert.equal(pending.length,1,'main remount must not issue a new compute request to the old transport before switch commits');
  commit();await tick();await tick();
  assert.equal(pending.length,operation==='login'?3:2);assert.ok(pending.slice(1).every(row=>row.server==='new'));
  pending.at(-1).resolve({profiles:[{id:'new-gpu',name:'new backend'}]});await tick();
  for(const row of pending.slice(0,-1))row.resolve({profiles:[{id:'stale-gpu',name:'old transport or prior shared project'}]});await oldLoad;await tick();
  assert.deepEqual(store.getState().profiles.map(row=>row.id),['new-gpu']);
  assert.equal(store.getState().selectedProfileId,'new-gpu');assert.equal(store.getState().isLoaded,true);
  if(operation==='login_inventory_failure')assert.match(values[14],/inventory is unavailable/);
  unsubscribe();delete global.window;
});

for(const flag of ['isLoading','isSplitting'])for(const loggedIn of [false,true])test(`${loggedIn?'project and disconnect':'login'} controls wait for ${flag} to settle`,()=>{
  const connection={server_url:'https://owned.example',project_id:'p',user:{id:'owner',username:'owner',administrator:false},expires_at:2000000000};
  const project={project:{id:'p',project_dir:'/owned/p',source_dataset_dir:'/owned/source'},isProjectBusy:false};
  const dataset={isLoading:false,isSplitting:false,setFolderPath(){}};
  const values=[loggedIn?connection:null,connection.server_url,'owner','controlled-password',[],'p',[],'','','','viewer','',{value:'',scope:null},false,''];let cursor=0;
  const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:()=>{},useState:initial=>{
    const index=cursor++;if(values[index]===undefined)values[index]=initial;return[values[index],value=>{values[index]=value;}];
  }};
  const Panel=load(path.resolve(__dirname,'SharedProjectPanel.tsx'),{
    react:{__esModule:true,default:react,...react},'lucide-react':{Users:'icon',LogIn:'icon',LogOut:'icon'},
    '../../services/api':{api:{},request:async()=>{},setSharedApiBase(){},setProjectContext(){},getApiPersistenceIdentity:()=> 'shared:https://owned.example',getProjectContextGeneration:()=>0,getProjectContext:()=>({workspace_id:'w',project_id:'p',actor_id:'owner',mode:'team'})},
    '../../stores/useProjectStore':{useProjectStore:Object.assign(selector=>selector(project),{getState:()=>project}),saveOpenEdits:async()=>{}},
    '../../stores/useComputeStore':{resetComputeTransportCache(){}},
    '../../stores/useDatasetStore':{useDatasetStore:datasetHook(dataset)},
    '../../stores/useAnnotationStore':{useAnnotationStore:{getState:()=>({})}},
    '../../services/websocket':{telemetryService:{}},'../../services/hostAdapter':{host:{kind:()=> 'desktop',shared:{}}},
  }).SharedProjectPanel;
  const controls=()=>{cursor=0;const all=nodes(Panel());return loggedIn
    ? [all.find(node=>node.props?.['aria-label']==='공동 작업 프로젝트'),all.find(node=>node.props?.['aria-label']==='공동 작업 서버 연결 해제')]
    : [all.find(node=>node.type==='button'&&node.props.children.includes('연결·로그인'))];};
  assert.ok(controls().every(control=>control.props.disabled===false),'idle controls are available');
  dataset[flag]=true;
  assert.ok(controls().every(control=>control.props.disabled===true),'owned data work must settle before offering a session change');
  dataset[flag]=false;
  assert.ok(controls().every(control=>control.props.disabled===false),'the existing controls become the observable wait after restoration');
});

// Exercise the real context subscription and telemetry client through the real
// login handler. Only the external socket and server-discovery boundaries are
// controlled; no socket lifecycle method or context notification is replaced.
for(const outcome of ['success','auth_failure','inventory_failure','select_failure','sync_failure','no_project'])test(`shared login ${outcome} keeps telemetry owned by the completed login`,async()=>{
  const apiModule=load(path.resolve(__dirname,'../../services/api.ts'),{});
  const previousSocket=global.WebSocket;const sockets=[];
  class FakeSocket{
    static OPEN=1;static CONNECTING=0;
    constructor(url){this.url=url;this.readyState=FakeSocket.CONNECTING;this.closeCount=0;sockets.push(this);}
    send(){}
    close(){this.closeCount+=1;this.readyState=3;this.onclose?.();}
    open(){this.readyState=FakeSocket.OPEN;this.onopen();}
  }
  global.WebSocket=FakeSocket;
  const {telemetryService}=load(path.resolve(__dirname,'../../services/websocket.ts'),{'./api':apiModule});
  const oldContext={workspace_id:'old-workspace',project_id:'old-project',actor_id:'old-user',mode:'local'};
  const newContext={workspace_id:'new-workspace',project_id:'new-project',actor_id:'new-user',mode:'team'};
  const connection={server_url:'https://new.example',user:{id:'new-user',username:'tester',administrator:false},expires_at:2000000000};
  let resolveLogin,rejectLogin;
  const loginResult=new Promise((resolve,reject)=>{resolveLogin=resolve;rejectLogin=reject;});
  let complete;
  const completed=new Promise(resolve=>{complete=resolve;});
  const values=[null,connection.server_url,'tester','private-test-password',[],'',[],'','','','viewer','',{value:'',scope:null},false,''];let cursor=0;
  const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:()=>{},useState:initial=>{
    const index=cursor++;if(values[index]===undefined)values[index]=initial;
    return [values[index],value=>{values[index]=typeof value==='function'?value(values[index]):value;if(index===13&&values[index]===false)complete();}];
  }};
  const projectState={project:{id:newContext.project_id,project_dir:'/owned/new-project'},isProjectBusy:false,syncCurrentProject:async()=>{
    apiModule.setProjectContext(newContext);
    if(outcome==='sync_failure')throw new Error('Project synchronization refused after context discovery');
  }};
  const projectStore=Object.assign(selector=>selector(projectState),{getState:()=>projectState,setState:value=>Object.assign(projectState,value)});
  const request=async endpoint=>{
    if(endpoint==='/api/accounts/me'){
      if(outcome==='inventory_failure')throw new Error('Account inventory refused');
      return {projects:outcome==='no_project'?[]:[{id:newContext.project_id,path:'/owned/new-project',role:'trainer'}],selected_project_id:outcome==='no_project'?null:newContext.project_id};
    }
    if(endpoint==='/api/accounts/select-project'&&outcome==='select_failure')throw new Error('Project selection refused');
    return {};
  };
  const heard=[];
  const unsubscribe=telemetryService.subscribe((event,data)=>heard.push({event,data}));
  try{
    apiModule.setCachedPort(56001);apiModule.setProjectContext(oldContext);
    await telemetryService.connect();sockets[0].open();
    const oldSocket=sockets[0];
    const panel=load(path.resolve(__dirname,'SharedProjectPanel.tsx'),{
      react:{__esModule:true,default:react,...react},'lucide-react':{Users:'icon',LogIn:'icon',LogOut:'icon'},
      '../../services/api':{...apiModule,request},
      '../../stores/useProjectStore':{useProjectStore:projectStore,saveOpenEdits:async()=>{}},
      '../../stores/useComputeStore':{resetComputeTransportCache:()=>{}},
      '../../stores/useDatasetStore':{useDatasetStore:datasetHook()},
      '../../stores/useAnnotationStore':{useAnnotationStore:{getState:()=>({setReviewerName:()=>{},setImages:async()=>{}})}},
      '../../services/websocket':{telemetryService},
      '../../services/hostAdapter':{host:{kind:()=>'desktop',shared:{login:()=>loginResult,select:async()=>({...connection,project_id:newContext.project_id})}}},
    }).SharedProjectPanel;
    nodes(panel()).find(n=>n.type==='button'&&n.props.children.includes('연결·로그인')).props.onClick();
    await tick();
    assert.equal(oldSocket.readyState,FakeSocket.OPEN,'authentication has not succeeded: the prior stream remains available');
    assert.equal(oldSocket.closeCount,0);
    if(outcome==='auth_failure')rejectLogin(new Error('Authentication refused'));
    else resolveLogin(connection);
    await completed;
    if(outcome==='auth_failure'){
      assert.equal(sockets.length,1);assert.equal(oldSocket.closeCount,0);assert.equal(telemetryService.getStatus(),true);
      assert.deepEqual(apiModule.getProjectContext(),oldContext);assert.match(values[14],/Authentication refused/);
    }else if(outcome==='success'){
      const current=sockets.filter(socket=>new URL(socket.url).searchParams.get('project_context')===JSON.stringify(newContext));
      assert.equal(current.length,1,'one fresh accepted-context stream is opened, without closing and reopening it');
      assert.equal(oldSocket.closeCount,1);assert.equal(current[0].closeCount,0);
      current[0].open();
      assert.equal(telemetryService.getStatus(),true);
      current[0].onmessage({data:JSON.stringify({event:'current-project-event',project_context:newContext,data:{sequence:1}})});
      oldSocket.onmessage({data:JSON.stringify({event:'stale-project-event',project_context:oldContext,data:{sequence:2}})});
      assert.deepEqual(heard.filter(row=>row.event.endsWith('project-event')),[{event:'current-project-event',data:{sequence:1}}]);
      assert.equal(values[14],'');
    }else{
      assert.equal(oldSocket.closeCount,1);assert.equal(sockets.length,1,'incomplete selection never enables a newly discovered project stream');
      assert.equal(telemetryService.getStatus(),false);
      if(outcome==='no_project')assert.equal(values[14],'');
      else assert.match(values[14],/refused/);
    }
  }finally{unsubscribe();telemetryService.disconnect();global.WebSocket=previousSocket;}
});

// Login, project switch and logout remount the step views (resetComputeTransportCache), login once more after it has
// accepted the project. A ZIP upload started before a remount is cancelled with its dialog, so the data step holds new
// uploads while sharedSessionChanging is set: the flag has to cover every remount and the project acceptance that
// re-enables the data step, and has to clear however the change ends.
for(const operation of ['login','activate','disconnect','login_sync_failure'])test(`${operation} holds data-step uploads through every step-view remount and releases them when it ends`,async()=>{
  const isLogin=operation.startsWith('login');
  const connection={server_url:'https://new.example',user:{id:'user',username:'tester',administrator:false},expires_at:2000000000};
  const context={workspace_id:'workspace',project_id:'new-project',actor_id:'user',mode:'team'};
  const seen=[];let complete;const completed=new Promise(resolve=>{complete=resolve;});
  const projectState={project:{id:'new-project',project_dir:'/owned/new-project',source_dataset_dir:'/owned/source'},isProjectBusy:false,sharedSessionChanging:false,syncCurrentProject:async()=>{
    seen.push(['project accepted',projectState.sharedSessionChanging]);
    if(operation==='login_sync_failure')throw new Error('Project synchronization refused');
  }};
  const projectStore=Object.assign(selector=>selector(projectState),{getState:()=>projectState,setState:value=>Object.assign(projectState,value)});
  const values=[isLogin?null:{...connection,project_id:'old-project'},connection.server_url,'tester','private-test-password',[],isLogin?'':'old-project',[],'','','','viewer','',{value:'',scope:null},false,''];let cursor=0;
  const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:()=>{},useState:initial=>{
    const index=cursor++;if(values[index]===undefined)values[index]=initial;
    return [values[index],value=>{values[index]=typeof value==='function'?value(values[index]):value;if(index===13&&values[index]===false)complete();}];
  }};
  const request=async endpoint=>endpoint==='/api/accounts/me'?{projects:[{id:'new-project',path:'/owned/new-project',role:'trainer'}],selected_project_id:'new-project'}:{};
  const panel=load(path.resolve(__dirname,'SharedProjectPanel.tsx'),{
    react:{__esModule:true,default:react,...react},'lucide-react':{Users:'icon',LogIn:'icon',LogOut:'icon'},
    '../../services/api':{api:{},request,setSharedApiBase:()=>{},setProjectContext:()=>{},getProjectContext:()=>context,getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=>'shared:https://new.example'},
    '../../stores/useProjectStore':{useProjectStore:projectStore,saveOpenEdits:async()=>{}},
    '../../stores/useComputeStore':{resetComputeTransportCache:()=>seen.push(['step views remounted',projectState.sharedSessionChanging])},
    '../../stores/useDatasetStore':{useDatasetStore:datasetHook()},
    '../../stores/useAnnotationStore':{useAnnotationStore:{getState:()=>({setReviewerName:()=>{},setImages:async()=>{}})}},
    '../../services/websocket':{telemetryService:{disconnect:()=>{},connect:async()=>{seen.push(['telemetry connected',projectState.sharedSessionChanging]);}}},
    '../../services/hostAdapter':{host:{kind:()=>'desktop',shared:{login:async()=>connection,select:async id=>({...connection,project_id:id}),disconnect:async()=>{}}}},
  }).SharedProjectPanel;
  const list=nodes(panel());
  if(operation==='activate')list.find(n=>n.props?.['aria-label']==='공동 작업 프로젝트').props.onChange({target:{value:'new-project'}});
  else if(operation==='disconnect')list.find(n=>n.props?.['aria-label']==='공동 작업 서버 연결 해제').props.onClick();
  else list.find(n=>n.type==='button'&&n.props.children.includes('연결·로그인')).props.onClick();
  assert.equal(projectState.sharedSessionChanging,true,'held from the click, before the first server request');
  await completed;
  assert.equal(projectState.sharedSessionChanging,false,'released once the change has ended');
  assert.deepEqual(seen.filter(([,held])=>!held),[],'no remount, project acceptance or stream reconnect happens while uploads are allowed');
  const remounts=seen.filter(([event])=>event==='step views remounted').length;
  assert.equal(remounts,operation==='login'?2:1);
  assert.ok(seen.some(([event])=>event==='project accepted'));
  if(operation==='login_sync_failure')assert.match(values[14],/synchronization refused/);
  else{assert.equal(values[14],'');assert.equal(seen.at(-1)[0],'telemetry connected');}
});
