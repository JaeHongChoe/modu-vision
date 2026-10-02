import {useEffect,useRef,useState} from 'react';
import {flowEvaluation,formatFlowMetric,isCompatibleFlowCohort,type FlowEvaluation,type FlowImageEvidence,type ImageTruth,type TruthScope} from '../../services/flowEvaluation';
import type {FlowEvaluationCohort,FlowEvaluationHistory,SavedEvaluationFlow,TruthVerdict} from '../../services/flowEvaluationTypes';
import {datasetWorkflow,type ImageReviewMetadata,workflowError} from '../../services/datasetWorkflow';

export interface FlowEvaluationPanelProps {
  sourceDatasetPath?:string;
  embedded?:boolean;
  visible?:boolean;
  onEvidence?:(result:FlowEvaluation|null)=>void;
  savedVersionId?:string|null;
  /** Change this when the project or labelset changes even when the source stays the same. */
  contextKey?:string;
  /** Opens the exact source image in an image viewer; resolve false when it could not be opened. */
  onOpenImage?:(imagePath:string)=>boolean|void|Promise<boolean|void>;
  /** Selects the image for a flow run on the canvas. */
  onInspectImage?:(imagePath:string)=>void;
}

const field='rounded border border-slate-600 bg-slate-950 p-2 text-slate-100';
const button='rounded border border-cyan-800 px-3 py-2 text-cyan-200 disabled:opacity-40';

/** Evidence computed before a truth change is stale until the server checks it again. */
function markTruthChanged(result:FlowEvaluation,relativePath:string):FlowEvaluation {
  const reason=`truth_or_source_changed:${relativePath}`;
  return {...result,validity:{valid:false,reasons:result.validity.reasons.includes(reason)?result.validity.reasons:[...result.validity.reasons,reason]}};
}

/** The server's current view of a stored evaluation; the save itself already succeeded. */
async function readBack(evaluationId:string):Promise<FlowEvaluation|null> {
  try{return await flowEvaluation.read(evaluationId);}catch{return null;}
}

