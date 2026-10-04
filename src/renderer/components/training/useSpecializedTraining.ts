import {controlModelTraining,reconnectModelTraining,submitModelTraining} from '../../services/modelExecution';
import {activeJob,watchJob} from './jobProgress';
import {useComputeStore} from '../../stores/useComputeStore';
import {getApiPersistenceIdentity,request} from '../../services/api';
import {useEffect,useRef,useState} from 'react';
import {specializedApi,type SpecializedTrainingFamily,type SpecializedTrainingJob} from '../../services/specializedApi';
import {useProjectStore} from '../../stores/useProjectStore';
import {useTaskHandoff} from './useTaskHandoff';
import {selectHandoffRecord} from './taskHandoff';
import {scopedTrainingJob,type JobSnapshot} from './scopedTrainingJob';

export const isActiveSpecializedJob=(job:SpecializedTrainingJob|null) => !!job && activeJob(job.status);

export function useSpecializedTraining(family:SpecializedTrainingFamily,onComplete:(job:SpecializedTrainingJob)=>void) {
  const handoff=useTaskHandoff(family==='defect-gan'?'defect_gan':family);
  const project=useProjectStore(state=>state.project);
  const projectDir=useProjectStore(state=>state.projectDir);
  const source=project?.source_dataset_dir ?? '';
  const labelset=project?.active_labelset_id ?? 'default';
  const [jobs,setJobs]=useState<SpecializedTrainingJob[]>([]);
  const compute=useComputeStore();const apiIdentity=getApiPersistenceIdentity();
  const scope=`${family}\n${projectDir}\n${source}\n${labelset}\n${compute.selectedProfileId}\n${compute.transportRevision}\n${apiIdentity}`;
  const [snapshot,setSnapshot]=useState<JobSnapshot<SpecializedTrainingJob>|null>(null);
  const job=scopedTrainingJob(snapshot,scope,source,labelset,family==='defect-gan'?'defect_gan':family);
  const setJob=(row:SpecializedTrainingJob|null)=>setSnapshot(row?{scope,job:row}:null);
  const [error,setError]=useState('');
  const [readFailures,setReadFailures]=useState(0);  // a failed read schedules the next one instead of ending the watch
  const complete=useRef(onComplete);complete.current=onComplete;
  const completed=useRef(new Set<string>());
  const current=()=> {const state=useProjectStore.getState(),target=useComputeStore.getState();return state.projectDir===projectDir && (state.project?.source_dataset_dir ?? '')===source && (state.project?.active_labelset_id ?? 'default')===labelset&&target.selectedProfileId===compute.selectedProfileId&&target.transportRevision===compute.transportRevision&&getApiPersistenceIdentity()===apiIdentity;};
  useEffect(()=>{
    let active=true;setJobs([]);setJob(null);setError('');completed.current.clear();
    if(projectDir)void specializedApi.trainingJobs(family).then(result=>{
      if(!active||!current())return;
      const rows=result.jobs.filter(item=>item.source_dataset_path===source && item.training_provenance.labelset_id===labelset);
      setJobs(rows);setJob(handoff?(handoff.transport&&handoff.transport!=='local'?rows.find(row=>row.job_id===handoff.jobId)||null:handoff.kind==='automated'?rows.find(row=>row.job_id===handoff.jobId)||null:selectHandoffRecord(rows,handoff)||null):rows.find(item=>watchJob(item.status)) ?? rows[0] ?? null);

      if(handoff?.transport&&handoff.transport!=='local'&&handoff.executionJobId){void controlModelTraining<SpecializedTrainingJob>({job_id:handoff.jobId,execution_job_id:handoff.executionJobId,compute_profile_id:handoff.transport,status:handoff.status},'status',()=>Promise.reject(new Error('서버 작업 식별자가 필요합니다.'))).then(row=>{if(active&&current()){setJob(row);setJobs(rows=>[row,...rows.filter(item=>item.job_id!==row.job_id)]);}}).catch(cause=>{if(active&&current())setError(String(cause));});}
    }).catch(cause=>{if(active&&current())setError(String(cause.message ?? cause));});
    return()=>{active=false;};
  },[scope,handoff?.jobId,handoff?.selectionId]);
  useEffect(()=>{
    if(!job)return;
    if(job.status==='completed'&&!completed.current.has(job.job_id)){completed.current.add(job.job_id);complete.current(job);}
    if(!watchJob(job.status))return;  // an active job, or one whose state may still change (a dropped connection)
    let active=true;
    const timer=setTimeout(()=>{void controlModelTraining<SpecializedTrainingJob>(job,'status',()=>specializedApi.trainingJob(family,job.job_id)).then(record=>{
      if(!active||!current())return;
      setJob(record);setJobs(rows=>rows.map(item=>item.job_id===record.job_id?record:item));setReadFailures(0);
    }).catch(cause=>{if(active&&current()){setError(String(cause.message ?? cause));setReadFailures(count=>count+1);}});},isActiveSpecializedJob(job)&&!readFailures?600:3000);  // after a failed read, retry slowly
    return()=>{active=false;clearTimeout(timer);};
  },[family,job,projectDir,source,labelset,readFailures]);
  const start=async(path:string,epochs:number,warmStartJobId?:string,device:'cpu'|'cuda'|'mps'='cpu',options?:{recipe?:Record<string,unknown>;max_runtime_s?:number})=>{
    const config={dataset_path:path,epochs,device,...(warmStartJobId?{warm_start_job_id:warmStartJobId}:{}),...(options?.recipe?{recipe:options.recipe}:{}),...(options?.max_runtime_s!==undefined?{max_runtime_s:options.max_runtime_s}:{})};
    setError('');const record=await submitModelTraining<SpecializedTrainingJob>(family==='defect-gan'?'defect_gan':family,config,()=>options?.recipe||options?.max_runtime_s!==undefined?request<SpecializedTrainingJob>(`/api/${family}/train`,{method:'POST',body:JSON.stringify({...config,background:true})}):specializedApi.startTraining(family,path,epochs,warmStartJobId,device));
    if(current()){setJob(record);setJobs(rows=>[record,...rows.filter(item=>item.job_id!==record.job_id)]);}
  };
  const cancel=async()=>{
    if(!job)return;
    try{const record=await controlModelTraining<SpecializedTrainingJob>(job,'cancel',()=>specializedApi.cancelTraining(family,job.job_id));if(current())setJob(record);}
    catch(cause){if(current())setError(cause instanceof Error?cause.message:String(cause));}
  };
  const reconnect=async()=>{
    if(!job)return;
    try{const record=await reconnectModelTraining<SpecializedTrainingJob>(job);if(current())setJob({...job,...record});}
    catch(cause){if(current())setError(cause instanceof Error?cause.message:String(cause));}
  };
  const reopen=(identifier:string)=>setJob(jobs.find(item=>item.job_id===identifier) ?? null);
  return {jobs,job,error,start,cancel,reconnect,reopen,active:isActiveSpecializedJob(job)};
}
