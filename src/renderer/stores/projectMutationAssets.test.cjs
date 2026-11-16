// Missing renewal after a durable project mutation must break actual media reads.
// Real API and mutation stores; only synthetic HTTP and unrelated job/annotation stores differ.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};}
const state=value=>({getState:()=>value,setState:update=>Object.assign(value,typeof update==='function'?update(value):update)});
async function fixture(t,{source=null,holdMint=false,rejectMint=false,holdCurrent=false,separateCurrent=false,rejectList=false,holdGenerate=false,holdImport=false,rejectGenerate=false,rejectImport=false,projectRoot='/owned/p',foreignGenerate=false,realLogin=false}={}){
 const old={window:global.window,fetch:global.fetch},storage=Object.getOwnPropertyDescriptor(global,'localStorage'),cache=new Map(),mocks=new Map(),origin='https://localhost:8443';
 Object.defineProperty(global,'localStorage',{configurable:true,value:{getItem:()=>null,setItem(){}}});
 const root=__dirname,resolve=ref=>path.resolve(root,ref),put=(ref,value)=>mocks.set(resolve(ref),value);
 function load(file){file=resolve(file);if(mocks.has(file))return mocks.get(file);if(cache.has(file))return cache.get(file).exports;
  const m=new Module(file,module);cache.set(file,m);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);
  m.require=ref=>{if(ref.startsWith('.')){const target=path.resolve(path.dirname(file),ref);if(mocks.has(target))return mocks.get(target);for(const suffix of ['.ts','.tsx'])if(fs.existsSync(target+suffix))return load(target+suffix);}return original(ref);};
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;
 }
 const annotation=state({isDirty:false,setTask(){},setImages:async()=>true,saveIfDirty:async()=>true});
 const flow=state({pipelineDirty:false,invalidateForDataChange(){}}),training=state({isTraining:false,invalidateForDataChange(){}}),evaluation=state({invalidateForDataChange(){}});
 put('./useAnnotationStore',{useAnnotationStore:annotation});put('./useFlowchartStore',{useFlowchartStore:flow});put('./useTrainingStore',{useTrainingStore:training});put('./useEvaluationStore',{useEvaluationStore:evaluation});
 put('./useInspectionRunStore',{useInspectionRunStore:state({isRunning:false})});put('./useModelAssistRunStore',{useModelAssistRunStore:state({activeOperations:0})});
 const api=load('../services/api.ts');put('../services/api',api);
 const dataset=load('./useDatasetStore.ts').useDatasetStore;put('./useDatasetStore',{useDatasetStore:dataset});
 const projectModule=load('./useProjectStore.ts'),store=projectModule.useProjectStore;put('./useProjectStore',projectModule);
 let server={id:'p',name:'P',project_dir:projectRoot,dataset_dir:'/outside/project-dataset',source_dataset_dir:source,task:'classification',active_labelset_id:'default',registration_id:'reg',workspace_id:'w'};
 const context=actor=>({workspace_id:'w',project_id:'p',actor_id:actor,mode:'team'}),binding=p=>JSON.stringify([p.source_dataset_dir,p.task,p.active_labelset_id]);
 const tickets=new Map(),calls=[],generationPaths=[],mintEntered=deferred(),currentEntered=deferred(),generateEntered=deferred(),importEntered=deferred(),release=deferred(),currentRelease=deferred();let mints=0,currents=0,imports=0,logins=0,sessionActor='actor';
 global.window={location:{origin}};
 global.fetch=async(url,options={})=>{
  const u=new URL(url),route=u.pathname;calls.push(route);
  if(route==='/api/accounts/login'){sessionActor=++logins===1?'actor':'other';return Response.json({expires_at:Date.now()/1000+3600,user:{id:sessionActor,username:'fixture',administrator:true},csrf_token:'fixture_csrf_abcdefghijklmnopqrstuvwxyz'});}
  if(route==='/api/dataset/raw/image.png')return new Response('pixels',{status:tickets.get(u.searchParams.get('local_asset_ticket'))===binding(server)?200:403});
  if(route==='/api/local-host/project-assets'){
   const number=++mints,ticket='asset_'+number.toString(16).padStart(48,'0');tickets.set(ticket,binding(server));
   if(number>1){mintEntered.resolve();if(holdMint&&number===2)await release.promise;if(rejectMint)return Response.json({detail:'Synthetic ticket renewal unavailable'},{status:503});}
   return Response.json({ticket,expires_in:600,registration_id:'reg',context:context(sessionActor)});
  }
  let value;
  if(route==='/api/dataset/generate'){
   const requested=JSON.parse(options.body).output_dir,paths=/^[A-Za-z]:|^\\\\/.test(projectRoot)?path.win32:path.posix;
   const candidate=paths.resolve('/runtime',requested),relative=paths.relative(projectRoot,candidate);
   generationPaths.push(candidate);generateEntered.resolve();
   // Same containment decision as SharedAuthorization: resolve selector,
   // then require it to be relative to the authorized project root.
   if(relative==='..'||relative.startsWith('..'+paths.sep)||paths.isAbsolute(relative))return Response.json({detail:'Request data is outside this project or invalid'},{status:403});
   if(holdGenerate&&generationPaths.length===1)await release.promise;
   if(rejectGenerate)return Response.json({detail:'Synthetic renderer unavailable'},{status:400});
   value={status:'success',count:2,classes:['OK','NG'],output_dir:foreignGenerate?'/outside/forged-result':candidate};
  }
  else if(route.endsWith('/projects/current')||route==='/api/project/current'){if(++currents>1&&holdCurrent){currentEntered.resolve();await (separateCurrent?currentRelease:release).promise;}value=server;}
  else if(route==='/api/project/update'){server={...server,...JSON.parse(options.body)};value=server;}
  else if(route==='/api/project/labelsets'){value={id:'ls_aaaaaaaaaaaa',name:'New'};}
  else if(route.startsWith('/api/project/labelsets/')&&route.endsWith('/activate')){server={...server,active_labelset_id:route.split('/')[4]};value=server;}
  else if(route==='/api/dataset/import'||route==='/api/dataset/current-summary'){if(++imports===1&&holdImport){importEntered.resolve();await release.promise;}if(rejectImport)return Response.json({detail:'Synthetic import unavailable'},{status:400});value={total_images:1,source_images:1,split:{train:1,val:0,test:0},classes:{good:1}};}
  else if(route==='/api/dataset/images')value={total:1,limit:48,offset:0,items:[]};
  else if(route.endsWith('/projects/list')||route==='/api/project/list'){if(rejectList)return Response.json({detail:'Fresh project catalog unavailable'},{status:503});value={projects:[]};}
  else throw Error('Unexpected HTTP boundary '+route);
  return Response.json(value,{headers:{'X-Vision-Context':JSON.stringify(context(sessionActor))}});
 };
 api.setSharedApiBase(origin,()=>({credentials:'same-origin',headers:{'X-Vision-CSRF':'synthetic-proof'}}));
 api.setBrowserSessionTransport({authenticated:()=>true,revision:()=>1,requestOptions:()=>({credentials:'same-origin'}),invalidate(){},connection:()=>({server_url:origin,user:{id:'actor'},project_id:'p'})});
 let login;
 if(realLogin){
  const {BrowserSession}=load('../services/browserSession.ts'),session=new BrowserSession({origin:()=>origin,fetch:(...args)=>fetch(...args),now:()=>Date.now()/1000});
  login=()=>session.login({server_url:origin,username:'fixture',password:'fixture-password-only'});
  await login();
  api.setBrowserSessionTransport({authenticated:()=>session.connection()!==null,revision:()=>session.revision(),requestOptions:(...args)=>session.requestOptions(...args),invalidate:()=>session.invalidate(),connection:()=>session.connection()});
 }
 api.setLocalHostTransport({available:()=>true,canProjects:()=>true,canProjectAssets:()=>true,requestHeaders:()=>({'X-Vision-Local-Tab':'synthetic-tab'}),prepareRequest:async()=>{},projectChanged(){},generation:()=>0,acceptResponseHeaders(){}});
 t.after(()=>{release.resolve();currentRelease.resolve();global.window=old.window;global.fetch=old.fetch;if(storage)Object.defineProperty(global,'localStorage',storage);else delete global.localStorage;});
 const initial=await api.api.project.getCurrent();await api.api.project.prepareContext(initial);api.api.project.acceptContext(initial);
 store.setState({project:initial,projectDir:initial.project_dir,projectName:initial.name,task:initial.task});
 return{api,store,dataset,projectModule,annotation,calls,generationPaths,mintEntered,currentEntered,generateEntered,importEntered,release,currentRelease,login,server:()=>server,mints:()=>mints,
  media:()=>fetch(api.resolveApiUrl('/api/dataset/raw/image.png?file_path=%2Fowned%2Fp%2Fimage.png')),
  changeActor(){api.setProjectContext(context('other'));},aba(){api.setProjectContext(context('other'));api.setProjectContext(context('actor'));},
  changeProject(){api.setProjectContext({...context('actor'),project_id:'other-project'});const project={...server,id:'other-project',project_dir:'/owned/other'};store.setState({project,projectDir:project.project_dir});}};
}
for(const mutation of ['source','task','labelset','new-labelset','source-retry'])test(`${mutation} mutation renews the read ticket before media is used`,async t=>{
 const f=await fixture(t,{source:mutation==='source'||mutation==='source-retry'?'/source/old':null});
 if(mutation==='source')await f.dataset.getState().importFolder('/source/new','classification');
 else if(mutation==='task')assert.equal((await f.store.getState().updateTask('segmentation')).ok,true);
 else if(mutation==='labelset')assert.equal(await f.store.getState().activateLabelset('ls_aaaaaaaaaaaa'),true);
 else if(mutation==='new-labelset')assert.equal(await f.store.getState().createAndActivateLabelset('New'),true);
 else{f.dataset.setState({folderPath:'/source/new',hasSelectedFolder:true,datasetKey:'/source/new\0classification',sourceSaveError:'previous save failed',importError:null});await f.projectModule.saveOpenEdits();}
 assert.equal((await f.media()).status,200,'the media read must carry a ticket matching the saved source/task/labelset');
 assert.ok(f.mints()>=2,'the initial project-selection ticket cannot authorize the changed scope');
});

