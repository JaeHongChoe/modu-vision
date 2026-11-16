const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'TrainingDataSplitBlock.tsx');
const nodes=v=>Array.isArray(v)?v.flatMap(nodes):v&&typeof v==='object'?[v,...nodes(v.props?.children)]:[];
const text=v=>Array.isArray(v)?v.map(text).join(''):v&&typeof v==='object'?text(v.props?.children):String(v??'');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
function fixture({state='saved',supported=true,task='detection'}={}){
 assert.ok(fs.existsSync(file),'missing training split block');
 const slots=[],effects=[],subscriptions=[];let cursor=0,effectCursor=0,externalCursor=0,pending=[],tree,generation=1,identity='local',authority='source-a',props={task,disabled:false},refusal=null;
 const listeners=new Set(),calls=[],held=[];
 const project={project:{id:'project-a',project_dir:'/scratch/a',source_dataset_dir:'/scratch/images',task,active_labelset_id:'default'},projectDir:'/scratch/a',task,isProjectBusy:false,sharedSessionChanging:false,setStep:async step=>calls.push(['step',step])};
 const training={isTraining:false,isRecoveringTraining:false};
 const dataset={folderPath:'/scratch/images',datasetKey:'/scratch/images\0'+task,sourceAuthority:authority,hasSelectedFolder:true,totalImages:100,sourceImages:100,importError:null,sourceSaveError:null,splitSupported:supported,splitUnavailableReason:supported===false?'이 형식은 원본 폴더의 분할을 사용합니다.':null,splitState:state,split:{train:70,val:20,test:10},trainRatio:.7,isLoading:false,isGenerating:false,isSplitting:false,splitError:null,
  applySplit(train,val,testing){calls.push(['split',train,val,testing]);const d=deferred();held.push(d);dataset.isSplitting=true;return d.promise;}};
 const store=value=>Object.assign(selector=>selector?selector(value):value,{getState:()=>value});
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),
  useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>slots[i]=typeof value==='function'?value(slots[i]):value];},
  useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},
  useEffect(fn,deps){const i=effectCursor++,old=effects[i];if(!old||!deps||deps.some((v,j)=>!Object.is(v,old.deps[j])))pending.push(()=>{old?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
  useSyncExternalStore(subscribe,snapshot){const i=externalCursor++;if(!subscriptions[i])subscriptions[i]=subscribe(()=>render());return snapshot();},
 };
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const mocks={react:{__esModule:true,default:react,...react},'../../stores/useDatasetStore':{useDatasetStore:store(dataset),readDatasetAuthority:()=>authority},'../../stores/useProjectStore':{useProjectStore:store(project),projectSwitchRefusal:()=>refusal,subscribeProjectSwitchRefusal:()=>()=>{}},'../../stores/useTrainingStore':{useTrainingStore:store(training)},'../../services/api':{getApiPersistenceIdentity:()=>identity,getProjectContextGeneration:()=>generation,getProjectContext:()=>({project_id:project.project.id,actor_id:'actor-a',workspace_id:'workspace-a',mode:'team'}),subscribeProjectContext:listener=>{listeners.add(listener);return()=>listeners.delete(listener);}}};
 m.require=name=>name in mocks?mocks[name]:name==='../../utils/datasetSplitCapability'?loadCapability():original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);
 function render(){cursor=0;effectCursor=0;externalCursor=0;pending=[];tree=m.exports.TrainingDataSplitBlock(props);for(const fn of pending)fn();return tree;}
 render();
 return{render,calls,held,dataset,project,training,props,
  buttons:()=>nodes(tree).filter(n=>n.type==='button'),
  button(label){const b=nodes(tree).find(n=>n.type==='button'&&text(n)===label);assert.ok(b,'button '+label);return b;},
  select(){const s=nodes(tree).find(n=>n.type==='select');assert.ok(s,'ratio selector');return s;},
  alerts:()=>nodes(tree).filter(n=>n.props?.role==='alert').map(text),text:()=>text(tree),
  refreshAuthority(){generation++;authority='source-b';dataset.sourceAuthority=authority;for(const fn of listeners)fn();},
  changeSource(){project.project={...project.project,source_dataset_dir:'/scratch/new'};dataset.folderPath='/scratch/new';dataset.datasetKey='/scratch/new\0'+task;generation++;render();},
  setRefusal(value){refusal=value;render();},
  complete(index=0,ratio=.7){dataset.isSplitting=false;dataset.splitState='saved';dataset.trainRatio=ratio;dataset.split={train:ratio===.8?80:70,val:ratio===.8?10:20,test:10};held[index].resolve();},
  unmount(){for(const effect of effects)effect?.cleanup?.();for(const stop of subscriptions)stop();},
 };
}
function loadCapability(){const target=path.resolve(__dirname,'../../utils/datasetSplitCapability.ts'),m=new Module(target,module);m._compile(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,target);return m.exports;}
const tick=()=>new Promise(setImmediate);
test('saved and source-folder partitions show actual counts and require an explicit ratio-edit action',()=>{
 for(const state of ['saved','source_folders']){const x=fixture({state});assert.match(x.text(),/70.*20.*10/);if(state==='source_folders')assert.match(x.text(),/원본.*폴더/);assert(!x.buttons().some(b=>text(b)==='데이터 나누기 저장'));x.button('분할 비율 바꾸기').props.onClick();x.render();assert.ok(x.select());assert.ok(x.button('데이터 나누기 저장'));assert.deepEqual(x.calls,[]);x.unmount();}
});
test('an explicit preset saves through the canonical store once and confirms its actual published split',async()=>{
 const x=fixture({state:'automatic'});x.select().props.onChange({target:{value:'80'}});x.render();const save=x.button('데이터 나누기 저장').props.onClick;const pending=save();await save();assert.deepEqual(x.calls,[['split',.8,.1,.1]]);x.complete(0,.8);await pending;x.render();assert.match(x.text(),/80.*10.*10/);assert.match(x.text(),/저장.*확인/);assert.deepEqual(x.alerts(),[]);assert(!x.buttons().some(b=>text(b)==='데이터 나누기 저장'));x.unmount();
});
test('a skipped canonical operation is an alert rather than invented save success',async()=>{
 const x=fixture({state:'automatic'});x.dataset.applySplit=async()=>{};await x.button('데이터 나누기 저장').props.onClick();x.render();assert.equal(x.alerts().length,1);assert.doesNotMatch(x.text(),/저장된 분할을 확인했습니다/);x.unmount();
});
for(const flag of ['isLoading','isSplitting','isGenerating','importError','sourceSaveError'])test('live dataset '+flag+' refuses dispatch even from a retained click',async()=>{
 const x=fixture({state:'automatic'});const save=x.button('데이터 나누기 저장').props.onClick;x.dataset[flag]=flag.endsWith('Error')?'current error':true;await save();assert.deepEqual(x.calls,[]);x.unmount();
});
for(const flag of ['isProjectBusy','sharedSessionChanging'])test('live project '+flag+' refuses dispatch',async()=>{
 const x=fixture({state:'automatic'});const save=x.button('데이터 나누기 저장').props.onClick;x.project[flag]=true;await save();assert.deepEqual(x.calls,[]);x.unmount();
});
for(const flag of ['isTraining','isRecoveringTraining'])test('live training '+flag+' refuses dispatch',async()=>{
 const x=fixture({state:'automatic'});const save=x.button('데이터 나누기 저장').props.onClick;x.training[flag]=true;await save();assert.deepEqual(x.calls,[]);x.unmount();
});
test('canonical workflow refusal and disabled parent prevent split writes',async()=>{
 const x=fixture({state:'automatic'});const old=x.button('데이터 나누기 저장').props.onClick;x.setRefusal('검사가 진행 중입니다.');await old;assert.deepEqual(x.calls,[]);x.setRefusal(null);x.props.disabled=true;x.render();await old;assert.deepEqual(x.calls,[]);x.unmount();
});
test('unsupported split shows the exact backend reason and opens the data step without applying',async()=>{
 const x=fixture({supported:false,state:'source_folders'});assert.ok(x.text().includes('이 형식은 원본 폴더의 분할을 사용합니다.'));assert(!x.buttons().some(b=>text(b)==='데이터 나누기 저장'));await x.button('사진 준비 열기').props.onClick();assert.deepEqual(x.calls,[['step',1]]);x.unmount();
});
test('the canonical legacy capability fallback permits classification but refuses unconfirmed detection',()=>{
 const classification=fixture({task:'classification',supported:null,state:'automatic'});assert.ok(classification.select());classification.unmount();
 const detection=fixture({task:'detection',supported:null,state:'automatic'});assert(!detection.buttons().some(b=>text(b)==='데이터 나누기 저장'));detection.unmount();
});
for(const mutation of ['authority','source','datasetKey','task'])test('scope '+mutation+' discards an unsaved ratio and rejects an old click',async()=>{
 const x=fixture({state:'automatic'});x.select().props.onChange({target:{value:'80'}});x.render();const old=x.button('데이터 나누기 저장').props.onClick;
 if(mutation==='authority')x.refreshAuthority();if(mutation==='source')x.changeSource();if(mutation==='datasetKey'){x.dataset.datasetKey='changed';x.render();}if(mutation==='task'){x.project.task='classification';x.render();}
 await old;assert.deepEqual(x.calls,[]);if(mutation==='authority'||mutation==='source')assert.equal(x.select().props.value,'70');x.unmount();
});
for(const outcome of ['success','failure'])test('late split '+outcome+' cannot publish feedback into a different authority',async()=>{
 const x=fixture({state:'automatic'});const pending=x.button('데이터 나누기 저장').props.onClick();x.refreshAuthority();
 if(outcome==='success')x.complete();else{x.dataset.isSplitting=false;x.held[0].reject(Error('Old refusal'));}await pending;await tick();x.render();assert.deepEqual(x.alerts(),[]);assert.doesNotMatch(x.text(),/저장된 분할을 확인했습니다/);x.unmount();
});
test('current split failure remains visible and preserves the selected unsaved ratio',async()=>{
 const x=fixture({state:'automatic'});x.select().props.onChange({target:{value:'80'}});x.render();const pending=x.button('데이터 나누기 저장').props.onClick();x.dataset.isSplitting=false;x.held[0].reject(Error('같은 원본이 분할 사이에 중복됩니다.'));await pending;x.render();assert.deepEqual(x.alerts(),['같은 원본이 분할 사이에 중복됩니다.']);assert.equal(x.select().props.value,'80');x.refreshAuthority();assert.deepEqual(x.alerts(),[]);x.unmount();
});
test('a new source may save after canonical reset while an old reply cannot clear its pending feedback',async()=>{
 const x=fixture({state:'automatic'});const old=x.button('데이터 나누기 저장').props.onClick();x.changeSource();x.dataset.isSplitting=false;x.render();
 const next=x.button('데이터 나누기 저장').props.onClick();assert.equal(x.held.length,2);
 x.held[0].reject(Error('Old source error'));await old;x.render();assert.deepEqual(x.alerts(),[]);assert.equal(x.button('데이터 나누기 저장').props.disabled,true);
 x.complete(1);await next;x.render();assert.match(x.text(),/저장된 분할을 확인했습니다/);x.unmount();
});