export function FlowEvaluationPanel({sourceDatasetPath='',savedVersionId,contextKey='',onOpenImage,onInspectImage,embedded=false,visible=true,onEvidence}:FlowEvaluationPanelProps) {
  const [open,setOpen]=useState(embedded&&visible), [busy,setBusy]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const [versions,setVersions]=useState<SavedEvaluationFlow[]>([]), [version,setVersion]=useState('');
  const [scope,setScope]=useState<TruthScope|null>(null), [cohorts,setCohorts]=useState<FlowEvaluationCohort[]>([]), [cohort,setCohort]=useState('');
  const [cohortName,setCohortName]=useState('시험 분할 전체'), [history,setHistory]=useState<FlowEvaluationHistory[]>([]);
  const [result,setResult]=useState<FlowEvaluation|null>(null), [evidence,setEvidence]=useState<FlowImageEvidence|null>(null);
  const [images,setImages]=useState<ImageReviewMetadata[]>([]), [imageTotal,setImageTotal]=useState(0), [imageOffset,setImageOffset]=useState(0), [image,setImage]=useState('');
  const [truth,setTruth]=useState<ImageTruth|null>(null), [verdict,setVerdict]=useState<TruthVerdict>('UNKNOWN');
  const [reviewer,setReviewer]=useState(''), [defects,setDefects]=useState<string[]>([]), [note,setNote]=useState('');
  const generation=useRef(0);
  const selectedVersion=useRef('');
  selectedVersion.current=version;

  useEffect(()=>{if(embedded&&visible)setOpen(true);},[embedded,visible]);
  useEffect(()=>{onEvidence?.(result&&result.version_id===version?result:null);},[result,version,onEvidence]);

  useEffect(()=>{
    generation.current++;setVersions([]);setVersion(savedVersionId||'');setScope(null);setCohorts([]);setCohort('');
    setHistory([]);setResult(null);setEvidence(null);setImages([]);setImageOffset(0);setImage('');setTruth(null);setError('');setNotice('');
  },[sourceDatasetPath,contextKey,savedVersionId]);

  useEffect(()=>{
    if(!open||!sourceDatasetPath)return;
    let active=true;setBusy(true);setError('');
    Promise.all([flowEvaluation.versions(sourceDatasetPath),flowEvaluation.cohorts(),flowEvaluation.history()])
      .then(([saved,frozen,runs])=>{if(active){setVersions(saved.pipelines);setVersion(previous=>previous||saved.pipelines.find(p=>p.is_active)?.version_id||saved.pipelines[0]?.version_id||'');setCohorts(frozen.cohorts);setHistory(runs.evaluations);}})
      .catch(e=>{if(active)setError(workflowError(e));}).finally(()=>{if(active)setBusy(false);});
    return()=>{active=false;};
  },[open,sourceDatasetPath,contextKey,savedVersionId]);

  useEffect(()=>{
    setScope(null);setTruth(null);if(!open||!version)return;let active=true;
    flowEvaluation.scope(version).then(value=>{if(active)setScope(value);}).catch(e=>{if(active)setError(workflowError(e));});
    return()=>{active=false;};
  },[open,version,sourceDatasetPath,contextKey,savedVersionId]);

  useEffect(()=>{
    if(!open||!sourceDatasetPath)return;let active=true;
    datasetWorkflow.list({folder_path:sourceDatasetPath,offset:imageOffset,limit:50}).then(rows=>{
      if(active){setImages(rows.items);setImageTotal(rows.total);setImage(previous=>rows.items.some(row=>row.file_path===previous)?previous:rows.items[0]?.file_path||'');}
    }).catch(e=>{if(active)setError(workflowError(e));});return()=>{active=false;};
  },[open,sourceDatasetPath,contextKey,imageOffset,savedVersionId]);

  useEffect(()=>{
    setTruth(null);setVerdict('UNKNOWN');setDefects([]);setNote('');if(!scope||!image)return;let active=true;
    flowEvaluation.truth(image,scope).then(row=>{if(active){setTruth(row);setVerdict(row.verdict);setDefects(row.defect_classes);}})
      .catch(e=>{if(active)setError(workflowError(e));});return()=>{active=false;};
  },[image,scope]);

  const run=async(action:(current:()=>boolean)=>Promise<void>)=>{
    const started=generation.current;const current=()=>generation.current===started;
    setBusy(true);setError('');setNotice('');
    try{await action(current);}catch(e){if(current())setError(workflowError(e));}finally{if(current())setBusy(false);}
  };
  const refreshHistory=async()=>{const rows=await flowEvaluation.history();return rows.evaluations;};
  const visibleRecords=result?.records.filter(row=>row.truth==='UNKNOWN'||row.error||row.decision==='REVIEW'||row.truth!==row.decision)||[];
  const compatible=cohorts.filter(row=>scope&&isCompatibleFlowCohort(row,scope));
  // A result read from history or finished after a version switch can belong to another version.
  const resultMatchesVersion=Boolean(result&&result.version_id===version);
  const openImage=async(imagePath:string)=>{
    if(!onOpenImage)return;
    setError('');
    try{
      const opened=await onOpenImage(imagePath);
      if(opened===false)setError('원본 이미지를 열지 못했습니다. 현재 프로젝트의 원본 폴더에 있는 이미지인지 확인하세요.');
      else setOpen(false);
    }catch(e){setError(workflowError(e));}
  };

  return <section className="relative shrink-0 border-t border-slate-700 bg-[#101722] text-xs" aria-label="전체 흐름 평가">
    {!embedded&&<button type="button" onClick={()=>setOpen(value=>!value)} aria-expanded={open} disabled={!sourceDatasetPath} className={`${button} m-2`}>전체 흐름 평가 · 정답 검토</button>}
    {open&&<div className={embedded?"space-y-4 p-4":"absolute bottom-full inset-x-2 z-[90] max-h-[80vh] space-y-4 overflow-y-auto rounded border border-slate-600 bg-[#151D2A] p-4 shadow-2xl"}>
      <div className="flex items-center justify-between"><h3 className="font-semibold text-slate-100">저장된 흐름 × 고정 시험 코호트</h3>{!embedded&&<button onClick={()=>setOpen(false)} className={button}>닫기</button>}</div>
      <p className="text-slate-400">실제 ROI·분기·Blob·최종 규칙을 CPU에서 실행합니다. 미확인 정답은 성능 집계에서 제외되며, 원본이나 라벨이 바뀌면 이전 근거의 유효성이 해제됩니다.</p>
      <div className="flex flex-wrap items-end gap-2"><label>저장된 흐름 버전<select aria-label="전체 흐름 평가 버전" value={version} disabled={busy} onChange={e=>{setVersion(e.target.value);setCohort('');}} className={`${field} ml-2 max-w-80`}><option value="">저장된 버전 선택</option>{versions.map(row=><option key={row.version_id} value={row.version_id}>{row.name} · {row.saved_at} · {row.version_id.slice(0,8)}</option>)}</select></label>
        <label>고정 시험 코호트<select aria-label="전체 흐름 시험 코호트" value={cohort} disabled={busy||!scope} onChange={e=>setCohort(e.target.value)} className={`${field} ml-2 max-w-80`}><option value="">코호트 선택</option>{compatible.map(row=><option key={row.cohort_id} value={row.cohort_id}>{row.name} · {row.count}개 · {row.created_at}</option>)}</select></label>
        <button disabled={busy||!version||!cohort} className={`${button} ${embedded?"bg-cyan-800 text-white":""}`} data-primary-action={embedded||undefined} onClick={()=>void run(async current=>{const requested=version;const value=await flowEvaluation.run(requested,cohort);const rows=await refreshHistory();if(!current())return;setHistory(rows);if(selectedVersion.current===requested){setResult(value);setEvidence(null);}else setNotice(`버전 ${requested}의 평가가 끝났습니다. 평가 이력에서 열 수 있습니다.`);})}>{embedded?'선택 코호트 평가':'전체 흐름 평가 실행'}</button>
      </div>
      {!versions.length&&!busy&&<p className="text-amber-200">실행 가능한 흐름을 먼저 저장하세요.</p>}
      {scope&&<p className="text-slate-400">정답 범위: {scope.task} · {scope.classes.join(', ')} · 라벨 세트 {scope.labelset_id}</p>}
      {scope&&<div className="flex flex-wrap gap-2" aria-label="정답 범위 클래스 역할">{scope.classes.map(name=><span key={name} className="rounded border border-slate-700 px-2 py-1">{name} · {{normal:'정상',defect:'결함',unknown:'역할 미확인'}[scope.class_semantics.roles[name]]||'역할 미확인'}</span>)}{scope.participating_tasks&&<span className="self-center text-slate-400">참여 모델: {scope.participating_tasks.join(' + ')}</span>}</div>}
      <details><summary className="cursor-pointer font-semibold text-cyan-200">이미지별 명시적 정답 검토</summary>
        <p className="my-2 text-slate-400">선택한 이미지의 픽셀을 검토한 후 정답을 저장하세요. 빈 라벨·라벨 승인·미작업은 정상 정답으로 취급하지 않습니다.</p>
        <div className="flex flex-wrap gap-2"><select aria-label="명시적 정답 이미지" value={image} disabled={busy} onChange={e=>setImage(e.target.value)} className={`${field} max-w-96`}><option value="">이미지 선택</option>{images.map(row=><option key={row.image_uuid} value={row.file_path}>{row.relative_path}</option>)}</select>
          <button className={button} disabled={!image||!onOpenImage} onClick={()=>void openImage(image)}>이미지 열기</button>
          <button className={button} disabled={busy||imageOffset===0} onClick={()=>setImageOffset(value=>Math.max(0,value-50))}>이전 50개</button><button className={button} disabled={busy||imageOffset+50>=imageTotal} onClick={()=>setImageOffset(value=>value+50)}>다음 50개</button>
          <span className="self-center text-slate-400">{imageTotal}개 중 {Math.min(imageOffset+1,imageTotal)}–{Math.min(imageOffset+50,imageTotal)}</span>
        </div>
        {truth&&<p className="my-2 text-slate-300">현재 정답: {truth.verdict}{truth.invalidated?(truth.unknown_reason==='participating_task_review_changed'?' · 참여 모델의 정답 검토가 바뀌어 재검토 필요':' · 원본/라벨 변경으로 무효화됨'):''} · 정답 버전 {truth.truth_revision} · 검토자 {truth.reviewer||'미지정'}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-2"><label>검토자<input aria-label="정답 검토자" value={reviewer} onChange={e=>setReviewer(e.target.value)} className={`${field} ml-2 w-32`}/></label><select aria-label="명시적 정답 판정" value={verdict} onChange={e=>{setVerdict(e.target.value as TruthVerdict);setDefects([]);}} disabled={busy||!truth} className={field}><option value="UNKNOWN">미확인</option><option value="OK">OK · 범위 내 결함 없음</option><option value="NG">NG · 결함 있음</option></select>
          {verdict==='NG'&&scope?.classes.filter(name=>scope.class_semantics.roles[name]==='defect').map(name=><label key={name} className="flex gap-1"><input type="checkbox" checked={defects.includes(name)} onChange={e=>setDefects(previous=>e.target.checked?[...previous,name]:previous.filter(value=>value!==name))}/>{name}</label>)}
          <input aria-label="정답 검토 메모" placeholder="검토 메모" value={note} onChange={e=>setNote(e.target.value)} className={`${field} w-56`}/>
          <button className={button} disabled={busy||!truth||!reviewer.trim()||(verdict==='NG'&&!defects.length)} onClick={()=>void run(async current=>{if(!truth)return;const shown=result?.evaluation_id;const value=await flowEvaluation.declareTruth(truth,verdict,defects,reviewer,note);if(current()){setTruth(value);setResult(previous=>previous&&markTruthChanged(previous,value.relative_path||truth.relative_path));}const rows=await refreshHistory();const reread=shown?await readBack(shown):null;if(current()){setNotice('선택 이미지 정답을 저장했습니다. 변경된 정답으로 새 시험 코호트를 고정하세요.');setHistory(rows);if(reread)setResult(previous=>previous?.evaluation_id===reread.evaluation_id?reread:previous);}})}>선택 이미지 정답 저장</button>
        </div>
      </details>
      <div className="flex flex-wrap items-center gap-2"><input aria-label="시험 코호트 이름" value={cohortName} onChange={e=>setCohortName(e.target.value)} className={field}/><button className={button} disabled={busy||!version||!scope||!cohortName.trim()} onClick={()=>void run(async current=>{const value=await flowEvaluation.freeze(version,cohortName);if(current()){setCohorts(previous=>[value,...previous]);setCohort(value.cohort_id);setNotice(`저장된 시험 분할 ${value.count}개를 고정했습니다.`);}})}>현재 시험 분할을 새 코호트로 고정</button><span className="text-slate-400">시험 분할 저장이 필요합니다. 학습·검증 분할과 동일한 이미지 내용은 거부합니다.</span></div>
      <div className="flex items-center gap-2"><label>평가 이력<select aria-label="전체 흐름 평가 이력" value={result?.evaluation_id||''} disabled={busy} onChange={e=>e.target.value&&void run(async current=>{const value=await flowEvaluation.read(e.target.value);if(current()){setResult(value);setEvidence(null);}})} className={`${field} ml-2`}><option value="">이력 선택</option>{history.map(row=><option key={row.evaluation_id} value={row.evaluation_id}>{row.created_at} · {row.coverage.total}개 · {row.validity.valid?'유효':'재평가 필요'}</option>)}</select></label><button disabled={busy} className={button} onClick={()=>void run(async current=>{const rows=await refreshHistory();if(current())setHistory(rows);if(result){const value=await flowEvaluation.read(result.evaluation_id);if(current())setResult(value);}})}>유효성 새로 확인</button></div>
      {result&&<div className="space-y-3 rounded border border-slate-700 p-3">
        {!resultMatchesVersion&&<p role="status" className="text-amber-200">이 결과는 다른 흐름 버전({result.version_id})의 평가입니다. 선택한 버전으로 다시 평가하거나 <button className="underline" onClick={()=>{setVersion(result.version_id);setCohort('');}}>해당 버전 보기</button>를 선택하세요.</p>}
        <p className={resultMatchesVersion&&result.validity.valid?'text-emerald-200':'text-amber-200'}>{!resultMatchesVersion?'선택한 버전의 평가 아님':result.validity.valid?'저장된 평가 근거 유효':'재평가 필요'} · 실행 {result.status} · 정답 확인 {result.coverage.known}/{result.coverage.total} · 미확인 {result.coverage.unknown} · 무효화 정답 {result.coverage.invalidated}</p>
        {!result.validity.valid&&<p className="break-all text-amber-200">{result.validity.reasons.join(', ')}</p>}
        <div className="flex flex-wrap gap-6"><span>미검출률 {formatFlowMetric(result.metrics.escape_rate)} · 불량 정답 {result.metrics.defect_count}개</span><span>과검출률 {formatFlowMetric(result.metrics.overkill_rate)} · 정상 정답 {result.metrics.normal_count}개</span><span>검토 판정 {formatFlowMetric(result.metrics.review_rate)}</span></div>
        {result.metrics.overkill_unavailable_reason&&<p className="text-amber-200">명시적 정상 정답이 없어 과검출률을 계산할 수 없습니다.</p>}
        {result.metrics.escape_unavailable_reason&&<p className="text-amber-200">명시적 불량 정답이 없어 미검출률을 계산할 수 없습니다.</p>}
        <table className="w-full max-w-lg text-left"><caption className="mb-1 text-left text-slate-400">정답 × 실제 흐름 최종 판정</caption><thead><tr><th>정답</th>{['OK','NG','REVIEW'].map(value=><th key={value}>{value}</th>)}</tr></thead><tbody>{(['OK','NG'] as const).map(value=><tr key={value} className="border-t border-slate-700"><th>{value}</th>{(['OK','NG','REVIEW'] as const).map(decision=><td key={decision} className="py-2">{result.confusion[value][decision]}</td>)}</tr>)}</tbody></table>
        <p className="text-slate-400">미검출 {result.escapes.length} · 과검출 {result.overkills.length} · 실행 오류 {result.errors.length} · 미확인 {result.unknown_truth.length}</p>
        <button className={button} disabled={busy||!resultMatchesVersion||!result.validity.valid||!visibleRecords.length} onClick={()=>void run(async current=>{const value=await flowEvaluation.reviewQueue(result.evaluation_id);if(current())setNotice(`2단계 검토 큐에 ${value.items.length}개를 등록했습니다. 큐 ${value.id}`);})}>오류·검토·미확인 이미지를 검토 큐로 보내기</button>
        <div className="max-h-40 overflow-auto"><table className="w-full text-left"><thead><tr><th>이미지</th><th>정답</th><th>판정</th><th>근거</th></tr></thead><tbody>{visibleRecords.map(row=><tr key={row.relative_path} className="border-t border-slate-700"><td className="p-2">{row.relative_path}</td><td>{row.truth}</td><td>{row.decision}</td><td><button className={button} onClick={()=>setEvidence(row)}>노드·ROI 보기</button></td></tr>)}</tbody></table>{!visibleRecords.length&&<p className="p-2 text-emerald-200">확인된 정답과 판정이 모두 일치합니다.</p>}</div>
        <details><summary className="cursor-pointer text-slate-400">평가 입력 식별 정보</summary><dl className="mt-2 space-y-1 break-all font-mono text-[10px]">{[['평가',result.evaluation_id],['흐름 버전',result.version_id],['그래프',result.graph_sha256],['코호트',result.cohort_sha256],['입력',result.input_sha256],['정답',result.truth_sha256],['모델',result.model_sha256],['기록',result.record_sha256]].map(([label,value])=><div key={label}><dt className="inline text-slate-400">{label}: </dt><dd className="inline">{value}</dd></div>)}</dl></details>
        {evidence&&<section className="space-y-2 rounded border border-slate-600 p-3" aria-label="평가 노드 ROI 근거"><div className="flex items-center justify-between"><strong>{evidence.relative_path} · {evidence.truth} → {evidence.decision}</strong><button onClick={()=>setEvidence(null)} className={button}>근거 닫기</button></div><p>{evidence.rejection_reason}</p>{evidence.truth_reason&&<p className="text-amber-200">미확인 사유: {evidence.truth_reason}</p>}
          <ol className="space-y-1">{evidence.node_evidence.map(node=><li key={node.node_id} className="rounded bg-slate-950 p-2"><strong>{node.name}</strong> · {node.status} · {node.branch_verdict||'—'} · 입력 {node.input_count??'—'} / 출력 {node.output_count??'—'}{node.skip_reason&&<span> · 건너뛴 사유 {node.skip_reason}</span>}<span className="block text-slate-400">선택 경로 {node.selected_edge_ids.join(', ')||'없음'}</span></li>)}</ol>
          <details><summary className="cursor-pointer">ROI·Blob·노드 상세 근거</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2 text-[10px]">{JSON.stringify({nodes:evidence.node_evidence,rois:evidence.roi_evidence},null,2)}</pre></details>
          {onOpenImage&&<button className={button} onClick={()=>void openImage(evidence.image_path)}>원본 이미지 열기</button>}
          {onInspectImage&&<button className={button} onClick={()=>{onInspectImage(evidence.image_path);setOpen(false);}}>흐름에서 이 이미지로 실행</button>}
        </section>}
      </div>}
      {busy&&<p role="status" className="text-cyan-200">전체 흐름 평가 처리 중…</p>}{error&&<p role="alert" className="rounded border border-red-800 p-2 text-red-200">{error}</p>}{notice&&<p role="status" className="text-emerald-200">{notice}</p>}
    </div>}
  </section>;
}