test('ticket failure after task persistence retains the applied task and exposes its error',async t=>{
 const f=await fixture(t,{rejectMint:true}),outcome=await f.store.getState().updateTask('segmentation');
 assert.equal(f.server().task,'segmentation');assert.equal(outcome.ok,false);assert.equal(outcome.applied,true);assert.equal(f.store.getState().task,'segmentation');assert.match(f.store.getState().projectError,/renewal unavailable/);
});
test('ticket failure after source persistence does not claim the source save failed',async t=>{
 const f=await fixture(t,{rejectMint:true});await assert.rejects(f.dataset.getState().importFolder('/source/new','classification'),/renewal unavailable/);
 assert.equal(f.server().source_dataset_dir,'/source/new');assert.equal(f.store.getState().project.source_dataset_dir,'/source/new');assert.equal(f.dataset.getState().sourceSaveError,null);assert.match(f.dataset.getState().importError,/renewal unavailable/);assert.equal(f.calls.includes('/api/dataset/images'),false);
});

for(const mutation of ['task','labelset','source'])for(const transition of ['actor','aba','project'])test(`held ${mutation} ticket cannot publish across an ${transition} change`,async t=>{
 const f=await fixture(t,{holdMint:true});let finished=false;
 const pending=(mutation==='task'?f.store.getState().updateTask('segmentation'):mutation==='labelset'?f.store.getState().activateLabelset('ls_aaaaaaaaaaaa'):f.dataset.getState().importFolder('/source/new','classification')).finally(()=>{finished=true;});
 pending.catch(()=>{});
 for(let i=0;i<100&&!finished&&f.mints()<2;i++)await tick();
 assert.equal(f.mints(),2,'the mutation must reach ticket preparation');
 transition==='actor'?f.changeActor():transition==='project'?f.changeProject():f.aba();const before=f.store.getState().project;f.store.setState({projectError:'Current recovery guidance'});f.release.resolve();await pending;
 assert.equal(f.store.getState().project,before);assert.equal(f.store.getState().projectError,'Current recovery guidance');assert.equal(f.api.resolveApiUrl('/api/dataset/raw/image.png?file_path=%2Fowned%2Fp%2Fimage.png'),'');
 assert.equal(f.store.getState().isProjectBusy,false,'the original mutex must release after authority changes so the new actor can work');
});

