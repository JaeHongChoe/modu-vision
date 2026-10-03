import type {SpecializedTrainingJob} from '../../services/specializedApi';
import {JobProgressView} from './JobProgressView';
import {JOB_STATUS_LABELS} from './jobProgress';

/** OCR and defect generation: the saved jobs of this source and labelset, and the selected job's shared progress view. */
export function SpecializedTrainingStatus({jobs,job,error,cancel,reconnect,reopen}:{jobs:SpecializedTrainingJob[];job:SpecializedTrainingJob|null;error:string;cancel:()=>Promise<void>;reconnect?:()=>Promise<void>;reopen:(id:string)=>void}) {
  return <div className="space-y-2 rounded border border-slate-600 p-3">
    <label>저장된 학습 작업 <select aria-label="저장된 학습 작업" value={job?.job_id ?? ''} onChange={event=>reopen(event.target.value)} className="ml-2 rounded bg-slate-900 p-1">
      {!jobs.length&&<option value="">작업 없음</option>}{jobs.map(item=><option key={item.job_id} value={item.job_id}>{item.job_id.slice(0,8)} · {JOB_STATUS_LABELS[item.status]||item.status}</option>)}
    </select></label>
    <JobProgressView job={job} busy={false} onCancel={()=>void cancel()} onReconnect={reconnect?()=>void reconnect():undefined}/>
    {job?.training_provenance?.dataset_version_id&&<p className="text-slate-400">입력 버전 {job.training_provenance.dataset_version_id}</p>}
    {error&&<p role="alert" className="text-rose-300">{error}</p>}
  </div>;
}
