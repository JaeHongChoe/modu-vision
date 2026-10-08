const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
const deferred=()=>{let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no;});return{promise,resolve,reject};};
function load(filename,mocks){const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const original=m.require.bind(m);m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:original(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,filename);return m.exports;}
function harness(){
 const project={task:'anomaly',language:'ko',projectDir:'/projects/A',project:{id:'A',source_dataset_dir:'/source',active_labelset_id:'default'}};
 const reads=[],writes=[],pending=[];let cursor=0;const slots=[],effects=[];
 const same=(a,b)=>a&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index]));
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useCallback(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps))slots[i]={deps,fn};return slots[i].fn;},useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const noWrite=async data=>{writes.push(data);throw Error('Read-only source restoration cannot write');};
 const api={project:{update:noWrite},dataset:{import:noWrite,currentSummary:request=>{reads.push({project_id:project.project.id,...request});const value=deferred();pending.push(value);return value.promise;},getImages:async()=>({images:[],total:0})},datasetImports:{revisions:async()=>({revisions:[],active_revision:null})}};
 const inert={getState:()=>({invalidateForDataChange(){}}),setState(){}};
 const store=load(path.resolve(__dirname,'../../stores/useDatasetStore.ts'),{'../services/api':{api},'./useTrainingStore':{useTrainingStore:inert},'./useEvaluationStore':{useEvaluationStore:inert},'./useFlowchartStore':{useFlowchartStore:inert},'./useProjectStore':{useProjectStore:{setState(){throw Error('Restore must not update the project');}}}}).useDatasetStore;
 store.setState({folderPath:'/source',hasSelectedFolder:true,datasetKey:'/source\0anomaly',lastImportedKey:'/source\0anomaly',totalImages:2});
 const hook=object=>Object.assign(selector=>selector?selector(object):object,{getState:()=>object});
 const file=path.join(__dirname,'DatasetStudio.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const helpers={'./classDistribution':load(path.join(__dirname,'classDistribution.ts'),{}),'./datasetImportView':load(path.join(__dirname,'datasetImportView.ts'),{}),'../../utils/datasetSplitCapability':load(path.resolve(__dirname,'../../utils/datasetSplitCapability.ts'),{})};
 const jsx=(type,props)=>({type,props});m.require=name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='../../stores/useDatasetStore'?{useDatasetStore:selector=>selector?selector(store.getState()):store.getState()}:name==='../../stores/useProjectStore'?{useProjectStore:hook(project)}:name==='../../services/api'?{api,resolveApiUrl:x=>x}:name==='../runtime/useDeliveryScope'?{useDeliveryScope:()=>({key:project.project.id})}:Object.hasOwn(helpers,name)?helpers[name]:name.startsWith('.')?new Proxy({},{get:()=>()=>null}):original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);
 const render=()=>{cursor=0;const tree=m.exports.DatasetStudio();effects.splice(0).forEach(fn=>fn());return tree;};
 const switchProject=id=>{project.project={...project.project,id};project.projectDir=`/projects/${id}`;store.getState().setFolderPath('');store.getState().setFolderPath('/source');};
 const summary=total=>({total_images:total,classes:{},split:{train:0,val:0,test:0}});
 return{store,render,switchProject,reads,writes,pending,summary};
}

test('same-folder project reset restores its own source exactly once without content writes',async()=>{
 const h=harness();h.render();assert.equal(h.reads.length,0,'already imported source is not fetched again');
 h.switchProject('B');h.render();assert.deepEqual(h.reads,[{project_id:'B',folder_path:'/source',task:'anomaly'}]);
 assert.equal(h.store.getState().datasetKey,'/source\0anomaly','real store marks admission synchronously');
 h.render();h.render();assert.equal(h.reads.length,1,'key publication does not create another import');
 h.pending[0].resolve(h.summary(2));await tick();h.render();assert.equal(h.store.getState().totalImages,2);assert.deepEqual(h.writes,[]);
});
test('a failed source read retains its error without an automatic retry loop',async()=>{
 const h=harness();h.render();h.switchProject('B');h.render();assert.equal(h.reads.length,1);
 h.pending[0].reject(Error('Saved project source changed'));await tick();
 for(let i=0;i<4;i++)h.render();assert.equal(h.reads.length,1);assert.equal(h.store.getState().importError,'Saved project source changed');assert.deepEqual(h.writes,[]);
});
test('an older in-flight same-folder summary cannot replace the next project source view',async()=>{
 const h=harness();h.render();h.switchProject('B');h.render();h.render();h.switchProject('C');h.render();
 assert.deepEqual(h.reads.map(row=>row.project_id),['B','C']);h.pending[0].resolve(h.summary(99));await tick();h.render();
 assert.equal(h.store.getState().totalImages,0,'obsolete summary is ignored');assert.equal(h.store.getState().isLoading,true);
 h.pending[1].resolve(h.summary(2));await tick();h.render();assert.equal(h.store.getState().totalImages,2);assert.equal(h.store.getState().isLoading,false);assert.deepEqual(h.writes,[]);
});
test('clearing a source does not dispatch a summary for the empty folder',()=>{
 const h=harness();h.render();h.store.getState().setFolderPath('');h.render();assert.deepEqual(h.reads,[]);assert.deepEqual(h.writes,[]);
});
