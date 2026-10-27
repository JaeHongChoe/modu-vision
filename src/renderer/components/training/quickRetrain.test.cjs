// Product direction §3.3 ④: '빠른 이어 학습' shortens the draft loop — a few epochs from the newest completed model,
// through the warm-start parent and epoch count the start request already carries (no new preset literal).
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),test=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'TrainingController.tsx'),'utf8');
test('the quick retrain button fills the warm-start parent and five epochs, and only when a parent exists',()=>{
 const start=source.indexOf("aria-label=\"빠른 이어 학습\"");assert.ok(start>0,'button present');
 const block=source.slice(source.lastIndexOf('{warmParents.length > 0',start),source.indexOf('</div>\n        )}',start));
 assert.match(block,/warmParents\.length > 0 && !resumeState && task !== 'anomaly' && !isTraining/);
 assert.match(block,/setWarmParentId\(parent\.job_id\); setManualEpochs\('5'\)/);
 assert.match(block,/warmParents\.find\(p => p\.job_id === completedJobId\) \?\? warmParents\[warmParents\.length - 1\]/,'the finished run, else the newest parent');
 assert.doesNotMatch(block,/setPreset\(/,'the preset the parent list was computed with stays');
 assert.match(block,/5회만 이어 학습/);
 // The start request already sends warm_start_job_id and config_overrides.epochs; nothing new goes to the backend.
 assert.match(source,/startTraining\(folderPath, task, resumeState\?undefined:warmParentId \|\| undefined, modelOptions, schedulingOptions\)/);
 assert.match(source,/manualTrainingRecipe\(manualEpochs, manualImageSize\)/);
});

// The real component under a small hook runtime: '빠른 이어 학습' sets the parent and five epochs in one click, and the
// epoch change must not re-read the parent list (which used to clear the parent it had just picked).
test('the parent picked by the quick retrain survives its own epoch change and goes out with the start request',async()=>{
 const Module=require('node:module'),ts=require('typescript');
 const slots=[],effects=[];let cursor=0;
 const changed=(old,deps)=>!old||!deps||deps.length!==old.deps.length||deps.some((dep,index)=>!Object.is(dep,old.deps[index]));
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),Fragment:'Fragment',
  useRef(initial){const index=cursor++;return slots[index]??(slots[index]={current:initial});},
  useState(initial){const index=cursor++;if(!(index in slots))slots[index]={value:typeof initial==='function'?initial():initial};const slot=slots[index];return[slot.value,value=>{slot.value=typeof value==='function'?value(slot.value):value;}];},
  useEffect(fn,deps){const index=cursor++,old=slots[index];if(changed(old,deps)){const entry={deps};slots[index]=entry;effects.push(()=>{old?.cleanup?.();entry.cleanup=fn();});}}};
 react.default=react;
 const store=state=>Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
 const compile=(file,mocks)=>{const filename=path.join(__dirname,file),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));
  m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:/^\.\.?\//.test(name)?new Proxy({},{get:(_,key)=>String(key)}):assert.fail(`${file} imports ${name}`);
  m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,filename);return m.exports;};
 const parentReads=[],starts=[];
 const parents=[{job_id:'job_old',created_at:'2026-10-20T00:00:00Z'},{job_id:'job_new',created_at:'2026-10-22T00:00:00Z'}];
 const {TrainingController}=compile('TrainingController.tsx',{react,'lucide-react':new Proxy({},{get:(_,key)=>String(key)}),
  './parentCandidate':{parentCandidateLabel:parent=>parent.job_id,parentCandidateNotice:''},
  './modelTrainingOptions':compile('modelTrainingOptions.ts',{}),
  './trainingStartSummary':compile('trainingStartSummary.ts',{'./modelTrainingOptions':compile('modelTrainingOptions.ts',{})}),
  '../../stores/assistContext':compile('../../stores/assistContext.ts',{}),
  '../common/useScopedError':compile('../common/useScopedError.ts',{react}),
  '../../services/api':{api:{training:{warmStartParents:async(...args)=>{parentReads.push(args[3]);return{parents,total:2};}}},request:async()=>({states:[],excluded:[]}),getApiPersistenceIdentity:()=>null},
  '../../stores/useProjectStore':{useProjectStore:store({task:'detection',language:'ko',setStep(){},projectDir:'/p',project:{id:'p',task:'detection',source_dataset_dir:'/data',active_labelset_id:'default'},isProjectBusy:false,technicalDetails:false,viewMode:'simple'})},
  '../../stores/useDatasetStore':{readDatasetAuthority:()=> 'owned',useDatasetStore:store({folderPath:'/data',totalImages:10,split:{train:6,val:2,test:2},isLoading:false,isSplitting:false,importError:null,splitError:null,splitSupported:true,splitUnavailableReason:null,datasetKey:'/data\0detection',sourceAuthority:'owned',splitState:'saved',classes:{},classSplitCounts:null})},
  '../../stores/useTrainingStore':{useTrainingStore:store({jobId:'job_new',status:'completed',isTraining:false,isRecoveringTraining:false,preset:'fast',setPreset(){},lossHistory:[],hardware:{},
   startTraining:async(...args)=>{starts.push(args);},stopTraining:async()=>{},recoverActiveJob:async()=>{},refreshCurrentJob:async()=>{},reconnectCurrentJob:async()=>{},setNextSettings(){}})},
  '../../stores/autoRunChannel':{registerAutoRun:()=>()=>{},registerTaskAutoRun:()=>()=>{}},
  '../../stores/useComputeStore':{useComputeStore:store({profiles:[],selectedProfileId:'',transportRevision:0,isLoaded:true,isLoading:false,loadError:null,error:null,probeResults:{},probePendingId:null,probeProfile:async()=>{}})},
  '../../utils/datasetSplitCapability':{isSplitUnavailable:()=>false},'../../utils/trainingComputeReadiness':{trainingComputeReadiness:()=>({ready:true})},
  './useTaskHandoff':{useTaskHandoff:()=>null},'./taskHandoff':{clearTaskHandoff(){}},'../common/errorActions':{trainingPresetBatchSize:()=>8},'./trainingLog':{trainingLogLines:()=>[]},
  './TrainingSchedulingSettings':{defaultTrainingScheduling:{},trainingSchedulingOptions:()=>({}),TrainingSchedulingSettings:'TrainingSchedulingSettings'},
  './useSelectedCoreJob':{useSelectedCoreJob:()=>({job:null,error:null})},
  '../dataset/importChecklist':{classGaps:()=>({thin:[]}),classStartBlocker:()=>null,SPLIT_MISSES_LABELS_TEXT:'',splitMissesLabels:()=>false},
  '../wizard/TaskChangeImpactDialog':{TaskChangeImpactDialog:'TaskChangeImpactDialog',taskPreviewScope:()=>''}});
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const text=node=>node==null||typeof node==='boolean'?'':typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);
 const settle=async()=>{let tree;for(let cycle=0;cycle<12;cycle++){cursor=0;tree=TrainingController();effects.splice(0).forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));}return tree;};
 const parentSelect=tree=>nodes(tree).find(node=>node.props?.['aria-label']==='재학습 시작 모델');
 let tree=await settle();
 assert.equal(parentReads.length,1);
 nodes(tree).find(node=>node.type==='button'&&node.props['aria-label']==='빠른 이어 학습').props.onClick();
 tree=await settle();
 assert.equal(parentSelect(tree).props.value,'job_new','the parent just picked stays picked');
 assert.equal(parentReads.length,1,'five epochs do not change which parents fit');
 assert.match(text(tree),/시작 모델 …job_new · 학습 횟수 5/);
 // The list only offers this label set's parents; one whose receipt predates the label-set record is offered and says so.
 assert.match(text(tree),/라벨 세트 기록 없음/);
 await nodes(tree).find(node=>node.type==='button'&&text(node).includes('선택 설정으로 학습 시작')).props.onClick();
 assert.equal(starts.length,1);
 assert.equal(starts[0][2],'job_new','the start request carries the warm-start parent');
 assert.equal(starts[0][3].epochs,5);
 // A model field the list is filtered by still re-reads it (and clears the parent, as before).
 nodes(tree).find(node=>node.props?.['aria-label']==='학습 모델 구조').props.onChange({target:{value:'yolo26s'}});
 tree=await settle();
 assert.equal(parentReads.length,2);
 assert.equal(parentSelect(tree).props.value,'');
 // A parent recorded under the active label set carries no such mark.
 parents[1].labelset_id='default';
 nodes(tree).find(node=>node.props?.['aria-label']==='학습 모델 구조').props.onChange({target:{value:'yolo26n'}});
 tree=await settle();
 nodes(tree).find(node=>node.type==='button'&&node.props['aria-label']==='빠른 이어 학습').props.onClick();
 tree=await settle();
 assert.match(text(tree),/시작 모델 …job_new · 학습 횟수 5/);
 assert.doesNotMatch(text(tree),/라벨 세트 기록 없음/);
});
