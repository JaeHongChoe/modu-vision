const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript');
const file=path.join(__dirname,'DatasetWorkflowPanel.tsx');
const tick=()=>new Promise(setImmediate);
function harness(){
 let cursor=0,generation=0,identity='local',mounted=true,lateStateWrites=0;
 const slots=[],effects=[],listeners=[],requests=[],downloads=[];
 const project={project:{id:'p-A',project_dir:'p-A',task:'classification',source_dataset_dir:'s-A'},projectDir:'p-A',task:'classification',isProjectBusy:false,openImageForLabeling:async()=>true},dataset={folderPath:'s-A',hasSelectedFolder:true,annotationsChanged:async()=>{},loadImages:async()=>{}},annotation={reviewerName:'fixture',setReviewerName:()=>{}};
 const react={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()}),useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{if(!mounted)lateStateWrites++;slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||!deps||deps.some((d,j)=>d!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=fn();});}}};
 const store=state=>{const fn=()=>state;fn.getState=()=>state;return fn;};
 const api={list:async()=>({items:[],total:0}),duplicates:async()=>({duplicates:[]}),savedSplit:async()=>({availability:'unavailable'}),export:(format,signal)=>new Promise((resolve,reject)=>requests.push({format,signal,resolve,reject}))};
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m.require=name=>name==='react'?react:name.includes('services/api')?{resolveApiUrl:p=>p,getProjectContextGeneration:()=>generation,getApiPersistenceIdentity:()=>identity,subscribeProjectContext:fn=>{listeners.push(fn);return()=>{};}}:name.includes('datasetWorkflow')?{datasetWorkflow:api,workflowError:e=>e.message}:name.includes('useDatasetStore')?{useDatasetStore:store(dataset)}:name.includes('useProjectStore')?{useProjectStore:store(project)}:name.includes('useAnnotationStore')?{useAnnotationStore:store(annotation)}:name.includes('projectPreferences')?{projectPreferences:{read:async()=>({tag_colors:{}})}}:name.includes('hostAdapter')?{host:{}}:name.includes('ProjectPreferencePanel')?{ProjectPreferencePanel:()=>null}:name.includes('ExternalMaskExchangePanel')?{ExternalMaskExchangePanel:()=>null}:Module.prototype.require.call(m,name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
 const nodes=tree=>{const found=[];const walk=n=>{if(n&&typeof n==='object'){found.push(n);n.children?.forEach(walk);}};walk(tree);return found;};
 const render=()=>{cursor=0;const tree=m.exports.DatasetWorkflowPanel();effects.splice(0).forEach(fn=>fn());return tree;};
 const button=name=>nodes(render()).find(n=>n.type==='button'&&n.children.includes(name));
 const text=()=>nodes(render()).flatMap(n=>n.children.filter(c=>typeof c==='string')).join('|');
 const globals={window:global.window,document:global.document};
 global.window={addEventListener(){},removeEventListener(){}};
 global.document={createElement:type=>{assert.equal(type,'a');return{href:'',download:'',click(){downloads.push({href:this.href,download:this.download});}};}};
 const unmount=()=>{mounted=false;for(const slot of slots)if(slot?.cleanup)slot.cleanup();};
 return{render,nodes,button,text,requests,downloads,lateStateWrites:()=>lateStateWrites,project,dataset,
  move:({projectDir,folderPath,transport,generationOnly}={})=>{if(projectDir!==undefined)project.projectDir=projectDir;if(folderPath!==undefined)dataset.folderPath=folderPath;if(transport!==undefined)identity=transport;generation++;listeners.forEach(fn=>fn());},
  unmount,restore:()=>{global.window=globals.window;global.document=globals.document;},
  finish:(i,overrides={})=>requests[i].resolve({download_url:'/api/download/owned-'+i+'.zip',image_count:3,annotation_count:4,...overrides})};
}
async function setup(){const h=harness();h.render();h.button('이미지 검토·그룹 분할·라벨 교환').props.onClick();h.render();await tick();return h;}
const cancelName='내보내기 요청 취소';
function click(h,name){const b=h.button(name);assert.ok(b,`Owning button missing: ${name}`);assert.equal(Boolean(b.props.disabled),false);b.props.onClick();}

test('own export has a real signal and explicit Cancel is unavailable without a pending request',async()=>{const h=await setup();try{
 assert.equal(h.button(cancelName)?.props.disabled,true);click(h,'라벨 내보내기');assert.equal(h.requests.length,1);assert.ok(h.requests[0].signal instanceof AbortSignal);assert.equal(h.requests[0].signal.aborted,false);assert.equal(h.requests[0].format,'labelme');assert.equal(h.button('라벨 내보내기').props.disabled,true);assert.equal(h.button(cancelName).props.disabled,false);click(h,cancelName);assert.equal(h.requests[0].signal.aborted,true);assert.equal(h.button(cancelName).props.disabled,true);assert.equal(h.downloads.length,0);assert.match(h.text(),/다운로드를 시작하지 않았습니다/);
 }finally{h.unmount();h.restore();}});
test('cancelled request late success cannot create an anchor or replace cancellation status',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');click(h,cancelName);h.finish(0);await tick();assert.equal(h.downloads.length,0);assert.match(h.text(),/다운로드를 시작하지 않았습니다/);assert.doesNotMatch(h.text(),/내보내기 완료/);
 }finally{h.unmount();h.restore();}});
