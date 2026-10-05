const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const test=require('node:test');
const ts=require('typescript');
const storageValues=new Map();Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>storageValues.get(key)||null,setItem:(key,value)=>storageValues.set(key,value),removeItem:key=>storageValues.delete(key)}});
if(typeof globalThis.window==='undefined')globalThis.window={addEventListener(){},removeEventListener(){}};
function load(relative,mocks={}){
 const filename=path.resolve(__dirname,relative);assert.ok(fs.existsSync(filename),'Team workflow implementation exists');
 const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const require=m.require.bind(m);m.require=name=>{
  if(mocks[name])return mocks[name];
  if(name.startsWith('.')){const local=path.resolve(path.dirname(filename),name);for(const suffix of ['.ts','.tsx'])if(fs.existsSync(local+suffix))return load(path.relative(__dirname,local+suffix),mocks);}
  return require(name);
 };
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);return m.exports;
}
const scope={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'default'},apiTransportIdentity:'local',transportRevision:1};
test('team scope isolates project, source, labelset and server including a reconnect',()=>{
 const {teamDataScope}=load('./teamDataWorkflow.ts');
 for(const next of [{...scope,project:{...scope.project,id:'other'}},{...scope,project:{...scope.project,source_dataset_dir:'/other'}},{...scope,project:{...scope.project,active_labelset_id:'another'}},{...scope,apiTransportIdentity:'shared:https://server'},{...scope,transportRevision:2}])assert.notEqual(teamDataScope(scope),teamDataScope(next));
});
test('lease can only supply a token to the exact image before expiration',()=>{
 const {imageLeaseToken}=load('./teamDataWorkflow.ts');const lease={image_uuid:'image-a',token:'owned',owner:'one',expires_at:120};
 assert.equal(imageLeaseToken(lease,'image-a',119),'owned');assert.equal(imageLeaseToken(lease,'image-b',119),undefined);assert.equal(imageLeaseToken(lease,'image-a',120),undefined);assert.equal(imageLeaseToken(null,'image-a',1),undefined);
});
test('publishing guidance preserves class IDs and rejects duplicate or blank classes',()=>{
 const {validatedBookCategories}=load('./teamDataWorkflow.ts');const row={id:7,name:' Scratch ',color:'#123456',definition:'Surface scratch',inclusion:'',exclusion:'',annotation_guidance:'',examples:[]};
 assert.equal(validatedBookCategories([row])[0].id,7);assert.equal(validatedBookCategories([row])[0].name,'Scratch');
 assert.throws(()=>validatedBookCategories([row,{...row,name:'Other'}]),/ID/);assert.throws(()=>validatedBookCategories([row,{...row,id:8}]),/이름/);assert.throws(()=>validatedBookCategories([{...row,name:' '}]),/이름/);
});
test('saved guidance palette never silently drops existing labels',()=>{
 const {bookPalette}=load('./teamDataWorkflow.ts');const result=bookPalette([{id:4,name:'part',color:'#123456'}],[{id:9,name:'legacy',color:'#654321'}]);
 assert.deepEqual(result.map(x=>x.name),['part','legacy']);assert.equal(result[0].id,4);
});
test('training transition blocks an empty approved cohort and keeps pending work explicit',()=>{
 const {reviewTrainingMessage}=load('./teamDataWorkflow.ts');
 assert.match(reviewTrainingMessage({ready:false,counts:{eligible:0,pending:4,approved:0},blockers:['승인 데이터가 없습니다.']}),/승인 데이터/);
 assert.match(reviewTrainingMessage({ready:true,counts:{eligible:3,pending:2,approved:3},blockers:[]}),/3/);assert.match(reviewTrainingMessage({ready:true,counts:{eligible:3,pending:2,approved:3},blockers:[]}),/2/);
});
test('annotation save sends the valid owned lease and cannot reuse it for a different image',async()=>{
 const bodies=[];const m=load('../../stores/useAnnotationStore.ts',{'../services/api':{api:{},getApiBaseUrl:()=>''},'./useDatasetStore':{useDatasetStore:{getState:()=>({annotationsChanged:async()=>{}})}},'../services/datasetWorkflow':{datasetWorkflow:{saveAnnotations:async body=>{bodies.push(body);return{status:'saved'};}}}});
 const state={currentImage:{image_id:'a',file_path:'/source/a.png',width:10,height:10},annotations:[],metadata:{image_uuid:'a',revision:3},imageDimensions:{width:10,height:10},annotationLoadStatus:'ready',reviewerName:'operator-a',isDirty:true,isSaving:false,teamEditingEnabled:true,editLease:{image_uuid:'a',token:'lease-a',owner:'operator-a',expires_at:Date.now()/1000+60}};
 m.useAnnotationStore.setState(state);assert.equal(await m.useAnnotationStore.getState().saveAnnotations(),true);assert.equal(bodies[0].lease_token,'lease-a');
 m.useAnnotationStore.setState({...state,currentImage:{...state.currentImage,image_id:'b',file_path:'/source/b.png'},metadata:{image_uuid:'b',revision:1}});
 assert.equal(await m.useAnnotationStore.getState().saveAnnotations(),false);assert.equal(bodies.length,1);assert.match(m.useAnnotationStore.getState().saveMessage,/편집 시작/);
});
test('clearing a project drops edit ownership and policy from the previous workspace',async()=>{
 const m=load('../../stores/useAnnotationStore.ts',{'../services/api':{api:{},getApiBaseUrl:()=>''},'./useDatasetStore':{},'../services/datasetWorkflow':{datasetWorkflow:{}}});
 m.useAnnotationStore.setState({isDirty:false,teamEditingEnabled:true,teamReviewEnabled:true,labelbookVersion:3,editLease:{image_uuid:'old',token:'old-token',owner:'old',expires_at:Date.now()/1000+60}});
 await m.useAnnotationStore.getState().setImages([]);const value=m.useAnnotationStore.getState();assert.equal(value.editLease,null);assert.equal(value.teamEditingEnabled,false);assert.equal(value.teamReviewEnabled,false);assert.equal(value.labelbookVersion,null);
});

