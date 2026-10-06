import {request,getApiPersistenceIdentity,getProjectContextGeneration} from './api';
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
 if(useComputeStore.getState().isLoaded===false)throw new Error('실행 서버 설정을 먼저 확인하세요.');
 const target=useComputeStore.getState();if(!target.selectedProfileId)return local();
 if(!target.isLoaded)throw new Error('실행 서버 설정을 먼저 확인하세요.');
 const profile=target.profiles.find(row=>row.id===target.selectedProfileId);if(!profile)throw new Error('선택한 실행 서버를 찾지 못했습니다.');
 const project=useProjectStore.getState().project;if(!project?.source_dataset_dir)throw new Error('프로젝트 원본을 연결하세요.');
 const {dataset_path,device,background,warm_start_job_id,queue,priority,max_runtime_s,...config}=options;
 if(device==='mps')throw new Error('선택 서버에서는 MPS 대신 서버 CPU 또는 CUDA를 선택하세요.');
 if(device&&device!=='cpu'&&device!=='cuda')throw new Error('선택 서버의 학습 장치를 확인하세요.');
 const row=await request<ExecutionIdentity>('/api/compute/jobs',{method:'POST',body:JSON.stringify({task,operation:'train',dataset_path:project.source_dataset_dir,
   family_dataset_path:dataset_path,compute_profile_id:profile.id,device:device==='cuda'?'cuda:0':'cpu',config_overrides:config,
   ...(queue!==undefined?{queue}:{}),...(priority!==undefined?{priority}:{}),...(max_runtime_s!==undefined?{max_runtime_s}:{}),
   ...(warm_start_job_id?{warm_start_job_id}:{})})});
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

type RecipeCompletion={task:string;jobId:string;context:string};
const recipeListeners=new Set<(event:RecipeCompletion)=>void>();
export function subscribeModelRecipes(listener:(event:RecipeCompletion)=>void){recipeListeners.add(listener);return()=>{recipeListeners.delete(listener);};}
export function getExecutionContextIdentity(){
 const project=useProjectStore.getState(),compute=useComputeStore.getState();
 return JSON.stringify([project.projectDir,project.project?.id,project.project?.source_dataset_dir,project.project?.active_labelset_id,compute.transportRevision,getApiPersistenceIdentity(),getProjectContextGeneration()]);
}
/** Local and selected native actions use the same strict, persisted recipe.
 * The optional legacy callback remains a source-compatibility parameter only;
 * it is never executed as a bypass or as fallback after a failed recipe. */
export async function executeModelRecipe<T>(task:ModelFamily,stage:'evaluate'|'predict'|'generate',params:Record<string,any>,_legacyLocal?:()=>Promise<T>):Promise<T>{
 const target=useComputeStore.getState();if(!target.isLoaded)throw new Error('실행 서버 설정을 먼저 확인하세요.');
 const selected=target.selectedProfileId,revision=target.transportRevision;
 const profile=selected?target.profiles.find(row=>row.id===selected):null;if(selected&&!profile)throw new Error('선택한 실행 서버를 찾지 못했습니다.');
 const {device='cpu',...options}=params;
 if(profile&&device==='mps')throw new Error('선택 서버에서는 MPS 대신 서버 CPU 또는 CUDA를 선택하세요.');
 if(!['cpu','cuda','cuda:0','mps'].includes(device))throw new Error('실행 장치를 확인하세요.');
 const signature=JSON.stringify(profile),context=getExecutionContextIdentity();
 const result=await request<T>('/api/model-execution/recipes',{method:'POST',body:JSON.stringify({task,stage,execution_target:profile?'selected_compute':'local',...(profile?{compute_profile_id:profile.id}:{}),device,params:options})});
 const current=useComputeStore.getState(),currentProfile=selected?current.profiles.find(row=>row.id===selected):null;
 if(current.selectedProfileId!==selected||current.transportRevision!==revision||JSON.stringify(currentProfile)!==signature||getExecutionContextIdentity()!==context)throw new Error('프로젝트 또는 실행 서버가 변경됐습니다. 현재 위치에서 다시 확인하세요.');
 for(const listener of recipeListeners)listener({task,jobId:String(options.job_id),context});
 return result;
}
