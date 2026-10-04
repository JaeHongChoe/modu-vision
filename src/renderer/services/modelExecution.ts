import {request} from './api';
import {useComputeStore} from '../stores/useComputeStore';
import {useProjectStore} from '../stores/useProjectStore';
import type {ModelFamily} from './modelTrainingProgram';

export type ExecutionIdentity={job_id:string;execution_job_id?:string;model_id?:string;compute_profile_id?:string;status:string;task?:string;[key:string]:any};
export function normalizeExecution(row:ExecutionIdentity):ExecutionIdentity {
 return {...row,execution_job_id:row.execution_job_id||row.job_id,job_id:row.model_id||row.job_id,
   epoch:row.current_epoch??row.epoch??0,epochs:row.total_epochs??row.epochs??0,
   batch:row.current_step??row.batch??0,batches:row.total_steps??row.batches??0,training_provenance:row.training_provenance||{},loss:row.current_train_loss??row.loss,error:typeof row.error==='object'?(row.error?.message||row.error?.details||row.error?.message_en):row.error};
}
export async function submitModelTraining<T>(task:ModelFamily,options:Record<string,any>,local:()=>Promise<T>):Promise<T> {
 const target=useComputeStore.getState();if(!target.selectedProfileId)return local();
 if(!target.isLoaded)throw new Error('실행 서버 설정을 먼저 확인하세요.');
 const profile=target.profiles.find(row=>row.id===target.selectedProfileId);if(!profile)throw new Error('선택한 실행 서버를 찾지 못했습니다.');
 const project=useProjectStore.getState().project;if(!project?.source_dataset_dir)throw new Error('프로젝트 원본을 연결하세요.');
 const {dataset_path,device,background,warm_start_job_id,...config}=options;
 const row=await request<ExecutionIdentity>('/api/compute/jobs',{method:'POST',body:JSON.stringify({task,operation:'train',dataset_path:project.source_dataset_dir,
   family_dataset_path:dataset_path,compute_profile_id:profile.id,device:profile.gpu_selector?'cuda:0':'cpu',config_overrides:config,...(warm_start_job_id?{warm_start_job_id}:{})})});
 return normalizeExecution({...row,compute_profile_id:profile.id,dataset_path:dataset_path,source_dataset_path:project.source_dataset_dir}) as T;
}
export async function controlModelTraining<T>(row:ExecutionIdentity,action:'status'|'cancel',local:()=>Promise<T>):Promise<T>{
 if(!row.compute_profile_id)return local();
 const id=encodeURIComponent(row.execution_job_id||row.job_id);
 return normalizeExecution(await request<ExecutionIdentity>(`/api/compute/jobs/${id}${action==='cancel'?'/cancel':''}`,action==='cancel'?{method:'POST'}:undefined)) as T;
}
/** S2-09: asks the server's job manager to observe a disconnected server job again; a local job has nothing to reconnect. */
export async function reconnectModelTraining<T>(row:ExecutionIdentity):Promise<T>{
 if(!row.compute_profile_id)throw new Error('이 컴퓨터의 작업은 다시 연결하지 않습니다. 저장된 상태를 다시 확인하세요.');
 const id=encodeURIComponent(row.execution_job_id||row.job_id);
 return normalizeExecution(await request<ExecutionIdentity>(`/api/compute/jobs/${id}/reconnect`,{method:'POST'})) as T;
}
export async function requireLocalSearch<T>(local:()=>T|Promise<T>):Promise<T>{
 if(useComputeStore.getState().selectedProfileId)throw new Error('자동 후보 탐색은 로컬에서 실행합니다. 실행 위치를 이 컴퓨터로 선택하거나 위 작업대의 서버 학습을 사용하세요.');
 return local();
}
