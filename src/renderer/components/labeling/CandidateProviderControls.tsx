import React,{useEffect,useId,useState} from 'react';
import {api} from '../../services/api';
import {host} from '../../services/hostAdapter';
import {workflowError} from '../../services/datasetWorkflow';
import {foundationLabelingApi as provider,labelingJobActive,type FoundationSetup,type FoundationOptions,type FoundationProposal,type FoundationBatch,type FeatureModel,type FeatureJob,type ProviderReadiness,type RegionExample,type SizeControls} from '../../services/foundationLabelingApi';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useModelAssistRunStore} from '../../stores/useModelAssistRunStore';
import {useFoundationPromptStore} from '../../stores/useFoundationPromptStore';
import {foundationAllowed,buildFoundationRequest,regionExample,labelingScope,quickPointClassKey,quickPointClassKeyOf} from './foundationRequest';
interface Props{disabled:boolean;onCreated:(proposal:FoundationProposal)=>void;onOpenProposal:(path:string,id:string)=>Promise<void>}
const inputClass='rounded border border-slate-600 bg-slate-900 p-1.5 text-slate-100';
const statusNames:Record<string,string>={queued:'대기',running:'실행 중',cancelling:'취소 중',completed:'완료',stopped:'취소됨',failed:'실패',interrupted:'재시작으로 중단'};
/** The SAM2 preparation card's states, in the words of step 3's weights card. */
export type Sam2CardState='checking'|'ready'|'install'|'missing'|'incomplete'|'damaged'|'path'|'device';
export const sam2StateLabels:Record<Sam2CardState,string>={checking:'확인 중',ready:'준비됨',install:'설치 필요',missing:'파일 없음',incomplete:'파일 불완전',damaged:'파일 손상',path:'경로 오류',device:'장치 없음'};
/** The one card button a cause names; 'folder' is the folder dialog on the desktop and the typed path in the browser. */
export type Sam2CardAction='verify'|'folder'|'official'|null;
/** Measured size for the checklist and causes; below 1KB the bytes themselves, so a 134B git-lfs pointer never reads as 1KB. */
const fileSize=(bytes:number)=>bytes<1024?`${Math.max(0,bytes)}B`:bytes>=1048576?`${(bytes/1048576).toFixed(1)}MB`:`${Math.round(bytes/1024)}KB`;
// Ready is the backend's gate (config.json and model.safetensors found); nothing has opened the weights yet, so the cause claims no more.
const SAM2_READY_CAUSE='SAM2 모델 파일을 찾았습니다. 캔버스에 점·박스를 찍고 후보를 생성하세요.';
const SAM2_INSTALL_CAUSE='백엔드 Python 환경에 transformers 라벨링 의존성이 없습니다. requirements-semantic-labeling.txt를 설치한 뒤 파일 확인을 누르세요.';
const SAM2_DEVICE_CAUSE='선택한 실행 장치를 쓸 수 없습니다. CPU 또는 사용 가능한 GPU를 고르세요.';
const SAM2_DAMAGE:Record<string,string>={empty:'빈 파일입니다',not_safetensors:'safetensors 형식이 아닙니다(git-lfs 포인터 등)',truncated:'헤더에 적힌 크기와 맞지 않습니다(받다 끊긴 파일 등)'};
// An older backend names no state; its error texts are stable, so the first blocker is read from their prefixes.
const LEGACY_SAM2_ERRORS:[string,Sam2CardState,string,Sam2CardAction][]=[
  ['SAM2 requires the optional transformers','install',SAM2_INSTALL_CAUSE,'verify'],
  ['Configure mask_model_dir','missing','SAM2 모델 폴더가 지정되지 않았거나 없습니다. 공식 페이지에서 받은 파일이 있는 폴더를 선택하세요.','folder'],
  ['SAM2 config.json and model.safetensors','incomplete','config.json 또는 model.safetensors가 없거나 SAM2 모델이 아닙니다. 공식 페이지에서 같은 모델의 파일을 받아 이 폴더에 두세요.','official'],
  ['SAM2 model configuration','incomplete','config.json이 없거나 읽을 수 없습니다. 공식 페이지에서 같은 모델의 파일을 받아 이 폴더에 두세요.','official'],
  ['Device ','device',SAM2_DEVICE_CAUSE,null],
];
/** The card's state, Korean cause and next action for one readiness ('이유. 다음 행동.'); null readiness means not read yet. */
export function sam2ReadinessView(readiness:ProviderReadiness|null|undefined):{state:Sam2CardState;label:string;cause:string;action:Sam2CardAction;warning:string|null;missing:string[]}{
  const missing=readiness?.missing_files??readiness?.expected_files?.filter(file=>!file.present).map(file=>file.name)??[];
  const finish=(state:Sam2CardState,cause:string,action:Sam2CardAction)=>({state,label:sam2StateLabels[state],cause,action,missing,
    // The gate (ready) is unchanged: the processor file is read when the model loads, so a ready folder without it is named here.
    warning:state==='ready'&&missing.includes('preprocessor_config.json')?'preprocessor_config.json이 없어 후보를 생성할 때 모델을 불러오지 못합니다. 공식 페이지에서 이 파일을 받아 같은 폴더에 두세요.':null});
  // The backend gate passes any model.safetensors file; one whose header does not account for its size is named here, before a click.
  const ready=()=>{
    const check=readiness?.weights_check;if(!check||check==='ok')return finish('ready',SAM2_READY_CAUSE,null);
    const bytes=readiness?.expected_files?.find(file=>file.name==='model.safetensors')?.bytes;
    return finish('damaged',`model.safetensors${bytes!=null?`(측정 크기 ${fileSize(bytes)})`:''}가 ${SAM2_DAMAGE[check]??'손상되었습니다'}. 공식 페이지에서 model.safetensors를 다시 받아 이 폴더에 두세요.`,'official');
  };
  // Not read yet is not a file verdict: the label stays neutral until GET /setup answers.
  if(!readiness)return finish('checking','모델 준비 상태를 아직 읽지 못했습니다. 파일 확인을 누르세요.','verify');
  switch(readiness.state){
    case 'ready':return ready();
    case 'dependency_missing':return finish('install',SAM2_INSTALL_CAUSE,'verify');
    case 'not_configured':return finish('missing','SAM2 모델 폴더를 아직 지정하지 않았습니다. 공식 페이지에서 받은 파일이 있는 폴더를 선택하세요.','folder');
    case 'path_invalid':return finish('path','지정한 경로가 폴더가 아니거나 읽을 수 없습니다. 폴더를 다시 선택하세요.','folder');
    case 'files_missing':return finish('missing','선택한 폴더에 SAM2 파일이 없습니다. 아래 목록의 파일을 이 폴더에 두세요.','official');
    case 'files_incomplete':return finish('incomplete',`없는 파일: ${missing.join(', ')}. 공식 페이지에서 같은 모델의 파일을 받아 이 폴더에 두세요.`,'official');
    case 'invalid_config':return finish('incomplete','config.json을 읽을 수 없습니다. 공식 페이지의 config.json으로 바꾸세요.','official');
    case 'wrong_model_type':return finish('incomplete',`config.json의 model_type이 ${readiness.model_type||'(없음)'}이라 SAM2 모델이 아닙니다. SAM2 모델 폴더를 선택하세요.`,'folder');
    case 'device_unavailable':return finish('device',SAM2_DEVICE_CAUSE,null);
    default:{
      if(readiness.ready)return ready();
      const legacy=LEGACY_SAM2_ERRORS.find(([prefix])=>readiness.error?.startsWith(prefix));
      return legacy?finish(legacy[1],legacy[2],legacy[3]):finish('missing',(readiness.error||'SAM2 모델이 준비되지 않았습니다.')+' 모델 폴더를 선택하세요.','folder');
    }
  }
}
/** Why a generate button is off, or null when it can run: the reason sits beside the disabled button instead of an error after a click. */
export function generateBlockedReason(input:{setup:FoundationSetup|null;prompt:string;currentScope:boolean;disabled:boolean;busy:boolean;running:boolean;hasImage:boolean;label:string},kind:'image'|'batch',selected=0):string|null{
  if(!input.currentScope)return '프로젝트가 바뀌어 준비 상태를 다시 읽는 중입니다. 잠시 뒤 다시 시도하세요.';
  if(!input.setup)return '모델 준비 상태를 읽는 중입니다. 잠시 기다리세요.';
  // The card's own cause is the action, so each state gets its own next step; a damaged weight file blocks here too.
  const sam2=sam2ReadinessView(input.setup.providers.foundation);
  if(!input.setup.providers.foundation.ready||sam2.state==='damaged')return `SAM2 모델이 준비되지 않았습니다(${sam2.label}). ${sam2.cause}`;
  if(input.prompt.trim()&&!input.setup.providers.grounding_dino.ready)return '대상 설명을 쓰려면 Grounding DINO 텍스트 모델도 필요합니다. 설명을 비우거나 텍스트 모델 폴더를 선택하세요.';
  if(input.disabled)return '저장하지 않은 라벨이 있거나 다른 작업이 진행 중입니다. 저장이 끝난 뒤 다시 시도하세요.';
  if(input.busy||input.running)return '요청이 진행 중입니다. 끝난 뒤 다시 시도하세요.';
  if(kind==='image'){
    if(!input.hasImage)return '열린 이미지가 없습니다. 이미지를 선택하세요.';
    if(!input.label.trim())return '후보 클래스가 비어 있습니다. 클래스 이름을 입력하세요.';
  }else if(!selected)return '선택한 이미지가 없습니다. 일괄 실행할 이미지를 고르세요.';
  return null;
}
export const CandidateProviderControls:React.FC<Props>=({disabled,onCreated,onOpenProposal})=>{
  const {currentImage,annotations,selectedAnnotationId,setActiveTool,activeCategory,categories}=useAnnotationStore();
  const {images,folderPath}=useDatasetStore();const project=useProjectStore(s=>s.project);const projectDir=useProjectStore(s=>s.projectDir);
  const scope=labelingScope({projectDir,project});const [loadedScope,setLoadedScope]=useState(scope);const currentScope=loadedScope===scope;
  const {points,boxes,pointLabel,setPointLabel,clear,bind,bindProject,projectScope:promptScope,quickOptions:rememberedOptions,quickPointEnabled,setQuickPointEnabled,configureQuickPoint}=useFoundationPromptStore();
  // The popover unmounts when closed. Its opted-in settings survive only in this visit's scope/class-bound snapshot.
  // Compared like the point controller does (quickPointClassKey): a reload that only reorders/recolours keeps them.
  const restored=quickPointEnabled&&promptScope===scope&&quickPointClassKeyOf(rememberedOptions?.categoryContext)===quickPointClassKey(activeCategory,categories)?rememberedOptions:null;
  const initial=restored?.options;
  const [setup,setSetup]=useState<FoundationSetup|null>(null);const [prompt,setPrompt]=useState(initial?.prompt??'');const [label,setLabel]=useState(initial?.label??'defect');
  const [positive,setPositive]=useState<RegionExample[]>(initial?.positive_examples??[]);const [negative,setNegative]=useState<RegionExample[]>(initial?.negative_examples??[]);
  const [examplePath,setExamplePath]=useState('');const [exampleRoi,setExampleRoi]=useState('');
  const [exclusions,setExclusions]=useState<{imagePath:string;regions:number[][]}>(restored&&restored.imagePath===currentImage?.file_path?{imagePath:restored.imagePath,regions:initial?.exclude_regions??[]}:{imagePath:'',regions:[]});
  const [excludeRoi,setExcludeRoi]=useState('');
  const excludeRegions=exclusions.imagePath===currentImage?.file_path?exclusions.regions:[];
  const [device,setDevice]=useState(initial?.device??'cpu');const [geometry,setGeometry]=useState<'mask'|'polygon'|'bbox'>(initial?.output_geometry??'mask');const [threshold,setThreshold]=useState(initial?.threshold??.5);const [textThreshold,setTextThreshold]=useState(initial?.text_threshold??.25);const [maxCandidates,setMaxCandidates]=useState(initial?.max_candidates??20);
  const [sizes,setSizes]=useState<SizeControls>({min_area:initial?.min_area??0,min_width:initial?.min_width??0,min_height:initial?.min_height??0,max_area:initial?.max_area,max_width:initial?.max_width,max_height:initial?.max_height});
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const [featureModels,setFeatureModels]=useState<FeatureModel[]>([]);const [modelId,setModelId]=useState(initial?.suggestion_model_id??'');const [featureJob,setFeatureJob]=useState<FeatureJob|null>(null);
  const [epochs,setEpochs]=useState(100);const [learningRate,setLearningRate]=useState(.05);
  const [checkpoint,setCheckpoint]=useState('');const [checkpointHash,setCheckpointHash]=useState('');
  const [maskDir,setMaskDir]=useState('');const [textDir,setTextDir]=useState('');const reasonId=useId();
  const [batches,setBatches]=useState<FoundationBatch[]>([]);const [batchId,setBatchId]=useState('');const [batchPaths,setBatchPaths]=useState<Set<string>>(new Set());
  const visibleBatches=currentScope?batches:[];const visibleModels=currentScope?featureModels:[];
  const activeBatch=visibleBatches.find(b=>labelingJobActive(b.status));const batch=visibleBatches.find(b=>b.id===batchId);
  const running=(currentScope&&labelingJobActive(featureJob?.status))||!!activeBatch;
  const sameProject=()=>labelingScope(useProjectStore.getState())===scope;
  useEffect(()=>{bind(currentImage?.file_path||'');setExclusions(previous=>previous.imagePath===currentImage?.file_path?previous:{imagePath:'',regions:[]});setExcludeRoi('');},[bind,currentImage?.file_path]);
  useEffect(()=>{if(!restored)setExclusions({imagePath:'',regions:[]});setExcludeRoi('');},[scope]);
  useEffect(()=>{if(!restored)setLabel(activeCategory.name);},[activeCategory.name]);
  // A pixel mask is a segmentation label (readiness refuses it for detection training): a detection project starts on boxes.
  useEffect(()=>{if(!restored)setGeometry(project?.task==='detection'?'bbox':'mask');},[project?.task]);
  useEffect(()=>{
    let active=true;setLoadedScope(scope);setBusy(false);setSetup(null);setBatches([]);setBatchId('');setFeatureJob(null);setFeatureModels([]);if(!restored){setPositive([]);setNegative([]);setModelId('');}setBatchPaths(new Set());setExamplePath('');setExampleRoi('');setCheckpoint('');setCheckpointHash('');setMaskDir('');setTextDir('');setError('');setNotice('');bindProject(scope);
    const remembered=localStorage.getItem(`foundation-feature-job:${scope}`);
    void Promise.all([provider.setup(),provider.featureModels(),provider.batches(),provider.featureJobs().then(result=>result.jobs.find(j=>j.id===remembered)||result.jobs[0]||null)])
      .then(([s,m,b,j])=>{if(active&&sameProject()){setSetup(s);setFeatureModels(m.models);setBatches(b.batches);setBatchId(b.batches[0]?.id||'');setFeatureJob(j);setCheckpoint(s.configuration.feature_checkpoint||'');setCheckpointHash(s.configuration.feature_sha256||'');setMaskDir(s.configuration.mask_model_dir||'');setTextDir(s.configuration.model_dir||'');}})
      .catch(e=>{if(active&&sameProject())setError(workflowError(e));});return()=>{active=false;};
  },[scope,bindProject]);
  useEffect(()=>{
    if(!running)return;useModelAssistRunStore.getState().begin();let active=true;
    const poll=async()=>{try{
      if(activeBatch){const next=await provider.batch(activeBatch.id);if(active&&sameProject())setBatches(old=>old.map(b=>b.id===next.id?next:b));}
      if(featureJob&&labelingJobActive(featureJob.status)){
        const next=await provider.training(featureJob.id);
        // Fetch the completed model before changing running. Marking the job
        // terminal first cleans up this effect while this request is in flight.
        const models=next.status==='completed'?await provider.featureModels():null;
        if(active&&sameProject()){
          if(models){setFeatureModels(models.models);setModelId(next.model_id||'');}
          setFeatureJob(next);
        }
      }
    }catch(e){if(active&&sameProject())setError(workflowError(e));}};
    const timer=window.setInterval(()=>void poll(),1000);return()=>{active=false;window.clearInterval(timer);useModelAssistRunStore.getState().end();};
  },[running,activeBatch?.id,featureJob?.id,scope]);
  const run=async(action:()=>Promise<void>,disarmQuick=false)=>{if(!currentScope||!sameProject())return;setBusy(true);setError('');setNotice('');if(disarmQuick&&useFoundationPromptStore.getState().quickPointEnabled){setQuickPointEnabled(false);setNotice('다른 작업을 시작해 점 입력 후 후보 만들기를 껐습니다. 작업이 끝나면 설정을 확인하고 다시 켜세요.');}try{await action();}catch(e){if(sameProject())setError(workflowError(e));}finally{if(sameProject())setBusy(false);}};
  const options=():FoundationOptions=>({label,prompt,device,threshold,text_threshold:textThreshold,max_candidates:maxCandidates,output_geometry:geometry,
    positive_examples:positive,negative_examples:negative,points,boxes,exclude_regions:excludeRegions,...sizes,class_ids:Object.fromEntries(categories.filter(c=>c.id>0&&c.id<=255).map(c=>[c.name,c.id])),...(modelId?{suggestion_model_id:modelId}:{})});
  // Keep the selected settings after this popover closes. Points/boxes are read atomically from the prompt store when
  // the next canvas point arrives; changing settings invalidates an older request without cancelling it.
  const quickOptions=JSON.stringify({...options(),points:[],boxes:[]});
  const quickCategory=JSON.stringify([activeCategory,categories]);
  const quickTaskSupported=project?.task==='detection'||project?.task==='segmentation';
  const quickReady=quickTaskSupported&&foundationAllowed(setup,prompt)&&sam2ReadinessView(setup?.providers.foundation).state!=='damaged';
  useEffect(()=>{
    // Checking is not an unavailable model: closing before setup answers must keep the last validated snapshot.
    if(!setup||!currentScope)return;
    configureQuickPoint(scope,currentImage?.file_path||'',JSON.parse(quickOptions),quickReady,quickCategory);
    if(!quickReady&&useFoundationPromptStore.getState().quickPointEnabled){setQuickPointEnabled(false);setNotice('모델 준비 상태가 바뀌어 점 입력 후 후보 만들기를 껐습니다. 모델·프로젝트를 확인하고 다시 켜세요.');}
  },[configureQuickPoint,setQuickPointEnabled,scope,currentImage?.file_path,quickOptions,quickReady,quickCategory,setup,currentScope]);
  const addExclusion=(selected=false)=>{
    if(!currentImage||!currentScope||!sameProject())return;
    try{
      const roi=selected?regionExample(currentImage.file_path,annotations.find(a=>a.id===selectedAnnotationId))?.roi:excludeRoi.split(',').map(v=>v.trim()===''?NaN:Number(v));
      if(!roi||roi.length!==4||roi.some(v=>!Number.isFinite(v))||roi[0]<0||roi[1]<0||roi[2]<=roi[0]||roi[3]<=roi[1])throw new Error('제외 영역의 원본 좌표 x1, y1, x2, y2 또는 박스·다각형 라벨을 선택하세요.');
      if(excludeRegions.length>=100)throw new Error('제외 영역은 이미지당 최대 100개입니다.');
      setExclusions({imagePath:currentImage.file_path,regions:[...excludeRegions,[...roi]]});setError('');
    }catch(e){setError(workflowError(e));}
  };
  const generate=()=>run(async()=>{if(!currentImage||!setup)return;const imagePath=currentImage.file_path;
    useModelAssistRunStore.getState().begin();try{const current=await provider.setup();if(!sameProject())return;setSetup(current);const next=await provider.generate(buildFoundationRequest(imagePath,options(),current));if(sameProject()&&useAnnotationStore.getState().currentImage?.file_path===imagePath)onCreated(next);}finally{useModelAssistRunStore.getState().end();}});
  const applySetup=(next:FoundationSetup)=>{setSetup(next);setMaskDir(next.configuration.mask_model_dir||'');setTextDir(next.configuration.model_dir||'');};
  const chooseModel=(field:'mask_model_dir'|'model_dir')=>run(async()=>{const path=await host.selectFolder({purpose:'model-directory',title:field==='mask_model_dir'?'로컬 SAM2 모델 폴더':'로컬 Grounding DINO 모델 폴더'});if(path&&sameProject()){const next=await provider.configure({[field]:path});if(sameProject())applySetup(next);}},true);
  // The typed path is the browser/shared-server way to point at a folder; the backend only checks that it is a directory.
  const saveFolder=(field:'mask_model_dir'|'model_dir',path:string)=>run(async()=>{
    try{const next=await provider.configure({[field]:path.trim()});if(sameProject())applySetup(next);}
    catch(e){throw new Error(/existing local model directory/.test(workflowError(e))?'지정한 경로를 폴더로 찾지 못했습니다. 백엔드가 읽을 수 있는 폴더 경로를 입력하세요.':workflowError(e));}
  },true);
  const saveMaskDir=()=>saveFolder('mask_model_dir',maskDir);
  // Verify re-reads readiness: file presence, sizes and the weight file's SHA256 (identification only; nothing is downloaded).
  const verify=()=>run(async()=>{const next=await provider.setup();if(sameProject())setSetup(next);},true);
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
    if(sameProject()){
      setFeatureJob(next);localStorage.setItem(`foundation-feature-job:${scope}`,next.id);
      // A small real fit may finish before the start response is serialized.
      // Such a terminal response never starts the active-job polling effect.
      if(next.status==='completed'){
        const models=await provider.featureModels();
        if(sameProject()){setFeatureModels(models.models);setModelId(next.model_id||'');}
      }
    }
  },true);
  const loadAll=()=>run(async()=>{if(!folderPath||!project)return;const paths:string[]=[];let total=Infinity;
    while(paths.length<total&&paths.length<5000){const page=await api.dataset.getImages({folder_path:folderPath,task:project.task,limit:500,offset:paths.length});if(!sameProject())return;paths.push(...page.items.map(i=>i.file_path));total=page.total;if(!page.items.length)break;}
    setBatchPaths(new Set(paths));if(paths.length<total)throw new Error('최대 5,000개까지 선택했습니다. 더 큰 데이터는 범위를 나눠 실행하세요.');
  });
  const startBatch=()=>run(async()=>{if(!setup||!batchPaths.size)return;
    // Point/box coordinates are image-specific; a keyword/example batch shares only semantic prompts.
    const current=await provider.setup();if(!sameProject())return;setSetup(current);const next=await provider.startBatch({...buildFoundationRequest('',{...options(),points:[],boxes:[],exclude_regions:[]},current),image_paths:[...batchPaths]});
    if(sameProject()){setBatches(old=>[next,...old]);setBatchId(next.id);}
  },true);
  const foundation=setup?.providers.foundation;const view=sam2ReadinessView(foundation);const official=foundation?.official_reference;
  // The backend gate passes a damaged weight file; the loader would refuse it after the click, so both generate buttons stop here.
  const damaged=view.state==='damaged';
  const noInference=!currentScope||disabled||busy||running||!currentImage||!foundationAllowed(setup,prompt)||damaged;
  const blocked={setup,prompt,currentScope,disabled,busy,running,hasImage:!!currentImage,label};
  const imageReason=generateBlockedReason(blocked,'image');const batchReason=generateBlockedReason(blocked,'batch',batchPaths.size);
  // Ready is one collapsed line (step 3's '준비됨을 한 줄로'); anything to fix keeps the card open. The folder can still be changed when ready.
  const cardOpen=view.state!=='ready'||!!view.warning||!!foundation?.expected_files?.some(file=>file.present&&file.bytes===0);
  // The one button the cause names is the card's primary action; the rest stay outlined. text-white keeps it legible in the light theme.
  const canPick=host.can('pickPaths');
  const cardButton=(primary:boolean)=>`rounded px-2 py-1 disabled:opacity-40 ${primary?'bg-indigo-700 font-semibold text-white':'border border-indigo-600'}`;
  return <section className="space-y-3 rounded-lg border border-indigo-800 bg-[#101722] p-3" aria-label="SAM2 기반 라벨링">
    <h4 className="font-semibold text-indigo-100">SAM2 mask · 텍스트 · positive/negative 예시</h4>{host.localBrowser?.()&&<p className="mt-2 text-xs text-slate-400">선택한 반입 자료는 프로젝트 작업 폴더에 검증·복사합니다. 원본은 그대로 보관합니다.</p>}
    {/* Step 3's weights card, for SAM2: state, Korean cause, the loader's file list, the folder, and the weight file's identification. The app never downloads a model. */}
    <section aria-label="SAM2 모델 준비" className="rounded border border-slate-600 bg-[#0D1420] p-2">
      <details open={cardOpen}><summary className="cursor-pointer"><strong role="status" className={view.state==='ready'?'text-emerald-300':view.state==='checking'?'text-slate-300':'text-amber-200'}>SAM2 모델 준비 · {view.label}</strong></summary>
      <div className="mt-2 space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2"><p className="text-xs text-slate-300">{view.cause}</p><button type="button" disabled={busy||running} onClick={()=>void verify()} className={cardButton(view.action==='verify')}>파일 확인</button></div>
      {foundation?.expected_files&&<ul aria-label="SAM2 필요 파일" className="text-[11px]">{foundation.expected_files.map(file=><li key={file.name} className={file.present&&file.bytes!==0&&!(damaged&&file.name==='model.safetensors')?'text-emerald-200':'text-amber-200'}>{file.present?'✓':'✗'} {file.name}{file.present&&file.bytes!=null?` · ${fileSize(file.bytes)}${file.bytes===0?' · 빈 파일 · 다시 받으세요':''}`:''}</li>)}</ul>}
      <p className="text-[11px] text-slate-400">크기·라이선스는 공식 페이지에서 확인하세요. 앱은 모델 파일을 내려받지 않습니다. 파일을 직접 받아 한 폴더에 두고 그 폴더를 지정하세요.</p>
      <label className="block text-[11px]">SAM2 모델 폴더<input aria-label="SAM2 모델 폴더 경로" value={maskDir} onChange={e=>setMaskDir(e.target.value)} placeholder="config.json, preprocessor_config.json, model.safetensors가 있는 폴더" className={`mt-1 w-full ${inputClass}`}/></label>
      <div className="flex flex-wrap items-center gap-2">
        {canPick&&<button type="button" disabled={busy||running} onClick={()=>void chooseModel('mask_model_dir')} className={cardButton(view.action==='folder')}>SAM2 폴더 선택</button>}
        <button type="button" disabled={busy||running||!maskDir.trim()} onClick={()=>void saveMaskDir()} className={cardButton(view.action==='folder'&&!canPick)}>경로 저장</button>
        {official?.url?<button type="button" onClick={()=>void host.openLink(official.url!)} className={cardButton(view.action==='official')}>공식 모델 페이지 열기</button>:<span className="text-[11px] text-slate-400">{official?.family||'SAM2'} · 공식 배포처의 모델 페이지에서 파일을 받으세요.</span>}
      </div>
      {foundation?.model_sha256&&<p className="break-all text-[11px]" aria-label="SAM2 파일 식별 정보">model.safetensors · SHA256: {foundation.model_sha256}<span className="block text-slate-400">{foundation.official_match&&foundation.official_pinned?`공식 고정 판(${foundation.official_pinned.repo_id}@${foundation.official_pinned.revision.slice(0,7)}, ${foundation.official_pinned.license})의 파일과 같습니다. 품질 승인을 뜻하지 않습니다.`:'파일 내용의 식별값입니다. 제공처 진위·모델 구조·품질 승인을 뜻하지 않습니다.'}</span></p>}
      {view.warning&&<p className="text-xs text-amber-200">{view.warning}</p>}
      </div></details>
    </section>
    <details><summary className="cursor-pointer text-cyan-200">텍스트 모델·DINOv3 설정 · {setup?.providers.grounding_dino.ready?'텍스트 모델 준비됨':'텍스트 모델 미설정'}</summary>
      <div className="mt-2 space-y-2">{canPick&&<button disabled={busy||running} onClick={()=>void chooseModel('model_dir')} className="rounded border border-indigo-600 px-2 py-1">텍스트 모델 폴더 선택</button>}
        {/* The typed path is the browser/shared-server way, as for SAM2. */}
        <label className="block text-[11px]">Grounding DINO 모델 폴더<input aria-label="Grounding DINO 모델 폴더 경로" value={textDir} onChange={e=>setTextDir(e.target.value)} placeholder="config.json, model.safetensors, tokenizer 파일이 있는 폴더" className={`mt-1 w-full ${inputClass}`}/></label>
        <button type="button" disabled={busy||running||!textDir.trim()} onClick={()=>void saveFolder('model_dir',textDir)} className="rounded border border-indigo-600 px-2 py-1">텍스트 모델 경로 저장</button>
        <p className="break-all text-[10px] text-slate-400">Grounding DINO: {setup?.configuration.model_dir||'미설정'}{setup?.providers.grounding_dino.official_match?' · 공식 고정 파일과 같음':''}</p>
        <p className="text-amber-200">{setup?.providers.grounding_dino.error}</p>
        <label className="block">DINOv3 checkpoint 경로 (선택)<input aria-label="DINOv3 checkpoint 경로" value={checkpoint} onChange={e=>setCheckpoint(e.target.value)} className={`mt-1 w-full ${inputClass}`}/></label>
        <label className="block">DINOv3 SHA256<input aria-label="DINOv3 checkpoint SHA256" value={checkpointHash} onChange={e=>setCheckpointHash(e.target.value)} className={`mt-1 w-full ${inputClass}`}/></label>
        <button disabled={busy||running} onClick={()=>void run(async()=>{const next=await provider.configure({feature_backbone:'dinov3_vits16',feature_checkpoint:checkpoint||null,feature_sha256:checkpointHash||null});if(sameProject())setSetup(next);},true)} className="rounded border border-indigo-600 px-2 py-1">DINO 경로 저장</button>
        <p className="text-[10px] text-slate-400">선택한 backend Python 환경에 requirements-semantic-labeling.txt를 설치하고 공식 모델을 로컬 폴더에 준비하세요. 실행 중에는 모델을 다운로드하지 않습니다. 예시 이미지와 few-label 학습은 authentic DINOv3 가중치가 필요합니다.</p>
      </div></details>
    <div className="flex flex-wrap gap-2"><button disabled={!currentImage} onClick={()=>setActiveTool('foundation_point')} className="rounded border border-cyan-700 px-2 py-1">캔버스 점 prompt</button><button disabled={!currentImage} onClick={()=>setActiveTool('foundation_box')} className="rounded border border-cyan-700 px-2 py-1">캔버스 박스 prompt</button>
      <select aria-label="SAM2 점 positive negative" value={pointLabel} onChange={e=>setPointLabel(Number(e.target.value) as 0|1)} className={inputClass}><option value={1}>positive 점</option><option value={0}>negative 점</option></select><button onClick={clear} className="rounded border border-slate-600 px-2 py-1">점·박스 지우기</button></div>
    <p className="text-[10px] text-slate-400">현재 원본 좌표 · 점 {points.length} / 박스 {boxes.length}. 후보를 검토하고 채택하면 브러시 mask 또는 다각형으로 편집할 수 있습니다.</p>
    <label className="flex items-center gap-2"><input type="checkbox" aria-label="점 입력 후 후보 만들기" checked={quickPointEnabled} disabled={!quickPointEnabled&&(!quickTaskSupported||noInference||!label.trim())} onChange={event=>setQuickPointEnabled(event.target.checked)}/>점 입력 후 후보 만들기</label>
    {!quickTaskSupported&&<p className="text-[11px] text-amber-200">점 입력 후 후보 만들기는 검출·분할 프로젝트에서만 사용할 수 있습니다.</p>}
    <p className="text-[11px] text-slate-400">이번 방문 동안 새 점을 찍으면 현재 설정으로 후보를 만듭니다. 결과를 확인하고 직접 채택하세요. 실행 중 추가한 점은 끝난 뒤 후보 생성 버튼으로 다시 만드세요.</p>
    <details><summary className="cursor-pointer text-cyan-200">현재 이미지 제외 영역 · {excludeRegions.length}개</summary><div className="mt-2 space-y-2">
      <input aria-label="제외할 원본 영역" value={excludeRoi} onChange={e=>setExcludeRoi(e.target.value)} placeholder="x1, y1, x2, y2 (원본 px)" className={`w-full ${inputClass}`}/>
      <button disabled={!currentScope||disabled||!currentImage||busy||running} onClick={()=>addExclusion()} className="rounded border border-slate-600 px-2 py-1">제외 영역 추가</button><button disabled={!currentScope||disabled||!currentImage||busy||running} onClick={()=>addExclusion(true)} className="ml-2 rounded border border-slate-600 px-2 py-1">선택 라벨의 박스 영역 제외</button>
      <ul>{excludeRegions.map((box,i)=><li key={i} className="flex justify-between text-[10px]"><span>제외 {i+1}: [{box.join(', ')}]</span><button disabled={busy||running} aria-label={`제외 영역 ${i+1} 제거`} onClick={()=>setExclusions({imagePath:currentImage?.file_path||'',regions:excludeRegions.filter((_,j)=>i!==j)})}>제거</button></li>)}</ul>
      <p className="text-[10px] text-slate-400">제외 사각형과 겹치는 원본 픽셀을 mask에서 제거한 뒤 크기를 검사합니다. 다각형 라벨은 둘러싼 박스로 제외합니다. 내부 구멍은 pixel mask에서 보존됩니다. 이미지 변경 시 초기화되며 일괄 후보에는 적용되지 않습니다.</p>
    </div></details>
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
    <button disabled={noInference||!label.trim()} aria-describedby={imageReason?reasonId:undefined} onClick={()=>void generate()} className="w-full rounded bg-indigo-700 px-3 py-2 font-semibold disabled:opacity-40">{busy?'추론 요청 중…':'현재 이미지 SAM2 후보 생성'}</button>
    {imageReason&&<p id={reasonId} className="text-xs text-amber-200">{imageReason}</p>}
    <details><summary className="cursor-pointer text-cyan-200">few-label 학습·refine</summary><div className="mt-2 space-y-2"><p className="text-[10px] text-slate-400">저장된 두 클래스 이상 영역이 필요합니다. 배경은 background 클래스로 직접 라벨링하세요. Frozen DINOv3 + 실제 softmax head를 학습하고 refine은 새 모델 버전을 만듭니다.</p>
      <div className="flex gap-2"><label>epochs<input aria-label="few-label epochs" type="number" min={1} max={1000} value={epochs} onChange={e=>setEpochs(Number(e.target.value))} className={`ml-1 w-20 ${inputClass}`}/></label><label>학습률<input aria-label="few-label 학습률" type="number" min={.0001} max={1} step={.001} value={learningRate} onChange={e=>setLearningRate(Number(e.target.value))} className={`ml-1 w-20 ${inputClass}`}/></label></div>
      <button disabled={disabled||busy||running} onClick={()=>void train(false)} className="rounded border border-indigo-600 px-2 py-1 disabled:opacity-40">새 분류기 학습</button><button disabled={disabled||busy||running||!modelId} onClick={()=>void train(true)} className="ml-2 rounded border border-indigo-600 px-2 py-1 disabled:opacity-40">선택 모델 refine</button>
      {currentScope&&featureJob&&<p role="status" className="break-all text-[11px]">{featureJob.id} · {statusNames[featureJob.status]} · 영역 {featureJob.training_regions??'—'} · loss {featureJob.loss??'—'} {labelingJobActive(featureJob.status)&&<button disabled={busy} onClick={()=>void run(async()=>{const j=await provider.cancelTraining(featureJob.id);if(sameProject())setFeatureJob(j);})} className="ml-2 text-amber-300">학습 취소</button>} {featureJob.error&&<span className="block text-red-300">{featureJob.error}</span>}</p>}
    </div></details>
    <details><summary className="cursor-pointer text-cyan-200">키워드·예시 이미지 일괄 후보와 저장 기록</summary><div className="mt-2 space-y-2"><p className="text-[10px] text-slate-400">배치는 공통 텍스트·positive/negative 예시를 사용합니다. 현재 이미지 점·박스·제외 영역은 각 이미지에서 개별 실행하세요.</p>
      <button disabled={busy||running} onClick={()=>void loadAll()} className="rounded border border-slate-600 px-2 py-1">가져온 이미지 전체 선택</button><button onClick={()=>setBatchPaths(new Set())} className="ml-2 rounded border border-slate-600 px-2 py-1">선택 해제</button><span className="ml-2">{batchPaths.size}개</span>
      <div className="max-h-28 overflow-auto">{images.map(i=><label key={i.file_path} className="block truncate"><input type="checkbox" checked={batchPaths.has(i.file_path)} onChange={e=>setBatchPaths(old=>{const next=new Set(old);if(e.target.checked)next.add(i.file_path);else next.delete(i.file_path);return next;})} className="mr-2"/>{i.file_name}</label>)}</div>
      <button disabled={disabled||busy||running||!batchPaths.size||!foundationAllowed(setup,prompt)||damaged} onClick={()=>void startBatch()} className="rounded bg-indigo-700 px-3 py-2 disabled:opacity-40">비동기 일괄 후보 시작</button>{activeBatch&&<button disabled={busy||activeBatch.status==='cancelling'} onClick={()=>void run(async()=>{const next=await provider.cancelBatch(activeBatch.id);if(sameProject())setBatches(old=>old.map(b=>b.id===next.id?next:b));})} className="ml-2 rounded border border-amber-600 px-3 py-2 text-amber-200">배치 취소</button>}
      {batchReason&&<p className="text-xs text-amber-200">{batchReason}</p>}
      <select aria-label="SAM2 일괄 작업 기록" value={batchId} onChange={e=>setBatchId(e.target.value)} className={`w-full ${inputClass}`}><option value="">저장된 작업 없음</option>{visibleBatches.map(b=><option key={b.id} value={b.id}>{b.created_at} · {statusNames[b.status]} · {b.id}</option>)}</select>
      {batch&&<><p role="status">{statusNames[batch.status]} · {batch.processed}/{batch.total} · 후보 {batch.generated} · 빈 결과 {batch.zero_candidates} · 실패 {batch.failed}</p>{batch.error&&<p className="text-red-300">{batch.error}</p>}<ul className="max-h-40 overflow-auto">{batch.entries.map((e,i)=><li key={i} className="flex justify-between gap-2 py-1"><span className="truncate">{e.image_path.split('/').pop()} · {e.status} ({e.candidate_count??0})</span>{e.proposal_id&&<button onClick={()=>void run(()=>onOpenProposal(e.image_path,e.proposal_id!))} className="text-cyan-200">검토 열기</button>}{e.error&&<span className="text-red-300">{e.error}</span>}</li>)}</ul></>}
    </div></details>
    <p className="text-[10px] text-slate-500">SAM2 IoU·DINO 유사도는 결함 확률로 보정되지 않았습니다. 다각형 외곽선은 내부 구멍을 표현하지 않으며 pixel mask가 구멍을 유지합니다. 장치가 없으면 명시적으로 실패합니다.</p>
    {error&&<p role="alert" className="rounded border border-red-800 p-2 text-red-200">{error}</p>}{notice&&<p role="status">{notice}</p>}
  </section>;
};
