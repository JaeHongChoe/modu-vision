import {useEffect,useRef,useState} from 'react';
import {captureIntake,captureRoutingLabel,type CaptureCandidate,type CaptureVersion} from '../../services/captureIntake';
import {workflowError} from '../../services/datasetWorkflow';

export interface CaptureIntakePanelProps {
  sourceDatasetPath?:string;
  contextKey?:string;
  /** Explicit source selection is owned by the parent workflow. */
  onUseSource?:(sourcePath:string)=>void|Promise<void>;
}
const button='rounded border border-cyan-800 px-3 py-2 text-cyan-200 disabled:opacity-40';
const field='rounded border border-slate-600 bg-slate-950 p-2 text-slate-100';

export function CaptureIntakePanel({sourceDatasetPath='',contextKey='',onUseSource}:CaptureIntakePanelProps) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [candidates,setCandidates]=useState<CaptureCandidate[]>([]),[versions,setVersions]=useState<CaptureVersion[]>([]);
  const [selected,setSelected]=useState<Set<string>>(new Set()),[detail,setDetail]=useState<CaptureCandidate|null>(null),[preview,setPreview]=useState('');
  const [actor,setActor]=useState(''),[note,setNote]=useState(''),[name,setName]=useState('서비스 캡처 검토 버전'),[jobIds,setJobIds]=useState('');
  const generation=useRef(0);
  useEffect(()=>{generation.current++;setCandidates([]);setVersions([]);setSelected(new Set());setDetail(null);setPreview('');setError('');setNotice('');setBusy(false);},[sourceDatasetPath,contextKey]);
  useEffect(()=>{
    if(!open||!sourceDatasetPath)return;let active=true;setBusy(true);
    Promise.all([captureIntake.list(),captureIntake.versions()]).then(([rows,saved])=>{if(active){setCandidates(rows.candidates);setVersions(saved.versions);}})
      .catch(e=>{if(active)setError(workflowError(e));}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;};
  },[open,sourceDatasetPath,contextKey]);
  useEffect(()=>{
    setPreview('');if(!detail?.snapshot_path||detail.stale)return;let active=true;
    captureIntake.preview(detail.candidate_id).then(value=>{if(active)setPreview(value.data_url);})
      .catch(e=>{if(active)setError(workflowError(e));});return()=>{active=false;};
  },[detail]);
  const run=async(action:(current:()=>boolean)=>Promise<void>)=>{
    const started=generation.current;const current=()=>generation.current===started;setBusy(true);setError('');setNotice('');
    try{await action(current);}catch(e){if(current())setError(workflowError(e));}finally{if(current())setBusy(false);}
  };
  const review=(decision:'adopt'|'reject')=>run(async current=>{
    if(!detail)return;const row=await captureIntake.review(detail,actor,decision,note);if(current()){setDetail(row);setCandidates(previous=>previous.map(value=>value.candidate_id===row.candidate_id?row:value));setNotice(decision==='adopt'?'채택 후보 검토를 저장했습니다. 새 소유 데이터 버전을 생성하세요.':'후보 제외 검토를 저장했습니다.');}
  });
  const eligible=(row:CaptureCandidate)=>!row.stale&&row.routing==='unknown'&&row.review?.decision==='adopt';
  const selectedRows=candidates.filter(row=>selected.has(row.candidate_id)&&eligible(row));

  return <section className="relative shrink-0 border-b border-slate-700 bg-[#101722] text-xs" aria-label="서비스 캡처 데이터 개선">
    <button className={`${button} m-2`} disabled={!sourceDatasetPath} onClick={()=>setOpen(value=>!value)} aria-expanded={open}>서비스 캡처 · 데이터 개선 후보</button>
    {open&&<div className="absolute inset-x-2 top-full z-[95] max-h-[80vh] space-y-3 overflow-y-auto rounded border border-slate-600 bg-[#151D2A] p-4 shadow-2xl">
      <div className="flex items-center justify-between"><h3 className="font-semibold text-slate-100">검사 캡처 등록 → 사람 검토 → 새 데이터 버전</h3><button className={button} onClick={()=>setOpen(false)}>닫기</button></div>
      <p className="text-slate-400">실제 프로젝트 서비스 작업 기록과 이미지 해시를 보존합니다. 예측 OK/NG는 정답으로 자동 변환되지 않습니다. 중복·실패 캡처는 별도로 표시되며, 시험 분할을 저장한 후 후보를 등록할 수 있습니다.</p>
      <div className="flex flex-wrap items-center gap-2"><input aria-label="캡처 서비스 작업 ID" value={jobIds} onChange={e=>setJobIds(e.target.value)} placeholder="작업 ID 쉼표 구분 · 비우면 최근 완료 100개" className={`${field} w-80`}/><button className={button} disabled={busy} onClick={()=>void run(async current=>{const ids=jobIds.split(',').map(value=>value.trim()).filter(Boolean);const registered=await captureIntake.register(ids);const rows=await captureIntake.list();if(current()){setCandidates(rows.candidates);setNotice(`${registered.total}개 작업의 실제 캡처 기록을 등록했습니다.`);}})}>실제 서비스 캡처 등록</button><button className={button} disabled={busy} onClick={()=>void run(async current=>{const [rows,saved]=await Promise.all([captureIntake.list(),captureIntake.versions()]);if(current()){setCandidates(rows.candidates);setVersions(saved.versions);setSelected(new Set());}})}>새로 확인</button></div>
      <div className="max-h-56 overflow-auto"><table className="w-full text-left"><thead><tr>{['선택','서비스 작업','예측','정답','분류','사람 검토','근거'].map(label=><th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{candidates.map(row=><tr key={row.candidate_id} className="border-t border-slate-700"><td><input type="checkbox" aria-label={`${row.candidate_id} 채택 선택`} checked={selected.has(row.candidate_id)} disabled={busy||!eligible(row)} onChange={e=>setSelected(previous=>{const next=new Set(previous);if(e.target.checked)next.add(row.candidate_id);else next.delete(row.candidate_id);return next;})}/></td><td className="p-2 font-mono" title={row.origin.job_id}>{row.origin.job_id.slice(0,12)}<span className="block font-sans text-slate-500">{row.origin.capture_source} · {row.origin.service_state}</span></td><td>{row.source_prediction||'없음'}</td><td>미확인</td><td className={row.stale?'text-amber-200':row.routing==='failed'?'text-red-200':'text-slate-300'}>{row.stale?'근거 변경됨':captureRoutingLabel(row.routing)}</td><td>{row.review?`${row.review.decision==='adopt'?'채택 후보':'제외'} · ${row.review.actor}`:'검토 대기'}</td><td><button className={button} onClick={()=>{setDetail(row);setNote('');}}>캡처 검토</button></td></tr>)}</tbody></table>{!candidates.length&&!busy&&<p className="p-4 text-center text-slate-400">등록된 서비스 캡처 후보가 없습니다.</p>}</div>
      {detail&&<div className="space-y-2 rounded border border-slate-600 p-3" aria-label="캡처 후보 검토"><div className="flex items-center justify-between"><strong>{detail.candidate_id}</strong><button className={button} onClick={()=>setDetail(null)}>검토 닫기</button></div>
        {preview&&<img src={preview} alt="해시로 고정한 실제 서비스 캡처" className="max-h-64 max-w-full rounded bg-black"/>}
        <p>예측 {detail.source_prediction||'없음'} · 정답 미확인 · {captureRoutingLabel(detail.routing)}</p>
        {detail.duplicate_of&&<p className="text-amber-200">중복 근거: {detail.duplicate_of}</p>}{(detail.failure||detail.stale_reason)&&<p className="text-red-200">{detail.failure||detail.stale_reason}</p>}
        <div className="flex flex-wrap gap-2"><input aria-label="캡처 검토자" value={actor} onChange={e=>setActor(e.target.value)} placeholder="검토자 이름" className={field}/><input aria-label="캡처 검토 메모" value={note} onChange={e=>setNote(e.target.value)} placeholder="채택/제외 근거" className={`${field} w-72`}/><button className={button} disabled={busy||!actor.trim()||detail.stale||detail.routing!=='unknown'} onClick={()=>void review('adopt')}>새 데이터에 채택할 후보로 검토</button><button className={button} disabled={busy||!actor.trim()||detail.stale} onClick={()=>void review('reject')}>후보 제외 검토</button></div>
        <details><summary className="cursor-pointer text-slate-400">검사 작업·모델·노드·ROI 근거</summary><pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2 text-[10px]">{JSON.stringify({source_sha256:detail.source_sha256,truth:detail.truth_verdict,origin:detail.origin},null,2)}</pre></details>
      </div>}
      <div className="flex flex-wrap items-center gap-2"><input aria-label="캡처 채택 버전 이름" value={name} onChange={e=>setName(e.target.value)} className={field}/><button className={button} disabled={busy||!selectedRows.length||!actor.trim()||!name.trim()} onClick={()=>void run(async current=>{const version=await captureIntake.adopt(selectedRows.map(row=>row.candidate_id),actor,name);const rows=await captureIntake.list();if(current()){setVersions(previous=>[version,...previous]);setCandidates(rows.candidates);setSelected(new Set());setNotice(`${version.adopted.length}개 캡처를 새 소유 데이터 버전에 복사했습니다. 원본 선택은 아래에서 별도로 진행하세요.`);}})}>검토한 {selectedRows.length}개로 새 소유 데이터 버전 생성</button></div>
      <p className="text-amber-200">채택 캡처는 학습 분할에 배정하되 학습 제외·라벨 검토 필요로 시작합니다. 2단계에서 라벨과 정답을 검토하고 기존 승인 정책을 충족한 후 명시적으로 학습에 포함하세요. 기존 시험 이미지와 분할은 보존됩니다.</p>
      <details open={!!versions.length}><summary className="cursor-pointer font-semibold">채택 데이터 버전 ({versions.length})</summary><div className="mt-2 space-y-2">{versions.map(version=><article key={version.version_id} className="space-y-1 rounded border border-slate-700 p-3"><strong>{version.name}</strong> · {version.created_at} · 채택 {version.adopted.length} / 고정 시험 {version.fixed_test_records.length}<p className="break-all font-mono text-[10px] text-slate-400">{version.source_dataset_path}</p><p className="text-slate-400">상위 모델은 동일 과업·클래스 순서·아키텍처와 검증된 데이터 계보가 필요합니다. 클래스 변경은 현재 학습의 상위 모델 검증에서 차단됩니다.</p><details><summary className="cursor-pointer text-slate-400">원본·시험·정책 해시</summary><p className="break-all font-mono text-[10px]">버전 {version.record_sha256}<br/>고정 시험 {version.fixed_test_sha256}<br/>검토 정책 {version.review_policy_sha256}</p></details><button className={button} disabled={busy||!onUseSource||sourceDatasetPath===version.source_dataset_path} onClick={()=>onUseSource&&void run(async current=>{const value=await captureIntake.version(version.version_id);if(current())await onUseSource(value.source_dataset_path);})}>이 버전을 데이터 원본으로 명시적 선택</button></article>)}</div></details>
      {busy&&<p role="status" className="text-cyan-200">캡처 데이터 개선 처리 중…</p>}{error&&<p role="alert" className="rounded border border-red-800 p-2 text-red-200">{error}</p>}{notice&&<p role="status" className="text-emerald-200">{notice}</p>}
    </div>}
  </section>;
}
