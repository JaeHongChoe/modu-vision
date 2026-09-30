import type {SpecializedTrainingJob} from '../../services/specializedApi';
import {isActiveSpecializedJob} from './useSpecializedTraining';

export function SpecializedTrainingStatus({jobs,job,error,cancel,reopen}:{jobs:SpecializedTrainingJob[];job:SpecializedTrainingJob|null;error:string;cancel:()=>Promise<void>;reopen:(id:string)=>void}) {
  return <div className="space-y-2 rounded border border-slate-600 p-3">
    <label>저장된 학습 작업 <select aria-label="저장된 학습 작업" value={job?.job_id ?? ''} onChange={event=>reopen(event.target.value)} className="ml-2 rounded bg-slate-900 p-1">
      {!jobs.length&&<option value="">작업 없음</option>}{jobs.map(item=><option key={item.job_id} value={item.job_id}>{item.job_id.slice(0,8)} · {item.status}</option>)}
    </select></label>
    {job&&<p role="status">{job.status} · epoch {job.epoch}/{job.epochs} · batch {job.batch}/{job.batches}{job.loss!==undefined&&` · loss ${job.loss.toFixed(4)}`}<span className="block text-slate-400">입력 버전 {job.training_provenance.dataset_version_id}</span></p>}
    {isActiveSpecializedJob(job)&&<button type="button" onClick={()=>void cancel()} disabled={job?.status==='stopping'} className="rounded border border-rose-600 px-3 py-1 text-rose-200">{job?.status==='stopping'?'중지 처리 중':'학습 중지'}</button>}
    {(error||job?.error)&&<p role="alert" className="text-rose-300">{error||job?.error}</p>}
  </div>;
}
