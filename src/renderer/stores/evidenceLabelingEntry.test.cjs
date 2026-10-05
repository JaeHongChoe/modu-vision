const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,mocks){const name=path.resolve(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:req(n);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const value=initial=>({getState:()=>initial,setState:update=>Object.assign(initial,update)});
function fixture(){
 const meta={file_path:'/source/part.png',content_hash:'a'.repeat(64),revision:7};let identity='local',epoch=1,reads=0,applications=0;
 const annotation=value({isDirty:false,annotationLoadStatus:'ready',currentImage:null,metadata:null,setImages:async images=>{applications++;Object.assign(annotation.getState(),{currentImage:images[0],metadata:{...meta}});return true;}});
 const dataset=value({folderPath:'/source',images:[{image_id:'part',file_path:meta.file_path}]});
 const mocks={'../services/api':{api:{},getApiPersistenceIdentity:()=>identity,getProjectContextGeneration:()=>epoch,getProjectContext:()=>null},
  '../services/datasetWorkflow':{datasetWorkflow:{image:async()=>{reads++;return {...meta};}},workflowError:e=>e.message},
  './projectViewState':{projectViewScope:p=>p?.id,rememberProjectStep(){}},'./useAnnotationStore':{useAnnotationStore:annotation},'./useDatasetStore':{useDatasetStore:dataset},
  './useFlowchartStore':{useFlowchartStore:value({})},'./useTrainingStore':{useTrainingStore:value({})},'./useInspectionRunStore':{useInspectionRunStore:value({})},'./useModelAssistRunStore':{useModelAssistRunStore:value({})}};
 const store=load('./useProjectStore.ts',mocks).useProjectStore;store.setState({project:{id:'p',project_dir:'/project',task:'classification',source_dataset_dir:'/source',active_labelset_id:'default'},projectDir:'/project',task:'classification',activeStep:4});
 return{store,mocks,meta,annotation,dataset,applications:()=>applications,reads:()=>reads,changeApi(){identity='other';epoch++;},open:()=>store.getState().openImageForLabeling('part',meta.file_path,{imageSha256:'a'.repeat(64),revision:7})};
}
test('actual project store verifies gallery image metadata before entering current-label mode',async()=>{const f=fixture();assert.equal(await f.open(),true);assert.equal(f.reads(),1);assert.equal(f.applications(),1);assert.equal(f.store.getState().activeStep,2);});
test('changed image hash or revision refuses before replacing the selected annotation',async()=>{for(const change of [{content_hash:'b'.repeat(64)},{revision:8}]){const f=fixture();Object.assign(f.meta,change);assert.equal(await f.open(),false);assert.equal(f.applications(),0);assert.equal(f.store.getState().activeStep,4);}});
test('API authority change during exact metadata read cannot open old labels in the new authority',async()=>{const f=fixture();f.mocks['../services/datasetWorkflow'].datasetWorkflow.image=async()=>{f.changeApi();return {...f.meta};};assert.equal(await f.open(),false);assert.equal(f.applications(),0);assert.equal(f.store.getState().activeStep,4);});
test('annotation read changed after entry check remains blocked and does not navigate into labeling',async()=>{const f=fixture();f.annotation.getState().setImages=async images=>{f.annotation.setState({currentImage:images[0],metadata:{...f.meta,revision:8}});return true;};assert.equal(await f.open(),false);assert.equal(f.store.getState().activeStep,4);assert.equal(f.annotation.getState().annotationLoadStatus,'error');});
test('actual annotation store ignores a delayed old-account image read even with the same image object',async()=>{
 let release,epoch=1;const pending=new Promise(resolve=>release=resolve);
 const m=load('./useAnnotationStore.ts',{'../services/api':{api:{},getApiBaseUrl:()=>'',getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>epoch},
  '../services/datasetWorkflow':{datasetWorkflow:{annotations:()=>pending}},'./useDatasetStore':{},
  '../components/labeling/foundationRequest':{labelCategoryPalette:()=>[]},'../components/labeling/convertedAnnotation':{},'../components/labeling/teamDataWorkflow':{}});
 const store=m.useAnnotationStore;store.setState({currentImage:{image_id:'part',file_path:'/source/part.png'},isDirty:false});
 const reading=store.getState().loadAnnotationsForCurrent();epoch++;release({image_id:'part',annotations:[{id:'old-actor'}],metadata:{revision:7}});
 assert.equal(await reading,false);assert.deepEqual(store.getState().annotations,[]);assert.equal(store.getState().metadata,null);
});

test('a gallery bound to another source refuses captured-evidence label entry',async()=>{const f=fixture();f.dataset.getState().folderPath='/other';assert.equal(await f.open(),false);assert.equal(f.applications(),0);assert.equal(f.store.getState().activeStep,4);});
