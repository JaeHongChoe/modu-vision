import {useEffect,useRef,useState} from 'react';
import {ArrowDown,ArrowUp,Image as ImageIcon,Trash2} from 'lucide-react';
import {request,getApiPersistenceIdentity,getProjectContextGeneration} from '../../services/api';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useTrainingStore} from '../../stores/useTrainingStore';
import {inputPreparationScope,moveInputOperation,supportsInputPreparation,validateInputRecipe,type FixedInputOperation,type FixedInputRecipe} from './inputPreparation';

type Preview={input_recipe_sha256?:string;source_sha256?:string;source_size:number[];prepared_size:number[];original_image:string;prepared_image:string;original_target:string|null;prepared_target:string|null;target_status:string};
const recipeRevision=(recipe:FixedInputRecipe|null,hash:string|null)=>JSON.stringify([hash,recipe]);
const names={crop:'고정 ROI',rotate90:'고정 회전',resize:'크기 조절',brightness:'밝기',contrast:'대비'};
export function TrainingInputPreparation({task,imageSize=256,disabled=false}:{task:string;imageSize?:number|number[];disabled?:boolean}){
  const project=useProjectStore(state=>state.project),dataset=useDatasetStore();
  const {inputRecipe,inputRecipeScope,inputRecipeSha256,setInputRecipe}=useTrainingStore();
  const identity=getApiPersistenceIdentity(),generation=getProjectContextGeneration();
  const scope=inputPreparationScope({apiIdentity:identity,projectId:project?.id||'',labelsetId:project?.active_labelset_id||'default',source:project?.source_dataset_dir||'',task});
  const persistenceKey=`modu.input-preparation.v1:${identity}:${scope}`;
  const saved=inputRecipeScope===scope?inputRecipe:null;
  const savedHash=inputRecipeScope===scope?inputRecipeSha256:null;
  const savedRevision=recipeRevision(saved,savedHash);
  const editorBase=useRef<{scope:string|null;revision:string;operations:FixedInputOperation[]}>({scope:null,revision:savedRevision,operations:saved?.operations||[]});
  const [conflict,setConflict]=useState(false);
  const [operations,setOperations]=useState<FixedInputOperation[]>(saved?.operations||[]);
  const [imagePath,setImagePath]=useState('');const [preview,setPreview]=useState<{key:string;value:Preview}|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');
  const recipe:FixedInputRecipe={version:1,operations};
  const inputSize=Array.isArray(imageSize)?imageSize:[imageSize,imageSize];
  const currentKey=JSON.stringify([scope,identity,generation,dataset.sourceAuthority,dataset.datasetKey,imagePath,recipe,inputSize]);
  const active=useRef(currentKey);active.current=currentKey;
  const currentPreview=preview?.key===currentKey?preview.value:null;
  useEffect(()=>{
    const base=editorBase.current;
    if(base.scope!==scope){
      let restored:FixedInputRecipe|null=saved,hash:string|null=savedHash;
      if(inputRecipeScope!==scope)try{const records=JSON.parse(localStorage.getItem(persistenceKey)||'[]');const record=records.at(-1);if(record){restored=validateInputRecipe(record.recipe,task);hash=record.sha256;}}catch{/* invalid drafts never replace current model inputs */}
      editorBase.current={scope,revision:recipeRevision(restored,hash),operations:restored?.operations||[]};
      if(restored&&inputRecipeScope!==scope)setInputRecipe(scope,restored,hash);
      setOperations(restored?.operations||[]);setConflict(false);
      setPreview(null);setError('');setNotice('');setBusy(false);setImagePath('');
    }else if(base.revision!==savedRevision){
      if(JSON.stringify(operations)!==JSON.stringify(base.operations)){
        setConflict(true);setPreview(null);setNotice('');
      }else{
        editorBase.current={scope,revision:savedRevision,operations:saved?.operations||[]};
        setOperations(saved?.operations||[]);setConflict(false);setPreview(null);setError('');setNotice('저장한 입력 규칙을 편집기에 반영했습니다.');
      }
    }
  },[scope,savedRevision]); // scope includes API transport, source, task and labelset
  const reloadSaved=()=>{
    editorBase.current={scope,revision:savedRevision,operations:saved?.operations||[]};
    setOperations(saved?.operations||[]);setConflict(false);setPreview(null);setError('');setNotice('');
  };
  useEffect(()=>{setBusy(false);},[currentKey,savedRevision]);
  useEffect(()=>{if(!imagePath&&dataset.images.length)setImagePath(dataset.images[0].file_path);},[dataset.images,imagePath]);
  const edit=(next:FixedInputOperation[])=>{setOperations(next);setNotice('');setError('');};
  const update=(index:number,row:FixedInputOperation)=>edit(operations.map((value,i)=>i===index?row:value));
  const inspect=async()=>{
    const key=currentKey;
    try{if(getApiPersistenceIdentity()!==identity)throw new Error('연결 대상이 바뀌었습니다. 현재 입력 규칙을 다시 확인하세요.');validateInputRecipe(recipe,task);setBusy(true);setError('');
      const value=await request<Preview>('/api/training/input-preparation/preview',{method:'POST',body:JSON.stringify({task,image_path:imagePath,input_recipe:recipe,image_size:inputSize})});
      if(active.current===key&&getProjectContextGeneration()===generation&&getApiPersistenceIdentity()===identity)setPreview({key,value});
    }catch(cause){if(active.current===key)setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(active.current===key)setBusy(false);}
  };
  const save=()=>{
    try{
      const live=useTrainingStore.getState();
      const liveRevision=recipeRevision(live.inputRecipeScope===scope?live.inputRecipe:null,live.inputRecipeScope===scope?live.inputRecipeSha256:null);
      if(getApiPersistenceIdentity()!==identity||editorBase.current.scope!==scope||editorBase.current.revision!==liveRevision||conflict)throw new Error('저장한 규칙이 바뀌었습니다. 저장한 규칙을 불러온 뒤 다시 편집하세요.');
      const value=validateInputRecipe(recipe,task);if(value.operations.length&&!currentPreview)throw new Error('변경한 규칙으로 실제 입력 미리보기를 확인한 뒤 저장하세요.');
      const hash=currentPreview?.input_recipe_sha256||null;
      let records:Array<unknown>=[];try{records=JSON.parse(localStorage.getItem(persistenceKey)||'[]');if(!Array.isArray(records))records=[];}catch{/* first version */}
      localStorage.setItem(persistenceKey,JSON.stringify([...records.slice(-19),{recipe:value,sha256:hash,saved_at:new Date().toISOString()}]));
      editorBase.current={scope,revision:recipeRevision(value.operations.length?value:null,hash),operations:value.operations};
      setConflict(false);setInputRecipe(scope,value.operations.length?value:null,hash);setNotice('다음 학습에 사용할 입력 규칙을 저장했습니다. 학습·검증·시험·검사·내보내기에 함께 고정됩니다.');
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));}
  };
  const changed=JSON.stringify(saved?.operations||[])!==JSON.stringify(operations);
  return <section aria-label="학습 흐름 준비" className="workspace-section text-sm text-[var(--workspace-text)]">
    <h2 className="flex items-center gap-2 font-semibold"><ImageIcon className="h-4 w-4 text-[var(--workspace-accent)]"/>학습 흐름 준비</h2>
    <p className="mt-2 text-[var(--workspace-muted)]">원본·분할 → 고정 입력 규칙 → 모델 입력 {inputSize.join(' × ')} → 학습 전용 무작위 증강 → 모델 학습</p>
    <p className="mt-1 text-xs text-[var(--workspace-muted)]">저장한 모델은 같은 입력 규칙으로 평가·검사합니다. 5단계에서는 학습한 모델로 검사·판정 흐름을 완성합니다.</p>
    {!supportsInputPreparation(task)?<p className="mt-3 text-[var(--workspace-warning)]" role="note">현재 고정 입력 규칙은 분류·영역 분할에서 지원합니다. 검출·OCR·방향·이상 탐지 등은 기존 입력을 사용합니다.</p>:<>
      <fieldset disabled={disabled||busy} className="mt-3 space-y-3">
        <legend className="sr-only">학습 전 고정 입력 규칙</legend>
        <ol className="space-y-2">{operations.map((row,index)=><li key={index} className="workspace-record flex flex-wrap items-center gap-2">
          <strong className="min-w-24">{index+1}. {names[row.op]}</strong>
          {row.op==='crop'&&row.roi.map((value,i)=><label key={i} className="workspace-field max-w-24">{['왼쪽','위','오른쪽','아래'][i]} <input aria-label={`${index+1}번 ROI ${['왼쪽','위','오른쪽','아래'][i]}`} type="number" min="0" max="1" step="0.05" className="tabular-nums" value={value} onChange={event=>{const roi=[...row.roi] as typeof row.roi;roi[i]=Number(event.target.value);update(index,{...row,roi});}}/></label>)}
          {row.op==='rotate90'&&<label className="workspace-field max-w-40">반시계 <select aria-label={`${index+1}번 회전 각도`} value={row.turns} className="tabular-nums" onChange={event=>update(index,{...row,turns:Number(event.target.value)})}><option value={1}>90°</option><option value={2}>180°</option><option value={3}>270°</option></select></label>}
          {(row.op==='brightness'||row.op==='contrast')&&<label className="workspace-field max-w-24">배율 <input aria-label={`${index+1}번 ${names[row.op]} 배율`} type="number" min="0.25" max="4" step="0.1" className="tabular-nums" value={row.factor} onChange={event=>update(index,{...row,factor:Number(event.target.value)})}/></label>}
          {row.op==='resize'&&(['width','height'] as const).map(key=><label key={key} className="workspace-field max-w-24">{key==='width'?'너비':'높이'} <input aria-label={`${index+1}번 ${key==='width'?'너비':'높이'}`} type="number" min="16" max="4096" step="16" className="tabular-nums" value={row[key]} onChange={event=>update(index,{...row,[key]:Number(event.target.value)})}/></label>)}
          <button type="button" aria-label={`${index+1}번 규칙 위로`} disabled={index===0} className="workspace-button" onClick={()=>edit(moveInputOperation(operations,index,-1))}><ArrowUp className="h-3 w-3"/></button>
          <button type="button" aria-label={`${index+1}번 규칙 아래로`} disabled={index===operations.length-1} className="workspace-button" onClick={()=>edit(moveInputOperation(operations,index,1))}><ArrowDown className="h-3 w-3"/></button>
          <button type="button" aria-label={`${index+1}번 규칙 삭제`} className="workspace-button" onClick={()=>edit(operations.filter((_,i)=>i!==index))}><Trash2 className="h-3 w-3"/></button>
        </li>)}</ol>
        {!operations.length&&<p className="text-xs text-[var(--workspace-muted)]">추가 규칙 없음 · 기존 모델 입력 크기와 내부 정규화를 사용합니다.</p>}
        <div className="flex flex-wrap gap-2">{([{op:'crop',roi:[0,0,1,1]},{op:'rotate90',turns:1},{op:'brightness',factor:1},{op:'contrast',factor:1},{op:'resize',width:256,height:256}] as FixedInputOperation[]).map(row=><button type="button" key={row.op} disabled={operations.length>=16} className="workspace-button" onClick={()=>edit([...operations,row])}>+ {names[row.op]}</button>)}</div>
        <label className="workspace-field max-w-xl">실제 미리보기 이미지 <select className="max-w-full" value={imagePath} onChange={event=>setImagePath(event.target.value)}>{!dataset.images.length&&<option value="">1단계에서 이미지를 불러오세요</option>}{dataset.images.map(image=><option key={image.file_path} value={image.file_path}>{image.file_name}{image.label?` · ${image.label}`:''}</option>)}</select></label>
        <div className="flex flex-wrap gap-2"><button type="button" disabled={!imagePath} className="workspace-button" onClick={()=>void inspect()}>{busy?'입력 확인 중…':'실제 입력 미리보기'}</button><button type="button" className="workspace-button workspace-button--primary" disabled={conflict||(operations.length>0&&!currentPreview)} onClick={save}>규칙 저장</button><button type="button" className="workspace-button" onClick={reloadSaved}>편집 취소</button></div>
      </fieldset>
      {currentPreview&&<div className="mt-3 grid gap-3 sm:grid-cols-2">{[['원본',currentPreview.original_target||currentPreview.original_image,currentPreview.source_size],['준비한 입력',currentPreview.prepared_target||currentPreview.prepared_image,currentPreview.prepared_size]].map(([label,url,size])=><figure key={String(label)} className="workspace-record"><figcaption className="mb-2 text-xs">{String(label)} · {(size as number[]).join(' × ')}{task==='segmentation'?' · 청록: 정답 영역':''}</figcaption><img className="h-48 w-full object-contain" src={String(url)} alt={`${label} ${task==='segmentation'?'이미지와 정답 영역':'학습 이미지'}`}/></figure>)}</div>}
      <p className="mt-3 text-xs text-[var(--workspace-muted)]">{changed?'저장하지 않은 변경이 있습니다. 다음 학습은 마지막 저장 규칙을 사용합니다.':saved?`저장한 규칙 ${inputRecipeSha256?.slice(0,12)||''} · 다음 학습에 적용`:'기존 입력 사용'} · 규칙을 바꾸면 새 학습·평가가 필요합니다. 기존 모델과 평가 기록은 보존됩니다.</p>
      {operations.some(row=>row.op==='crop')&&<p className="mt-1 text-xs text-[var(--workspace-muted)]">ROI 밖의 영역은 검사하지 않습니다. 분할 마스크는 원본 좌표로 되돌리고 제외 영역은 배경으로 둡니다.</p>}
      {conflict&&<div role="alert" className="workspace-record mt-2 text-[var(--workspace-warning)]"><p>도우미 또는 다른 작업에서 저장한 규칙이 바뀌었습니다. 편집 내용은 보관했습니다. 저장한 규칙을 불러온 뒤 다시 편집하세요.</p><button type="button" className="workspace-button mt-2" onClick={reloadSaved}>저장한 규칙 불러오기</button></div>}
      {notice&&<p role="status" className="mt-2 text-[var(--workspace-accent)]">{notice}</p>}{error&&<p role="alert" className="mt-2 text-[var(--workspace-danger)]">{error}</p>}
    </>}
  </section>;
}
