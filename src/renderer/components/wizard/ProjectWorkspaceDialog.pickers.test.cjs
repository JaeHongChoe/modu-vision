const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(relative,mocks={}){const file=path.resolve(__dirname,relative),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;}
function nodes(v){return Array.isArray(v)?v.flatMap(nodes):v&&typeof v==='object'?[v,...nodes(v.props?.children)]:[];}
function text(v){return Array.isArray(v)?v.map(text).join(''):v&&typeof v==='object'?text(v.props?.children):String(v??'');}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
function mounted(tab,{local=false,kind='desktop',pickPaths=true}={}){
 const slots=[],effects=[],subscriptions=[],contextListeners=new Set();let cursor=0,effectCursor=0,externalCursor=0,pending=[],tree,generation=1,closed=0;const operations=[],choices=[];
 const react={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:{...props,children}}),
  useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},
  useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},
  useSyncExternalStore(subscribe,snapshot){const i=externalCursor++;if(!subscriptions[i])subscriptions[i]=subscribe(()=>render());return snapshot();},
  useEffect(fn,deps){const i=effectCursor++,old=effects[i];if(!old||!deps||deps.some((v,j)=>!Object.is(v,old.deps[j])))pending.push(()=>{old?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
 };
 const state={project:{id:'a',name:'Owned project',project_dir:'/scratch/a',task:'classification'},viewMode:'full',recentProjects:[],isProjectBusy:false,projectError:null,
  loadRecentProjects:async()=>{},clearProjectError(){},...Object.fromEntries(['createProject','openProject','backupProject','restoreProject','recoverProject'].map(name=>[name,async()=>{operations.push(name);return false;}]))};
 const store=Object.assign(()=>state,{getState:()=>state});
 const host={can:()=>pickPaths,unavailableReason:()=> 'Use a server path',kind:()=>kind,localBrowser:()=>local,selectFolder:()=>choose(),selectFile:()=>choose()};
 function choose(){const d=deferred();choices.push(d);return d.promise;}
 const Dialog=load('ProjectWorkspaceDialog.tsx',{react:{__esModule:true,default:react,...react},'lucide-react':new Proxy({},{get:()=> 'icon'}),
  '../../stores/useProjectStore':{useProjectStore:store,projectSwitchRefusal:()=>null,subscribeProjectSwitchRefusal:()=>()=>{}},
  '../../services/hostAdapter':{host,hostErrorMessage:(error,fallback)=>error instanceof Error&&error.message?error.message:fallback},
  '../../services/api':{getProjectContextGeneration:()=>generation,subscribeProjectContext:listener=>{contextListeners.add(listener);return()=>contextListeners.delete(listener);},api:{project:{}}},
  '../common/useWorkspaceDialogFocus':{useWorkspaceDialogFocus:()=>()=>{}},'./ArtifactRetentionPanel':{ArtifactRetentionPanel:'retention'},
  '../onboarding/projectPurpose':load('../onboarding/projectPurpose.ts'),
 }).ProjectWorkspaceDialog;
 const render=()=>{cursor=0;effectCursor=0;externalCursor=0;pending=[];tree=Dialog({initialTab:tab,onClose:()=>closed++});for(const fn of pending)fn();return tree;};
 render();
 return{render,choices,operations,state,closed:()=>closed,setLocal(value){local=value;render();},text:()=>text(tree),
  label(name){const label=nodes(tree).find(n=>n.type==='label'&&text(n).startsWith(name));assert.ok(label,name);return{input:()=>nodes(label).find(n=>n.type==='input'),button:()=>nodes(label).find(n=>n.type==='button')};},
  alerts:()=>nodes(tree).filter(n=>n.props?.role==='alert').map(text),
  switchTab(name){const button=nodes(tree).find(n=>n.type==='button'&&text(n)===name);assert.ok(button,name);button.props.onClick();render();},
  changeProject(){state.project={...state.project,id:'b',project_dir:'/scratch/b'};generation++;render();},
  advanceContext(){generation++;for(const listener of contextListeners)listener();},
  unmount(){for(const effect of effects)effect?.cleanup?.();for(const unsubscribe of subscriptions)unsubscribe();},
 };
}
const cases=[['create','저장 폴더'],['open','프로젝트 폴더'],['backup','백업 파일 저장 폴더'],['restore','백업 파일'],['restore','새 프로젝트 폴더'],['template','새 프로젝트 폴더 · 비우면 기본 위치']];
// Local helper grants describe the actual native selection, so arbitrary text must not replace that selection.
for(const[tab,label]of cases)test(`${tab}/${label}: localhost uses chooser-managed paths without accepting arbitrary edits`,async()=>{
 const x=mounted(tab,{local:true,kind:'browser'});assert.equal(x.label(label).input().props.value,'');assert.equal(x.label(label).input().props.readOnly,true);
 assert.ok(x.text().includes('선택 창'),'the restriction explains how to choose a path');
 x.label(label).input().props.onChange({target:{value:'/outside/arbitrary'}});x.render();assert.equal(x.label(label).input().props.value,'');
 const choice=x.label(label).button().props.onClick();x.choices[0].resolve('/scratch/granted');await choice;await new Promise(setImmediate);x.render();
 assert.equal(x.label(label).input().props.value,label==='새 프로젝트 폴더'?'/scratch/granted/restored-project':'/scratch/granted');assert.deepEqual(x.operations,[]);x.unmount();
});
// An already displayed error is also authority-owned; pending-reply fences alone cannot hide it after a switch.
for(const change of ['project','authority'])test(`displayed chooser refusal is hidden after mounted ${change} switch and current refusal still appears`,async()=>{
 const x=mounted('open');const first=x.label('프로젝트 폴더').button().props.onClick();x.choices[0].reject(Error('A chooser refusal'));await first;await new Promise(setImmediate);x.render();assert.deepEqual(x.alerts(),['A chooser refusal']);
 if(change==='project')x.changeProject();else x.advanceContext();
 assert.deepEqual(x.alerts(),[],'old feedback disappears on authority notification without another user action');
 const second=x.label('프로젝트 폴더').button().props.onClick();x.choices[1].reject(Error('Current chooser refusal'));await second;await new Promise(setImmediate);x.render();assert.deepEqual(x.alerts(),['Current chooser refusal']);x.unmount();
});
for(const kind of ['desktop','browser'])for(const[tab,label]of cases)test(`${kind}/${tab}/${label}: nonlocal paths remain editable and survive local transition`,()=>{
 const x=mounted(tab,{kind});assert.notEqual(x.label(label).input().props.readOnly,true);
 x.label(label).input().props.onChange({target:{value:'/server/existing'}});x.render();assert.equal(x.label(label).input().props.value,'/server/existing');
 x.setLocal(true);assert.equal(x.label(label).input().props.value,'/server/existing');assert.equal(x.label(label).input().props.readOnly,true);x.unmount();
});
test('missing native picker capability retains the generic typed-path fallback',()=>{
 const x=mounted('open',{local:true,kind:'browser',pickPaths:false});assert.notEqual(x.label('프로젝트 폴더').input().props.readOnly,true);x.label('프로젝트 폴더').input().props.onChange({target:{value:'/server/typed'}});x.render();assert.equal(x.label('프로젝트 폴더').input().props.value,'/server/typed');x.unmount();
});
// Removing any picker catch must reject this component action instead of showing its host refusal.
for(const[tab,label]of cases)test(`${tab}/${label}: chooser refusal is visible and preserves typed path without submitting`,async()=>{
 const x=mounted(tab);x.label(label).input().props.onChange({target:{value:'/scratch/typed'}});x.render();
 const action=x.label(label).button().props.onClick();x.choices[0].reject(Error('열린 선택창을 먼저 마치세요.'));
 await assert.doesNotReject(async()=>{await action;});await new Promise(setImmediate);x.render();
 assert.deepEqual(x.alerts(),['열린 선택창을 먼저 마치세요.']);assert.equal(x.label(label).input().props.value,'/scratch/typed');assert.deepEqual(x.operations,[]);assert.equal(x.closed(),0);x.unmount();
});
for(const[tab,label]of cases)test(`${tab}/${label}: accepted choice updates only its path; cancellation preserves it`,async()=>{
 const x=mounted(tab);const choice=x.label(label).button().props.onClick();x.choices[0].resolve('/scratch/selected/');await choice;await new Promise(setImmediate);x.render();
 const expected=label==='새 프로젝트 폴더'?'/scratch/selected/restored-project':'/scratch/selected/';
 assert.equal(x.label(label).input().props.value,expected);assert.deepEqual(x.operations,[]);
 const cancel=x.label(label).button().props.onClick();x.choices[1].resolve(null);await cancel;await new Promise(setImmediate);x.render();
 assert.equal(x.label(label).input().props.value,expected);assert.deepEqual(x.alerts(),[]);assert.deepEqual(x.operations,[]);x.unmount();
});
// Removing a lifetime/context/choice fence must allow an old native result to overwrite current UI.
for(const change of ['tab','project','context','unmount','newer'])for(const outcome of ['success','error'])test(`late chooser ${outcome} after ${change} cannot publish`,async()=>{
 const x=mounted('open');const action=x.label('프로젝트 폴더').button().props.onClick();
 if(change==='tab')x.switchTab('새 프로젝트');
 if(change==='project')x.changeProject();
 if(change==='context')x.advanceContext();
 if(change==='unmount')x.unmount();
 let newer;if(change==='newer'){newer=x.label('프로젝트 폴더').button().props.onClick();x.choices[1].resolve('/scratch/newer');await newer;await new Promise(setImmediate);}
 if(outcome==='success')x.choices[0].resolve('/scratch/stale');else x.choices[0].reject(Error('stale refusal'));
 await assert.doesNotReject(async()=>{await action;});await new Promise(setImmediate);x.render();
 assert.deepEqual(x.alerts(),[]);if(change==='tab')x.switchTab('폴더에서 열기');
 assert.equal(x.label('프로젝트 폴더').input().props.value,change==='newer'?'/scratch/newer':'');assert.deepEqual(x.operations,[]);x.unmount();
});
