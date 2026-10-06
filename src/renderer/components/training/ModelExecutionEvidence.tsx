import {useEffect,useRef,useState} from 'react';
import {request} from '../../services/api';
import {getExecutionContextIdentity,subscribeModelRecipes} from '../../services/modelExecution';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
type Receipt={receipt_id:string;evidence_sha256:string;execution_target:string;compute_profile_name:string|null;compute_profile_id:string|null;device:string;stage:string;checkpoint_sha256:string;input_binding_sha256:string;runtime:{device_name:string;process_id:number;gpu_uuid?:string;torch_version?:string}};
export function ModelExecutionEvidence({task,jobId}:{task:string;jobId:string}){
 useProjectStore();useComputeStore();
 const context=getExecutionContextIdentity();const scope=[context,task,jobId].join('\n');
 const sequence=useRef(0);const current=useRef(scope);current.current=scope;const [record,setRecord]=useState<{scope:string;rows:Receipt[]}>({scope,rows:[]}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const load=async()=>{const started=scope,startedSequence=++sequence.current;setBusy(true);setError('');try{const value=await request<{receipts:Receipt[]}>(`/api/model-execution/recipes?task=${encodeURIComponent(task)}&job_id=${encodeURIComponent(jobId)}`);if(current.current===started&&sequence.current===startedSequence)setRecord({scope:started,rows:value.receipts});}catch(e){if(current.current===started&&sequence.current===startedSequence)setError(e instanceof Error?e.message:String(e));}finally{if(current.current===started&&sequence.current===startedSequence)setBusy(false);}};
 useEffect(()=>{setRecord({scope,rows:[]});setError('');setBusy(false);if(jobId)void load();return subscribeModelRecipes(event=>{if(current.current===scope&&event.context===context&&event.task===task&&event.jobId===jobId)void load();});},[scope]);
 const rows=record.scope===scope?record.rows:[];
 return <section aria-label="저장된 실행 위치 기록" className="my-3 rounded border border-slate-700 p-3">
  <div className="flex items-center justify-between"><b>평가·예측 실행 기록</b><button type="button" disabled={!jobId||busy} onClick={()=>void load()} className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40">실행 기록 새로 읽기</button></div>
  {error&&<p role="alert" className="text-rose-300">{error}</p>}
  {!rows.length&&<p className="mt-2 text-slate-400">이 컴퓨터와 선택 서버에서 완료된 평가·예측·생성의 실행 위치와 파일 해시를 저장합니다.</p>}
  {rows.map(row=><details key={row.receipt_id} className="mt-2" open={rows[0]===row}><summary>{row.stage} · {row.compute_profile_name||'이 컴퓨터'} · {row.device} · {row.runtime.device_name}</summary><p className="break-all">기록 {row.receipt_id}</p><p>실행 프로세스 {row.runtime.process_id}{row.runtime.gpu_uuid?` · GPU ${row.runtime.gpu_uuid}`:''}{row.runtime.torch_version?` · PyTorch ${row.runtime.torch_version}`:''}</p><p className="break-all">모델 {row.checkpoint_sha256}</p><p className="break-all">입력 {row.input_binding_sha256}</p><p className="break-all">기록 해시 {row.evidence_sha256}</p></details>)}
 </section>;
}
