import {useEffect,useRef,useState} from 'react';
import {RefreshCw,Square,ListChecks} from 'lucide-react';
import {request,getApiPersistenceIdentity} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {normalizeTask,tasksForScope,taskLifecycle,taskSelection,terminalTask,taskSnapshotForScope,observationSummary,releasableReservation,type TaskRow,type Reservation} from './taskCenterModel';
import {saveTaskHandoff,taskHandoffScope,taskHandoffContextScope,taskDestination,modelFamilies,type TaskStep} from './taskHandoff';
import type {VisionTask} from '../../types';
import {programButton,programInput} from './ProgramWorkbenchControls';
import {cancellable,jobProgress} from './jobProgress';  // S2-09: the same state names and reading as every family workbench

const familyLabels:Record<string,string>={classification:'분류',segmentation:'영역 분할',detection:'객체 검출',anomaly:'이상 탐지',patch_classification:'패치 분류',ocr:'문자 인식',rotated_detection:'회전 객체',rotation:'정방향 보정',defect_gan:'결함 생성',enhancement:'이미지 개선'};
const resourceLabels:Record<string,string>={released:'예약 반환 확인',reserved:'예약 유지',reserved_uncertain:'상태 불명 · 예약 유지',unconfirmed:'예약 반환 미확인'};
export function TaskCenter({initialOpen=false,onNavigate}:{initialOpen?:boolean;onNavigate?:(step:TaskStep)=>void|Promise<void>}={}) {
  const {project,projectDir,setStep,isProjectBusy}=useProjectStore();const transport=useComputeStore(state=>state.transportRevision);const profiles=useComputeStore(state=>state.profiles);const selectedTarget=useComputeStore(state=>state.selectedProfileId);
  const source=project?.source_dataset_dir || '';const labelset=project?.active_labelset_id || 'default';
  const stableScope=taskHandoffScope({projectDir,project,selectedProfileId:selectedTarget,apiTransportIdentity:getApiPersistenceIdentity()});
  const scope=JSON.stringify([stableScope,transport]);const current=useRef(scope);current.current=scope;
  const [familyFilter,setFamilyFilter]=useState('all');
  const [opened,setOpened]=useState(initialOpen);const generation=useRef(0);const [rows,setRows]=useState<TaskRow[]>([]);const [leases,setLeases]=useState<Reservation[]|null>(null);const [selected,setSelected]=useState('');const [error,setError]=useState('');const [checked,setChecked]=useState<number|null>(null);const [busy,setBusy]=useState(false);
  const [snapshotScope,setSnapshotScope]=useState(scope);const visibleRows=taskSnapshotForScope(snapshotScope,scope,rows);
  // S1-04: the operator's confirmation that a job whose exit could not be proven no longer uses its device (null = closed)
  const [releaseReason,setReleaseReason]=useState<string|null>(null);
  // A release whose outcome could not be written to the job ledger says so until another job or scope is selected.
  const [releaseNotice,setReleaseNotice]=useState('');
  useEffect(()=>{setReleaseReason(null);setReleaseNotice('');},[selected,scope]);
  const refresh=async()=>{
    if(!source)return;const sequence=++generation.current;
    try {
      const result=await request<{tasks:Record<string,any>[];reservations:Reservation[]|null;errors:Array<{kind:string;message:string}>;source_dataset_path:string;labelset_id:string}>('/api/training-workspace/tasks');
      if(current.current!==scope||generation.current!==sequence)return;
      if(result.source_dataset_path!==source||result.labelset_id!==labelset)throw new Error('현재 프로젝트 작업 응답과 출처가 다릅니다. 다시 확인하세요.');
      const own=tasksForScope(result.tasks.map(raw=>normalizeTask(raw.kind,raw)),source,labelset);
      setRows(own);setSnapshotScope(scope);setLeases(result.reservations);setChecked(Date.now());setError(result.errors.map(row=>`${row.kind}: ${row.message}`).join(' · '));
      setSelected(old=>own.some(row=>row.key===old)?old:own.find(row=>!terminalTask(row.status))?.key || own[0]?.key || '');
    }catch(cause){if(current.current===scope&&generation.current===sequence){setError(cause instanceof Error?cause.message:String(cause));setLeases(null);}}
  };
  useEffect(()=>{
    setRows([]);setLeases(null);setError('');setChecked(null);setBusy(false);setSelected(taskSelection(localStorage,stableScope)||'');
    if(!opened)return;void refresh();const timer=setInterval(()=>void refresh(),2500);return()=>clearInterval(timer);
  },[scope,opened]);
  useEffect(()=>{if(selected&&visibleRows.some(row=>row.key===selected))taskSelection(localStorage,stableScope,selected);},[selected,scope,visibleRows]);
  const filteredRows=familyFilter==='all'?visibleRows:visibleRows.filter(row=>taskDestination(row).family===familyFilter);
  const job=filteredRows.find(row=>row.key===selected);const lifecycle=job?taskLifecycle(job,leases):null;
  const contextScope=taskHandoffContextScope({projectDir,project,transportRevision:transport,selectedProfileId:selectedTarget,apiTransportIdentity:getApiPersistenceIdentity()});
  const navigationIsCurrent=()=>taskHandoffContextScope({...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()})===contextScope;
  const navigate=async()=>{
    if(!job||busy||useProjectStore.getState().isProjectBusy||!navigationIsCurrent())return;setBusy(true);setError('');
    try{
      // A partially applied task change leaves a project error until the user
      // reopens/reimports successfully. Matching the new task alone is not ready.
      const projectError=useProjectStore.getState().projectError;
      if(projectError)throw new Error(projectError);
      const target=taskDestination(job);if(target.family&&['classification','segmentation','detection','anomaly'].includes(target.family)&&useProjectStore.getState().task!==target.family){
      const outcome=await useProjectStore.getState().setTask(target.family as VisionTask);
      if(!outcome.ok)throw new Error(outcome.error);
    }
      if(current.current!==scope||!navigationIsCurrent()||useProjectStore.getState().isProjectBusy)throw new Error('프로젝트 범위가 바뀌었거나 변경 중입니다. 작업 센터에서 다시 확인하세요.');
      saveTaskHandoff(localStorage,{...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()},job);await setStep(target.step);if(navigationIsCurrent())await onNavigate?.(target.step);
    }catch(cause){if(current.current===scope)setError(cause instanceof Error?cause.message:String(cause));}finally{if(current.current===scope)setBusy(false);}
  };
  const releaseReservation=async(lease:Reservation)=>{
    if(!job||busy||releaseReason===null)return;const started=scope;setBusy(true);setError('');
    try {
      // the fence the operator saw: a reservation re-taken meanwhile is refused, never released
      const answer=await request<{outcome_recorded?:boolean}>('/api/training/reservations/confirm-release',{method:'POST',body:JSON.stringify({job_id:job.id,confirm:true,reason:releaseReason.trim(),fence:lease.fence??null})});
      if(current.current!==started)return;setReleaseReason(null);await refresh();
      if(current.current===started)setReleaseNotice(answer?.outcome_recorded===false?'예약은 해제했지만 해제 결과를 작업 기록에 남기지 못했습니다. 해제 확인 기록은 남아 있습니다.':'');
    }catch(cause){if(current.current===started)setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(current.current===started)setBusy(false);}
  };
  const control=async(action:'cancel'|'reconnect')=>{
    if(!job||busy)return;setBusy(true);setError('');
    try {
      const id=encodeURIComponent(job.id);let path:string;let body:Record<string,string>={};
      if(job.kind==='training'){path=`/api/training/${action==='cancel'?'stop':'reconnect'}`;body={job_id:job.id};}
      else if(job.kind==='labeling-batch'){path=`/api/label-candidates/batches/${id}/cancel`;}
      else if(job.kind==='labeling-feature'){path=`/api/label-suggestions/feature-train/${id}/cancel`;}
      else if(job.kind==='optimization'){path=`/api/export/flow/optimization-jobs/${id}/cancel`;}
      else {if(action==='reconnect')throw new Error('이 작업은 저장된 기록을 다시 읽어 확인하세요.');path=job.kind==='automated'?`/api/automated-training/jobs/${id}/cancel`:`/api/${job.kind}/jobs/${id}/cancel`;}
      await request(path,{method:'POST',body:JSON.stringify(body)});if(current.current===scope)await refresh();
    }catch(cause){if(current.current===scope)setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(current.current===scope)setBusy(false);}
  };
  if(!projectDir)return null;
  return <details open={opened} onToggle={event=>setOpened(event.currentTarget.open)} className="rounded-xl border border-slate-600 bg-[#101A28] p-4 text-sm text-slate-200"><summary className="cursor-pointer font-semibold"><ListChecks className="mr-2 inline h-4 w-4 text-cyan-300" />작업 센터 · 현재 프로젝트 {visibleRows.length}개</summary><div className="mt-4 space-y-3">
    <p className="text-slate-300">학습·라벨링·검사·내보내기·최적화 기록을 같은 작업 ID로 다시 확인합니다. 취소 요청 후 실행 종료와 예약 반환을 확인하세요.</p>
    <label className="block">작업 모델 종류<select aria-label="작업 모델 종류" value={familyFilter} onChange={event=>{const family=event.target.value;setFamilyFilter(family);setSelected(visibleRows.find(row=>family==='all'||taskDestination(row).family===family)?.key||'');}} className={programInput}><option value="all">전체 작업</option>{modelFamilies.map(family=><option key={family} value={family}>{familyLabels[family]}</option>)}</select></label><div className="flex gap-3"><select aria-label="저장 작업 다시 열기" value={selected} onChange={event=>setSelected(event.target.value)} className={programInput}><option value="">저장된 작업 선택</option>{filteredRows.map((row,index)=><option key={row.key} value={row.key}>작업 {index+1} · {familyLabels[taskDestination(row).family||row.task]||row.task}{row.totalEpochs?` · Epoch ${row.epoch}/${row.totalEpochs}`:''} · {jobProgress(row.raw).label} · {row.transport==='local'?'이 컴퓨터':profiles.find(profile=>profile.id===row.transport)?.name || '서버'}</option>)}</select><button type="button" className={programButton} disabled={busy} onClick={()=>void refresh()}><RefreshCw className="mr-1 inline h-3 w-3" />같은 작업 확인</button></div>
    {job&&lifecycle&&<div role="status" className="rounded border border-slate-600 p-3"><strong>{jobProgress(job.raw).label}</strong><p className="mt-2">{familyLabels[taskDestination(job).family||job.task]||job.task} · {job.transport==='local'?'이 컴퓨터':profiles.find(row=>row.id===job.transport)?.name || '저장 서버'} · Epoch {job.epoch}/{job.totalEpochs||'미기록'}</p>{!observationSummary(job)&&<p className="mt-1">취소: {lifecycle.cancellation==='requested'?'요청 접수':lifecycle.cancellation==='acknowledged'?'종료 응답 확인':'요청 없음'} · 실행 종료: {lifecycle.termination==='confirmed'?'확인':lifecycle.termination==='unconfirmed'?'미확인':'진행 중'} · {resourceLabels[lifecycle.resource]}</p>}
      {/* the family workbenches' next action fits only a model family's job (not a labeling batch, an optimization or an export) */}
      {!observationSummary(job)&&taskDestination(job).family&&jobProgress(job.raw).nextAction&&<p className="mt-2 text-amber-200">다음 행동: {jobProgress(job.raw).nextAction}</p>}
      {(()=>{const summary=observationSummary(job);if(!summary)return null;return <div className="mt-2 space-y-1">
        {summary.steps.length>0&&<ol aria-label="취소 확인 단계" className="flex flex-wrap gap-2 text-xs">{summary.steps.map(step=><li key={step.label} className={step.done?'text-emerald-300':'text-slate-500'}>{step.done?'✓':'○'} {step.label}</li>)}</ol>}
        {summary.facts.length>0&&<p className="text-xs text-slate-300">{summary.facts.join(' · ')}</p>}
        {summary.nextAction&&<p className="text-amber-200">다음 행동: {summary.nextAction}</p>}
      </div>;})()}
      {job.raw.error&&<p role="alert" className="mt-2 break-words text-rose-200">{job.status==='failed'?'실패 원인':'기록된 원인'}: {typeof job.raw.error==='string'?job.raw.error:job.raw.error?.message||JSON.stringify(job.raw.error)}</p>}
      {releaseNotice&&<p role="status" className="mt-2 text-xs text-amber-300">{releaseNotice}</p>}
      {(()=>{const lease=releasableReservation(job,leases);if(!lease)return null;return <div className="mt-2 rounded border border-amber-700/60 bg-amber-950/20 p-2 text-xs">
        <p className="text-amber-200">이 작업의 종료를 확인하지 못해 장치 예약이 유지되고 있습니다. 작업이 더 이상 장치를 쓰지 않는 것을 확인했다면 사유를 남기고 예약을 해제할 수 있습니다. {lease.remote?'서버 작업은 이 화면에서 실행 여부를 확인하지 않으므로, 서버에서 작업이 끝난 것을 먼저 확인하세요.':'이 컴퓨터에서 실행 중인 작업자가 확인되면 해제하지 않습니다.'}</p>
        {releaseReason===null?<button type="button" className={`${programButton} mt-2`} disabled={busy} onClick={()=>setReleaseReason('')}>예약 해제 확인</button>
        :<div className="mt-2 flex flex-wrap items-center gap-2"><input aria-label="예약 해제 사유" value={releaseReason} maxLength={500} onChange={event=>setReleaseReason(event.target.value)} placeholder="예: PC를 재시작해 작업이 끝난 것을 확인함" className={`${programInput} min-w-[240px] flex-1`}/>
          <button type="button" className={programButton} disabled={busy||releaseReason.trim().length<3} onClick={()=>void releaseReservation(lease)}>해제 확정</button>
          <button type="button" className={programButton} disabled={busy} onClick={()=>setReleaseReason(null)}>취소</button></div>}
      </div>;})()}
      <div className="mt-3 flex flex-wrap gap-2">{job.raw.cancel_supported!==false&&(cancellable({status:job.status})||['stopping','cancelling'].includes(job.status))&&<button type="button" disabled={busy||['stopping','cancelling'].includes(job.status)} onClick={()=>void control('cancel')} className={programButton}><Square className="mr-1 inline h-3 w-3" />취소 요청</button>}{job.status==='disconnected'&&job.kind==='training'&&<button type="button" className={programButton} disabled={busy} onClick={()=>void control('reconnect')}>같은 서버 작업 재연결</button>}<button type="button" disabled={busy||isProjectBusy} onClick={()=>navigate()} className={programButton}>{job.kind==='inspection'?'검사 기록 화면':job.task==='labeling'?'라벨 검토 화면':['optimization','export'].includes(job.kind)?'패키지·배포 화면':job.status==='completed'?'완료 후보 평가로 이동':'학습 화면으로 이동'}</button></div>
      <details className="mt-3 text-slate-400"><summary className="cursor-pointer">작업 식별자·저장 근거</summary><p className="mt-2 break-all">{job.key}</p><p className="break-all">출처: {job.source} · 정답 버전: {job.labelset || '작업 기록에 없음 · 프로젝트 범위'}</p></details></div>}
    {checked&&<p className="text-slate-400">마지막 응답 확인: {new Date(checked).toLocaleTimeString()}</p>}{error&&<p role="alert" className="rounded bg-rose-950/30 p-3 text-rose-200">{error}</p>}
  </div></details>;
}
