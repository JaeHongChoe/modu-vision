// A new accepted namespace must never expose the previous image to synchronous
// consumers (including TeamDataPanel). Real API acceptance and annotation/store
// transitions; only HTTP replies, preparation timing and unrelated stores differ.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const Module=require('node:module'),test=require('node:test'),ts=require('typescript');
const storage=new Map();Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
const A={id:'A',name:'A',project_dir:'/projects/A',source_dataset_dir:'/source/A',task:'segmentation',active_labelset_id:'labels-A'};
const B={id:'B',name:'B',project_dir:'/projects/B',source_dataset_dir:'/source/B',task:'segmentation',active_labelset_id:'labels-B'};
const context=p=>({workspace_id:'workspace',project_id:p.id,actor_id:'actor',mode:'local'});
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};}
function fixture(t,{rejectPreparation=false}={}){
 storage.clear();storage.set('modu-annotation-draft:v1:old-A','unsent original A draft');
 const dependencies={},cache=new Map();
 function load(relative){const filename=path.resolve(__dirname,relative);if(cache.has(filename))return cache.get(filename).exports;
  const m=new Module(filename,module);cache.set(filename,m);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const original=m.require.bind(m);
  m.require=name=>{if(Object.hasOwn(dependencies,name))return dependencies[name];if(name.startsWith('.')){const local=path.resolve(path.dirname(filename),name);for(const suffix of ['.ts','.tsx'])if(fs.existsSync(local+suffix))return load(path.relative(__dirname,local+suffix));}return original(name);};
  m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);return m.exports;
 }
 const state=value=>({getState:()=>value,setState:update=>Object.assign(value,typeof update==='function'?update(value):update)});
 const api=load('../services/api.ts');dependencies['../services/api']=api;dependencies['./api']=api;
 const dataset=state({hasSelectedFolder:true,folderPath:A.source_dataset_dir,datasetKey:A.source_dataset_dir+'\0segmentation',importError:null,isLoading:false,isSplitting:false,isGenerating:false,classes:{},setFolderPath(value){dataset.getState().folderPath=value;},ensureImported:async()=>{}});
 dependencies['./useDatasetStore']={useDatasetStore:dataset};
 const annotation=load('./useAnnotationStore.ts').useAnnotationStore;dependencies['./useAnnotationStore']={useAnnotationStore:annotation};
 const flow=state({pipelineDirty:false,isLoading:false,isSaving:false,isRunning:false,selectedImage:null});
 dependencies['./useFlowchartStore']={useFlowchartStore:flow};dependencies['./useTrainingStore']={useTrainingStore:state({isTraining:false})};dependencies['./useInspectionRunStore']={useInspectionRunStore:state({isRunning:false})};dependencies['./useModelAssistRunStore']={useModelAssistRunStore:state({activeOperations:0})};
 const project=load('./useProjectStore.ts').useProjectStore,prepared=deferred(),release=deferred();let server=A;
 const previousFetch=global.fetch;global.fetch=async(url,options={})=>{const route=new URL(url).pathname;let body;
  if(route==='/api/project/current')body=server;else if(route==='/api/project/open'){server=B;body=B;}else if(route==='/api/project/list')body={projects:[]};else throw Error('Unexpected HTTP boundary '+route);
  return{ok:true,status:200,headers:new Headers({'X-Vision-Context':JSON.stringify(context(server))}),json:async()=>structuredClone(body)};
 };
 api.setSharedApiBase('https://owned.invalid',()=>({credentials:'include'}));api.setProjectContext(context(A));
 api.api.project.prepareContext=async()=>{prepared.resolve();await release.promise;if(rejectPreparation)throw Error('Preparation refused');};
 project.setState({project:A,projectDir:A.project_dir,projectName:A.name,task:A.task,activeStep:1});
 const oldImage={image_id:'part-A',file_path:'/source/A/part.png',width:256,height:256},oldMetadata={image_uuid:'old-A-uuid',revision:3,team:{reviews:[{actor:'reviewer-A',decision:'approve'}]}};
 annotation.setState({images:[oldImage],currentImageIndex:0,currentImage:oldImage,activeImage:oldImage,externalSelectionPath:oldImage.file_path,metadata:oldMetadata,loadedAnnotationContext:'old-A-context',annotations:[{id:'saved-A',type:'bbox',bbox:[2,3,20,21]}],selectedAnnotationId:'saved-A',history:[{saved:'A'}],future:[{future:'A'}],isDirty:false,editLease:{image_uuid:'old-A-uuid',token:'old-client-lease',owner:'owner-A',expires_at:9999999999},editLeaseAcquiring:{image_uuid:'old-A-uuid'},teamEditingEnabled:true,teamReviewEnabled:true});
 const picks=()=>{const s=annotation.getState();return Object.fromEntries(['images','currentImageIndex','currentImage','activeImage','externalSelectionPath','metadata','loadedAnnotationContext','annotations','selectedAnnotationId','history','future','editLease','editLeaseAcquiring','teamEditingEnabled','teamReviewEnabled','isDirty'].map(k=>[k,s[k]]));};
 const before=picks(),seen=[];
 const inspect=frontier=>{if(project.getState().project?.id==='B')seen.push({frontier,context:api.getProjectContext(),view:picks()});};
 const stopProject=project.subscribe(()=>inspect('project-publication')),stopContext=api.subscribeProjectContext(()=>inspect('context-notification'));
 t.after(()=>{release.resolve();stopProject();stopContext();global.fetch=previousFetch;});
 return{api,project,annotation,prepared,release,picks,before,seen,oldMetadata,open:()=>project.getState().openProject(B.project_dir)};
}
test('accepted project publication retires old annotation image custody before any consumer sees the new scope',async t=>{
 const f=fixture(t),opening=f.open();await f.prepared.promise;assert.deepEqual(f.picks(),f.before,'held preparation retains the original image, saved metadata, votes and client lease');assert.equal(f.project.getState().project.id,'A');
 f.release.resolve();assert.equal(await opening,true);assert.ok(f.seen.length>=2);
 for(const row of f.seen){assert.equal(row.context.project_id,'B');assert.equal(row.view.currentImage,null,row.frontier+' must not expose old image');assert.equal(row.view.activeImage,null);assert.equal(row.view.metadata,null,row.frontier+' must not send old UUID under B');assert.equal(row.view.loadedAnnotationContext,null);assert.equal(row.view.externalSelectionPath,null);assert.deepEqual(row.view.images,[]);assert.deepEqual(row.view.annotations,[]);assert.deepEqual(row.view.history,[]);assert.deepEqual(row.view.future,[]);assert.equal(row.view.currentImageIndex,-1);assert.equal(row.view.selectedAnnotationId,null);assert.equal(row.view.editLease,null);assert.equal(row.view.editLeaseAcquiring,null);assert.equal(row.view.teamEditingEnabled,false);assert.equal(row.view.teamReviewEnabled,false);}
 assert.deepEqual(f.oldMetadata.team.reviews,[{actor:'reviewer-A',decision:'approve'}],'retiring client view does not edit saved review data');
});
for(const refusal of ['preparation','new-actor','late-edit'])test(`a ${refusal} refusal retains old image custody without accepting the new namespace`,async t=>{
 const f=fixture(t,{rejectPreparation:refusal==='preparation'}),opening=f.open();await f.prepared.promise;
 if(refusal==='new-actor')f.api.setProjectContext({...context(A),actor_id:'new-actor'});
 if(refusal==='late-edit')f.annotation.setState({isDirty:true});
 const before=f.picks(),draft=storage.get('modu-annotation-draft:v1:old-A');f.release.resolve();assert.equal(await opening,false);assert.equal(f.project.getState().project.id,'A');assert.deepEqual(f.picks(),before);assert.equal(storage.get('modu-annotation-draft:v1:old-A'),draft);assert.equal(f.seen.length,0);assert.ok(f.project.getState().projectError);
});