test('an older source ticket reply cannot overwrite a newer completed import',async t=>{
 const f=await fixture(t,{holdMint:true}),older=f.dataset.getState().importFolder('/source/older','classification');
 await f.mintEntered.promise;
 await f.dataset.getState().importFolder('/source/newer','classification');
 assert.equal((await f.media()).status,200);const url=f.api.resolveApiUrl('/api/dataset/raw/image.png?file_path=%2Fowned%2Fp%2Fimage.png');
 f.release.resolve();await older;
 assert.equal(f.dataset.getState().folderPath,'/source/newer');assert.equal(f.store.getState().project.source_dataset_dir,'/source/newer');assert.equal(f.api.resolveApiUrl('/api/dataset/raw/image.png?file_path=%2Fowned%2Fp%2Fimage.png'),url);assert.equal((await f.media()).status,200);
});

test('a held source-save retry ticket cannot replace a newer imported source ticket',async t=>{
 const f=await fixture(t,{source:'/source/old',holdMint:true});
 f.dataset.setState({folderPath:'/source/retry',hasSelectedFolder:true,datasetKey:'/source/retry\0classification',sourceSaveError:'previous save failed',importError:null});
 const retry=f.projectModule.saveOpenEdits();await f.mintEntered.promise;
 await f.dataset.getState().importFolder('/source/newer','classification');
 assert.equal((await f.media()).status,200);const url=f.api.resolveApiUrl('/api/dataset/raw/image.png?file_path=%2Fowned%2Fp%2Fimage.png');
 f.release.resolve();await retry;
 assert.equal(f.store.getState().project.source_dataset_dir,'/source/newer');assert.equal(f.api.resolveApiUrl('/api/dataset/raw/image.png?file_path=%2Fowned%2Fp%2Fimage.png'),url);assert.equal((await f.media()).status,200);
});