test('two activations before rerender submit exactly one owning export request',async()=>{const h=await setup();try{
 const start=h.button('라벨 내보내기').props.onClick;start();start();assert.equal(h.requests.length,1);h.finish(0);await tick();assert.equal(h.downloads.length,1);
 }finally{h.unmount();h.restore();}});
test('abort rejection stays cancellation rather than an error alert',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');click(h,cancelName);h.requests[0].reject(new DOMException('Aborted','AbortError'));await tick();assert.equal(h.nodes(h.render()).filter(n=>n.props.role==='alert').length,0);assert.match(h.text(),/취소/);
 }finally{h.unmount();h.restore();}});
test('late first completion and retained first Cancel cannot settle or abort the new owned request',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');const oldCancel=h.button(cancelName).props.onClick;oldCancel();click(h,'라벨 내보내기');h.finish(0);await tick();oldCancel();assert.equal(h.requests.length,2);assert.equal(h.requests[1].signal.aborted,false);assert.equal(h.button('라벨 내보내기').props.disabled,true);assert.equal(h.button(cancelName).props.disabled,false);assert.equal(h.downloads.length,0);h.finish(1);await tick();assert.deepEqual(h.downloads,[{href:'/api/download/owned-1.zip',download:'labelme_annotations.zip'}]);assert.equal(h.button(cancelName).props.disabled,true);
 }finally{h.unmount();h.restore();}});
test('retained old start after a project switch submits nothing',async()=>{const h=await setup();try{
 const oldStart=h.button('라벨 내보내기').props.onClick;h.move({projectDir:'p-B',folderPath:'s-B'});oldStart();assert.equal(h.requests.length,0);
 }finally{h.unmount();h.restore();}});
test('project source transport and generation switches abort only the owned export and fence its completion',async()=>{
 for(const move of [{projectDir:'p-B',folderPath:'s-B'},{folderPath:'s-B'},{transport:'shared-B'},{generationOnly:true}]){const h=await setup();try{
  click(h,'라벨 내보내기');h.move(move);h.render();assert.equal(h.requests[0].signal.aborted,true);h.finish(0);await tick();assert.equal(h.downloads.length,0);assert.doesNotMatch(h.text(),/내보내기 완료|다운로드를 시작하지 않았습니다/);await tick();assert.equal(h.button(cancelName).props.disabled,true);
 }finally{h.unmount();h.restore();}}
});
test('response arriving after a generation changes but before rerender is not adopted',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');h.move({generationOnly:true});h.finish(0);await tick();assert.equal(h.downloads.length,0);h.render();assert.equal(h.requests[0].signal.aborted,true);assert.equal(h.button(cancelName).props.disabled,true);
 }finally{h.unmount();h.restore();}});
test('unmount aborts the owned export and late completion performs no component state writes',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');h.unmount();assert.equal(h.requests[0].signal.aborted,true);h.finish(0);await tick();assert.equal(h.downloads.length,0);assert.equal(h.lateStateWrites(),0);
 }finally{h.restore();}});
test('panel dismissal prevents late download but is not the explicit Cancel action',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');click(h,'이미지 검토·그룹 분할·라벨 교환');assert.equal(h.requests[0].signal.aborted,true);h.finish(0);await tick();assert.equal(h.downloads.length,0);assert.equal(h.button('이미지 검토·그룹 분할·라벨 교환').props['aria-expanded'],false);
 }finally{h.unmount();h.restore();}});
test('ordinary export error remains visible and a fresh explicit retry downloads one captured format',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');h.requests[0].reject(new Error('HTTP 503 owned export unavailable'));await tick();assert.match(h.text(),/HTTP 503 owned export unavailable/);assert.equal(h.button('라벨 내보내기').props.disabled,false);click(h,'라벨 내보내기');const selected=h.nodes(h.render()).find(n=>n.type==='select'&&n.props.value==='labelme');selected.props.onChange({target:{value:'yolo'}});h.render();h.finish(1);await tick();assert.deepEqual(h.downloads,[{href:'/api/download/owned-1.zip',download:'labelme_annotations.zip'}]);assert.match(h.text(),/3개 이미지 · 4개 라벨 내보내기 완료/);assert.equal(h.nodes(h.render()).filter(n=>n.props.role==='alert').length,0);
 }finally{h.unmount();h.restore();}});
test('a retained completed Cancel cannot announce cancellation after download began',async()=>{const h=await setup();try{
 click(h,'라벨 내보내기');const oldCancel=h.button(cancelName).props.onClick;h.finish(0);await tick();oldCancel();assert.equal(h.downloads.length,1);assert.match(h.text(),/내보내기 완료/);assert.doesNotMatch(h.text(),/다운로드를 시작하지 않았습니다/);
 }finally{h.unmount();h.restore();}});
test('the original export API forwards its own signal without changing method or format body',()=>{
 const target=path.resolve(__dirname,'../../services/datasetWorkflow.ts');const calls=[];const m=new Module(target,module);m.filename=target;m.paths=Module._nodeModulePaths(path.dirname(target));m.require=name=>name==='./api'?{request:(url,options)=>{calls.push({url,options});return Promise.resolve({});}}:name.includes('foundationRequest')?{buildModelRequest:()=>{}}:Module.prototype.require.call(m,name);
 m._compile(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,target);const controller=new AbortController();m.exports.datasetWorkflow.export('coco',controller.signal);assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/dataset/formats/export');assert.equal(calls[0].options.method,'POST');assert.deepEqual(JSON.parse(calls[0].options.body),{format:'coco'});assert.equal(calls[0].options.signal,controller.signal);
});
