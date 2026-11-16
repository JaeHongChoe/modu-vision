import {useEffect,useRef,useState} from 'react';
import {ArrowDown,ArrowUp,Image as ImageIcon,Trash2} from 'lucide-react';
import {request,getApiPersistenceIdentity,getProjectContextGeneration} from '../../services/api';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useTrainingStore} from '../../stores/useTrainingStore';
import {useAssistantStore} from '../../stores/useAssistantStore';
import {inputPreparationContract} from '../../services/assistantInputPreparation';
import {inputCodeSize,inputPreparationScope,moveInputOperation,supportsInputPreparation,validateInputRecipe,type FixedInputOperation,type FixedInputRecipe} from './inputPreparation';

type Preview={input_recipe_sha256?:string;source_sha256?:string;source_size:number[];prepared_size:number[];original_image:string;prepared_image:string;original_target:string|null;prepared_target:string|null;target_status:string};
const recipeRevision=(recipe:FixedInputRecipe|null,hash:string|null)=>JSON.stringify([hash,recipe]);
const names={crop:'고정 ROI',rotate90:'고정 회전',resize:'크기 조절',brightness:'밝기',contrast:'대비',code:'코드 변환'};
export function TrainingInputPreparation({task,imageSize=256,disabled=false}:{task:string;imageSize?:number|number[];disabled?:boolean}){
  const project=useProjectStore(state=>state.project),dataset=useDatasetStore();
  const {inputRecipe,inputRecipeScope,inputRecipeSha256,setInputRecipe}=useTrainingStore();
  const identity=getApiPersistenceIdentity(),generation=getProjectContextGeneration();
  const sourceAuthority=dataset.sourceAuthority,datasetKey=dataset.datasetKey;
  const scope=inputPreparationScope({apiIdentity:identity,projectId:project?.id||'',labelsetId:project?.active_labelset_id||'default',source:project?.source_dataset_dir||'',task});
  const persistenceKey=`modu.input-preparation.v1:${identity}:${scope}`;
  const saved=inputRecipeScope===scope?inputRecipe:null;
  const savedHash=inputRecipeScope===scope?inputRecipeSha256:null;
  const savedRevision=recipeRevision(saved,savedHash);
  const editorBase=useRef<{scope:string|null;revision:string;operations:FixedInputOperation[]}>({scope:null,revision:savedRevision,operations:saved?.operations||[]});
  const [conflict,setConflict]=useState(false);
  const [operations,setOperations]=useState<FixedInputOperation[]>(saved?.operations||[]);
  const [selectedOperation,setSelectedOperation]=useState(0);
  const [codeRequest,setCodeRequest]=useState('');
  const [codeRequestError,setCodeRequestError]=useState('');
  const operationButtons=useRef(new Map<number,HTMLButtonElement>());
  const focusOperation=useRef(false);
  const firstAddButton=useRef<HTMLButtonElement|null>(null);
  useEffect(()=>{if(focusOperation.current){focusOperation.current=false;(operationButtons.current.get(selectedOperation)||firstAddButton.current)?.focus();}},[selectedOperation,operations]);
  const [imagePath,setImagePath]=useState('');const [preview,setPreview]=useState<{key:string;value:Preview}|null>(null);
  const [previewMode,setPreviewMode]=useState<'all'|'block'>('all');
  const [blockPreview,setBlockPreview]=useState<{key:string;through:number;value:Preview}|null>(null);
  const previewSequence=useRef(0),previewPending=useRef(false),mounted=useRef(true),disabledRef=useRef(disabled);
  disabledRef.current=disabled;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;previewSequence.current++;};},[]);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');
  const recipe:FixedInputRecipe={version:1,operations};
  const inputSize=Array.isArray(imageSize)?imageSize:[imageSize,imageSize];
  const currentKey=JSON.stringify([scope,identity,generation,dataset.sourceAuthority,dataset.datasetKey,imagePath,recipe,inputSize]);
  const active=useRef(currentKey);active.current=currentKey;
  const helpKey=JSON.stringify([currentKey,selectedOperation,codeRequest]);
  const activeHelp=useRef(helpKey);activeHelp.current=helpKey;
  const codeContract=inputPreparationContract(task);
  const currentPreview=preview?.key===currentKey?preview.value:null;
  const through=Math.min(selectedOperation+1,operations.length);
  const currentBlockPreview=blockPreview?.key===currentKey&&blockPreview.through===through?blockPreview.value:null;
  const visiblePreview=previewMode==='all'?currentPreview:currentBlockPreview||(through===operations.length?currentPreview:null);
  useEffect(()=>{
    const base=editorBase.current;
    if(base.scope!==scope){
      let restored:FixedInputRecipe|null=saved,hash:string|null=savedHash;
      if(inputRecipeScope!==scope)try{const records=JSON.parse(localStorage.getItem(persistenceKey)||'[]');const record=records.at(-1);if(record){restored=validateInputRecipe(record.recipe,task);hash=record.sha256;}}catch{/* invalid drafts never replace current model inputs */}
      editorBase.current={scope,revision:recipeRevision(restored,hash),operations:restored?.operations||[]};
      if(restored&&inputRecipeScope!==scope)setInputRecipe(scope,restored,hash);
      setOperations(restored?.operations||[]);setConflict(false);
      setSelectedOperation(0);
      setCodeRequest('');
      setCodeRequestError('');
      setPreview(null);setBlockPreview(null);setPreviewMode('all');setError('');setNotice('');setBusy(false);setImagePath('');
    }else if(base.revision!==savedRevision){
      if(JSON.stringify(operations)!==JSON.stringify(base.operations)){
        setConflict(true);setPreview(null);setNotice('');
      }else{
        editorBase.current={scope,revision:savedRevision,operations:saved?.operations||[]};
        setOperations(saved?.operations||[]);setSelectedOperation(0);setConflict(false);setPreview(null);setError('');setNotice('저장한 입력 규칙을 편집기에 반영했습니다.');
      }
    }
  },[scope,savedRevision]); // scope includes API transport, source, task and labelset
  const reloadSaved=()=>{
    editorBase.current={scope,revision:savedRevision,operations:saved?.operations||[]};
    setOperations(saved?.operations||[]);setSelectedOperation(0);setConflict(false);setPreview(null);setError('');setNotice('');
  };
  useEffect(()=>{previewSequence.current++;previewPending.current=false;setBusy(false);},[currentKey,savedRevision]);
  useEffect(()=>{if(!imagePath&&dataset.images.length)setImagePath((dataset.images.find(image=>image.split==='train')||dataset.images[0]).file_path);},[dataset.images,imagePath]);
  const edit=(next:FixedInputOperation[])=>{setOperations(next);setNotice('');setError('');setCodeRequestError('');};
  const update=(index:number,row:FixedInputOperation)=>edit(operations.map((value,i)=>i===index?row:value));
  const add=(row:FixedInputOperation)=>{focusOperation.current=true;setSelectedOperation(operations.length);edit([...operations,row]);};
  const move=(index:number,delta:number)=>{focusOperation.current=true;setSelectedOperation(index+delta);edit(moveInputOperation(operations,index,delta));};
  const remove=(index:number)=>{focusOperation.current=true;setSelectedOperation(Math.max(0,Math.min(selectedOperation,operations.length-2)));edit(operations.filter((_,i)=>i!==index));};
  const requestCodeHelp=()=>{
    const row=operations[selectedOperation],live=useTrainingStore.getState();
    if(!mounted.current||disabledRef.current||previewPending.current||live.isTraining||live.isRecoveringTraining||activeHelp.current!==helpKey||getProjectContextGeneration()!==generation||getApiPersistenceIdentity()!==identity||row?.op!=='code'||!codeRequest.trim())return;
    const liveProject=useProjectStore.getState(),liveDataset=useDatasetStore.getState();
    const liveScope=inputPreparationScope({apiIdentity:getApiPersistenceIdentity(),projectId:liveProject.project?.id||'',labelsetId:liveProject.project?.active_labelset_id||'default',source:liveProject.project?.source_dataset_dir||'',task:liveProject.task});
    if(liveScope!==scope||liveDataset.sourceAuthority!==sourceAuthority||liveDataset.datasetKey!==datasetKey)return;
    setCodeRequestError('');
    const prompt=[
      '현재 사진 전처리의 코드 블록을 작성·수정해 주세요.',
      `요청: ${codeRequest.trim()}`,
      `selected_operation_index: ${selectedOperation} (0부터 시작). 이 코드 블록만 요청에 맞게 바꾸고 다른 블록의 값·코드와 순서를 보존하세요.`,
      `지원 언어: ${codeContract.language}. Python·JavaScript 실행 환경이 아닌 제한된 수치식입니다.`,
      'image는 float64 RGB(0~255)이며 result에 같은 H×W×3 크기의 유한한 결과를 넣습니다. 정답 마스크와 이미지 크기는 바꾸지 않습니다.',
      `허용 함수: ${codeContract.functions.join(', ')}. 코드 한도: ${codeContract.limits.code_utf8_bytes} UTF-8 바이트·${codeContract.limits.lines}줄. import·파일·네트워크·시스템 호출·루프는 지원하지 않습니다.`,
      `현재 편집 중인 전체 input_recipe:\n\`\`\`json\n${JSON.stringify(recipe,null,2)}\n\`\`\``,
      `FFT 시작 예시(현장 성능 미검증):\n${codeContract.fft_example.code}`,
      '설정할 때는 현재 프로젝트·원본·라벨셋의 전체 입력 규칙을 실제 미리보기로 검증하세요. 부분 미리보기는 전체 저장 근거가 아니며, 저장 또는 실행을 확인한 기록 없이 완료했다고 말하지 마세요.',
    ].join('\n\n');
    if(prompt.length>20_000){setNotice('');setCodeRequestError('전체 규칙과 요청이 도우미 작성칸의 20,000자 한도를 넘습니다. 코드나 요청 길이를 줄여 다시 시도하세요. 현재 편집 내용은 유지됩니다.');return;}
    if(useAssistantStore.getState().openWithDraft(prompt)===false){setNotice('');setCodeRequestError('도우미 작성칸에 요청을 넣지 못했습니다. 요청 길이와 도우미 상태를 확인한 뒤 다시 시도하세요.');return;}
    setNotice('요청문과 전체 규칙을 도우미 작성칸에 넣었습니다. 도우미에서 보내기를 눌러 진행하세요.');
  };
  const inspect=async()=>{
    const key=currentKey;
    const live=useTrainingStore.getState();
    if(disabledRef.current||previewPending.current||live.isTraining||live.isRecoveringTraining||active.current!==key||getProjectContextGeneration()!==generation)return;
    const sequence=++previewSequence.current;
    const owns=()=>mounted.current&&active.current===key&&previewSequence.current===sequence&&getProjectContextGeneration()===generation&&getApiPersistenceIdentity()===identity;
    const prefix=previewMode==='block'?through:operations.length;
    const complete=prefix===operations.length;
    previewPending.current=true;
    try{
      if(getApiPersistenceIdentity()!==identity)throw new Error('연결 대상이 바뀌었습니다. 현재 입력 규칙을 다시 확인하세요.');
      validateInputRecipe(recipe,task);setBusy(true);setError('');
      const value=await request<Preview>('/api/training/input-preparation/preview',{method:'POST',body:JSON.stringify({task,image_path:imagePath,input_recipe:{...recipe,operations:operations.slice(0,prefix)},image_size:inputSize})});
      if(owns()){
        if(complete)setPreview({key,value});
        setBlockPreview({key,through:prefix,value});
      }
    }catch(cause){if(owns())setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(owns()){previewPending.current=false;setBusy(false);}}
  };
  const save=()=>{
    try{
      const live=useTrainingStore.getState();
      if(!mounted.current||active.current!==currentKey||disabledRef.current||previewPending.current||live.isTraining||live.isRecoveringTraining||getProjectContextGeneration()!==generation)return;
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
  return <section aria-label="학습 흐름 준비" className="training-input-editor text-sm text-[var(--workspace-text)]">
    <h2 className="sr-only"><ImageIcon className="h-4 w-4 text-[var(--workspace-accent)]"/>사진 전처리</h2>
    <p className="training-input-editor__intro text-[var(--workspace-muted)]">변환 블록을 선택해 설정하세요. 원본은 보존하고, 미리보기로 확인한 전체 규칙을 학습에 사용합니다.</p>

    {!supportsInputPreparation(task)?<p className="mt-3 text-[var(--workspace-warning)]" role="note">현재 고정 입력 규칙은 분류·영역 분할에서 지원합니다. 검출·OCR·방향·이상 탐지 등은 기존 입력을 사용합니다.</p>:<>
      <fieldset disabled={disabled||busy} className="training-input-editor__workspace">
        <legend className="sr-only">학습 전 고정 입력 규칙</legend>
        <div className="training-input-editor__pipeline flex flex-wrap items-center gap-2" aria-label="전처리 블록 순서">
          <span className="workspace-badge">원본 사진</span>
          {operations.map((row,index)=><span key={index} className="inline-flex items-center gap-2"><span aria-hidden="true">→</span><button type="button" className="workspace-button" data-input-operation={index} ref={button=>{if(button)operationButtons.current.set(index,button);else operationButtons.current.delete(index);}} aria-pressed={selectedOperation===index} onClick={()=>setSelectedOperation(index)}>{index+1}. {names[row.op]}</button></span>)}
          <span aria-hidden="true">→</span><span className="workspace-badge">모델 입력 {inputSize.join(' × ')}</span>
        </div>
        <div className="training-input-editor__settings"><h3 className="font-semibold mb-3">선택 블록 설정</h3><ol className="space-y-2">{operations.map((row,index)=><li key={index} data-operation-settings={index} hidden={selectedOperation!==index} className={selectedOperation===index?'workspace-record flex flex-wrap items-center gap-2':'hidden'}>
          <strong className="min-w-24">{index+1}. {names[row.op]}</strong>
          {row.op==='crop'&&row.roi.map((value,i)=><label key={i} className="workspace-field max-w-24">{['왼쪽','위','오른쪽','아래'][i]} <input aria-label={`${index+1}번 ROI ${['왼쪽','위','오른쪽','아래'][i]}`} type="number" min="0" max="1" step="0.05" className="tabular-nums" value={value} onChange={event=>{const roi=[...row.roi] as typeof row.roi;roi[i]=Number(event.target.value);update(index,{...row,roi});}}/></label>)}
          {row.op==='rotate90'&&<label className="workspace-field max-w-40">반시계 <select aria-label={`${index+1}번 회전 각도`} value={row.turns} className="tabular-nums" onChange={event=>update(index,{...row,turns:Number(event.target.value)})}><option value={1}>90°</option><option value={2}>180°</option><option value={3}>270°</option></select></label>}
          {(row.op==='brightness'||row.op==='contrast')&&<label className="workspace-field max-w-24">배율 <input aria-label={`${index+1}번 ${names[row.op]} 배율`} type="number" min="0.25" max="4" step="0.1" className="tabular-nums" value={row.factor} onChange={event=>update(index,{...row,factor:Number(event.target.value)})}/></label>}
          {row.op==='resize'&&(['width','height'] as const).map(key=><label key={key} className="workspace-field max-w-24">{key==='width'?'너비':'높이'} <input aria-label={`${index+1}번 ${key==='width'?'너비':'높이'}`} type="number" min="16" max="4096" step="16" className="tabular-nums" value={row[key]} onChange={event=>update(index,{...row,[key]:Number(event.target.value)})}/></label>)}
          {row.op==='code'&&<div className="training-input-editor__code">
            <p>코드를 직접 쓰지 않아도 FFT 예시에서 시작하거나 도우미에게 원하는 변화를 설명할 수 있습니다.</p>
            <div className="training-input-editor__code-tools">
              <button type="button" className="workspace-button" onClick={()=>update(index,{...row,code:codeContract.fft_example.code})}>FFT 예시 넣기</button>
              <button type="button" className="workspace-button workspace-button--quiet" onClick={()=>update(index,{...row,code:codeContract.operation.code})}>기본 코드로 되돌리기</button>
            </div>
            <p className="text-xs text-[var(--workspace-muted)]">예시는 특정 반복무늬를 줄이는 시작점입니다. 제거 효과와 결함 보존은 검증되지 않았으므로 학습 사진으로 비교하세요.</p>
            <p className="text-xs text-[var(--workspace-muted)]">코드에 들어가는 사진은 최대 {codeContract.limits.input_pixels.toLocaleString('ko-KR')}픽셀입니다. 큰 사진은 이 코드 앞에 크기 조절이나 ROI 블록을 놓으세요.</p>
            <details className="training-input-editor__code-disclosure"><summary>코드 보기·직접 수정</summary>
              <label className="workspace-field">전처리 코드
                <textarea aria-label={`${index+1}번 전처리 코드`} rows={8} maxLength={8192} spellCheck={false}
                  className="training-input-editor__code-source font-mono resize-none" value={row.code} onChange={event=>update(index,{...row,code:event.currentTarget.value})}/>
              </label>
              <p className="text-xs text-[var(--workspace-muted)]">{inputCodeSize(row.code).lines.toLocaleString('ko-KR')} / 32줄 · {inputCodeSize(row.code).bytes.toLocaleString('ko-KR')} / 8,192 UTF-8 바이트</p>
              <p className="text-xs text-[var(--workspace-muted)]"><code>image</code>는 0~255의 RGB 입력이며 <code>result</code>에 같은 크기의 결과를 넣습니다. 기본 코드는 입력을 그대로 통과시킵니다.</p>
              <details><summary>지원하는 수치식과 함수</summary><p className="text-xs text-[var(--workspace-muted)]">변수 대입·숫자·사칙연산·비교식을 사용할 수 있습니다. {codeContract.functions.join(' · ')}. import·파일·네트워크·루프와 일반 Python·JavaScript는 지원하지 않습니다.</p><p className="text-xs text-[var(--workspace-muted)]"><code>{codeContract.notch_mask.signature}</code> · fx·fy는 반복무늬의 주파수, radius는 제거 범위입니다. 기본값이 모든 사진에 맞는 것은 아닙니다.</p></details>
            </details>
          </div>}
          <button type="button" aria-label={`${index+1}번 규칙 위로`} disabled={index===0} className="workspace-button" onClick={()=>move(index,-1)}><ArrowUp className="h-3 w-3"/></button>
          <button type="button" aria-label={`${index+1}번 규칙 아래로`} disabled={index===operations.length-1} className="workspace-button" onClick={()=>move(index,1)}><ArrowDown className="h-3 w-3"/></button>
          <button type="button" aria-label={`${index+1}번 규칙 삭제`} className="workspace-button" onClick={()=>remove(index)}><Trash2 className="h-3 w-3"/></button>
        </li>)}</ol>
        {operations[selectedOperation]?.op==='code'&&<section className="training-input-editor__code-assistant" aria-label="코드 작성 도우미">
          <label className="workspace-field">도우미에게 요청할 내용<textarea aria-label="도우미에게 요청할 내용" className="resize-none" rows={3} maxLength={1200} value={codeRequest}
            placeholder="예: 반복되는 줄무늬를 줄이고 작은 흠집은 남겨줘" onChange={event=>{setCodeRequest(event.currentTarget.value);setCodeRequestError('');}}/></label>
          <button type="button" className="workspace-button" disabled={!codeRequest.trim()} onClick={requestCodeHelp}>도우미에게 요청</button>
          <p className="text-xs text-[var(--workspace-muted)]">요청과 현재 편집 중인 전체 규칙을 도우미 작성칸에 넣습니다. 다른 블록은 보존하도록 요청합니다. 도우미에서 보내기를 눌러 진행하세요. 이미지는 함께 보내지 않습니다.</p>
          {codeRequestError&&<p role="alert" className="text-xs text-[var(--workspace-danger)]">{codeRequestError}</p>}
        </section>}
        {!operations.length&&<p className="text-xs text-[var(--workspace-muted)]">추가 규칙 없음 · 기존 모델 입력 크기와 내부 정규화를 사용합니다.</p>}
        <div className="flex flex-wrap gap-2" aria-label="전처리 블록 추가">{([{op:'crop',roi:[0,0,1,1]},{op:'rotate90',turns:1},{op:'brightness',factor:1},{op:'contrast',factor:1},{op:'resize',width:256,height:256}] as FixedInputOperation[]).map(row=><button type="button" key={row.op} ref={row.op==='crop'?firstAddButton:undefined} disabled={operations.length>=16} className="workspace-button" onClick={()=>add(row)}>+ {names[row.op]}</button>)}</div>
        <button type="button" className="workspace-button mt-2" disabled={operations.length>=16} onClick={()=>add({op:'code',language:'modu-numeric-v1',code:codeContract.operation.code})}>+ 코드 블록</button>
        </div>
        <div className="training-input-editor__preview-controls">
        <label className="workspace-field max-w-xl">실제 미리보기 이미지 <select className="max-w-full" value={imagePath} onChange={event=>setImagePath(event.target.value)}>{!dataset.images.length&&<option value="">1단계에서 이미지를 불러오세요</option>}{dataset.images.map(image=><option key={image.file_path} value={image.file_path}>{image.file_name}{image.split?` · ${{train:'학습',val:'검증',test:'시험'}[image.split]||image.split}`:''}{image.label?` · ${image.label}`:''}</option>)}</select></label>
        {dataset.images.some(image=>image.file_path===imagePath&&(image.split==='val'||image.split==='test'))&&<p role="note" className="text-xs text-[var(--workspace-warning)]">검증·시험 사진입니다. 변환을 조정할 때는 학습 사진을 선택해 시험 결과의 독립성을 지키세요.</p>}
        {operations.length>0&&<label className="workspace-field">미리보기 범위<select aria-label="미리보기 범위" value={previewMode} onChange={event=>setPreviewMode(event.target.value==='block'?'block':'all')}><option value="all">전체 규칙 → 모델 입력</option><option value="block">{through}번 블록까지 → 모델 입력</option></select></label>}
        <div className="flex flex-wrap gap-2"><button type="button" disabled={!imagePath} className="workspace-button" onClick={()=>void inspect()}>{busy?'입력 확인 중…':previewMode==='block'?'선택 블록 미리보기':'실제 입력 미리보기'}</button><button type="button" className="workspace-button workspace-button--primary" disabled={conflict||(operations.length>0&&!currentPreview)} onClick={save}>규칙 저장</button><button type="button" className="workspace-button" onClick={reloadSaved}>편집 취소</button></div>
        </div>
      {previewMode==='block'&&operations.length>0&&<p role="note" className="training-input-editor__preview-note text-xs text-[var(--workspace-muted)]">{through}번 블록까지 적용한 뒤 모델 입력 크기로 맞춘 결과입니다. {through<operations.length?'이후 블록은 포함하지 않습니다. 전체 규칙을 미리본 뒤 저장하세요.':'모든 블록을 포함하므로 이 결과로 전체 규칙을 저장할 수 있습니다.'}</p>}
      {visiblePreview&&<div className="training-input-editor__comparison mt-3 grid gap-3 sm:grid-cols-2">{[['원본',visiblePreview.original_target||visiblePreview.original_image,visiblePreview.source_size],['준비한 입력',visiblePreview.prepared_target||visiblePreview.prepared_image,visiblePreview.prepared_size]].map(([label,url,size])=><figure key={String(label)} className="workspace-record"><figcaption className="mb-2 text-xs">{String(label)} · {(size as number[]).join(' × ')}{task==='segmentation'?' · 청록: 정답 영역':''}</figcaption><img className="w-full object-contain" src={String(url)} alt={`${label} ${task==='segmentation'?'이미지와 정답 영역':'학습 이미지'}`}/></figure>)}</div>}
      {!visiblePreview&&<div className="training-input-editor__comparison training-input-editor__empty" role="status"><ImageIcon className="h-6 w-6" aria-hidden="true"/><p>원본과 변환 결과를 여기서 비교합니다.</p><p className="text-xs">미리볼 사진과 범위를 고른 뒤 미리보기를 실행하세요.</p></div>}
      </fieldset>
      <p className="mt-3 text-xs text-[var(--workspace-muted)]">{changed?'저장하지 않은 변경이 있습니다. 다음 학습은 마지막 저장 규칙을 사용합니다.':saved?`저장한 규칙 ${inputRecipeSha256?.slice(0,12)||''} · 다음 학습에 적용`:'기존 입력 사용'} · 규칙을 바꾸면 새 학습·평가가 필요합니다. 기존 모델과 평가 기록은 보존됩니다.</p>
      <details className="mt-3 text-xs text-[var(--workspace-muted)]"><summary className="cursor-pointer">학습용 사진 늘리기와 검사 흐름은 어떻게 다른가요?</summary><p className="mt-2">선택한 모델이 제공하는 무작위 증강은 학습 사진에만 적용합니다. 검증·시험 사진은 늘리지 않습니다. 학습이 끝난 뒤 검사 사용에서 모델과 판정 규칙을 연결합니다.</p></details>
      {operations.some(row=>row.op==='crop')&&<p className="mt-1 text-xs text-[var(--workspace-muted)]">ROI 밖의 영역은 검사하지 않습니다. 분할 마스크는 원본 좌표로 되돌리고 제외 영역은 배경으로 둡니다.</p>}
      {conflict&&<div role="alert" className="workspace-record mt-2 text-[var(--workspace-warning)]"><p>도우미 또는 다른 작업에서 저장한 규칙이 바뀌었습니다. 편집 내용은 보관했습니다. 저장한 규칙을 불러온 뒤 다시 편집하세요.</p><button type="button" className="workspace-button mt-2" onClick={reloadSaved}>저장한 규칙 불러오기</button></div>}
      {notice&&<p role="status" className="mt-2 text-[var(--workspace-accent)]">{notice}</p>}{error&&<p role="alert" className="mt-2 text-[var(--workspace-danger)]">{error}</p>}
    </>}
  </section>;
}
