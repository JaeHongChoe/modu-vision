import {useEffect,useRef,useState} from 'react';
import {specializedApi,type SpecializedTrainingFamily,type SpecializedTrainingJob} from '../../services/specializedApi';
import {useProjectStore} from '../../stores/useProjectStore';
import {useTaskHandoff} from './useTaskHandoff';
import {selectHandoffRecord} from './taskHandoff';
import {scopedTrainingJob,type JobSnapshot} from './scopedTrainingJob';

export const isActiveSpecializedJob=(job:SpecializedTrainingJob|null) => !!job && ['queued','running','stopping'].includes(job.status);

export function useSpecializedTraining(family:SpecializedTrainingFamily,onComplete:(job:SpecializedTrainingJob)=>void) {
  const handoff=useTaskHandoff(family==='defect-gan'?'defect_gan':family);
  const project=useProjectStore(state=>state.project);
  const projectDir=useProjectStore(state=>state.projectDir);
  const source=project?.source_dataset_dir ?? '';
  const labelset=project?.active_labelset_id ?? 'default';
  const [jobs,setJobs]=useState<SpecializedTrainingJob[]>([]);
  const scope=`${family}\n${projectDir}\n${source}\n${labelset}`;
  const [snapshot,setSnapshot]=useState<JobSnapshot<SpecializedTrainingJob>|null>(null);
  const job=scopedTrainingJob(snapshot,scope,source,labelset,family==='defect-gan'?'defect_gan':family);
  const setJob=(row:SpecializedTrainingJob|null)=>setSnapshot(row?{scope,job:row}:null);
  const [error,setError]=useState('');
  const complete=useRef(onComplete);complete.current=onComplete;
  const completed=useRef(new Set<string>());
  const current=()=> {const state=useProjectStore.getState();return state.projectDir===projectDir && (state.project?.source_dataset_dir ?? '')===source && (state.project?.active_labelset_id ?? 'default')===labelset;};
  useEffect(()=>{
    let active=true;setJobs([]);setJob(null);setError('');completed.current.clear();
    if(projectDir)void specializedApi.trainingJobs(family).then(result=>{
      if(!active||!current())return;
      const rows=result.jobs.filter(item=>item.source_dataset_path===source && item.training_provenance.labelset_id===labelset);
      setJobs(rows);setJob(handoff?(handoff.kind==='automated'?rows.find(row=>row.job_id===handoff.jobId)||null:selectHandoffRecord(rows,handoff)||null):rows.find(item=>isActiveSpecializedJob(item)) ?? rows[0] ?? null);
    }).catch(cause=>{if(active&&current())setError(String(cause.message ?? cause));});
    return()=>{active=false;};
  },[family,projectDir,source,labelset,handoff?.jobId,handoff?.selectionId]);
  useEffect(()=>{
    if(!job)return;
    if(job.status==='completed'&&!completed.current.has(job.job_id)){completed.current.add(job.job_id);complete.current(job);}
    if(!isActiveSpecializedJob(job))return;
    let active=true;
    const timer=setTimeout(()=>{void specializedApi.trainingJob(family,job.job_id).then(record=>{
      if(!active||!current())return;
      setJob(record);setJobs(rows=>rows.map(item=>item.job_id===record.job_id?record:item));
    }).catch(cause=>{if(active&&current())setError(String(cause.message ?? cause));});},600);
    return()=>{active=false;clearTimeout(timer);};
  },[family,job,projectDir,source,labelset]);
  const start=async(path:string,epochs:number,warmStartJobId?:string,device:'cpu'|'cuda'|'mps'='cpu')=>{
    setError('');const record=await specializedApi.startTraining(family,path,epochs,warmStartJobId,device);
    if(current()){setJob(record);setJobs(rows=>[record,...rows.filter(item=>item.job_id!==record.job_id)]);}
  };
  const cancel=async()=>{
    if(!job)return;
    try{const record=await specializedApi.cancelTraining(family,job.job_id);if(current())setJob(record);}
    catch(cause){if(current())setError(cause instanceof Error?cause.message:String(cause));}
  };
  const reopen=(identifier:string)=>setJob(jobs.find(item=>item.job_id===identifier) ?? null);
  return {jobs,job,error,start,cancel,reopen,active:isActiveSpecializedJob(job)};
}
