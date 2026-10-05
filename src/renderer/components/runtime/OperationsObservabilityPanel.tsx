import {useEffect,useRef,useState} from 'react';
import {getProjectContextGeneration,request,subscribeProjectContext} from '../../services/api';
import {useDeliveryScope} from './useDeliveryScope';
type Log={id:number;kind:string;job_id:string|null;trace_id:string;event:string;state:string;at:number|string;message:string;payload:unknown};
type Policy={revision:number;enabled:boolean;external_telemetry:false};
type Observation={service:string;readiness:{available:boolean;status:string|null};external_telemetry:false;can_configure:boolean;policy:Policy;
 metrics:{measured_at:number;disk:{available:boolean;free_bytes:number};gpu:{available:boolean;devices:Array<{id:string;utilization_percent:number;memory_used_mib:number;memory_total_mib:number}>;reason:string|null}};
 queue:{available:boolean;states:Record<string,number>;outstanding:number;capacity:number|null;backlog:boolean};training_events:{available:boolean;backlog:boolean};
 logs:{items:Log[];total:number;has_more:boolean};notifications:{items:Array<{id:number;job_id:string|null;reason:string;at:number}>;total:number};error_catalog:Record<string,string>};
const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
export function OperationsObservabilityPanel({canConfigure,initialJobId=''}:{canConfigure:boolean;initialJobId?:string}){
 const [epoch,setEpoch]=useState(getProjectContextGeneration),{key}=useDeliveryScope(String(epoch));
 const [open,setOpen]=useState(false),[offset,setOffset]=useState(0),[state,setState]=useState(''),[job,setJob]=useState(''),[query,setQuery]=useState('');
 const [record,setRecord]=useState<{token:string;view:string;value:Observation}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [enabled,setEnabled]=useState(false),[actor,setActor]=useState(''),[reason,setReason]=useState('');const editing=useRef(false);
 const generation=getProjectContextGeneration(),token=JSON.stringify([key,generation,initialJobId]),view=JSON.stringify([offset,state,query]);
 const gate=useRef({token,view,canConfigure});gate.current={token,view,canConfigure};const sequence=useRef(0);
 const current=()=>gate.current.token===token&&getProjectContextGeneration()===generation;
 const snapshot=record?.token===token&&record.view===view?record.value:null;
 const refresh=async()=>{if(!current())return;const id=++sequence.current;
  const value=await request<Observation>(`/api/product-delivery/operations/observability?limit=50&offset=${offset}${state?'&state='+encodeURIComponent(state):''}${query?'&job_id='+encodeURIComponent(query):''}`);
  if(current()&&gate.current.view===view&&sequence.current===id){setRecord({token,view,value});if(!editing.current)setEnabled(value.policy.enabled);}
 };
 useEffect(()=>subscribeProjectContext(()=>setEpoch(getProjectContextGeneration())),[]);
 useEffect(()=>{setRecord(null);setOffset(0);setState('');setJob(initialJobId);setQuery(initialJobId);setActor('');setReason('');setEnabled(false);setError('');setNotice('');setBusy(false);editing.current=false;},[token]);
 useEffect(()=>{if(!open)return;let live=true;const update=()=>refresh().catch(e=>{if(live&&current()&&gate.current.view===view)setError(String(e.message||e));});void update();const timer=setInterval(()=>void update(),10000);return()=>{live=false;sequence.current++;clearInterval(timer);};},[token,view,open]);
 const save=async()=>{if(!current()||!gate.current.canConfigure||!snapshot?.can_configure)return;setBusy(true);setError('');setNotice('');
  try{await request('/api/product-delivery/operations/notification-policy',{method:'PUT',body:JSON.stringify({enabled,expected_revision:snapshot.policy.revision,actor,reason})});
   if(current()){editing.current=false;await refresh();if(current())setNotice('내부 알림 정책 저장됨');}
  }catch(e){if(current())setError(String((e as Error).message||e));}finally{if(current())setBusy(false);}
 };
 const control=!canConfigure||!snapshot?.can_configure||busy;
 return <details className="rounded border border-slate-600 p-4" onToggle={e=>setOpen(e.currentTarget.open)}><summary className="cursor-pointer font-semibold">운영 상태·영속 로그·내부 알림</summary>
 {open&&<section aria-label="운영 관측과 알림" className="mt-3 space-y-3 text-sm">
  <p className="text-slate-400">검사와 학습 작업의 저장된 기록을 조회합니다. 실패 알림은 명시적으로 켜며, 외부로 로그를 전송하지 않습니다.</p>
  {!snapshot?<p role="status">현재 프로젝트의 저장 기록 확인 중…</p>:<>
   <p>서비스 {snapshot.service} · 실제 worker 준비 {snapshot.readiness.available?snapshot.readiness.status:'확인 불가'} · 미처리 {snapshot.queue.outstanding}/{snapshot.queue.capacity??'한도 확인 불가'}</p>
   <p>실행 백엔드 디스크 여유 {snapshot.metrics.disk.available?(snapshot.metrics.disk.free_bytes/1024**3).toFixed(1)+' GiB':'측정 불가'} · 외부 전송 꺼짐</p>
   <div>{snapshot.metrics.gpu.available?snapshot.metrics.gpu.devices.map(gpu=><p key={gpu.id}>{gpu.id} · GPU {gpu.utilization_percent}% · 메모리 {gpu.memory_used_mib}/{gpu.memory_total_mib} MiB</p>):<p>실행 백엔드 NVIDIA GPU 지표 확인 불가 · {snapshot.metrics.gpu.reason}</p>}</div>
   {(snapshot.queue.backlog||snapshot.training_events.backlog)&&<p className="text-amber-300">새 기록을 페이지 단위로 동기화 중입니다. 새로고침 후 계속 조회하세요.</p>}
   {!snapshot.training_events.available&&<p className="text-amber-300">학습 작업의 프로젝트 연결을 확인할 수 없어 검사 기록만 조회합니다.</p>}
   <fieldset className="space-y-2 rounded border border-slate-700 p-3"><legend>내부 실패·필요행동 알림 · 정책 v{snapshot.policy.revision}</legend>
    <label className="flex gap-2"><input aria-label="내부 실패 알림" type="checkbox" checked={enabled} disabled={control} onChange={e=>{if(current()&&gate.current.canConfigure){editing.current=true;setEnabled(e.target.checked);}}}/>새 실패·서비스 준비 변화 알림을 기록</label>
    <input aria-label="운영 알림 작업자" maxLength={100} value={actor} disabled={control} placeholder="작업자" onChange={e=>{if(current()&&gate.current.canConfigure)setActor(e.target.value);}} className="rounded bg-slate-800 p-2"/>
    <input aria-label="운영 알림 변경 사유" maxLength={1000} value={reason} disabled={control} placeholder="변경 사유" onChange={e=>{if(current()&&gate.current.canConfigure)setReason(e.target.value);}} className="rounded bg-slate-800 p-2"/>
    <button className={button} disabled={control||!actor.trim()||!reason.trim()} onClick={()=>void save()}>알림 정책 저장</button>
    <p className="text-slate-400">켜기 전에 이미 조회한 오류는 다시 알리지 않습니다. 같은 기록을 반복 조회해도 새 알림을 만들지 않습니다.</p>
   </fieldset>
   <details><summary>저장된 내부 알림 {snapshot.notifications.total}건</summary><ul>{snapshot.notifications.items.map(item=><li key={item.id} className="mt-1 break-all">{new Date(item.at*1000).toLocaleString()} · {item.job_id||'프로젝트'} · {item.reason}</li>)}</ul>{snapshot.notifications.total>50&&<p>최근 50건을 표시합니다. 전체 기록은 프로젝트에 보존됩니다.</p>}</details>
  </>}
  <div className="flex flex-wrap gap-2"><input aria-label="운영 로그 작업 ID" value={job} maxLength={128} placeholder="검사·학습 작업 ID" onChange={e=>{if(current())setJob(e.target.value);}} className="rounded bg-slate-800 p-2"/>
   <button className={button} onClick={()=>{if(current()){setQuery(job.trim());setOffset(0);}}}>작업 이력 조회</button>
   <select aria-label="운영 로그 상태" value={state} onChange={e=>{if(current()){setState(e.target.value);setOffset(0);}}} className="rounded bg-slate-800 p-2"><option value="">전체 상태</option>{['running','completed','error','delivery_error','failed','interrupted','ready','stopped','unavailable','version_mismatch'].map(value=><option key={value}>{value}</option>)}</select>
   <button className={button} disabled={busy} onClick={()=>void refresh().catch(e=>{if(current())setError(String(e.message||e));})}>운영 기록 새로고침</button></div>
  {snapshot&&<><p>영속 로그 {snapshot.logs.items.length?offset+1:0}–{offset+snapshot.logs.items.length}/{snapshot.logs.total} · 현재 조회 {query||'전체 작업'}</p>
   <div className="max-h-96 space-y-2 overflow-auto">{snapshot.logs.items.map(row=><article key={row.id} data-log-id={row.id} className="rounded border border-slate-700 p-2"><p>{typeof row.at==='number'?new Date(row.at*1000).toLocaleString():row.at} · {row.kind} · {row.state}</p><p className="break-all">{row.job_id||row.trace_id} · {row.event}</p><p className="break-all">{row.message}</p><details><summary>기록 상세</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(row.payload,null,2)}</pre></details></article>)}</div>
   <div className="flex gap-2"><button className={button} disabled={offset===0} onClick={()=>{if(current())setOffset(Math.max(0,offset-50));}}>이전 로그 페이지</button><button className={button} disabled={!snapshot.logs.has_more} onClick={()=>{if(current())setOffset(offset+50);}}>다음 로그 페이지</button></div>
   <details><summary>오류별 다음 작업</summary>{Object.entries(snapshot.error_catalog).map(([code,text])=><p key={code}>{code} · {text}</p>)}</details></>}
  {notice&&<p role="status" className="text-emerald-300">{notice}</p>}{error&&<p role="alert" className="break-all text-red-300">{error}</p>}
 </section>}
 </details>;
}
