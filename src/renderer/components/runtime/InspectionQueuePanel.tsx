import {useEffect,useRef,useState} from 'react';
import {useProjectStore} from '../../stores/useProjectStore';
import {getProjectContextGeneration,request,subscribeProjectContext} from '../../services/api';
import {useDeliveryScope} from './useDeliveryScope';

type Job={job_id:string;image_path:string;image_sha256:string;state:string;attempts:number;retry_count:number;replay_count:number;replay_of:string|null;replay_reason:string|null;dead_letter_reason:string|null;error:string|null;runtime_binding_sha256:string;runtime_binding:Record<string,unknown>|null;created_at:string};
type Queue={jobs:Job[];total:number;offset:number;limit:number;has_more:boolean;outstanding:number;capacity:number;max_attempts:number;max_queue_age_seconds:number};
type Export={filename:string;format:'json'|'csv';content:string;count:number;total:number;truncated:boolean};
const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
const states=['queued','running','completed','error','delivery_pending','delivery_error'];
export function InspectionQueuePanel({ready,canReview}:{ready:boolean;canReview:boolean}){
 const language=useProjectStore(s=>s.language),t=(ko:string,en:string)=>language==='ko'?ko:en;
 const [epoch,setEpoch]=useState(getProjectContextGeneration),{key}=useDeliveryScope(String(epoch));
 const [open,setOpen]=useState(false),[offset,setOffset]=useState(0),[filter,setFilter]=useState('');
 const [record,setRecord]=useState<{key:string;view:string;data:Queue}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [selected,setSelected]=useState(''),[operator,setOperator]=useState(''),[reason,setReason]=useState(''),[events,setEvents]=useState<unknown>(null);
 const generation=getProjectContextGeneration(),token=JSON.stringify([key,generation]),view=JSON.stringify([offset,filter]);
 const gate=useRef({token,view,ready,canReview});gate.current={token,view,ready,canReview};
 const sequence=useRef(0),current=()=>gate.current.token===token&&getProjectContextGeneration()===generation;
 const queue=record?.key===token&&record.view===view?record.data:null;
 useEffect(()=>subscribeProjectContext(()=>setEpoch(getProjectContextGeneration())),[]);
 useEffect(()=>{setRecord(null);setOffset(0);setFilter('');setSelected('');setOperator('');setReason('');setEvents(null);setError('');setNotice('');setBusy(false);},[token]);
 const refresh=async()=>{
  if(!current()||!gate.current.ready)return;
  const id=++sequence.current,startedView=view;
  const result=await request<Queue>(`/api/product-delivery/operator/queue?limit=50&offset=${offset}${filter?'&state='+encodeURIComponent(filter):''}`);
  if(current()&&gate.current.ready&&gate.current.view===startedView&&id===sequence.current)setRecord({key:token,view:startedView,data:result});
 };
 useEffect(()=>{
  if(!open||!ready)return;
  let live=true;
  const update=()=>refresh().catch(e=>{if(live&&current()&&gate.current.view===view)setError(String(e.message||e));});
  void update();const timer=setInterval(()=>void update(),5000);
  return()=>{live=false;sequence.current++;clearInterval(timer);};
 },[token,view,open,ready]);
 const action=async(fn:()=>Promise<void>,mutation=false)=>{
  if(!current()||!gate.current.ready||(mutation&&!gate.current.canReview))return;
  setBusy(true);setError('');setNotice('');
  try{await fn();}catch(e){if(current())setError(String((e as Error).message||e));}
  finally{if(current())setBusy(false);}
 };
 const retry=(job:Job)=>action(async()=>{
  await request(`/api/product-delivery/operator/queue/${encodeURIComponent(job.job_id)}/retry`,{method:'POST'});
  if(current()){await refresh();if(current())setNotice(t('동일 검사 재시도 등록됨 · 완료 결과를 확인하세요.','Retry queued for the same inspection; check its final result.'));}
 },true);
 const replay=()=>action(async()=>{
  const result=await request<{job_id:string}>(`/api/product-delivery/operator/queue/${encodeURIComponent(selected)}/replay`,{method:'POST',body:JSON.stringify({operator,reason})});
  if(current()){setSelected('');setReason('');setEvents(null);await refresh();if(current())setNotice(t('새 검사로 재처리 등록됨: ','Replay queued as a new inspection: ')+result.job_id);}
 },true);
 const exportResults=(format:'json'|'csv')=>action(async()=>{
  const result=await request<Export>('/api/product-delivery/operator/queue-export?limit=5000&format='+format);
  if(!current()||!gate.current.ready)return;
  const url=URL.createObjectURL(new Blob([result.content],{type:format==='json'?'application/json':'text/csv;charset=utf-8'}));
  try{const link=document.createElement('a');link.href=url;link.download=result.filename;link.click();}finally{URL.revokeObjectURL(url);}
  setNotice(`${result.count}/${result.total} `+t('건 내보내기','records exported')+(result.truncated?t(' · 최신 5,000건으로 제한됨',' · limited to the latest 5,000 records'):''));
 });
 return <details className="rounded border border-slate-600 p-4" onToggle={e=>setOpen(e.currentTarget.open)}>
  <summary className="cursor-pointer font-semibold">{t('입력 대기열·실패 재처리','Input queue and failed inspections')}</summary>
  {open&&<section aria-label={t('검사 입력 대기열','Inspection input queue')} className="mt-3 space-y-3">
   <p className="text-slate-400">{t('재시도는 같은 검사 ID, 재처리는 새 검사 ID입니다. 둘 다 접수 당시 레시피와 입력 해시를 유지합니다. 서버가 한도·만료·대기열 용량을 확인합니다.','Retry retains the inspection ID; replay creates a new ID. Both keep the admitted recipe and input hash. The service enforces attempt, expiry and capacity limits.')}</p>
   {!ready?<p role="status">{t('프로젝트 검사 서비스를 시작하면 저장된 대기열을 조회할 수 있습니다.','Start the project inspection service to read its persisted queue.')}</p>:<>
    <div className="flex flex-wrap items-center gap-2"><select aria-label={t('대기열 상태 필터','Queue state filter')} className="rounded bg-slate-800 p-2" value={filter} disabled={busy} onChange={e=>{setFilter(e.target.value);setOffset(0);setSelected('');setEvents(null);}}><option value="">{t('전체 상태','All states')}</option>{states.map(s=><option key={s}>{s}</option>)}</select><button className={button} disabled={busy} onClick={()=>void action(refresh)}>{t('대기열 새로고침','Refresh queue')}</button>{(['json','csv'] as const).map(format=><button key={format} className={button} disabled={busy} onClick={()=>void exportResults(format)}>{format.toUpperCase()+t(' 결과 내보내기',' result export')}</button>)}</div>
    {queue&&<p>{t('미처리 ','Outstanding ')}{queue.outstanding}/{queue.capacity} · {t('조회 ','Showing ')}{queue.jobs.length?offset+1:0}–{offset+queue.jobs.length}/{queue.total} · {t('시도 한도 ','Attempt limit ')}{queue.max_attempts} · {t('접수 유효시간 ','Admission lifetime ')}{queue.max_queue_age_seconds}s</p>}
    <div className="max-h-96 space-y-2 overflow-auto">{queue?.jobs.map(job=><article key={job.job_id} data-job-id={job.job_id} className="rounded border border-slate-700 p-3"><div className="flex flex-wrap justify-between gap-2"><b>{job.image_path.split(/[\\/]/).pop()}</b><span>{job.state} · {t('시도 ','Attempts ')}{job.attempts} · {t('재시도 ','Retries ')}{job.retry_count} · {t('재처리 ','Replays ')}{job.replay_count}</span></div><p className="mt-1 break-all text-xs text-slate-400">{job.job_id} · {job.created_at}</p>{job.dead_letter_reason&&<p className="mt-1 text-amber-300">{job.dead_letter_reason}</p>}{job.error&&<p className="break-all text-amber-300">{job.error}</p>}{job.replay_of&&<p className="mt-1 break-all">{t('이전 검사 ','Previous inspection ')}{job.replay_of} · {job.replay_reason}</p>}<details className="mt-2 text-xs"><summary>{t('접수 버전·입력 해시','Admitted version and input hash')}</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({image_sha256:job.image_sha256,runtime_binding_sha256:job.runtime_binding_sha256,runtime_binding:job.runtime_binding},null,2)}</pre></details><div className="mt-2 flex flex-wrap gap-2"><button className={button} disabled={busy} onClick={()=>void action(async()=>{const id=job.job_id;const result=await request(`/api/product-delivery/operator/queue/${encodeURIComponent(id)}/events`);if(current()){setSelected(id);setEvents(result);setReason('');}})}>{t('처리 이력','Processing history')}</button>{job.state==='error'&&<button className={button} disabled={busy||!canReview} onClick={()=>void retry(job)}>{t('동일 검사 재시도','Retry same inspection')}</button>}{['error','delivery_error'].includes(job.state)&&<button className={button} disabled={busy||!canReview} onClick={()=>{if(current()&&gate.current.canReview){setSelected(job.job_id);setEvents(null);setReason('');}}}>{t('새 검사로 재처리','Replay as new inspection')}</button>}</div></article>)}</div>
    {queue?.total===0&&<p>{t('이 상태의 검사 입력이 없습니다.','No inspections in this state.')}</p>}
    <div className="flex gap-2"><button className={button} disabled={busy||offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>{t('이전 페이지','Previous page')}</button><button className={button} disabled={busy||!queue?.has_more} onClick={()=>setOffset(offset+50)}>{t('다음 페이지','Next page')}</button></div>
    {selected&&queue?.jobs.some(j=>j.job_id===selected)&&<fieldset className="space-y-2 rounded border border-amber-700 p-3"><legend>{t('선택 검사 ','Selected inspection ')}{selected}</legend>{events?<pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(events,null,2)}</pre>:<><p>{t('원래 접수 버전으로 새 검사를 등록합니다. 현재 활성 레시피로 바꾸지 않습니다.','Creates a new inspection under the originally admitted release. The current active recipe is not substituted.')}</p><input aria-label={t('재처리 작업자','Replay operator')} maxLength={100} value={operator} disabled={busy||!canReview} onChange={e=>setOperator(e.target.value)} className="w-full rounded bg-slate-800 p-2"/><textarea aria-label={t('재처리 사유','Replay reason')} maxLength={1000} value={reason} disabled={busy||!canReview} onChange={e=>setReason(e.target.value)} className="w-full rounded bg-slate-800 p-2"/><button className={button} disabled={busy||!canReview||!operator.trim()||reason.trim().length<3} onClick={()=>void replay()}>{t('사유를 기록하고 재처리','Record reason and replay')}</button></>}</fieldset>}
   </>}
   {busy&&<p role="status">{t('실제 서비스 응답 확인 중…','Waiting for the service response…')}</p>}{notice&&<p role="status" className="text-emerald-300">{notice}</p>}{error&&<p role="alert" className="break-all text-red-300">{error}</p>}
  </section>}
 </details>;
}
