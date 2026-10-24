// The drawing class is a project class: the project's classes (the dataset's counts, most used first) join the palette,
// the most used one is the default, and the built-in example names never become the class a box is saved under. A
// project with no class yet draws under the neutral stand-in 'defect'. Runs the real store, palette and label-book code.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function compile(file,mocks={}){
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);
 m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;
}
const labeling=path.join(__dirname,'../components/labeling');
const palette=compile(path.join(labeling,'foundationRequest.ts')),team=compile(path.join(labeling,'teamDataWorkflow.ts'));
function fixture({task='detection',classes={},labels=[],maskClasses}={}){
 const image={image_id:'part_00',file_path:'/source/part_00.png',width:128,height:128};let current=labels;
 const dataset={folderPath:'/source',classes};
 const exported=compile(path.join(__dirname,'useAnnotationStore.ts'),{'../services/api':{api:{},getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>1},
  '../services/datasetWorkflow':{datasetWorkflow:{annotations:async(_id,file_path)=>({image_id:'part_00',image_width:128,image_height:128,annotations:current,...(maskClasses?{mask_classes:maskClasses}:{}),metadata:{image_uuid:'part_00',file_path,revision:1}})}},
  './useDatasetStore':{useDatasetStore:{getState:()=>dataset}},'../components/labeling/foundationRequest':palette,
  '../components/labeling/convertedAnnotation':{},'../components/labeling/teamDataWorkflow':team});
 const store=exported.useAnnotationStore;
 store.setState({currentImage:image,activeImage:image,images:[image],currentImageIndex:0,task,imageDimensions:{width:128,height:128}});
 return{store,exported,dataset,setLabels(next){current=next;},async load(){assert.equal(await store.getState().loadAnnotationsForCurrent(),true);return store.getState();}};
}
const names=state=>state.categories.map(c=>c.name);

test('the QA project draws under its own class: OK and defect are the chips and defect is the drawing class',async()=>{
 const f=fixture({classes:{defect:24}});const state=await f.load();
 assert.deepEqual(names(state),['OK','defect']);
 assert.deepEqual([state.activeCategory.id,state.activeCategory.name,state.currentLabel],[1,'defect','defect']);
 assert.ok(!names(state).includes('Solder Bridge'),'an example name is never in the palette by itself');
});

test('the most used class is the default, and a class the person picks stays picked on the next image',async()=>{
 const f=fixture({classes:{dent:2,scratch:10}});let state=await f.load();
 assert.equal(state.activeCategory.name,'scratch');assert.deepEqual(names(state),['OK','scratch','dent']);
 state.setActiveCategory(state.categories.find(c=>c.name==='dent'));state=await f.load();
 assert.equal(state.activeCategory.name,'dent');
});

test('a project with no class yet draws under the neutral stand-in, which leaves once the project has its own classes',async()=>{
 const f=fixture({classes:{}});let state=await f.load();
 assert.deepEqual(names(state),['OK','defect']);assert.deepEqual([state.activeCategory.id,state.activeCategory.name],[1,'defect']);
 f.dataset.classes={scratch:3};state=await f.load();
 assert.deepEqual(names(state),['OK','scratch']);assert.equal(state.activeCategory.name,'scratch');
});

test('the counts the import summary invents are not classes: segmentation defect_mask, anomaly good',async()=>{
 let state=await fixture({task:'segmentation',classes:{defect_mask:1}}).load();
 assert.deepEqual(names(state),['OK','defect']);assert.equal(state.activeCategory.name,'defect');
 state=await fixture({task:'anomaly',classes:{good:40,defect:6}}).load();
 assert.deepEqual(names(state),['OK','defect']);assert.equal(state.activeCategory.name,'defect');
 state=await fixture({task:'anomaly',classes:{good:40}}).load();
 assert.deepEqual(names(state),['OK','defect'],'only normal images: the stand-in, never good');
});

test('classification regions default to the defect folder class, never the OK tag',async()=>{
 const state=await fixture({task:'classification',classes:{OK:30,NG:20}}).load();
 assert.deepEqual(names(state),['OK','NG']);assert.deepEqual([state.activeCategory.id,state.activeCategory.name],[1,'NG']);
});

test('an image\'s own labels still lead the palette and keep their ids',async()=>{
 const f=fixture({classes:{defect:24},labels:[{id:'a',type:'bbox',label:'defect',category_id:1,bbox:[1,1,9,9]}]});const state=await f.load();
 assert.deepEqual(state.categories.map(c=>[c.id,c.name]),[[1,'defect'],[0,'OK']]);assert.equal(state.activeCategory.name,'defect');
});