test('a labelset reset that resumes in a new actor cannot clear that actors dataset',async t=>{
 const f=await fixture(t),entered=deferred(),release=deferred();t.after(()=>release.resolve());
 f.annotation.getState().setImages=async()=>{entered.resolve();await release.promise;return true;};
 const changing=f.store.getState().activateLabelset('ls_aaaaaaaaaaaa');await entered.promise;
 f.changeActor();f.dataset.getState().setFolderPath('/source/new-actor');f.store.setState({projectError:'Current recovery guidance'});release.resolve();
 assert.equal(await changing,false);assert.equal(f.dataset.getState().folderPath,'/source/new-actor');assert.equal(f.store.getState().projectError,'Current recovery guidance');
});

test('a source retry cannot start its write after the owning actor leaves during the pre-save read',async t=>{
 const f=await fixture(t,{source:'/source/old',holdCurrent:true});
 f.dataset.setState({folderPath:'/source/retry',hasSelectedFolder:true,datasetKey:'/source/retry\0classification',sourceSaveError:'previous save failed',importError:null});
 const saving=f.projectModule.saveOpenEdits();saving.catch(()=>{});await f.currentEntered.promise;f.changeActor();f.release.resolve();
 await assert.rejects(saving);assert.equal(f.calls.includes('/api/project/update'),false,'an old source retry must not write under the next actor');assert.equal(f.server().source_dataset_dir,'/source/old');
});

