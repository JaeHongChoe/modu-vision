import {useEffect,useState} from 'react';
import {request} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {programButton,programInput} from './ProgramWorkbenchControls';

type Comparison={comparison_id:string;heldout_count:number;heldout_sha256:string;synthetic_train_count:number;
  same_original_test_cohort:boolean;quality_approved:false;evidence_sha256:string;
  models:{before:{job_id:string};after:{job_id:string}};
  results:{before:{metrics:{accuracy:number;macro_f1:number}};after:{metrics:{accuracy:number;macro_f1:number}}};
  metric_delta:{accuracy:number;macro_f1:number}};

export function GANDownstreamComparison(){
  const projectDir=useProjectStore(state=>state.projectDir),source=useProjectStore(state=>state.project?.source_dataset_dir || '');
  const [jobs,setJobs]=useState<Array<{job_id:string;task:string;status:string}>>([]),[before,setBefore]=useState(''),[after,setAfter]=useState('');
  const [report,setReport]=useState<Comparison|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const current=()=>useProjectStore.getState().projectDir===projectDir&&(useProjectStore.getState().project?.source_dataset_dir||'')===source;
  useEffect(()=>{let active=true;setJobs([]);setBefore('');setAfter('');setReport(null);setBusy(false);setError('');
    if(projectDir)void Promise.all([request<{jobs:Array<{job_id:string;task:string;status:string}>}>('/api/training/jobs'),request<{comparison:Comparison|null}>('/api/defect-gan/downstream-comparison')]).then(([list,saved])=>{
      if(!active||!current())return;setJobs(list.jobs.filter(row=>row.task==='classification'&&row.status==='completed'));setReport(saved.comparison);
      if(saved.comparison){setBefore(saved.comparison.models.before.job_id);setAfter(saved.comparison.models.after.job_id);}
    }).catch(cause=>{if(active&&current())setError(String(cause.message||cause));});return()=>{active=false;};
  },[projectDir,source]);
  const compare=async()=>{setBusy(true);setError('');try{
    const result=await request<Comparison>('/api/defect-gan/downstream-comparison',{method:'POST',body:JSON.stringify({adopted_dataset_path:source,before_job_id:before,after_job_id:after})});
    if(current())setReport(result);
  }catch(cause){if(current())setError(cause instanceof Error?cause.message:String(cause));}finally{if(current())setBusy(false);}};
  return <section aria-label="GAN 채택 전후 검사 비교" className="space-y-3 rounded border border-violet-700 p-3">
    <h4 className="font-semibold">생성 데이터 채택 전후 검사 모델 비교</h4>
    <p>채택 데이터로 학습한 모델과 채택 전 모델을 선택하세요. 두 모델 모두 원본의 전체 시험 이미지로 검사하고 비교 기록을 저장합니다.</p>
    <div className="grid grid-cols-2 gap-2">{([['채택 전 검사 모델',before,setBefore],['채택 후 검사 모델',after,setAfter]] as const).map(([label,value,setValue])=><label key={label}>{label}<select aria-label={label} value={value} disabled={busy} onChange={event=>{setValue(event.target.value);setReport(null);}} className={programInput}><option value="">완료 모델 선택</option>{jobs.map(job=><option key={job.job_id} value={job.job_id}>{job.job_id}</option>)}</select></label>)}</div>
    <button disabled={busy||!before||!after||before===after} onClick={()=>void compare()} className={programButton}>{busy?'원본 시험 이미지 검사 중…':'같은 원본 시험 데이터로 비교·저장'}</button>
    {error&&<p role="alert">{error}</p>}
    {report&&<div aria-label="저장된 GAN 채택 전후 비교" className="space-y-1">
      <p>동일 원본 시험 {report.heldout_count}장 · 생성 데이터는 학습 {report.synthetic_train_count}장만 편입</p>
      <p>정확도 {report.results.before.metrics.accuracy.toFixed(4)} → {report.results.after.metrics.accuracy.toFixed(4)} · Macro F1 {report.results.before.metrics.macro_f1.toFixed(4)} → {report.results.after.metrics.macro_f1.toFixed(4)}</p>
      <p>시험 묶음 SHA {report.heldout_sha256}</p><p>비교 기록 {report.comparison_id}</p>
      <p>모델 선택을 위한 비교 근거입니다. 품질 승인·운영 배포는 수행하지 않습니다.</p>
    </div>}
  </section>;
}
