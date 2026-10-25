const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
const deferred=()=>{let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no;});return{promise,resolve,reject};};
function load(filename,mocks){const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const original=m.require.bind(m);m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:original(name);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,filename);return m.exports;}
function harness(validationFixture){
 const project={task:'anomaly',language:'ko',projectDir:'/projects/A',project:{id:'A',project_dir:'/projects/A',task:'anomaly',source_dataset_dir:'/source',active_labelset_id:'default'}};
 const reads=[],writes=[],pending=[];let cursor=0,contextGeneration=0;const slots=[],effects=[];
 const same=(a,b)=>a&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index]));
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useCallback(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps))slots[i]={deps,fn};return slots[i].fn;},useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const noWrite=async data=>{writes.push(data);throw Error('Read-only source restoration cannot write');};
 const api={project:{update:noWrite},dataset:{import:noWrite,currentSummary:request=>{reads.push({project_id:project.project.id,...request});const value=deferred();pending.push(value);return value.promise;},getImages:async()=>({images:[],total:0})},datasetImports:{revisions:async()=>{if(validationFixture?.readRevisions)return validationFixture.readRevisions({...project.project});if(validationFixture?.revisionError)throw Error('revision unavailable');return validationFixture?{revisions:[validationFixture.revision],active_revision:validationFixture.revision.revision_id}:{revisions:[],active_revision:null};},sourceStatus:async id=>validationFixture.readStatus?validationFixture.readStatus(id):validationFixture.current}};
 const inert={getState:()=>({invalidateForDataChange(){}}),setState(){}};
 const apiModule={api,getProjectContext:()=>({workspace_id:'workspace',project_id:project.project.id,actor_id:'actor',mode:'local'}),getProjectContextGeneration:()=>contextGeneration,getApiPersistenceIdentity:()=> 'local',resolveApiUrl:x=>x};
 const datasetModule=load(path.resolve(__dirname,'../../stores/useDatasetStore.ts'),{'../services/api':apiModule,'./useTrainingStore':{useTrainingStore:inert},'./useEvaluationStore':{useEvaluationStore:inert},'./useFlowchartStore':{useFlowchartStore:inert},'./useProjectStore':{useProjectStore:{getState:()=>project,setState(){throw Error('Restore must not update the project');}}}}),store=datasetModule.useDatasetStore;
 store.setState({folderPath:'/source',hasSelectedFolder:true,datasetKey:'/source\0anomaly',sourceAuthority:datasetModule.readDatasetAuthority(),lastImportedKey:'/source\0anomaly',totalImages:2});
 const hook=object=>Object.assign(selector=>selector?selector(object):object,{getState:()=>object});
 const file=path.join(__dirname,'DatasetStudio.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const shared=load(path.resolve(__dirname,'../../stores/assistContext.ts'),{});
 const helpers={'./classDistribution':load(path.join(__dirname,'classDistribution.ts'),{}),'./datasetImportView':load(path.join(__dirname,'datasetImportView.ts'),{}),'./splitCalibrator':load(path.join(__dirname,'splitCalibrator.ts'),{}),'./datasetLayout':load(path.join(__dirname,'datasetLayout.ts'),{}),'../../utils/datasetSplitCapability':load(path.resolve(__dirname,'../../utils/datasetSplitCapability.ts'),{}),'../../stores/assistContext':shared,'../common/useScopedError':load(path.resolve(__dirname,'../common/useScopedError.ts'),{react})};
 const jsx=(type,props)=>({type,props});m.require=name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='../../stores/useDatasetStore'?{readDatasetAuthority:datasetModule.readDatasetAuthority,useDatasetStore:Object.assign(selector=>selector?selector(store.getState()):store.getState(),{getState:store.getState})}:name==='../../stores/useProjectStore'?{useProjectStore:hook(project)}:name==='../../services/api'?apiModule:name==='../runtime/useDeliveryScope'?{useDeliveryScope:()=>({key:JSON.stringify([project.project,contextGeneration])})}:Object.hasOwn(helpers,name)?helpers[name]:name.startsWith('.')?new Proxy({},{get:()=>()=>null}):original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);
 const render=()=>{cursor=0;const tree=m.exports.DatasetStudio();effects.splice(0).forEach(fn=>fn());return tree;};
 const switchProject=id=>{project.projectDir=`/projects/${id}`;project.project={...project.project,id,project_dir:project.projectDir};store.getState().setFolderPath('');store.getState().setFolderPath('/source');};
 const summary=total=>({total_images:total,classes:{},split:{train:0,val:0,test:0}});
 return{store,project,shared,render,switchProject,reads,writes,pending,summary,advanceContext:()=>{contextGeneration++;},authority:datasetModule.readDatasetAuthority,unmount:()=>slots.forEach(slot=>slot?.cleanup?.())};
}

