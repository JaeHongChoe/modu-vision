const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function harness(){
 const file=path.join(__dirname,'DatasetWorkflowPanel.tsx');
 let cursor=0,generation=0,identity='local';const slots=[],effects=[],listeners=[],reads=[];
 const config=(id,source)=>({id,name:id,project_dir:'/projects/'+id,task:'classification',source_dataset_dir:source,active_labelset_id:'default'});
 const project={project:config('A','/source-A'),projectDir:'/projects/A',task:'classification',isProjectBusy:false,openImageForLabeling:async()=>true};
 const dataset={folderPath:'/source-A',hasSelectedFolder:true,isLoading:false,annotationsChanged:async()=>{},loadImages:async()=>{}};
 const annotation={reviewerName:'fixture',setReviewerName(){}};
 const react={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()}),useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++;if(!slots[i]||deps.some((d,j)=>d!==slots[i].deps[j])){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const store=value=>Object.assign(()=>value,{getState:()=>value});
 const workflow={list:filters=>new Promise((resolve,reject)=>reads.push({filters:{...filters},project:project.project?{...project.project}:null,generation,identity,resolve,reject})),duplicates:async()=>({duplicates:[]}),savedSplit:async()=>({availability:'unavailable'})};
 const mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(__dirname);const original=mod.require.bind(mod);
 mod.require=name=>name==='react'?react:name.endsWith('/stores/useStepSheetStore')?{useStepSheetStore:Object.assign(select=>select({open:null}),{getState:()=>({open:null,show(){}})})}:name.endsWith('/services/api')?{resolveApiUrl:x=>x,getProjectContextGeneration:()=>generation,getApiPersistenceIdentity:()=>identity,subscribeProjectContext:fn=>{listeners.push(fn);return()=>{const i=listeners.indexOf(fn);if(i>=0)listeners.splice(i,1);};}}:name.endsWith('/services/datasetWorkflow')?{datasetWorkflow:workflow,workflowError:e=>e.message}:name.endsWith('/stores/useProjectStore')?{useProjectStore:store(project)}:name.endsWith('/stores/useDatasetStore')?{useDatasetStore:store(dataset)}:name.endsWith('/stores/useAnnotationStore')?{useAnnotationStore:store(annotation)}:name.endsWith('/services/projectPreferences')?{projectPreferences:{read:async()=>({tag_colors:{}})}}:name.endsWith('/services/hostAdapter')?{host:{}}:name==='./ProjectPreferencePanel'?{ProjectPreferencePanel:()=>null}:name==='./ExternalMaskExchangePanel'?{ExternalMaskExchangePanel:()=>null}:original(name);
 mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
 const priorWindow=global.window;global.window={addEventListener(){},removeEventListener(){}};
 const render=()=>{cursor=0;const tree=mod.exports.DatasetWorkflowPanel();effects.splice(0).forEach(fn=>fn());return tree;};
 const nodes=tree=>{const result=[];const walk=node=>{if(node&&typeof node==='object'){result.push(node);node.children?.forEach(walk);}};walk(tree);return result;};
 const button=name=>nodes(render()).find(node=>node.type==='button'&&node.children.includes(name));
 return{project,dataset,reads,config,render,nodes,button,text:()=>nodes(render()).flatMap(node=>node.children.filter(x=>typeof x==='string'||typeof x==='number')).join(''),
  open(){button('이미지 검토·그룹 분할·라벨 교환').props.onClick();render();},
  advance(transport){generation++;if(transport!==undefined)identity=transport;listeners.forEach(fn=>fn());},
  resolve(i,total=0){reads[i].resolve({items:[],total});},
  dispose(){for(const value of slots)value?.cleanup?.();global.window=priorWindow;}};
}
function owned(fn){return async()=>{const h=harness();try{await fn(h);}finally{h.dispose();}};}
test('coherent original project/source dispatches the original bounded metadata filters',owned(async h=>{
 h.open();assert.equal(h.reads.length,1);assert.deepEqual(h.reads[0].filters,{folder_path:'/source-A',state:'',product:'',lot:'',group:'',tag:'',limit:5000});h.resolve(0,7);await tick();assert.match(h.text(),/검색 결과 7개/);
}));
test('accepted new project while old dataset remains cannot dispatch during original project handoff',owned(async h=>{
 h.project.project=h.config('B','/source-B');h.project.projectDir='/projects/B';h.project.isProjectBusy=true;h.advance();h.open();assert.equal(h.reads.length,0);
}));
test('source mismatch stays refused even when project busy is already false',owned(async h=>{
 h.project.project=h.config('B','/source-B');h.project.projectDir='/projects/B';h.advance();h.open();assert.equal(h.reads.length,0);
}));
test('new folder awaiting server source adoption cannot be read under the prior source',owned(async h=>{
 h.dataset.folderPath='/source-B';h.open();assert.equal(h.reads.length,0);
}));
test('a same-source project refresh remains suspended until original busy flag settles',owned(async h=>{
 h.project.isProjectBusy=true;h.open();assert.equal(h.reads.length,0);h.project.isProjectBusy=false;h.render();assert.equal(h.reads.length,1);h.resolve(0);await tick();
}));
test('settled new project and source resume exactly one new owning metadata read',owned(async h=>{
 h.project.project=h.config('B','/source-B');h.project.projectDir='/projects/B';h.project.isProjectBusy=true;h.advance();h.open();h.dataset.folderPath='/source-B';h.render();assert.equal(h.reads.length,0);h.project.isProjectBusy=false;h.render();assert.equal(h.reads.length,1);assert.equal(h.reads[0].filters.folder_path,'/source-B');assert.equal(h.reads[0].project.id,'B');h.resolve(0,2);await tick();assert.match(h.text(),/검색 결과 2개/);
}));
test('in-flight dataset adoption cannot dispatch until original dataset loading settles',owned(async h=>{
 h.dataset.isLoading=true;h.open();assert.equal(h.reads.length,0);h.dataset.isLoading=false;h.render();assert.equal(h.reads.length,1);h.resolve(0);await tick();
}));
test('project directory and task must describe the same accepted project before read',owned(async h=>{
 h.project.projectDir='/projects/not-A';h.open();assert.equal(h.reads.length,0);h.project.projectDir='/projects/A';h.project.task='segmentation';h.render();assert.equal(h.reads.length,0);h.project.task='classification';h.render();assert.equal(h.reads.length,1);h.resolve(0);await tick();
}));
test('late source read success cannot adopt after project source changes before rerender',owned(async h=>{
 h.open();h.project.project={...h.project.project,source_dataset_dir:'/source-B'};h.resolve(0,991);await tick();assert.doesNotMatch(h.text(),/검색 결과 991개/);assert.doesNotMatch(h.text(),/처리 중/);
}));
test('late source read error cannot appear after a label-set change before rerender',owned(async h=>{
 h.open();h.project.project={...h.project.project,active_labelset_id:'other'};h.reads[0].reject(new Error('stale-labelset-error'));await tick();assert.doesNotMatch(h.text(),/stale-labelset-error/);assert.equal(h.reads.length,2);h.resolve(1);await tick();
}));
test('A to B to A authority epochs refuse original A completion while fresh A read proceeds',owned(async h=>{
 h.open();h.project.project=h.config('B','/source-B');h.project.projectDir='/projects/B';h.dataset.folderPath='/source-B';h.advance();h.render();h.project.project=h.config('A','/source-A');h.project.projectDir='/projects/A';h.dataset.folderPath='/source-A';h.advance();h.render();assert.equal(h.reads.length,3);h.resolve(0,993);await tick();assert.doesNotMatch(h.text(),/검색 결과 993개/);h.resolve(1,992);h.resolve(2,3);await tick();assert.match(h.text(),/검색 결과 3개/);
}));
test('an ordinary current metadata error remains visible without filtering backend ownership refusals',owned(async h=>{
 h.open();h.reads[0].reject(new Error("Dataset path does not match the active project's source."));await tick();assert.match(h.text(),/Dataset path does not match the active project's source/);
}));