function hooks(){let cursor=0;const slots=[],effects=[];const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>slots[i]=typeof value==='function'?value(slots[i]):value];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>slots[i].cleanup=fn());}}};return{react,unmount:()=>slots.forEach(row=>row?.cleanup?.()),render:fn=>{cursor=0;const tree=fn();effects.splice(0).forEach(fn=>fn());return tree;}};}
const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(Boolean)});
function nodes(tree){const result=[];const visit=node=>{if(node&&typeof node==='object'){result.push(node);node.children?.flat().forEach(visit);}};visit(tree);return result;}
function panelFixture(workspacePromise){
 let epoch=0;const h=hooks();h.react.useSyncExternalStore=(_subscribe,snapshot)=>snapshot();const project={...scope,setStep:async()=>{}};const compute={transportRevision:1,selectedProfileId:null};const image={image_uuid:'a',file_path:'/source/a.png',relative_path:'a.png',revision:1,team:{edit_lease:null,assignment:null,reviews:[],review_status:'pending'}};
 const annotation={reviewerName:'operator-a',metadata:image,currentImage:{file_path:image.file_path},categories:[],editLease:null,isDirty:false,isSaving:false,setMetadata:value=>annotation.metadata=value,setTeamEditingEnabled:value=>annotation.teamEditingEnabled=value,setTeamReviewEnabled:()=>{},setLabelbook:()=>{},setEditLease:value=>annotation.editLease=value};
 const workspace={scope:{project_id:'p',source:'/source',labelset_id:'default'},book:null,book_history:[],settings:{revision:1,editing_enabled:true,review_enabled:true,required_reviews:2,prevent_self_review:true,approved_only_training:true},members:[]};
 const readiness={ready:false,counts:{total:1,eligible:0,approved:0,pending:1},blockers:['승인 데이터가 없습니다.']};const calls=[];
 const api={workspace:()=>workspacePromise||Promise.resolve(workspace),image:async()=>({image:annotation.metadata}),readiness:async()=>readiness,queue:async()=>({items:[],total:0,offset:0,limit:30}),acquire:async(row,actor)=>{calls.push([row.image_uuid,actor]);return{image:{...image,revision:2,team:{...image.team,edit_lease:{owner:actor,expires_at:Date.now()/1000+120}}},lease_token:'valid-token'};}};
 const store=value=>Object.assign(()=>value,{getState:()=>value});
 const module=load('./TeamDataPanel.tsx',{'react':h.react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{},'../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/useComputeStore':{useComputeStore:store(compute)},'../../stores/useAnnotationStore':{useAnnotationStore:store(annotation)},'../../services/api':{getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>epoch,subscribeProjectContext:()=>()=>{}},'../../services/teamDataApi':{teamDataApi:api},'../../services/datasetWorkflow':{workflowError:error=>error.message||String(error)},'../common/WorkspaceDialog':{},'./LabelbookEditor':{}});
 return{h,project,annotation,calls,api,actorChange:()=>{epoch++;},render:()=>h.render(()=>module.TeamDataPanel())};
}
test('actual team panel edit-start action binds returned token to selected image',async()=>{
 const fixture=panelFixture();fixture.render();await new Promise(setImmediate);let tree=fixture.render();nodes(tree).find(node=>node.type==='button'&&node.children.some(child=>typeof child==='string'&&child.includes('팀 작업'))).props.onClick();tree=fixture.render();await nodes(tree).find(node=>node.type==='button'&&node.children.includes('편집 시작')).props.onClick();
 assert.deepEqual(fixture.calls,[['a','operator-a']]);assert.equal(fixture.annotation.editLease.token,'valid-token');assert.equal(fixture.annotation.metadata.revision,2);fixture.h.unmount();
});
test('a delayed old-project workspace cannot enable editing in a switched project',async()=>{
 let resolve;const pending=new Promise(done=>resolve=done);const fixture=panelFixture(pending);fixture.render();fixture.project.project={...fixture.project.project,id:'new-project'};resolve({book:null,book_history:[],settings:{editing_enabled:true},members:[]});await new Promise(setImmediate);assert.equal(fixture.annotation.teamEditingEnabled,false);assert.equal(fixture.annotation.editLease,null);fixture.h.unmount();
});
test('same-token lease renewal uses the latest expiry after the original deadline',async()=>{
 const oldInterval=globalThis.setInterval,oldClear=globalThis.clearInterval,oldNow=Date.now;const timers=[];
 globalThis.setInterval=fn=>{timers.push(fn);return fn;};globalThis.clearInterval=()=>{};
 const fixture=panelFixture();let renewals=0;fixture.api.renew=async(image,actor,token)=>{renewals++;return{image:{...image,team:{...image.team,edit_lease:{owner:actor,expires_at:Date.now()/1000+120}}},lease_token:token};};
 try{
  fixture.render();await new Promise(setImmediate);let tree=fixture.render();nodes(tree).find(node=>node.type==='button'&&node.children.some(child=>typeof child==='string'&&child.includes('팀 작업'))).props.onClick();tree=fixture.render();await nodes(tree).find(node=>node.type==='button'&&node.children.includes('편집 시작')).props.onClick();await new Promise(setImmediate);fixture.render();
  const firstDeadline=fixture.annotation.editLease.expires_at;fixture.annotation.editLease={...fixture.annotation.editLease,expires_at:firstDeadline+120};Date.now=()=> (firstDeadline+10)*1000;
  timers.at(-1)();await new Promise(setImmediate);assert.equal(renewals,1);assert.equal(fixture.annotation.editLease.token,'valid-token');assert.ok(fixture.annotation.editLease.expires_at>firstDeadline+10);
 }finally{fixture.h.unmount();globalThis.setInterval=oldInterval;globalThis.clearInterval=oldClear;Date.now=oldNow;}
});
test('final adjudication explains approval and prevents another review of the resolved image',async()=>{
 const fixture=panelFixture();fixture.annotation.metadata.team={...fixture.annotation.metadata.team,review_status:'approved',reviews:[{actor:'reviewer-b',decision:'approve'},{actor:'reviewer-c',decision:'reject'}],adjudication:{actor:'reviewer-d',decision:'approve',reason:'Confirmed after reviewer discussion'}};
 fixture.render();await new Promise(setImmediate);let tree=fixture.render();nodes(tree).find(node=>node.type==='button'&&node.children.some(child=>typeof child==='string'&&child.includes('팀 작업'))).props.onClick();tree=fixture.render();
 assert.ok(nodes(tree).some(node=>node.children.includes('Confirmed after reviewer discussion')));
 assert.equal(nodes(tree).find(node=>node.type==='button'&&node.children.includes('승인 표 제출')).props.disabled,true);fixture.h.unmount();
});
test('a delayed lease renewal cannot replace newer saved metadata or review state',async()=>{
 const oldInterval=globalThis.setInterval,oldClear=globalThis.clearInterval;const timers=[];let resolve;
 globalThis.setInterval=fn=>{timers.push(fn);return fn;};globalThis.clearInterval=()=>{};
 const fixture=panelFixture();fixture.api.renew=()=>new Promise(done=>resolve=done);
 try{
  fixture.render();await new Promise(setImmediate);let tree=fixture.render();nodes(tree).find(node=>node.type==='button'&&node.children.some(child=>typeof child==='string'&&child.includes('팀 작업'))).props.onClick();tree=fixture.render();await nodes(tree).find(node=>node.type==='button'&&node.children.includes('편집 시작')).props.onClick();await new Promise(setImmediate);fixture.render();
  const older=fixture.annotation.metadata;timers.at(-1)();
  fixture.annotation.metadata={...older,revision:3,team:{...older.team,review_status:'approved',reviews:[{actor:'reviewer-b',decision:'approve'}]}};
  const expiry=Date.now()/1000+180;resolve({image:{...older,team:{...older.team,edit_lease:{owner:'operator-a',expires_at:expiry}}},lease_token:'valid-token'});await new Promise(setImmediate);
  assert.equal(fixture.annotation.metadata.revision,3);assert.equal(fixture.annotation.metadata.team.review_status,'approved');assert.equal(fixture.annotation.editLease.expires_at,expiry);
 }finally{fixture.h.unmount();globalThis.setInterval=oldInterval;globalThis.clearInterval=oldClear;}
});

test('a delayed same-project team response cannot enable editing after the account namespace changes',async()=>{
 let resolve;const pending=new Promise(done=>resolve=done),f=panelFixture(pending);f.render();f.actorChange();resolve({book:null,book_history:[],settings:{editing_enabled:true},members:[]});await new Promise(setImmediate);assert.equal(f.annotation.teamEditingEnabled,false);assert.equal(f.annotation.editLease,null);f.h.unmount();
});