test('failed source restoration is readable in common guidance even before source acceptance, then clears with the error',async()=>{
 const h=harness();h.render();h.switchProject('B');h.render();await tick();h.pending[0].reject(Error('저장 원본을 읽지 못했습니다. 폴더를 다시 선택하세요.'));await tick();h.render();
 assert.equal(h.store.getState().sourceAuthority,null);
 assert.deepEqual(h.shared.assistPanelErrors(),[{source:'데이터 준비',text:'저장 원본을 읽지 못했습니다. 폴더를 다시 선택하세요.'}]);
 h.store.setState({importError:null});h.render();assert.deepEqual(h.shared.assistPanelErrors(),[]);h.unmount();
});

test('current source-save and split failures are summarized together and owned cleanup preserves other panels',()=>{
 const h=harness();h.store.setState({sourceSaveError:'프로젝트 경로 저장 실패',splitError:'분할 저장 실패'});h.render();
 assert.deepEqual(h.shared.assistPanelErrors(),[{source:'데이터 준비',text:'프로젝트 경로 저장 실패 · 분할 저장 실패'}]);
 const other=h.shared.registerPanelError('데이터 준비','다른 패널 오류');h.unmount();
 assert.deepEqual(h.shared.assistPanelErrors(),[{source:'데이터 준비',text:'다른 패널 오류'}]);other();
});

test('dataset guidance retires visible errors while project or session switching is in progress',()=>{
 const h=harness();h.store.setState({splitError:'분할 저장 실패'});h.render();assert.equal(h.shared.assistPanelErrors().length,1);
 h.project.isProjectBusy=true;h.render();assert.deepEqual(h.shared.assistPanelErrors(),[]);
 h.project.isProjectBusy=false;h.render();assert.equal(h.shared.assistPanelErrors().length,1);
 h.project.sharedSessionChanging=true;h.render();assert.deepEqual(h.shared.assistPanelErrors(),[]);h.unmount();
});

test('retained dataset errors cannot acquire a changed project, source, task or labelset scope',()=>{
 for(const change of [
  h=>{h.project.project.id='B';h.project.projectDir='/projects/B';},
  h=>{h.project.project.source_dataset_dir='/new';h.store.setState({folderPath:'/new',datasetKey:'/new\0anomaly'});},
  h=>{h.project.task=h.project.project.task='detection';h.store.setState({datasetKey:'/source\0detection'});},
  h=>{h.project.project.active_labelset_id='next';},
 ]){
  const h=harness();h.store.setState({splitError:'이전 입력의 분할 오류'});h.render();assert.equal(h.shared.assistPanelErrors().length,1);
  change(h);const seen=[];const unsubscribe=h.shared.subscribeAssistContext(()=>seen.push(...h.shared.assistPanelErrors()));h.render();h.render();
  assert.deepEqual(h.shared.assistPanelErrors(),[]);assert.deepEqual(seen,[]);
  h.store.setState({splitError:null,sourceAuthority:h.authority(),isLoading:false});h.render();h.store.setState({splitError:'이전 입력의 분할 오류'});h.render();
  assert.deepEqual(h.shared.assistPanelErrors(),[{source:'데이터 준비',text:'이전 입력의 분할 오류'}]);unsubscribe();h.unmount();
 }
});

test('same-folder project reset restores its own source exactly once without content writes',async()=>{
 const h=harness();h.render();assert.equal(h.reads.length,0,'already imported source is not fetched again');
 h.switchProject('B');h.render();await tick();assert.deepEqual(h.reads,[{project_id:'B',folder_path:'/source',task:'anomaly'}]);
 assert.equal(h.store.getState().datasetKey,'/source\0anomaly','real store marks the accepted read before awaiting its summary');
 h.render();h.render();assert.equal(h.reads.length,1,'key publication does not create another import');
 h.pending[0].resolve(h.summary(2));await tick();h.render();assert.equal(h.store.getState().totalImages,2);assert.deepEqual(h.writes,[]);
});
test('a failed source read retains its error without an automatic retry loop',async()=>{
 const h=harness();h.render();h.switchProject('B');h.render();await tick();assert.equal(h.reads.length,1);
 h.pending[0].reject(Error('Saved project source changed'));await tick();
 for(let i=0;i<4;i++)h.render();assert.equal(h.reads.length,1);assert.equal(h.store.getState().importError,'Saved project source changed');assert.deepEqual(h.writes,[]);
});
test('an older in-flight same-folder summary cannot replace the next project source view',async()=>{
 const h=harness();h.render();h.switchProject('B');h.render();await tick();h.render();h.switchProject('C');h.render();await tick();
 assert.deepEqual(h.reads.map(row=>row.project_id),['B','C']);h.pending[0].resolve(h.summary(99));await tick();h.render();
 assert.equal(h.store.getState().totalImages,0,'obsolete summary is ignored');assert.equal(h.store.getState().isLoading,true);
 h.pending[1].resolve(h.summary(2));await tick();h.render();assert.equal(h.store.getState().totalImages,2);assert.equal(h.store.getState().isLoading,false);assert.deepEqual(h.writes,[]);
});
test('clearing a source does not dispatch a summary for the empty folder',()=>{
 const h=harness();h.render();h.store.getState().setFolderPath('');h.render();assert.deepEqual(h.reads,[]);assert.deepEqual(h.writes,[]);
});

