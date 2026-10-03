import React,{useEffect,useState} from 'react';
import {api} from '../../services/api';
import {host} from '../../services/hostAdapter';
import {workflowError} from '../../services/datasetWorkflow';
import {foundationLabelingApi as provider,labelingJobActive,type FoundationSetup,type FoundationOptions,type FoundationProposal,type FoundationBatch,type FeatureModel,type FeatureJob,type RegionExample,type SizeControls} from '../../services/foundationLabelingApi';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useModelAssistRunStore} from '../../stores/useModelAssistRunStore';
import {useFoundationPromptStore} from '../../stores/useFoundationPromptStore';
import {foundationAllowed,buildFoundationRequest,regionExample,labelingScope} from './foundationRequest';
interface Props{disabled:boolean;onCreated:(proposal:FoundationProposal)=>void;onOpenProposal:(path:string,id:string)=>Promise<void>}
const inputClass='rounded border border-slate-600 bg-slate-900 p-1.5 text-slate-100';
const statusNames:Record<string,string>={queued:'대기',running:'실행 중',cancelling:'취소 중',completed:'완료',stopped:'취소됨',failed:'실패',interrupted:'재시작으로 중단'};
export const CandidateProviderControls:React.FC<Props>=({disabled,onCreated,onOpenProposal})=>{
  const {currentImage,annotations,selectedAnnotationId,setActiveTool,activeCategory,categories}=useAnnotationStore();
  const {images,folderPath}=useDatasetStore();const project=useProjectStore(s=>s.project);const projectDir=useProjectStore(s=>s.projectDir);
  const scope=labelingScope({projectDir,project});const [loadedScope,setLoadedScope]=useState(scope);const currentScope=loadedScope===scope;
  const {points,boxes,pointLabel,setPointLabel,clear,bind}=useFoundationPromptStore();
  const [setup,setSetup]=useState<FoundationSetup|null>(null);const [prompt,setPrompt]=useState('');const [label,setLabel]=useState('defect');
  const [positive,setPositive]=useState<RegionExample[]>([]);const [negative,setNegative]=useState<RegionExample[]>([]);
  const [examplePath,setExamplePath]=useState('');const [exampleRoi,setExampleRoi]=useState('');
  const [device,setDevice]=useState('cpu');const [geometry,setGeometry]=useState<'mask'|'polygon'|'bbox'>('mask');const [threshold,setThreshold]=useState(.5);const [textThreshold,setTextThreshold]=useState(.25);const [maxCandidates,setMaxCandidates]=useState(20);
  const [sizes,setSizes]=useState<SizeControls>({min_area:0,min_width:0,min_height:0});
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const [featureModels,setFeatureModels]=useState<FeatureModel[]>([]);const [modelId,setModelId]=useState('');const [featureJob,setFeatureJob]=useState<FeatureJob|null>(null);
  const [epochs,setEpochs]=useState(100);const [learningRate,setLearningRate]=useState(.05);
  const [checkpoint,setCheckpoint]=useState('');const [checkpointHash,setCheckpointHash]=useState('');
  const [batches,setBatches]=useState<FoundationBatch[]>([]);const [batchId,setBatchId]=useState('');const [batchPaths,setBatchPaths]=useState<Set<string>>(new Set());
  const visibleBatches=currentScope?batches:[];const visibleModels=currentScope?featureModels:[];
  const activeBatch=visibleBatches.find(b=>labelingJobActive(b.status));const batch=visibleBatches.find(b=>b.id===batchId);
  const running=(currentScope&&labelingJobActive(featureJob?.status))||!!activeBatch;
  const sameProject=()=>labelingScope(useProjectStore.getState())===scope;
  useEffect(()=>{bind(currentImage?.file_path||'');},[bind,currentImage?.file_path]);
  useEffect(()=>{setLabel(activeCategory.name);},[activeCategory.name]);
  useEffect(()=>{
    let active=true;setLoadedScope(scope);setBusy(false);setSetup(null);setBatches([]);setBatchId('');setFeatureJob(null);setFeatureModels([]);setPositive([]);setNegative([]);setModelId('');setBatchPaths(new Set());setExamplePath('');setExampleRoi('');setCheckpoint('');setCheckpointHash('');setError('');setNotice('');clear();
    const remembered=localStorage.getItem(`foundation-feature-job:${scope}`);
    void Promise.all([provider.setup(),provider.featureModels(),provider.batches(),provider.featureJobs().then(result=>result.jobs.find(j=>j.id===remembered)||result.jobs[0]||null)])
      .then(([s,m,b,j])=>{if(active&&sameProject()){setSetup(s);setFeatureModels(m.models);setBatches(b.batches);setBatchId(b.batches[0]?.id||'');setFeatureJob(j);setCheckpoint(s.configuration.feature_checkpoint||'');setCheckpointHash(s.configuration.feature_sha256||'');}})
      .catch(e=>{if(active&&sameProject())setError(workflowError(e));});return()=>{active=false;};
  },[scope,clear]);
  useEffect(()=>{
    if(!running)return;useModelAssistRunStore.getState().begin();let active=true;
    const poll=async()=>{try{
      if(activeBatch){const next=await provider.batch(activeBatch.id);if(active&&sameProject())setBatches(old=>old.map(b=>b.id===next.id?next:b));}
      if(featureJob&&labelingJobActive(featureJob.status)){const next=await provider.training(featureJob.id);if(active&&sameProject()){setFeatureJob(next);if(next.status==='completed'){const models=await provider.featureModels();if(active&&sameProject()){setFeatureModels(models.models);setModelId(next.model_id||'');}}}}
    }catch(e){if(active&&sameProject())setError(workflowError(e));}};
    const timer=window.setInterval(()=>void poll(),1000);return()=>{active=false;window.clearInterval(timer);useModelAssistRunStore.getState().end();};
  },[running,activeBatch?.id,featureJob?.id,scope]);
  const run=async(action:()=>Promise<void>)=>{if(!currentScope||!sameProject())return;setBusy(true);setError('');setNotice('');try{await action();}catch(e){if(sameProject())setError(workflowError(e));}finally{if(sameProject())setBusy(false);}};
  const options=():FoundationOptions=>({label,prompt,device,threshold,text_threshold:textThreshold,max_candidates:maxCandidates,output_geometry:geometry,
    positive_examples:positive,negative_examples:negative,points,boxes,...sizes,class_ids:Object.fromEntries(categories.filter(c=>c.id>0&&c.id<=255).map(c=>[c.name,c.id])),...(modelId?{suggestion_model_id:modelId}:{})});
  const generate=()=>run(async()=>{if(!currentImage||!setup)return;const imagePath=currentImage.file_path;
    useModelAssistRunStore.getState().begin();try{const current=await provider.setup();if(!sameProject())return;setSetup(current);const next=await provider.generate(buildFoundationRequest(imagePath,options(),current));if(sameProject()&&useAnnotationStore.getState().currentImage?.file_path===imagePath)onCreated(next);}finally{useModelAssistRunStore.getState().end();}});
  const chooseModel=(field:'mask_model_dir'|'model_dir')=>run(async()=>{const path=await host.selectFolder({title:field==='mask_model_dir'?'로컬 SAM2 모델 폴더':'로컬 Grounding DINO 모델 폴더'});if(path&&sameProject()){const next=await provider.configure({[field]:path});if(sameProject())setSetup(next);}});
  const addExample=(kind:'positive'|'negative',selected=false)=>{
    try{let example:RegionExample|null;
      if(selected)example=currentImage?regionExample(currentImage.file_path,annotations.find(a=>a.id===selectedAnnotationId)):null;
      else{const roi=exampleRoi.split(',').map(v=>Number(v.trim()));if(!examplePath||roi.length!==4||roi.some(v=>!Number.isFinite(v))||roi[2]<=roi[0]||roi[3]<=roi[1])throw new Error('가져온 이미지와 원본 좌표 x1, y1, x2, y2를 선택하세요.');example={image_path:examplePath,roi:roi as RegionExample['roi']};}
      if(!example)throw new Error('선택한 박스 또는 다각형 라벨이 필요합니다.');
      if(kind==='positive')setPositive(old=>[...old,example!]);else setNegative(old=>[...old,example!]);setError('');
    }catch(e){setError(workflowError(e));}
  };
  const train=(refine:boolean)=>run(async()=>{
    const next=await provider.train({device,backbone:setup?.configuration.feature_backbone||'dinov3_vits16',epochs,learning_rate:learningRate,
      ...(checkpoint?{pretrained_checkpoint:checkpoint}:{}),...(checkpointHash?{pretrained_sha256:checkpointHash}:{}),...(refine?{parent_model_id:modelId}:{})});
    if(sameProject()){setFeatureJob(next);localStorage.setItem(`foundation-feature-job:${scope}`,next.id);}
  });
  const loadAll=()=>run(async()=>{if(!folderPath||!project)return;const paths:string[]=[];let total=Infinity;
    while(paths.length<total&&paths.length<5000){const page=await api.dataset.getImages({folder_path:folderPath,task:project.task,limit:500,offset:paths.length});if(!sameProject())return;paths.push(...page.items.map(i=>i.file_path));total=page.total;if(!page.items.length)break;}
    setBatchPaths(new Set(paths));if(paths.length<total)throw new Error('최대 5,000개까지 선택했습니다. 더 큰 데이터는 범위를 나눠 실행하세요.');
  });
  const startBatch=()=>run(async()=>{if(!setup||!batchPaths.size)return;
    // Point/box coordinates are image-specific; a keyword/example batch shares only semantic prompts.
    const current=await provider.setup();if(!sameProject())return;setSetup(current);const next=await provider.startBatch({...buildFoundationRequest('',{...options(),points:[],boxes:[]},current),image_paths:[...batchPaths]});
    if(sameProject()){setBatches(old=>[next,...old]);setBatchId(next.id);}
  });
  const noInference=!currentScope||disabled||busy||running||!currentImage||!foundationAllowed(setup,prompt);
  return <section className="space-y-3 rounded-lg border border-indigo-800 bg-[#101722] p-3" aria-label="SAM2 기반 라벨링">
    <h4 className="font-semibold text-indigo-100">SAM2 mask · 텍스트 · positive/negative 예시</h4>
    <details><summary className="cursor-pointer text-cyan-200">로컬 모델 준비 · {setup?.providers.foundation.ready?'SAM2 준비됨':'SAM2 설정 필요'}</summary>
      <div className="mt-2 space-y-2"><button disabled={busy||running} onClick={()=>void chooseModel('mask_model_dir')} className="rounded border border-indigo-600 px-2 py-1">SAM2 폴더 선택</button><button disabled={busy||running} onClick={()=>void chooseModel('model_dir')} className="ml-2 rounded border border-indigo-600 px-2 py-1">텍스트 모델 폴더 선택</button>
        <p className="break-all text-[10px] text-slate-400">SAM2: {setup?.configuration.mask_model_dir||'미설정'}<br/>Grounding DINO: {setup?.configuration.model_dir||'미설정'}</p>
        <p className="text-amber-200">{setup?.providers.foundation.error}</p><p className="text-amber-200">{setup?.providers.grounding_dino.error}</p>
        <label className="block">DINOv3 checkpoint 경로 (선택)<input aria-label="DINOv3 checkpoint 경로" value={checkpoint} onChange={e=>setCheckpoint(e.target.value)} className={`mt-1 w-full ${inputClass}`}/></label>
        <label className="block">DINOv3 SHA256<input aria-label="DINOv3 checkpoint SHA256" value={checkpointHash} onChange={e=>setCheckpointHash(e.target.value)} className={`mt-1 w-full ${inputClass}`}/></label>
        <button disabled={busy||running} onClick={()=>void run(async()=>{const next=await provider.configure({feature_backbone:'dinov3_vits16',feature_checkpoint:checkpoint||null,feature_sha256:checkpointHash||null});if(sameProject())setSetup(next);})} className="rounded border border-indigo-600 px-2 py-1">DINO 경로 저장</button>
        <p className="text-[10px] text-slate-400">선택한 backend Python 환경에 requirements-semantic-labeling.txt를 설치하고 공식 모델을 로컬 폴더에 준비하세요. 실행 중에는 모델을 다운로드하지 않습니다. 예시 이미지와 few-label 학습은 authentic DINOv3 가중치가 필요합니다.</p>
      </div></details>
    <div className="flex flex-wrap gap-2"><button disabled={!currentImage} onClick={()=>setActiveTool('foundation_point')} className="rounded border border-cyan-700 px-2 py-1">캔버스 점 prompt</button><button disabled={!currentImage} onClick={()=>setActiveTool('foundation_box')} className="rounded border border-cyan-700 px-2 py-1">캔버스 박스 prompt</button>
      <select aria-label="SAM2 점 positive negative" value={pointLabel} onChange={e=>setPointLabel(Number(e.target.value) as 0|1)} className={inputClass}><option value={1}>positive 점</option><option value={0}>negative 점</option></select><button onClick={clear} className="rounded border border-slate-600 px-2 py-1">점·박스 지우기</button></div>
    <p className="text-[10px] text-slate-400">현재 원본 좌표 · 점 {points.length} / 박스 {boxes.length}. 후보를 검토하고 채택하면 브러시 mask 또는 다각형으로 편집할 수 있습니다.</p>
    <label className="block">대상 설명 (선택)<textarea aria-label="텍스트 검출 프롬프트" value={prompt} onChange={e=>setPrompt(e.target.value)} rows={3} placeholder="scratch. crack. ceramic part." className={`mt-1 w-full ${inputClass}`}/></label>
    <p className="text-[10px] text-slate-400">{prompt.length.toLocaleString()}자 · 긴 원문과 모델별 분할 입력을 보존합니다. 영문 Grounding DINO 구문을 사용하세요. 텍스트를 입력하면 SAM2와 텍스트 모델이 모두 준비되어야 합니다.</p>
    <label className="block">후보 클래스<input aria-label="SAM2 후보 클래스" value={label} onChange={e=>setLabel(e.target.value)} className={`ml-2 ${inputClass}`}/></label>
    <details><summary className="cursor-pointer text-cyan-200">이미지 영역 예시 · positive {positive.length} / negative {negative.length}</summary>
      <div className="mt-2 space-y-2"><select aria-label="예시 이미지 선택" value={examplePath} onChange={e=>setExamplePath(e.target.value)} className={`w-full ${inputClass}`}><option value="">가져온 이미지 선택</option>{images.map(i=><option key={i.file_path} value={i.file_path}>{i.file_name}</option>)}</select>
        <input aria-label="예시 이미지 영역" value={exampleRoi} onChange={e=>setExampleRoi(e.target.value)} placeholder="x1, y1, x2, y2 (원본 px)" className={`w-full ${inputClass}`}/>
        <div className="flex flex-wrap gap-2">{(['positive','negative'] as const).map(kind=><React.Fragment key={kind}><button onClick={()=>addExample(kind)} className="rounded border border-slate-600 px-2 py-1">{kind} 예시 추가</button><button onClick={()=>addExample(kind,true)} className="rounded border border-slate-600 px-2 py-1">선택 라벨 → {kind}</button></React.Fragment>)}</div>
        {(['positive','negative'] as const).map(kind=><ul key={kind}>{(kind==='positive'?positive:negative).map((e,i)=><li key={i} className="flex items-center justify-between gap-2 text-[10px]"><span className="truncate">{kind}: {e.image_path.split('/').pop()} [{e.roi.join(', ')}]</span><button aria-label={`${kind} 예시 ${i+1} 제거`} onClick={()=>kind==='positive'?setPositive(v=>v.filter((_,j)=>i!==j)):setNegative(v=>v.filter((_,j)=>i!==j))}>제거</button></li>)}</ul>)}
      </div></details>
    <div className="grid grid-cols-2 gap-2"><label>실행 장치<select aria-label="라벨링 실행 장치" value={device} onChange={e=>setDevice(e.target.value)} className={`mt-1 w-full ${inputClass}`}>{['cpu','auto','mps','cuda:0','cuda:1'].map(d=><option key={d}>{d}</option>)}</select></label><label>출력 형태<select aria-label="SAM2 출력 형태" value={geometry} onChange={e=>setGeometry(e.target.value as typeof geometry)} className={`mt-1 w-full ${inputClass}`}><option value="mask">pixel mask (구멍 유지)</option><option value="polygon">편집 가능한 다각형</option><option value="bbox">박스</option></select></label></div>
    <div className="grid grid-cols-3 gap-2">{[['후보 점수',threshold,setThreshold],['텍스트 점수',textThreshold,setTextThreshold],['최대 후보',maxCandidates,setMaxCandidates]].map(([name,value,setter])=><label key={String(name)}>{String(name)}<input aria-label={String(name)} type="number" min={0} step={String(name)==='최대 후보'?1:.01} value={Number(value)} onChange={e=>(setter as (n:number)=>void)(Number(e.target.value))} className={`mt-1 w-full ${inputClass}`}/></label>)}</div>
    <details><summary className="cursor-pointer text-cyan-200">원본 px 면적·너비·높이 필터</summary><div className="mt-2 grid grid-cols-2 gap-2">{([['min_area','최소 면적'],['max_area','최대 면적'],['min_width','최소 너비'],['max_width','최대 너비'],['min_height','최소 높이'],['max_height','최대 높이']] as const).map(([key,title])=><label key={key}>{title}<input aria-label={`SAM2 ${title}`} type="number" min={0} value={sizes[key]??''} placeholder="제한 없음" onChange={e=>setSizes(old=>({...old,[key]:e.target.value===''?undefined:Number(e.target.value)}))} className={`mt-1 w-full ${inputClass}`}/></label>)}</div></details>
    <label className="block">few-label 후보 분류기<select aria-label="few-label 모델 선택" value={modelId} onChange={e=>setModelId(e.target.value)} className={`mt-1 w-full ${inputClass}`}><option value="">SAM2 기본 후보</option>{visibleModels.map(m=><option key={m.id} value={m.id}>{m.id} · {m.classes.join(', ')} · {m.parent_model_id?'refined':'new'}</option>)}</select></label>
    {modelId&&<p className="break-all text-[10px] text-slate-400">SHA256 {visibleModels.find(m=>m.id===modelId)?.checkpoint_sha256} · 라벨 세트 {visibleModels.find(m=>m.id===modelId)?.labelset_id}</p>}
    <button disabled={noInference||!label.trim()} onClick={()=>void generate()} className="w-full rounded bg-indigo-700 px-3 py-2 font-semibold disabled:opacity-40">{busy?'추론 요청 중…':'현재 이미지 SAM2 후보 생성'}</button>
    <details><summary className="cursor-pointer text-cyan-200">few-label 학습·refine</summary><div className="mt-2 space-y-2"><p className="text-[10px] text-slate-400">저장된 두 클래스 이상 영역이 필요합니다. 배경은 background 클래스로 직접 라벨링하세요. Frozen DINOv3 + 실제 softmax head를 학습하고 refine은 새 모델 버전을 만듭니다.</p>
      <div className="flex gap-2"><label>epochs<input aria-label="few-label epochs" type="number" min={1} max={1000} value={epochs} onChange={e=>setEpochs(Number(e.target.value))} className={`ml-1 w-20 ${inputClass}`}/></label><label>학습률<input aria-label="few-label 학습률" type="number" min={.0001} max={1} step={.001} value={learningRate} onChange={e=>setLearningRate(Number(e.target.value))} className={`ml-1 w-20 ${inputClass}`}/></label></div>
      <button disabled={disabled||busy||running} onClick={()=>void train(false)} className="rounded border border-indigo-600 px-2 py-1 disabled:opacity-40">새 분류기 학습</button><button disabled={disabled||busy||running||!modelId} onClick={()=>void train(true)} className="ml-2 rounded border border-indigo-600 px-2 py-1 disabled:opacity-40">선택 모델 refine</button>
      {currentScope&&featureJob&&<p role="status" className="break-all text-[11px]">{featureJob.id} · {statusNames[featureJob.status]} · 영역 {featureJob.training_regions??'—'} · loss {featureJob.loss??'—'} {labelingJobActive(featureJob.status)&&<button disabled={busy} onClick={()=>void run(async()=>{const j=await provider.cancelTraining(featureJob.id);if(sameProject())setFeatureJob(j);})} className="ml-2 text-amber-300">학습 취소</button>} {featureJob.error&&<span className="block text-red-300">{featureJob.error}</span>}</p>}
    </div></details>
    <details><summary className="cursor-pointer text-cyan-200">키워드·예시 이미지 일괄 후보와 저장 기록</summary><div className="mt-2 space-y-2"><p className="text-[10px] text-slate-400">배치는 공통 텍스트·positive/negative 예시를 사용합니다. 현재 이미지 점·박스는 각 이미지에서 개별 실행하세요.</p>
      <button disabled={busy||running} onClick={()=>void loadAll()} className="rounded border border-slate-600 px-2 py-1">가져온 이미지 전체 선택</button><button onClick={()=>setBatchPaths(new Set())} className="ml-2 rounded border border-slate-600 px-2 py-1">선택 해제</button><span className="ml-2">{batchPaths.size}개</span>
      <div className="max-h-28 overflow-auto">{images.map(i=><label key={i.file_path} className="block truncate"><input type="checkbox" checked={batchPaths.has(i.file_path)} onChange={e=>setBatchPaths(old=>{const next=new Set(old);if(e.target.checked)next.add(i.file_path);else next.delete(i.file_path);return next;})} className="mr-2"/>{i.file_name}</label>)}</div>
      <button disabled={disabled||busy||running||!batchPaths.size||!foundationAllowed(setup,prompt)} onClick={()=>void startBatch()} className="rounded bg-indigo-700 px-3 py-2 disabled:opacity-40">비동기 일괄 후보 시작</button>{activeBatch&&<button disabled={busy||activeBatch.status==='cancelling'} onClick={()=>void run(async()=>{const next=await provider.cancelBatch(activeBatch.id);if(sameProject())setBatches(old=>old.map(b=>b.id===next.id?next:b));})} className="ml-2 rounded border border-amber-600 px-3 py-2 text-amber-200">배치 취소</button>}
      <select aria-label="SAM2 일괄 작업 기록" value={batchId} onChange={e=>setBatchId(e.target.value)} className={`w-full ${inputClass}`}><option value="">저장된 작업 없음</option>{visibleBatches.map(b=><option key={b.id} value={b.id}>{b.created_at} · {statusNames[b.status]} · {b.id}</option>)}</select>
      {batch&&<><p role="status">{statusNames[batch.status]} · {batch.processed}/{batch.total} · 후보 {batch.generated} · 빈 결과 {batch.zero_candidates} · 실패 {batch.failed}</p>{batch.error&&<p className="text-red-300">{batch.error}</p>}<ul className="max-h-40 overflow-auto">{batch.entries.map((e,i)=><li key={i} className="flex justify-between gap-2 py-1"><span className="truncate">{e.image_path.split('/').pop()} · {e.status} ({e.candidate_count??0})</span>{e.proposal_id&&<button onClick={()=>void run(()=>onOpenProposal(e.image_path,e.proposal_id!))} className="text-cyan-200">검토 열기</button>}{e.error&&<span className="text-red-300">{e.error}</span>}</li>)}</ul></>}
    </div></details>
    <p className="text-[10px] text-slate-500">SAM2 IoU·DINO 유사도는 결함 확률로 보정되지 않았습니다. 다각형 외곽선은 내부 구멍을 표현하지 않으며 pixel mask가 구멍을 유지합니다. 장치가 없으면 명시적으로 실패합니다.</p>
    {error&&<p role="alert" className="rounded border border-red-800 p-2 text-red-200">{error}</p>}{notice&&<p role="status">{notice}</p>}
  </section>;
};
