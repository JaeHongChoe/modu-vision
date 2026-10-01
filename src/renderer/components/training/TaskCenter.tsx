import {useEffect,useRef,useState} from 'react';
import {RefreshCw,Square,ListChecks} from 'lucide-react';
import {request,getApiPersistenceIdentity} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {normalizeTask,tasksForScope,taskLifecycle,taskSelection,terminalTask,taskSnapshotForScope,type TaskRow,type Reservation} from './taskCenterModel';
import {saveTaskHandoff,taskHandoffScope,taskHandoffContextScope,taskDestination,modelFamilies,type TaskStep} from './taskHandoff';
import type {VisionTask} from '../../types';
import {programButton,programInput} from './ProgramWorkbenchControls';

const labels:Record<string,string>={queued:'대기',preparing:'준비 중',transferring:'전송 중',running:'실행 중',stopping:'취소 요청 · 종료 확인 중',cancelling:'취소 요청 · 종료 확인 중',unverified:'저장 기록 · 검증 필요',syncing:'결과 동기화 중',disconnected:'연결 끊김 · 상태 미확인',completed:'완료 후보',aborted:'중단 확인',cancelled:'취소 확인',failed:'실패',stopped:'중단 확인',interrupted:'실행 주체 없음 · 재개 확인 필요'};
const familyLabels:Record<string,string>={classification:'분류',segmentation:'영역 분할',detection:'객체 검출',anomaly:'이상 탐지',patch_classification:'패치 분류',ocr:'문자 인식',rotated_detection:'회전 객체',rotation:'정방향 보정',defect_gan:'결함 생성',enhancement:'이미지 개선'};
const resourceLabels:Record<string,string>={released:'예약 반환 확인',reserved:'예약 유지',reserved_uncertain:'상태 불명 · 예약 유지',unconfirmed:'예약 반환 미확인'};
export function TaskCenter({initialOpen=false,onNavigate}:{initialOpen?:boolean;onNavigate?:(step:TaskStep)=>void|Promise<void>}={}) {
  const {project,projectDir,setStep}=useProjectStore();const transport=useComputeStore(state=>state.transportRevision);const profiles=useComputeStore(state=>state.profiles);const selectedTarget=useComputeStore(state=>state.selectedProfileId);
  const source=project?.source_dataset_dir || '';const labelset=project?.active_labelset_id || 'default';
  const stableScope=taskHandoffScope({projectDir,project,selectedProfileId:selectedTarget,apiTransportIdentity:getApiPersistenceIdentity()});
  const scope=JSON.stringify([stableScope,transport]);const current=useRef(scope);current.current=scope;
  const [familyFilter,setFamilyFilter]=useState('all');
  const [opened,setOpened]=useState(initialOpen);const generation=useRef(0);const [rows,setRows]=useState<TaskRow[]>([]);const [leases,setLeases]=useState<Reservation[]|null>(null);const [selected,setSelected]=useState('');const [error,setError]=useState('');const [checked,setChecked]=useState<number|null>(null);const [busy,setBusy]=useState(false);
  const [snapshotScope,setSnapshotScope]=useState(scope);const visibleRows=taskSnapshotForScope(snapshotScope,scope,rows);
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
    if(!job||busy||!navigationIsCurrent())return;setBusy(true);setError('');
    try{const target=taskDestination(job);if(target.family&&['classification','segmentation','detection','anomaly'].includes(target.family)&&useProjectStore.getState().task!==target.family)await useProjectStore.getState().setTask(target.family as VisionTask);
      if(current.current!==scope||!navigationIsCurrent())throw new Error('프로젝트 범위가 바뀌었습니다. 작업 센터에서 다시 확인하세요.');
      saveTaskHandoff(localStorage,{...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()},job);await setStep(target.step);if(navigationIsCurrent())await onNavigate?.(target.step);
    }catch(cause){if(current.current===scope)setError(cause instanceof Error?cause.message:String(cause));}finally{if(current.current===scope)setBusy(false);}
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
    <label className="block">작업 모델 종류<select aria-label="작업 모델 종류" value={familyFilter} onChange={event=>{const family=event.target.value;setFamilyFilter(family);setSelected(visibleRows.find(row=>family==='all'||taskDestination(row).family===family)?.key||'');}} className={programInput}><option value="all">전체 작업</option>{modelFamilies.map(family=><option key={family} value={family}>{familyLabels[family]}</option>)}</select></label><div className="flex gap-3"><select aria-label="저장 작업 다시 열기" value={selected} onChange={event=>setSelected(event.target.value)} className={programInput}><option value="">저장된 작업 선택</option>{filteredRows.map((row,index)=><option key={row.key} value={row.key}>작업 {index+1} · {familyLabels[row.task]||row.task}{row.totalEpochs?` · Epoch ${row.epoch}/${row.totalEpochs}`:''} · {labels[row.status]||row.status} · {row.transport==='local'?'이 컴퓨터':profiles.find(profile=>profile.id===row.transport)?.name || '서버'}</option>)}</select><button type="button" className={programButton} disabled={busy} onClick={()=>void refresh()}><RefreshCw className="mr-1 inline h-3 w-3" />같은 작업 확인</button></div>
    {job&&lifecycle&&<div role="status" className="rounded border border-slate-600 p-3"><strong>{labels[job.status]||job.status}</strong><p className="mt-2">{familyLabels[job.task]||job.task} · {job.transport==='local'?'이 컴퓨터':profiles.find(row=>row.id===job.transport)?.name || '저장 서버'} · Epoch {job.epoch}/{job.totalEpochs||'미기록'}</p><p className="mt-1">취소: {lifecycle.cancellation==='requested'?'요청 접수':lifecycle.cancellation==='acknowledged'?'종료 응답 확인':'요청 없음'} · 실행 종료: {lifecycle.termination==='confirmed'?'확인':lifecycle.termination==='unconfirmed'?'미확인':'진행 중'} · {resourceLabels[lifecycle.resource]}</p>
      <div className="mt-3 flex flex-wrap gap-2">{job.raw.cancel_supported!==false&&!terminalTask(job.status)&&job.status!=='interrupted'&&<button type="button" disabled={busy||['stopping','cancelling'].includes(job.status)} onClick={()=>void control('cancel')} className={programButton}><Square className="mr-1 inline h-3 w-3" />취소 요청</button>}{job.status==='disconnected'&&job.kind==='training'&&<button type="button" className={programButton} disabled={busy} onClick={()=>void control('reconnect')}>같은 서버 작업 재연결</button>}<button type="button" disabled={busy} onClick={()=>navigate()} className={programButton}>{job.kind==='inspection'?'검사 기록 화면':job.task==='labeling'?'라벨 검토 화면':['optimization','export'].includes(job.kind)?'패키지·배포 화면':job.status==='completed'?'완료 후보 평가로 이동':'학습 화면으로 이동'}</button></div>
      <details className="mt-3 text-slate-400"><summary className="cursor-pointer">작업 식별자·저장 근거</summary><p className="mt-2 break-all">{job.key}</p><p className="break-all">출처: {job.source} · 정답 버전: {job.labelset || '작업 기록에 없음 · 프로젝트 범위'}</p>{job.raw.error&&<p role="alert" className="mt-2 text-rose-200">{typeof job.raw.error==='string'?job.raw.error:JSON.stringify(job.raw.error)}</p>}</details></div>}
    {checked&&<p className="text-slate-400">마지막 응답 확인: {new Date(checked).toLocaleTimeString()}</p>}{error&&<p role="alert" className="rounded bg-rose-950/30 p-3 text-rose-200">{error}</p>}
  </div></details>;
}