function descendants(node,output=[]){if(!node||typeof node!=='object')return output;if(Array.isArray(node)){node.forEach(n=>descendants(n,output));return output;}output.push(node);descendants(node.props?.children,output);return output;}
const slider=tree=>descendants(tree).find(n=>n.type==='input'&&n.props.type==='range');
test('assistant split prefill updates an already mounted bar without saving or overwriting later manual input',()=>{
 const h=harness();h.store.setState({splitSupported:true,split:{train:16,val:2,test:2},totalImages:20,splitState:'saved'});
 let tree=h.render();assert.equal(slider(tree).props.value,80);
 h.store.getState().prefillSplit(0.7);h.render();tree=h.render();assert.equal(slider(tree).props.value,70);
 assert.deepEqual(h.store.getState().split,{train:16,val:2,test:2});assert.deepEqual(h.writes,[]);
 slider(tree).props.onChange({target:{value:'85'}});tree=h.render();assert.equal(slider(tree).props.value,85);
 h.render();tree=h.render();assert.equal(slider(tree).props.value,85,'a consumed request does not undo a manual edit');
});
test('a split prefill from another dataset or authority is not adopted',()=>{
 const h=harness();h.store.setState({splitSupported:true,split:{train:16,val:2,test:2},totalImages:20,splitState:'saved'});h.render();
 h.store.getState().prefillSplit(0.6);const old=h.store.getState().splitPrefill;
 h.switchProject('B');h.store.setState({datasetKey:'/source\0anomaly',sourceAuthority:'other',splitPrefill:old,split:{train:16,val:2,test:2},totalImages:20,splitState:'saved'});
 h.render();const tree=h.render();assert.equal(slider(tree).props.value,80);assert.deepEqual(h.writes,[]);
});