const synthetic={task:'classification',num_samples:2,modality:'pcb',split_ratio:0.5};
for(const projectRoot of ['/owned/p','C:\\owned\\p','\\\\server\\share\\project'])test(`synthetic generation uses a fresh owned directory and renews imported media (${projectRoot})`,async t=>{
 const f=await fixture(t,{projectRoot});
 await f.dataset.getState().generateSynthetic(synthetic);const first=f.generationPaths[0];assert.equal((await f.media()).status,200);
 await f.dataset.getState().generateSynthetic(synthetic);assert.notEqual(f.generationPaths[1],first,'each explicit generation preserves prior output');
 const paths=projectRoot.startsWith('/')?path.posix:path.win32;
 for(const output of f.generationPaths){assert.equal(paths.dirname(output),projectRoot);assert.match(paths.basename(output),/^generated-synthetic_pcb_[a-f0-9-]+$/);}
 assert.equal(f.store.getState().project.source_dataset_dir,f.generationPaths[1]);assert.equal(f.dataset.getState().isGenerating,false);assert.equal((await f.media()).status,200);
});
for(const transition of ['actor','aba','project','source','new-generation'])test(`a held synthetic result cannot replace a newer ${transition}`,async t=>{
 const f=await fixture(t,{holdGenerate:true}),older=f.dataset.getState().generateSynthetic(synthetic);older.catch(()=>{});
 await f.generateEntered.promise;
 if(transition==='actor')f.changeActor();else if(transition==='aba')f.aba();else if(transition==='project')f.changeProject();
 else if(transition==='new-generation')await f.dataset.getState().generateSynthetic({...synthetic,modality:'wafer'});
 else await f.dataset.getState().importFolder('/source/newer','classification');
 const folder=f.dataset.getState().folderPath,project=f.store.getState().project,imports=f.calls.filter(route=>route==='/api/dataset/import').length;
 f.release.resolve();await older;
 assert.equal(f.dataset.getState().folderPath,folder);assert.equal(f.store.getState().project,project);assert.equal(f.calls.filter(route=>route==='/api/dataset/import').length,imports);
});
test('synthetic generation failure is visible without changing the saved source',async t=>{
 const f=await fixture(t,{source:'/source/original',rejectGenerate:true});
 await assert.rejects(f.dataset.getState().generateSynthetic(synthetic),/Synthetic renderer unavailable/);
 assert.match(f.dataset.getState().importError,/Synthetic renderer unavailable/);assert.equal(f.server().source_dataset_dir,'/source/original');assert.equal(f.dataset.getState().isGenerating,false);
});
test('successful generation followed by failed import retains the output and reports the failed stage',async t=>{
 const f=await fixture(t,{source:'/source/original',rejectImport:true});
 await assert.rejects(f.dataset.getState().generateSynthetic(synthetic),error=>/생성.*완료/.test(error.message)&&error.message.includes(f.generationPaths[0])&&error.message.includes('Synthetic import unavailable'));
 assert.equal(f.server().source_dataset_dir,'/source/original');assert.match(f.dataset.getState().importError,/Synthetic import unavailable/);assert.equal(f.dataset.getState().isGenerating,false);
});
test('an unexpected generated output path is never imported into the project',async t=>{
 const f=await fixture(t,{foreignGenerate:true});await assert.rejects(f.dataset.getState().generateSynthetic(synthetic),/경로/);assert.equal(f.calls.includes('/api/dataset/import'),false);
});
test('generation for another task is refused before any files are requested',async t=>{
 const f=await fixture(t);await assert.rejects(f.dataset.getState().generateSynthetic({...synthetic,task:'segmentation'}),/작업 종류/);assert.equal(f.calls.includes('/api/dataset/generate'),false);
});

