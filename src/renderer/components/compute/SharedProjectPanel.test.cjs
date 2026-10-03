const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');const ts=require('typescript');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function hostAdapterModule(){const file=path.join(__dirname,'..','..','services','hostAdapter.ts');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function load(filename,mocks){const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:name==='../../services/hostAdapter'?hostAdapterModule():original(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,filename);return m.exports;}
function nodes(tree){if(!tree||typeof tree!=='object')return [];return [tree,...[].concat(tree.props?.children||[]).flatMap(nodes)];}
for(const operation of ['activate','login','disconnect','login_inventory_failure'])test(`${operation} switches the transport before remount loads and discards late old-server profiles`,async()=>{
  const isLogin=operation.startsWith('login');
  let server='old';const pending=[];let commit;
  const api={compute:{listProfiles:()=>new Promise(resolve=>pending.push({server,resolve})),getSelection:async()=>({compute_profile_id:`${server}-gpu`})}};
  const compute=load(path.resolve(__dirname,'../../stores/useComputeStore.ts'),{'../services/api':{api}});
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
    '../../services/api':{api,request,setSharedApiBase:()=>{},getProjectContext:()=>({workspace_id:'fixture',project_id:project.id,actor_id:'user',mode:'team'}),getApiPersistenceIdentity:()=>`shared:https://${server}.example`},
    '../../stores/useProjectStore':{useProjectStore:projectStore,saveOpenEdits:async()=>{}},
    '../../stores/useComputeStore':compute,
    '../../stores/useDatasetStore':{useDatasetStore:{getState:()=>({setFolderPath:()=>{}})}},
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
