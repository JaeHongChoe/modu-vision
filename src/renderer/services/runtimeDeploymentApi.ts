import {request,api} from './api';
export interface RuntimeOptions {device:string;cpu_threads:number;deadline_ms:number|null}
export interface OptimizationJob {job_id:string;status:'queued'|'running'|'stopping'|'completed'|'failed'|'cancelled'|'interrupted';package_path:string;options?:{package_dir:string;input_receipt?:{source_dataset_path:string}};error:string|null;result:{package_path:string;quality_approved:false;heldout_flow_count:number;heldout_flow_passed_count:number;models:Array<{job_id:string;task:string;precision:string;metrics:{max_absolute_error:number;mean_absolute_error:number;latency_mean_ms:number;reference_latency_mean_ms:number;measured_speedup:number;validation_image_count:number}}> }|null}
type RuntimeResult={final_verdict:string;roi_count:number;crops:Array<{roi_id:string;verdict:string;crop_thumbnail?:string;mask?:string;predicted_class?:string;recognized_text?:string;defect_area_px?:number;defect_score:number;bbox:number[]}>};
export type RuntimeHeldoutResult={index:number;total:number;image_sha256:string;reference:RuntimeResult;candidate:RuntimeResult;comparison:{status:string;mismatched_fields:string[]}};
export const runtimeDeploymentApi={
  capabilities:()=>request<{torch_devices:string[];openvino:{available:boolean;devices:string[];error?:string;version?:string};native_sdk:{languages:string[];transport:string}}>('/api/export/runtime-capabilities'),
  exportFlow:(data:Omit<Parameters<typeof api.export.flow>[0],'deployment_profile'> & {deployment_profile?:'standard'|'edge_cpu'|'edge_cuda';runtime_config?:RuntimeOptions})=>request<Awaited<ReturnType<typeof api.export.flow>>>('/api/export/flow',{method:'POST',body:JSON.stringify(data)}),
  optimize:(data:{package_dir:string;source_dataset_path:string;precision:'fp32'|'fp16'|'int8';device:string;cpu_threads:number;calibration_images:string[];validation_images:string[]})=>request<OptimizationJob>('/api/export/flow/optimize',{method:'POST',body:JSON.stringify(data)}),
  job:(id:string)=>request<OptimizationJob>(`/api/export/flow/optimization-jobs/${encodeURIComponent(id)}`),
  cancel:(id:string)=>request<OptimizationJob>(`/api/export/flow/optimization-jobs/${encodeURIComponent(id)}/cancel`,{method:'POST'}),
  prerequisites:(id:string)=>request<{approval_revision_ids:Record<string,string>}> (`/api/export/flow/optimization-jobs/${encodeURIComponent(id)}/approval-prerequisites`),
  heldout:(id:string,index:number)=>request<RuntimeHeldoutResult>(`/api/export/flow/optimization-jobs/${encodeURIComponent(id)}/heldout-results/${index}`),
  approve:(id:string,data:{reviewer:string;reason:string;holdout_reviewed:true;maximum_absolute_drift:number;approval_revision_ids:Record<string,string>})=>request<{package_path:string;release_policy_path:string}> (`/api/export/flow/optimization-jobs/${encodeURIComponent(id)}/approve`,{method:'POST',body:JSON.stringify(data)}),
};
