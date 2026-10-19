const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function load(relative,mocks={}){
 const file=path.resolve(__dirname,relative),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;
}
function nodes(value){if(Array.isArray(value))return value.flatMap(nodes);return value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];}
// Drive the actual Header's hook lifecycle/dependency equality. Store changes
// cause renders; this harness never installs a reset subscriber that calls load.
function hooks(){
 const states=[],effects=[];let stateCursor=0,effectCursor=0,pending=[];
 const react={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:{...props,children}}),
  useState(initial){const index=stateCursor++;if(!(index in states))states[index]=initial;return[states[index],value=>{states[index]=typeof value==='function'?value(states[index]):value;}];},
  useEffect(callback,deps){const index=effectCursor++,old=effects[index],changed=!old||!deps||!old.deps||deps.length!==old.deps.length||deps.some((value,i)=>!Object.is(value,old.deps[i]));if(changed)pending.push(()=>{old?.cleanup?.();effects[index]={deps:deps?.slice(),cleanup:callback()};});},
 };
 return{react,render(component){stateCursor=0;effectCursor=0;pending=[];const tree=component();for(const effect of pending)effect();return tree;},dispose(){for(const effect of effects)effect?.cleanup?.();}};
}
const context=actor=>({workspace_id:'owned-workspace',project_id:'owned-project',actor_id:actor,mode:'team'});
const origin='http://127.0.0.1:8000';
async function scenario(run){
 const oldFetch=global.fetch,oldWindow=global.window;const timers=new Map(),cleared=[];let timerId=0;
 global.window={api:{platform:'darwin',getBackendPort:async()=>8000},setTimeout(callback,delay){const id=++timerId;timers.set(id,{callback,delay});return id;},clearTimeout(id){cleared.push(id);timers.delete(id);}};
 const api=load('../../services/api.ts'),compute=load('../../stores/useComputeStore.ts',{'../services/api':api});
 const project={id:'owned-project',project_dir:'/owned/project',name:'Owned project',task:'classification'};
 const projectState={project,projectDir:project.project_dir,activeStep:1,task:'classification',language:'en',setStep:async()=>{},setLanguage(){},backendStatus:{healthy:true,port:8000,device:'cpu'},projectName:project.name,projectError:null,isProjectBusy:false};
 const projectStore=Object.assign(selector=>selector?selector(projectState):projectState,{getState:()=>projectState});
 const computeHook=Object.assign(selector=>selector?selector(compute.useComputeStore.getState()):compute.useComputeStore.getState(),{getState:compute.useComputeStore.getState});
 const calls=[];let signedIn=false,active=context('actor-a'),profile='gpu-2',holdOld=false,releaseOld;
 const response=(body,status=200,headers)=>new Response(JSON.stringify(body),{status,headers});
 global.fetch=async(url,options)=>{const route=new URL(url).pathname,headers=new Headers(options.headers);calls.push({route,method:options.method||'GET',headers});
  if(route==='/api/project/current')return response(project,200,{'X-Vision-Context':JSON.stringify(active)});
  if(!['/api/compute/profiles','/api/compute/selection'].includes(route))throw Error('Unexpected request '+route);
  if(!signedIn)return response({detail:'Authentication required'},401);
  const ownProfile=profile;
  if(route==='/api/compute/selection')return response({compute_profile_id:ownProfile});
  if(holdOld){holdOld=false;return new Promise(resolve=>{releaseOld=()=>resolve(response({profiles:[{id:ownProfile,name:'Old GPU profile'}]}));});}
  return response({profiles:[{id:ownProfile,name:'Saved GPU2 profile'}]});
 };
 const lifecycle=hooks(),icons=Object.fromEntries(['Activity','ChevronDown','Cpu','Disc','FolderKanban','GitFork','Globe','Rocket','Scan','Server','ShieldAlert','Tag','Wand2'].map(name=>[name,'icon']));
 const Header=load('WizardHeader.tsx',{
  react:{__esModule:true,default:lifecycle.react,...lifecycle.react},'lucide-react':icons,
  '../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeHook},
  '../../stores/useTrainingStore':{useTrainingStore:()=>({jobId:null,jobComputeProfileId:null,jobComputeLabel:null})},'../../services/api':api,
  '../compute/ComputeServerPanel':{ComputeServerPanel:'ComputeServerPanel'},'./ProjectWorkspaceDialog':{ProjectWorkspaceDialog:'ProjectWorkspaceDialog'},
  '../common/ProvenancePanel':{ProvenancePanel:'ProvenancePanel'},'./TaskChangeImpactDialog':{TaskChangeImpactDialog:'TaskChangeImpactDialog',taskPreviewScope:()=>''},
 }).WizardHeader;
 const render=()=>lifecycle.render(Header),selector=tree=>nodes(tree).find(node=>node.type==='select'&&['Compute location','Run on','실행 위치'].includes(node.props['aria-label']));
 const flush=async()=>{await tick();await tick();};
 const clearForLogin=()=>{signedIn=true;api.setSharedApiBase(origin);api.setProjectContext(null);compute.resetComputeTransportCache();};
 const accept=async(actor,nextProfile)=>{active=context(actor);profile=nextProfile;const current=await api.api.project.getCurrent();api.api.project.acceptContext(current);compute.resetComputeTransportCache();};
 try{await run({api,compute,calls,projectState,timers,cleared,render,selector,flush,clearForLogin,accept,hold:()=>{holdOld=true;},release:()=>{assert.ok(releaseOld,'old profile request was held');releaseOld();releaseOld=undefined;}});}
 finally{releaseOld?.();lifecycle.dispose();await tick();global.fetch=oldFetch;global.window=oldWindow;}
}

