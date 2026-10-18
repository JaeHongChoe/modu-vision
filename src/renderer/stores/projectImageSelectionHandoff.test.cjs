// Original project/flow stores, authority acceptance and HTTP serializer. Only
// HTTP replies and annotation/dataset boundaries are controlled. This is a store
// transition regression, not a React mount or browser/backend acceptance.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const Module=require('node:module'),test=require('node:test'),ts=require('typescript');
function load(relative,dependencies={}){
 const filename=path.resolve(__dirname,relative),m=new Module(filename,module);
 m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 m.require=name=>Object.hasOwn(dependencies,name)?dependencies[name]:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{
  module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);return m.exports;
}
const A={id:'A',name:'A',project_dir:'/projects/A',task:'segmentation',source_dataset_dir:'/source/A',active_labelset_id:'labels-A'};
const B={id:'B',name:'B',project_dir:'/projects/B',task:'segmentation',source_dataset_dir:'/source/B',active_labelset_id:'labels-B'};
const context=p=>({workspace_id:'workspace',project_id:p.id,actor_id:'actor',mode:'local'});
const image=p=>({source:'dataset',imagePath:p.source_dataset_dir+'/coordinate-512x256.png',fileName:'coordinate-512x256.png'});
const state=initial=>({getState:()=>initial,setState:update=>Object.assign(initial,typeof update==='function'?update(initial):update)});
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return{promise,resolve};}
function fixture(t,{target=B,reject=false,stale=false,editDuringOpen=false,holdAnnotation=false}={}){
 const api=load('../services/api.ts'),draft=load('../services/flowDraft.ts',{'./api':api});
 let projectStore,server=A,hold=null;const entered=deferred(),calls=[],observations=[],pending=[];
 const flow=load('./useFlowchartStore.ts',{
  '../services/api':api,'../services/flowDraft':draft,
  './useProjectStore':{useProjectStore:{getState:()=>projectStore.getState()}},
  '../components/flowchart/flowchartStartup':{getFlowchartModelTask:()=>null},
 }).useFlowchartStore;
 const annotation=state({isDirty:false,setTask(){},async setImages(){
  if(holdAnnotation){hold=deferred();entered.resolve();await hold.promise;}return true;}});
 const dataset=state({hasSelectedFolder:true,folderPath:A.source_dataset_dir,datasetKey:A.source_dataset_dir+'\0segmentation',
  lastImportedKey:A.source_dataset_dir+'\0segmentation',staleDatasetKeys:[],importError:null,
  isLoading:false,isSplitting:false,isGenerating:false,
  setFolderPath(folder){flow.getState().invalidateForDataChange();Object.assign(dataset.getState(),{
   folderPath:folder,datasetKey:folder?folder+'\0segmentation':null});},ensureImported:async()=>{}});
 projectStore=load('./useProjectStore.ts',{
  '../services/api':api,'./projectViewState':load('./projectViewState.ts'),
  '../services/datasetWorkflow':{datasetWorkflow:{},workflowError:error=>String(error?.message||error)},
  './useAnnotationStore':{useAnnotationStore:annotation},'./useDatasetStore':{useDatasetStore:dataset},
  './useFlowchartStore':{useFlowchartStore:flow},'./useTrainingStore':{useTrainingStore:state({isTraining:false})},
  './useInspectionRunStore':{useInspectionRunStore:state({isRunning:false})},
  './useModelAssistRunStore':{useModelAssistRunStore:state({activeOperations:0})},
 }).useProjectStore;
 api.setSharedApiBase('https://owned.invalid',()=>({credentials:'include'}));api.setProjectContext(context(A));
 projectStore.setState({project:A,projectDir:A.project_dir,projectName:A.name,task:A.task,activeStep:1});
 const graph={id:'flow',name:'Saved flow',nodes:[{id:'roi',type:'custom',position:{x:0,y:0},
  data:{node_type:'fixed_roi',label:'Saved ROI',params:{x:65,y:32,width:193,height:130}}}],edges:[]};
 flow.setState({pipeline:graph,cleanPipeline:graph,pipelineDirty:false});
 flow.getState().setSelectedImage(image(A));
 const previousFetch=global.fetch;
 global.fetch=async(url,options={})=>{
  const u=new URL(url),headers=Object.fromEntries(new Headers(options.headers));
  const row={path:u.pathname,query:Object.fromEntries(u.searchParams),method:options.method||'GET',
   headers,raw_body:options.body??null,credentials:options.credentials};calls.push(row);
  let body,status=200;
  if(u.pathname==='/api/project/current')body=server;
  else if(u.pathname==='/api/project/open'){
   assert.equal(row.method,'POST');assert.deepEqual(JSON.parse(row.raw_body),{project_dir:target.project_dir});
   assert.equal(headers['x-vision-project'],undefined);assert.equal(headers['x-vision-context'],undefined);
   if(reject){status=403;body={detail:'Project selection refused'};}
   else{server=target;body=target;if(stale)api.setProjectContext({ ...context(A),actor_id:'new-actor'});
    if(editDuringOpen)flow.getState().updateNodeData('roi',{label:'Edited while selecting'});}
  }else if(u.pathname==='/api/project/list')body={projects:[]};
  else if(u.pathname==='/api/flow-workspace/image-info'){
   const wanted=server.source_dataset_dir+'/coordinate-512x256.png';
   status=row.query.image_path===wanted&&headers['x-vision-project']===server.id?200:422;
   body=status===200?{width:512,height:256}:{detail:'Select an existing image from the current project dataset'};
  }else throw Error('Undeclared HTTP boundary: '+row.path);
  row.status=status;row.response_body=structuredClone(body);
  return{ok:status===200,status,statusText:status===200?'OK':'Refused',
   headers:new Headers({'X-Vision-Context':JSON.stringify(context(server))}),json:async()=>structuredClone(body)};
 };
 // Observe actual synchronous store notifications. A selected image is an
 // automatic read consumer, so emitting B+A here exposes the mounted effect's
 // risk before the original annotation await releases downstream invalidation.
 let lastKey='';
 const observe=()=>{
  const selected=flow.getState().selectedImage,key=JSON.stringify([api.getProjectContextGeneration(),dataset.getState().folderPath,selected?.imagePath]);
  observations.push({project:projectStore.getState().project.id,authority:api.getProjectContext(),selected:selected?.imagePath??null});
  if(key===lastKey)return;lastKey=key;if(!selected)return;
  pending.push(api.request('/api/flow-workspace/image-info?image_path='+encodeURIComponent(selected.imagePath))
   .then(()=>({ok:true}),error=>({error:error.message})));
 };
 const stopProject=projectStore.subscribe(observe),stopFlow=flow.subscribe(observe),stopContext=api.subscribeProjectContext(observe);observe();
 t.after(()=>{hold?.resolve();stopProject();stopFlow();stopContext();global.fetch=previousFetch;});
 return{api,flow,projectStore,annotation,dataset,calls,observations,entered,
  release(){hold?.resolve();},settle:async()=>Promise.all(pending),
  capture(name){if(process.env.PROJECT_IMAGE_PROOF_DIR){fs.mkdirSync(process.env.PROJECT_IMAGE_PROOF_DIR,{recursive:true});
   fs.writeFileSync(path.join(process.env.PROJECT_IMAGE_PROOF_DIR,name+'.json'),JSON.stringify({
    source_only:true,React_mount:false,controlled_HTTP_and_annotation_boundary:true,calls,observations,
    selected:flow.getState().selectedImage,visible_project:projectStore.getState().project.id},null,2)+'\n');}}
 };
}