const visibleText=node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(visibleText).join(''):(node?.props?.children!==undefined?visibleText(node.props.children):'');
function validatedSource(overrides={}){
 const revision={revision_id:'39d55e103d26abcdef',active:true,state:'prepared',task:'anomaly',source_root:'/source',image_count:6,valid_count:5,error_count:1,unreadable_folders:0,skipped_links:0,invalid_policy:'exclude',manifest_sha256:'a'.repeat(64),reused_entries:0,verified_all:1,follow_links:0,created_ns:1,publication_key:null,parent_revision:null,annotations_scanned:true,annotated:0,annotation_errors:0,duplicate_groups:0,duplicate_images:0,conflicting_duplicates:0,cross_split_duplicates:0,annotations_bind:0};
 return {revision,current:{revision_id:revision.revision_id,source_root:'/source',matches:true,added:0,removed:0,changed:0,gaps:0,gaps_changed:false,images:6,revision_images:6,skipped_links:0,basis:'stat',checked_ns:2},...overrides};
}
async function validationView(h){h.render();await tick();h.render();await tick();return visibleText(h.render());}
const revisionList=fixture=>({revisions:[fixture.revision],active_revision:fixture.revision.revision_id});
for(const change of ['project','labelset','context'])
test('a retained validation receipt cannot cross an accepted '+change+' switch while new revision/status reads are held',async()=>{
 const fixture=validatedSource(),nextRevision=deferred(),nextStatus=deferred();
 const h=harness(fixture);try{
  h.store.setState({importError:'quick summary unavailable'});
  assert.match(await validationView(h),/전체 검증 완료/);
  fixture.readRevisions=()=>nextRevision.promise;fixture.readStatus=()=>nextStatus.promise;
  if(change==='project'){h.project.project={...h.project.project,id:'B',project_dir:'/projects/B'};h.project.projectDir='/projects/B';h.project.isProjectBusy=true;}
  if(change==='labelset')h.project.project={...h.project.project,active_labelset_id:'next'};
  if(change==='context')h.advanceContext();
  assert.doesNotMatch(visibleText(h.render()),/전체 검증 완료/,'the first render must synchronously reject the previous authority');
  h.project.isProjectBusy=false;h.render();await tick();
  assert.doesNotMatch(visibleText(h.render()),/전체 검증 완료/,'held new revision list grants no acceptance');
  nextRevision.resolve(revisionList(fixture));await tick();h.render();await tick();
  assert.doesNotMatch(visibleText(h.render()),/전체 검증 완료/,'held source-status grants no acceptance');
  nextStatus.resolve(fixture.current);await tick();
  assert.match(visibleText(h.render()),/전체 검증 완료/,'the current authority can confirm its own receipt');
 }finally{nextRevision.resolve(revisionList(fixture));nextStatus.resolve(fixture.current);h.unmount();}
});
test('late old-authority revision and source-status results cannot publish under a new context',async()=>{
 for(const pendingKind of ['revision','status']){
  const fixture=validatedSource(),old=deferred(),current=deferred();let reads=0;
  if(pendingKind==='revision')fixture.readRevisions=()=>++reads===1?old.promise:current.promise;
  else fixture.readStatus=()=>++reads===1?old.promise:current.promise;
  const h=harness(fixture);try{
   h.store.setState({importError:'quick summary unavailable'});await validationView(h);
   h.advanceContext();h.render();await tick();h.render();await tick();
   old.resolve(pendingKind==='revision'?revisionList(fixture):fixture.current);await tick();
   assert.doesNotMatch(visibleText(h.render()),/전체 검증 완료/,'late '+pendingKind+' result belongs to the old context');
   current.resolve(pendingKind==='revision'?revisionList(fixture):fixture.current);await tick();h.render();await tick();
   assert.match(visibleText(h.render()),/전체 검증 완료/,'fresh '+pendingKind+' result belongs to this context');
  }finally{old.resolve(pendingKind==='revision'?revisionList(fixture):fixture.current);current.resolve(pendingKind==='revision'?revisionList(fixture):fixture.current);h.unmount();}
 }
});
test('a current independently validated revision remains visible when the quick summary failed',async()=>{
 const h=harness(validatedSource());try{
  h.store.setState({importError:'Permission denied while reading quick summary'});
  const text=await validationView(h);
  assert.match(text,/전체 검증 완료 · 활성 버전 39d55e103d26 · 6장 중 유효 5장, 손상·제외 1장/);
  assert.match(text,/Permission denied while reading quick summary/,'the failed quick summary keeps its own error');
 }finally{h.unmount();}
});
test('a quick summary error cannot revive stale quick-validation advice without an independent receipt',async()=>{
 const h=harness();try{
  h.store.setState({importError:'Permission denied while reading quick summary',quickValidation:{requested:true,complete:false,checked_images:64,scope:'sampled_first_64'}});
  assert.doesNotMatch(await validationView(h),/빠른 확인은 처음 64장/);
 }finally{h.unmount();}
});
for(const changed of ['loading','missing revision','different source','different current revision','changed current source'])
test('independent validation cannot claim completion with '+changed,async()=>{
 const fixture=validatedSource();
 if(changed==='missing revision')fixture.revisionError=true;
 if(changed==='different source')fixture.revision.source_root='/other-source';
 if(changed==='different current revision')fixture.current.revision_id='other-revision';
 if(changed==='changed current source'){fixture.current.matches=false;fixture.current.changed=1;}
 const h=harness(fixture);try{
  h.store.setState({importError:'Permission denied while reading quick summary',isLoading:changed==='loading'});
  assert.doesNotMatch(await validationView(h),/전체 검증 완료/);
 }finally{h.unmount();}
});
for(const task of ['classification','patch_classification','detection','segmentation','anomaly'])
test('rendered imbalance advice follows actual '+task+' training capability',()=>{
 for(const language of ['ko','en']){
  const h=harness();Object.assign(h.project,{task,language});h.project.project.task=task;
  h.store.setState({datasetKey:'/source\0'+task,totalImages:103,classes:{OK:100,NG:3},splitState:'saved',split:{train:80,val:10,test:13}});
  const tree=h.render(),text=visibleText(tree);
  assert.match(text,/20:1/);
  if(['classification','patch_classification'].includes(task))assert.match(text,language==='ko'?/클래스.*가중치/:/class.*weight/i);
  else assert.match(text,language==='ko'?/적은 클래스.*이미지|데이터.*확인/:/minority.*images|review.*data/i);
  assert.doesNotMatch(text,/Focal|Weighted loss rebalancing will be used/);
  assert.equal(descendants(tree).some(n=>n.props?.termKey==='focal_loss'),false,'imbalance alone cannot imply this task uses Focal');h.unmount();
 }
});
