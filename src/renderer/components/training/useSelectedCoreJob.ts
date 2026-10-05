import {useEffect,useRef,useState} from 'react';
import {getApiPersistenceIdentity,getProjectContextGeneration,request,subscribeProjectContext} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {useTaskHandoff} from './useTaskHandoff';
import {taskHandoffContextScope} from './taskHandoff';
import {watchJob,type JobRecord} from './jobProgress';

/** Reopen an explicitly selected saved core job without replacing an active training store or starting a worker. */
export function useSelectedCoreJob(currentJobId:string|null) {
  const handoff=useTaskHandoff();const project=useProjectStore();const compute=useComputeStore();
  const [epoch,setEpoch]=useState(getProjectContextGeneration);
  const generation=getProjectContextGeneration(),identity=getApiPersistenceIdentity();
  const context=taskHandoffContextScope({...project,...compute,apiTransportIdentity:identity});
  const scope=JSON.stringify([context,generation,epoch,handoff?.selectionId,handoff?.jobId,currentJobId]);
  const latest=useRef(scope);latest.current=scope;
  const [saved,setSaved]=useState<{scope:string;job:JobRecord|null;error:string}>({scope:'',job:null,error:''});
  useEffect(()=>subscribeProjectContext(()=>setEpoch(getProjectContextGeneration())),[]);
  useEffect(()=>{
    let active=true,timer:ReturnType<typeof setTimeout>|undefined;
    const same=()=>active&&latest.current===scope&&getProjectContextGeneration()===generation&&getApiPersistenceIdentity()===identity&&
      taskHandoffContextScope({...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()})===context;
    const family=handoff?.family;
    if(!handoff||!family||!['classification','segmentation','detection','anomaly'].includes(family)||handoff.status==='completed'||handoff.jobId===currentJobId)return;
    const inspect=async()=>{
      if(!same())return;
      try {
        const result=await request<{tasks:JobRecord[]}>('/api/training-workspace/tasks');
        if(!same())return;
        const job=result.tasks.find(row=>row.job_id===(handoff.executionJobId||handoff.jobId)&&row.task===family&&
          row.source_dataset_path===project.project?.source_dataset_dir&&
          (row.training_provenance?.labelset_id||'default')===(project.project?.active_labelset_id||'default')&&
          (row.compute_profile_id||'local')===handoff.transport);
        if(!job)throw new Error('선택한 저장 작업을 현재 출처·라벨 세트·실행 위치에서 찾지 못했습니다. 작업 센터에서 다시 확인하세요.');
        setSaved({scope,job,error:''});
        if(watchJob(job.status,job.observation))timer=setTimeout(()=>void inspect(),2000);
      } catch(cause){if(same())setSaved({scope,job:null,error:cause instanceof Error?cause.message:String(cause)});}
    };
    void inspect();return()=>{active=false;if(timer)clearTimeout(timer);};
  },[scope]);
  return saved.scope===scope?{job:saved.job,error:saved.error}:{job:null,error:''};
}
