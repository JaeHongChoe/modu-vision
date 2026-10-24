// Real dataset store and API authority serializer; HTTP and unrelated invalidation are controlled.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),{create}=require('zustand');
function load(relative,mocks={}){
 const file=path.resolve(__dirname,relative),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);
 m.require=ref=>Object.hasOwn(mocks,ref)?mocks[ref]:original(ref);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;
}
async function fixture(t){
 const api=load('../../services/api.ts');api.setCachedPort(5000);
 const inert={getState:()=>({invalidateForDataChange(){}})};
 const ds=load('../../stores/useDatasetStore.ts',{'../services/api':api,'./useTrainingStore':{useTrainingStore:inert},'./useEvaluationStore':{useEvaluationStore:inert},'./useFlowchartStore':{useFlowchartStore:inert}});
 const project=create(()=>({project:{id:'project-secret',project_dir:'/private/project',task:'classification',source_dataset_dir:'/private/source',active_labelset_id:'default'},projectDir:'/private/project',task:'classification',isProjectBusy:false}));
 const originalFetch=global.fetch;global.fetch=async url=>{const pathname=new URL(url).pathname;
  assert.ok(['/api/dataset/current-summary','/api/dataset/images'].includes(pathname),'unexpected network route: '+pathname);
  return new Response(JSON.stringify(pathname.endsWith('current-summary')?{total_images:1,classes:{OK:1},split:{train:1,val:0,test:0}}:{total:1,items:[],limit:48,offset:0}),{status:200});};
 t.after(()=>{global.fetch=originalFetch;});
 const study=load('firstUseStudyStore.ts',{'../../services/api':api,'../../stores/useDatasetStore':ds,'../../stores/useProjectStore':{useProjectStore:project},'./firstUseStudy':load('firstUseStudy.ts')});
 await ds.useDatasetStore.getState().importFolder('/private/source','classification',undefined,true);
 assert.equal(ds.useDatasetStore.getState().sourceAuthority,ds.readDatasetAuthority());
 return{api,ds,project,study};
}
test('study cannot bind a pending or stale same-path import authority',async t=>{
 const f=await fixture(t);assert.ok(f.study.currentFirstUseStudyScope());
 f.ds.useDatasetStore.setState({sourceAuthority:null});assert.equal(f.study.currentFirstUseStudyScope(),null);
 f.ds.useDatasetStore.setState({sourceAuthority:'old-authority'});assert.equal(f.study.currentFirstUseStudyScope(),null);
});
test('accepted source cannot bind while the original project handoff is busy',async t=>{
 const f=await fixture(t);f.project.setState({isProjectBusy:true});assert.equal(f.study.currentFirstUseStudyScope(),null);
});
test('local backend replacement invalidates accepted authority and seals only the bound study',async t=>{
 const f=await fixture(t),store=f.study.createFirstUseStudyStore(()=>1000);store.getState().start({mode:'automated',task:'first_flow'});
 store.getState().bind(f.study.currentFirstUseStudyScope());f.api.setCachedPort(5001);
 store.getState().checkScope(f.study.currentFirstUseStudyScope());assert.equal(store.getState().snapshot.status,'aborted');
 assert.doesNotMatch(JSON.stringify(store.getState().exportReport()),/private\/project|private\/source|project-secret|127\.0\.0\.1|5001/);
});
test('label-set change aborts and scope subscriptions clean up all original store/API listeners',async t=>{
 const f=await fixture(t),before=f.study.currentFirstUseStudyScope();let updates=0;const stop=f.study.subscribeFirstUseStudyScope(()=>updates++);
 f.project.setState({project:{...f.project.getState().project,active_labelset_id:'new'}});assert.notEqual(f.study.currentFirstUseStudyScope(),before);assert.equal(updates,1);
 stop();f.project.setState({isProjectBusy:true});f.ds.useDatasetStore.setState({sourceAuthority:null});f.api.setCachedPort(5002);assert.equal(updates,1);
});