test('accepted A to B clears A before B publication while original annotation clear is pending',async t=>{
 const f=fixture(t,{holdAnnotation:true});await f.settle();
 assert.deepEqual(f.calls.filter(r=>r.path.endsWith('/image-info')).map(r=>[r.headers['x-vision-project'],r.query.image_path,r.status]),[
  ['A','/source/A/coordinate-512x256.png',200]]);
 const opened=f.projectStore.getState().openProject(B.project_dir);
 await f.entered.promise;await f.settle();
 assert.equal(f.projectStore.getState().project.id,'B');assert.deepEqual(f.api.getProjectContext(),context(B));
 const selectedBeforeRelease=f.flow.getState().selectedImage;
 f.capture('accepted-before-annotation-release');f.release();assert.equal(await opened,true);await f.settle();
 assert.ok(f.calls.every(row=>row.status===200),'Original ownership validation must never receive B context with A image');
 assert.equal(selectedBeforeRelease,null,'A selection must be cleared before B notification and its automatic read');
 assert.ok(f.observations.every(row=>row.project!=='B'||row.selected===null));
 f.flow.getState().setSelectedImage(image(B));await f.settle();
 assert.deepEqual(f.calls.filter(r=>r.path.endsWith('/image-info')).map(r=>[r.headers['x-vision-project'],r.query.image_path,r.status]),[
  ['A','/source/A/coordinate-512x256.png',200],['B','/source/B/coordinate-512x256.png',200]]);
});

test('same namespace reopening preserves selected image and adds no automatic image read',async t=>{
 const f=fixture(t,{target:{...A}});await f.settle();const selected=f.flow.getState().selectedImage;
 assert.equal(await f.projectStore.getState().openProject(A.project_dir),true);await f.settle();
 assert.equal(f.flow.getState().selectedImage,selected);
 assert.equal(f.calls.filter(r=>r.path.endsWith('/image-info')).length,1);
 f.capture('same-namespace');
});

for(const [name,options]of [['HTTP refusal',{reject:true}],['stale authority candidate',{stale:true}],['edit while selecting',{editDuringOpen:true}]])
 test(name+' preserves A selection without accepting B',async t=>{
  const f=fixture(t,options);await f.settle();const selected=f.flow.getState().selectedImage;
  assert.equal(await f.projectStore.getState().openProject(B.project_dir),false);
  assert.equal(f.projectStore.getState().project.id,'A');assert.equal(f.flow.getState().selectedImage,selected);
  assert.notEqual(f.api.getProjectContext().project_id,'B');
  if(options.editDuringOpen)assert.equal(f.flow.getState().pipelineDirty,true);
  f.capture(name.replaceAll(' ','-'));
 });

for(const [name,target]of [
 ['source',{...A,source_dataset_dir:'/source/new'}],['labelset',{...A,active_labelset_id:'new-labels'}],
 ['task',{...A,task:'classification'}],['project directory',{...A,project_dir:'/projects/new-A'}],
])test('changed '+name+' clears selection inside accepted publication',async t=>{
 const f=fixture(t,{target});await f.settle();
 assert.equal(await f.projectStore.getState().openProject(target.project_dir),true);await f.settle();
 assert.equal(f.flow.getState().selectedImage,null);
 f.capture('changed-'+name.replaceAll(' ','-'));
});