for(const action of ['activateLabelset','createAndActivateLabelset'])test(`${action} never exposes the old annotations under a durable new labelset when minting fails`,async t=>{
 const f=await fixture(t,{rejectMint:true}),oldImage={image_id:'old',file_path:'/owned/p/old.png'};
 f.annotation.setState({images:[oldImage],currentImage:oldImage,activeImage:oldImage,maskUrl:'old-mask',metadata:{image_uuid:'old-uuid',revision:2},loadedAnnotationContext:'old-context',annotations:[{id:'old-label',type:'bbox',bbox:[1,2,3,4]}],selectedAnnotationId:'old-label',history:[['old-label']],future:[['old-label']],editLease:{token:'old-lease'},editLeaseAcquiring:{image_uuid:'old-uuid'},teamEditingEnabled:true,teamReviewEnabled:true});
 const snapshots=[],stop=f.store.subscribe(s=>{if(s.project?.active_labelset_id==='ls_aaaaaaaaaaaa')snapshots.push({...f.annotation.getState()});});t.after(stop);
 assert.equal(await f.store.getState()[action](action==='activateLabelset'?'ls_aaaaaaaaaaaa':'New'),false);
 assert.equal(f.server().active_labelset_id,'ls_aaaaaaaaaaaa');assert.equal(f.store.getState().project.active_labelset_id,'ls_aaaaaaaaaaaa');assert.ok(snapshots.length);
 for(const view of [...snapshots,f.annotation.getState()]){
  for(const field of ['currentImage','activeImage','metadata','loadedAnnotationContext','maskUrl','selectedAnnotationId','editLease','editLeaseAcquiring'])assert.equal(view[field],null,field+' cannot cross labelsets');
  for(const field of ['images','annotations','history','future'])assert.deepEqual(view[field],[],field+' cannot cross labelsets');
  assert.equal(view.teamEditingEnabled,false);assert.equal(view.teamReviewEnabled,false);
 }
 assert.match(f.store.getState().projectError,/renewal unavailable/);assert.equal(f.store.getState().isProjectBusy,false);
});

test('a generated import that finishes after a newer source cannot close the newer selection dialog',async t=>{
 const f=await fixture(t,{holdImport:true}),generating=f.dataset.getState().generateSynthetic(synthetic);await f.importEntered.promise;
 await f.dataset.getState().importFolder('/source/newer','classification');f.dataset.getState().setShowGeneratorModal(true);f.release.resolve();await generating;
 assert.equal(f.dataset.getState().folderPath,'/source/newer');assert.equal(f.dataset.getState().showGeneratorModal,true);assert.equal((await f.media()).status,200);
});

test('clean saved work does not require source-mutation authority before the source owner guard applies',async t=>{
 const f=await fixture(t,{source:'/source/saved'});
 // Existing project-view consumers deliberately provide only the API needed
 // by clean save/navigation. Authority reads belong to a source mutation.
 delete f.api.getProjectContext;delete f.api.getProjectContextGeneration;
 await assert.doesNotReject(f.projectModule.saveOpenEdits());
 assert.equal(f.calls.includes('/api/project/update'),false);
});

