const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function fixture(){
 let epoch=1,reads=0,saves=0,store;const image={image_id:'part',file_path:'/source/part.png',width:256,height:256};let saved=[];
 const dataset={folderPath:'/source',annotationsChanged:async()=>{await store.getState().syncDatasetImages([{...image}]);}};
 const file=path.join(__dirname,'useAnnotationStore.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 const mocks={'../services/api':{api:{},getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>epoch},
  '../services/datasetWorkflow':{datasetWorkflow:{annotations:async(_id,file_path)=>{reads++;return{image_id:'part',image_width:256,image_height:256,annotations:saved,metadata:{image_uuid:'part',file_path,revision:1}};},saveAnnotations:async body=>{saves++;saved=body.annotations;return{status:'saved',metadata:{image_uuid:'part',file_path:image.file_path,revision:2}};}}},
  './useDatasetStore':{useDatasetStore:{getState:()=>dataset}},'../components/labeling/foundationRequest':{labelCategoryPalette:categories=>categories},
  '../components/labeling/convertedAnnotation':{},'../components/labeling/teamDataWorkflow':{imageLeaseToken:()=>null}};
 m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:req(n);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);store=m.exports.useAnnotationStore;
 store.setState({currentImage:image,activeImage:image,images:[image],currentImageIndex:0,task:'segmentation',imageDimensions:{width:256,height:256}});
 return{store,image,dataset,reads:()=>reads,saves:()=>saves,changeActor(){epoch++;},async ready(){await store.getState().loadAnnotationsForCurrent();},edit(){store.getState().addAnnotation({id:'shape',type:'bbox',label:'Scratch',category_id:2,bbox:[20,20,60,60]});}};
}
test('saving and refreshing the same gallery keeps undo, selected object and image identity',async()=>{const f=fixture();await f.ready();f.edit();const before=f.store.getState();assert.equal(await before.saveAnnotations(),true);await new Promise(setImmediate);const after=f.store.getState();assert.equal(after.history,before.history);assert.equal(after.annotations,before.annotations);assert.equal(after.currentImage,before.currentImage);assert.equal(after.selectedAnnotationId,before.selectedAnnotationId);assert.equal(f.reads(),1);after.undo();assert.equal(f.store.getState().annotations.length,0);f.store.getState().redo();assert.equal(f.store.getState().annotations[0].id,'shape');});
test('a same-image gallery response cannot autosave or discard a newer dirty draft',async()=>{const f=fixture();await f.ready();f.edit();const before=f.store.getState();await before.syncDatasetImages([{...f.image}]);const after=f.store.getState();assert.equal(after.isDirty,true);assert.equal(after.history,before.history);assert.equal(after.annotations,before.annotations);assert.equal(f.saves(),0);});
test('the same path under another actor generation reloads labels and clears old undo',async()=>{const f=fixture();await f.ready();f.edit();await f.store.getState().saveAnnotations();await new Promise(setImmediate);f.changeActor();await f.store.getState().syncDatasetImages([{...f.image}]);assert.equal(f.reads(),2);assert.equal(f.store.getState().history.length,0);});
test('a different source image still performs a fresh annotation read',async()=>{const f=fixture();await f.ready();await f.store.getState().syncDatasetImages([{...f.image,file_path:'/other/part.png'}]);assert.equal(f.reads(),2);assert.equal(f.store.getState().currentImage.file_path,'/other/part.png');});
test('changing the task or source folder cannot retain the prior editing history',async()=>{for(const change of [f=>f.store.getState().setTask('detection'),f=>{f.dataset.folderPath='/new-source';}]){const f=fixture();await f.ready();f.edit();await f.store.getState().saveAnnotations();await new Promise(setImmediate);change(f);await f.store.getState().syncDatasetImages([{...f.image}]);assert.equal(f.reads(),2);assert.equal(f.store.getState().history.length,0);}});

test('empty source cannot create vector geometry or dirty history from a pointer completion',async()=>{
 const f=fixture();await f.ready();f.store.setState({currentImage:null,activeImage:null,images:[],currentImageIndex:-1});
 const before=f.store.getState();assert.equal(before.annotationLoadStatus,'ready');
 for(const item of [{id:'box',type:'bbox',label:'Scratch',category_id:2,bbox:[20,20,60,60]},{id:'poly',type:'polygon',label:'Scratch',category_id:2,polygon:[[20,20],[60,20],[40,60]],points:[[20,20],[60,20],[40,60]]},{id:'obb',type:'rotated_bbox',label:'Scratch',category_id:2,rotated_bbox:[40,40,40,40,30],direction_deg:315}]){
  before.addAnnotation(item);assert.equal(f.store.getState(),before,'Source-unbound vector completion changed original annotation state');
 }
 assert.equal(f.saves(),0);assert.equal(f.reads(),1);
});
test('a loaded original image still admits all three vector geometries and retains undo',async()=>{
 const f=fixture();await f.ready();
 for(const item of [{id:'box',type:'bbox',label:'Scratch',category_id:2,bbox:[20,20,60,60]},{id:'poly',type:'polygon',label:'Scratch',category_id:2,polygon:[[20,20],[60,20],[40,60]],points:[[20,20],[60,20],[40,60]]},{id:'obb',type:'rotated_bbox',label:'Scratch',category_id:2,rotated_bbox:[40,40,40,40,30],direction_deg:315}])f.store.getState().addAnnotation(item);
 const after=f.store.getState();assert.equal(after.currentImage,f.image);assert.deepEqual(after.annotations.map(row=>row.id),['box','poly','obb']);assert.equal(after.history.length,3);after.undo();assert.deepEqual(f.store.getState().annotations.map(row=>row.id),['box','poly']);assert.equal(f.saves(),0);assert.equal(f.reads(),1);
});