test('clearing the label book before the first load leaves the drawing class alone; a book decides the classes',async()=>{
 let f=fixture({classes:{defect:24}});const {FIRST_CLASS}=f.exported;
 f.store.getState().setLabelbook(null);assert.equal(f.store.getState().activeCategory,FIRST_CLASS,'never the OK tag');
 let state=await f.load();assert.equal(state.activeCategory.name,'defect');
 f=fixture({classes:{dent:5}});f.store.getState().setLabelbook(2,[{id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]);
 assert.equal(f.store.getState().activeCategory.name,'Scratch','a book class that is not the OK tag');
 state=await f.load();assert.ok(!names(state).includes('dent'),'a book sets the classes itself');assert.equal(state.activeCategory.name,'Scratch');
});

test('an example name added from the bar keeps its own id while it is free, and is then the drawing class',async()=>{
 let f=fixture({classes:{defect:24}});await f.load();
 f.store.getState().addCategory('Crack','#ef4444',3);
 assert.deepEqual([f.store.getState().activeCategory.id,f.store.getState().activeCategory.name],[3,'Crack']);
 f=fixture({classes:{defect:24}});await f.load();
 f.store.getState().addCategory('Solder Bridge','#3b82f6',1);
 assert.equal(f.store.getState().activeCategory.id,2,'id 1 is taken by defect: the next id after the highest');
 f=fixture({classes:{defect:24}});await f.load();
 f.store.getState().addCategory('Crack','#ef4444',3);f.store.getState().addCategory('Solder Bridge','#3b82f6',1);
 assert.equal(f.store.getState().activeCategory.id,4,'with ids 0, 1 and 3 taken: the next id after the highest');
});

test('emptying the gallery (project or label set switch) brings back the palette and drawing class of a fresh project',async()=>{
 const f=fixture({classes:{scratch:3}});await f.load();assert.equal(f.store.getState().activeCategory.name,'scratch');
 await f.store.getState().setImages([]);const state=f.store.getState();
 assert.deepEqual(names(state),['OK']);assert.equal(state.activeCategory,f.exported.FIRST_CLASS);assert.equal(state.currentLabel,'defect');
});

// Found in review: the stand-in is the store's own marked copy, so a class a person names 'defect' is a real class.
test('a class the person adds and names defect stays in the palette and stays the drawing class',async()=>{
 const f=fixture({classes:{scratch:3}});await f.load();
 f.store.getState().addCategory('defect');assert.deepEqual([f.store.getState().activeCategory.id,f.store.getState().activeCategory.name],[2,'defect']);
 const state=await f.load();
 assert.deepEqual(names(state),['OK','scratch','defect']);assert.equal(state.activeCategory.name,'defect');
});

test('with nothing picked, the open image\'s own classes come before the dataset counts',async()=>{
 // A segmentation project labelled only with Scratch masks: the import counts each mask image as 'defect'.
 const state=await fixture({task:'segmentation',classes:{defect:5},maskClasses:[{id:2,name:'Scratch',color:'#f59e0b'}]}).load();
 assert.equal(state.activeCategory.name,'Scratch');
});

test('a normal-class name is a chip but never the default drawing class',async()=>{
 let state=await fixture({task:'classification',classes:{good:300,scratch:40}}).load();
 assert.deepEqual(names(state),['OK','good','scratch']);assert.equal(state.activeCategory.name,'scratch');
 state=await fixture({task:'classification',classes:{'정상':40}}).load();
 assert.deepEqual(state.categories.map(c=>[c.id,c.name]),[[0,'OK'],[1,'정상'],[2,'defect']],'only a normal class: the stand-in joins under a free id');
 assert.deepEqual([state.activeCategory.id,state.activeCategory.name],[2,'defect']);
});

test('another source folder starts a fresh palette; a label book keeps its classes',async()=>{
 let f=fixture({classes:{scratch:3}});await f.load();assert.equal(f.store.getState().activeCategory.name,'scratch');
 Object.assign(f.dataset,{folderPath:'/other',classes:{dent:9}});
 let state=await f.load();assert.deepEqual(names(state),['OK','dent']);assert.equal(state.activeCategory.name,'dent');
 f=fixture({classes:{dent:5}});f.store.getState().setLabelbook(2,[{id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]);await f.load();
 f.dataset.folderPath='/other';state=await f.load();
 assert.deepEqual(names(state),['OK','Scratch'],'the book decides the classes');assert.equal(state.activeCategory.name,'Scratch');
});

// Found in re-review: drawing with the stand-in, or picking it, makes it a real class, and the default follows each image
// until the person picks or draws.
test('the stand-in drawn with or picked is a real class and stays on the next images',async()=>{
 let f=fixture({task:'classification',classes:{'정상':40}});let state=await f.load();
 assert.equal(state.activeCategory.name,'defect');
 state.addCategory('scratch');f.store.getState().setActiveCategory(f.store.getState().categories.find(c=>c.name==='defect'));
 state=await f.load();assert.deepEqual(names(state),['OK','정상','defect','scratch']);assert.equal(state.activeCategory.name,'defect','picked');
 f=fixture({classes:{}});await f.load();
 f.store.getState().addAnnotation({id:'box',type:'bbox',label:'defect',category_id:1,bbox:[1,1,9,9]});
 assert.equal(f.exported.isStandInCategory(f.store.getState().activeCategory),false,'drawn with: no longer the stand-in');
 // Saved before the next image (moving on saves), then the counts arrive without the drawn class yet.
 f.store.setState({isDirty:false});f.dataset.classes={scratch:1};state=await f.load();
 assert.deepEqual(names(state),['OK','defect','scratch']);assert.equal(state.activeCategory.name,'defect','the class the person drew with');
});

test('until the person picks or draws, each image gets its own default',async()=>{
 const f=fixture({task:'segmentation',classes:{defect:5}});let state=await f.load();
 assert.equal(state.activeCategory.name,'defect');assert.equal(state.classPicked,false);
 // The next image holds Scratch masks: its own class, not the class the previous image defaulted to.
 const next=fixture({task:'segmentation',classes:{defect:5},maskClasses:[{id:2,name:'Scratch',color:'#f59e0b'}]});
 next.store.setState({categories:state.categories,activeCategory:state.activeCategory,classPicked:false,paletteContext:state.paletteContext});
 state=await next.load();assert.equal(state.activeCategory.name,'Scratch');
});

test('normal-class names by the shared class rules: the store and utils/classSemantics agree on every case',()=>{
 const {isNormalClassName}=fixture().exported;
 const semantics=compile(path.join(__dirname,'../utils/classSemantics.ts'));
 const shared=JSON.parse(fs.readFileSync(path.join(__dirname,'../utils/classSemanticsCases.json'),'utf8')).cases;
 const extra=['good_parts','OK_images','Normal samples','정상_이미지','불량','not_ok','no defect','scratch','dent','OK NG'];
 for(const name of [...shared.map(c=>c.name),...extra])assert.equal(isNormalClassName(name),semantics.classRole(name)==='normal',name);
 for(const c of shared)assert.equal(isNormalClassName(c.name),c.role==='normal',`shared case ${c.name}`);
});

test('a label book never makes a normal class the drawing class by default',()=>{
 const f=fixture({classes:{}});
 f.store.getState().setLabelbook(3,[{id:0,name:'OK',color:'#10b981'},{id:1,name:'good',color:'#3b82f6'},{id:2,name:'scratch',color:'#f59e0b'}]);
 assert.equal(f.store.getState().activeCategory.name,'scratch');
});

// Found in the final check: letter case, a typed duplicate of the stand-in, and a stand-in the project comes to own.
test('a pick survives another spelling of the same class, and typing an existing name picks that class',async()=>{
 let f=fixture({classes:{dent:50,Scratch:10}});await f.load();
 f.store.getState().setActiveCategory(f.store.getState().categories.find(c=>c.name==='Scratch'));
 f.setLabels([{id:'a',type:'bbox',label:'scratch',category_id:7,bbox:[1,1,9,9]}]);let state=await f.load();
 assert.equal(state.activeCategory.name.toLowerCase(),'scratch');assert.equal(state.classPicked,true);
 f.setLabels([]);state=await f.load();assert.equal(state.activeCategory.name.toLowerCase(),'scratch','not the most used dent');
 f=fixture({task:'segmentation',classes:{defect_mask:3}});state=await f.load();
 assert.equal(f.exported.isStandInCategory(state.activeCategory),true);
 state.addCategory('Defect');state=f.store.getState();
 assert.deepEqual(state.categories.map(c=>[c.id,c.name]),[[0,'OK'],[1,'defect']],'no second class under another id');
 assert.equal(f.exported.isStandInCategory(state.activeCategory),false);assert.equal(state.classPicked,true);
});

test('a stand-in the project comes to own is a real class: the note goes',async()=>{
 const f=fixture({classes:{}});let state=await f.load();
 assert.equal(f.exported.isStandInCategory(state.activeCategory),true);
 f.dataset.classes={defect:5};state=await f.load();
 assert.deepEqual(names(state),['OK','defect']);assert.equal(state.activeCategory.name,'defect');
 assert.equal(f.exported.isStandInCategory(state.activeCategory),false);
 assert.ok(state.categories.every(c=>!f.exported.isStandInCategory(c)));
});