test('actual mounted Header refreshes saved compute selection after shared login while daemon health and port stay unchanged',async()=>scenario(async x=>{
 const stableLoad=x.compute.useComputeStore.getState().load,healthy=x.projectState.backendStatus.healthy,port=x.projectState.backendStatus.port;
 let tree=x.render();await x.flush();assert.equal(x.calls.filter(r=>r.route.startsWith('/api/compute/')).length,2);
 assert.equal(x.compute.useComputeStore.getState().isLoaded,false);assert.match(x.compute.useComputeStore.getState().loadError,/Authentication required/);
 assert.deepEqual([...x.timers.values()].map(row=>row.delay),[500],'original first retry remains500ms; no budget increase');
 const oldRetry=[...x.timers.keys()][0],beforeLogin=x.calls.length;
 x.clearForLogin();tree=x.render();await x.flush();
 assert.equal(x.calls.length,beforeLogin,'immediate reset with no accepted shared context must not dispatch discovery');
 assert.equal(x.compute.useComputeStore.getState().isLoaded,false);assert.equal(x.selector(tree).props.disabled,true);
 assert.ok(x.cleared.includes(oldRetry),'transport reset must clean up the old401 retry lifecycle');assert.equal(x.timers.size,0);
 await x.accept('actor-a','gpu-2');tree=x.render();await x.flush();tree=x.render();
 assert.equal(x.compute.useComputeStore.getState().load,stableLoad);assert.equal(x.projectState.backendStatus.healthy,healthy);assert.equal(x.projectState.backendStatus.port,port);
 const own=x.calls.slice(beforeLogin).filter(r=>r.route.startsWith('/api/compute/'));
 assert.equal(own.length,2,'accepted-project reset must cause the actual Header effect to fetch both profiles and selection');
 for(const row of own){assert.equal(row.method,'GET');assert.deepEqual(JSON.parse(row.headers.get('X-Vision-Context')),context('actor-a'));}
 assert.equal(x.compute.useComputeStore.getState().isLoaded,true);assert.equal(x.compute.useComputeStore.getState().loadError,null);
 assert.equal(x.selector(tree).props.disabled,false);assert.equal(x.selector(tree).props.value,'gpu-2');
 assert.ok(nodes(x.selector(tree)).some(n=>n.type==='option'&&n.props.value==='gpu-2'&&n.props.children.includes('Saved GPU2 profile')));
 assert.equal(x.calls.some(r=>r.method!=='GET'),false,'discovery never selects, saves, probes or launches a target');
}));

test('actual Header follows same-project new-actor reset and late old discovery cannot replace the accepted target',async()=>scenario(async x=>{
 x.clearForLogin();await x.accept('actor-a','gpu-old');x.hold();x.render();await x.flush();
 assert.equal(x.compute.useComputeStore.getState().isLoading,true);const before=x.calls.length;
 x.clearForLogin();x.render();await x.flush();assert.equal(x.calls.length,before,'no-context transition cannot reuse old authority');
 await x.accept('actor-b','gpu-new');x.render();await x.flush();let tree=x.render();
 assert.equal(x.selector(tree).props.value,'gpu-new');assert.equal(x.selector(tree).props.disabled,false);
 const own=x.calls.slice(before).filter(r=>r.route.startsWith('/api/compute/'));assert.equal(own.length,2);
 for(const row of own)assert.deepEqual(JSON.parse(row.headers.get('X-Vision-Context')),context('actor-b'));
 x.release();await x.flush();tree=x.render();
 assert.deepEqual(x.compute.useComputeStore.getState().profiles.map(p=>p.id),['gpu-new']);assert.equal(x.selector(tree).props.value,'gpu-new');
 assert.equal(x.compute.useComputeStore.getState().isLoaded,true);assert.equal(x.compute.useComputeStore.getState().loadError,null);assert.equal(x.timers.size,0);
}));
