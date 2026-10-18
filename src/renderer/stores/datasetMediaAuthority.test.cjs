// Real dataset store and API serializer; only HTTP and unrelated invalidation
// boundaries are supplied. These are source-state tests, not mounted UI/backend QA.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(relative,dependencies={}) {
 const file=path.resolve(__dirname,relative),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));
 const original=m.require.bind(m);m.require=name=>Object.hasOwn(dependencies,name)?dependencies[name]:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;
}
const context=(id,actor='actor')=>({workspace_id:'workspace',project_id:id,actor_id:actor,mode:'team'});
const inert={getState:()=>({invalidateForDataChange(){}}),setState(){}};
async function fixture(t,{sameFolder=false,target='A',actor='actor'}={}) {
 const api=load('../services/api.ts');api.setSharedApiBase('https://dataset.invalid');api.setProjectContext(context('B'));
 const datasetModule=load('./useDatasetStore.ts',{'../services/api':api,
  './useTrainingStore':{useTrainingStore:inert},'./useEvaluationStore':{useEvaluationStore:inert},'./useFlowchartStore':{useFlowchartStore:inert}});
 const store=datasetModule.useDatasetStore,calls=[];const folder=sameFolder?'/shared-source':'/source/B';
 const targetProject={id:target,name:target,project_dir:'/projects/'+target,source_dataset_dir:sameFolder?folder:'/source/'+target,task:'classification'};
 const before=global.fetch;
 global.fetch=async(url,options)=>{
  const parsed=new URL(url),headers=new Headers(options.headers);calls.push({path:parsed.pathname,folder:parsed.searchParams.get('folder_path'),context:JSON.parse(headers.get('X-Vision-Context')||'null')});
  let value,selected=context('B');
  if(parsed.pathname==='/api/dataset/current-summary')value={total_images:1,classes:{good:1},split:{train:1,val:0,test:0}};
  else if(parsed.pathname==='/api/dataset/images')value={total:1,limit:48,offset:0,items:[]};
  else if(parsed.pathname==='/api/project/current'){value=targetProject;selected=context(target,actor);}
  else throw Error('Undeclared HTTP transport boundary: '+parsed.pathname);
  return new Response(JSON.stringify(value),{status:200,headers:{'X-Vision-Context':JSON.stringify(selected)}});
 };
 t.after(()=>{global.fetch=before;});
 await store.getState().importFolder(folder,'classification',undefined,true);
 assert.deepEqual(calls.map(row=>[row.path,row.context.project_id]),[['/api/dataset/current-summary','B'],['/api/dataset/images','B']]);
 return{api,store,calls,datasetModule};
}
for(const sameFolder of [false,true])test(`changed explicit namespace retires old dataset before epoch increment (same folder ${sameFolder})`,async t=>{
 const f=await fixture(t,{sameFolder}),before=f.calls.length,epoch=f.api.getProjectContextGeneration();
 const project=await f.api.api.project.getCurrent();let pending;
 f.api.api.project.acceptContext(project,()=>{
  assert.equal(f.api.getProjectContextGeneration(),epoch,'exercise original synchronous pre-epoch callback');
  assert.equal(f.api.getProjectContext().project_id,'A');pending=f.store.getState().loadImages(1);
 });
 await pending;
 assert.equal(f.calls.filter(row=>row.path==='/api/dataset/images').length,1,
  'an old accepted B dataset must not dispatch with the newly accepted A context');
 assert.equal(f.calls.length,before+1);
});
test('same project but changed actor also retires the old accepted source before epoch increment',async t=>{
 const f=await fixture(t,{target:'B',actor:'new-actor',sameFolder:true}),project=await f.api.api.project.getCurrent();let pending;
 f.api.api.project.acceptContext(project,()=>{pending=f.store.getState().loadImages(1);});await pending;
 assert.equal(f.calls.filter(row=>row.path==='/api/dataset/images').length,1);
});
test('unchanged accepted context keeps ordinary same-source image refresh available',async t=>{
 const f=await fixture(t,{target:'B'}),epoch=f.api.getProjectContextGeneration(),project=await f.api.api.project.getCurrent();let pending;
 f.api.api.project.acceptContext(project,()=>{pending=f.store.getState().loadImages(1);});await pending;
 assert.equal(f.api.getProjectContextGeneration(),epoch);
 const images=f.calls.filter(row=>row.path==='/api/dataset/images');assert.equal(images.length,2);
 assert.ok(images.every(row=>row.folder==='/source/B'&&row.context.project_id==='B'));
});
test('first local port discovery still publishes the source accepted through the original API client',async t=>{
 const beforeWindow=global.window,beforeFetch=global.fetch;
 global.window={api:{getBackendPort:async()=>56050}};
 t.after(()=>{global.window=beforeWindow;global.fetch=beforeFetch;});
 const api=load('../services/api.ts'),calls=[];
 const store=load('./useDatasetStore.ts',{'../services/api':api,
  './useTrainingStore':{useTrainingStore:inert},'./useEvaluationStore':{useEvaluationStore:inert},'./useFlowchartStore':{useFlowchartStore:inert}}).useDatasetStore;
 assert.equal(api.resolveApiUrl('/'),'');
 global.fetch=async(url)=>{
  const parsed=new URL(url);assert.equal(parsed.origin,'http://127.0.0.1:56050');calls.push(parsed.pathname);
  const body=parsed.pathname==='/api/dataset/current-summary'?{total_images:1,classes:{good:1},split:{train:1,val:0,test:0}}:{total:1,limit:48,offset:0,items:[]};
  return new Response(JSON.stringify(body),{status:200});
 };
 await store.getState().importFolder('/source/B','classification',undefined,true);
 assert.equal(api.resolveApiUrl('/'),'http://127.0.0.1:56050/');
 assert.deepEqual(calls,['/api/dataset/current-summary','/api/dataset/images']);
 assert.notEqual(store.getState().sourceAuthority,null);
 assert.equal(store.getState().totalImages,1);
});