test('reauthentication clears an already empty source while retiring the prior generations busy ownership',async t=>{
 const f=await fixture(t,{holdGenerate:true,realLogin:true});f.dataset.getState().setFolderPath('');
 const old=f.dataset.getState().generateSynthetic(synthetic);await f.generateEntered.promise;assert.equal(f.dataset.getState().isGenerating,true);
 // The real BrowserSession login changes its proof/revision. Mirror the
 // documented local-auth panel reset, including its repeated empty folder.
 await f.login();f.api.setProjectContext(null);f.store.setState({project:null,projectDir:null,projectName:'프로젝트 선택',activeStep:1,recentProjects:[]});f.dataset.getState().setFolderPath('');await f.annotation.getState().setImages([],0);
 assert.equal(f.dataset.getState().isGenerating,false);assert.equal(f.projectModule.projectSwitchRefusal(),null,'the new account can select or create its project');
 const candidate=await f.api.api.project.getCurrent();await f.api.api.project.prepareContext(candidate);f.api.api.project.acceptContext(candidate);f.store.setState({project:candidate,projectDir:candidate.project_dir,task:candidate.task});
 await f.dataset.getState().generateSynthetic({...synthetic,modality:'wafer'});const newer=f.dataset.getState().folderPath;assert.match(newer,/generated-synthetic_wafer_/);
 f.release.resolve();await old;assert.equal(f.dataset.getState().folderPath,newer);assert.equal(f.dataset.getState().isGenerating,false);assert.equal((await f.media()).status,200);
});

test('reselecting the same nonempty folder preserves its current data and edits',async t=>{
 const f=await fixture(t);await f.dataset.getState().importFolder('/source/existing','classification');
 f.dataset.setState({images:[{image_id:'saved-image',file_path:'/source/existing/saved-image.png'}],activeClassFilter:'defect'});const before=f.dataset.getState();
 f.dataset.getState().setFolderPath('/source/existing');assert.equal(f.dataset.getState(),before);
});

for(const priorError of [null,'Current project permission issue'])test(`same-project health sync during saved-source restoration retains its existing error (${priorError})`,async t=>{
 const f=await fixture(t,{source:'/source/saved',holdImport:true});f.dataset.getState().setFolderPath('/source/saved');f.store.setState({projectError:priorError});
 const restoring=f.dataset.getState().ensureImported('classification');await f.importEntered.promise;assert.equal(f.dataset.getState().isLoading,true);
 await f.store.getState().syncCurrentProject();f.release.resolve();await restoring;
 assert.equal(f.dataset.getState().isLoading,false);assert.equal(f.dataset.getState().importError,null);assert.equal(f.dataset.getState().totalImages,1);
 assert.equal(f.store.getState().projectError,priorError,'a redundant sync must not turn successful restoration into a persistent switch refusal or erase a real error');
 assert.equal(f.store.getState().isProjectBusy,false);assert.equal(f.calls.includes('/api/project/update'),false,'restoring a saved source remains read-only');
});

test('an actual project switch remains refused while its current saved source is restoring',async t=>{
 const f=await fixture(t,{source:'/source/saved',holdImport:true});f.dataset.getState().setFolderPath('/source/saved');
 const restoring=f.dataset.getState().ensureImported('classification');await f.importEntered.promise;
 assert.equal(await f.store.getState().openProject('/owned/another'),false);assert.match(f.store.getState().projectError,/데이터 작업이 진행 중/);assert.equal(f.store.getState().project.project_dir,'/owned/p');
 f.release.resolve();await restoring;assert.match(f.store.getState().projectError,/데이터 작업이 진행 중/,'ordinary imports do not blanket-clear real navigation refusals');
});

test('a redundant restoration sync preserves a newer real project-catalog failure',async t=>{
 const f=await fixture(t,{source:'/source/saved',holdImport:true,holdCurrent:true,separateCurrent:true,rejectList:true});f.dataset.getState().setFolderPath('/source/saved');
 const restoring=f.dataset.getState().ensureImported('classification');await f.importEntered.promise;
 const syncing=f.store.getState().syncCurrentProject();await f.currentEntered.promise;
 await f.store.getState().loadRecentProjects();assert.equal(f.store.getState().projectError,'Fresh project catalog unavailable');
 f.currentRelease.resolve();await syncing;assert.equal(f.dataset.getState().isLoading,true,'the source restoration still owns its independent pending read');
 assert.equal(f.store.getState().projectError,'Fresh project catalog unavailable','the older health sync cannot restore its captured error over a newer failure');
 f.release.resolve();await restoring;assert.equal(f.store.getState().projectError,'Fresh project catalog unavailable');
});
