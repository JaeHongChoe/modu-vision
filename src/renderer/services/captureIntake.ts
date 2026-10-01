import {request} from './api';
export type CaptureRouting='unknown'|'duplicate'|'failed';
export type CaptureOrigin={
  job_id:string;image_path:string;capture_source:string;service_state:string;created_at:string;updated_at:string;
  runtime_identity:{manifest_sha256?:string;model_sha256?:Record<string,string>}|null;graph_sha256:string|null;
  node_evidence:Array<Record<string,unknown>>;roi_evidence:Array<Record<string,unknown>>;error:string|null;job_receipt_sha256:string;
};
export type CaptureCandidate={
  candidate_id:string;revision:number;created_at:string;source_sha256:string|null;truth_verdict:'UNKNOWN';
  routing:CaptureRouting;duplicate_of:string|null;failure:string|null;source_prediction:string|null;review_state:'pending'|'reviewed';
  review:{decision:'adopt'|'reject';actor:string;note:string;at:string;source_sha256:string}|null;
  adoptions:string[];origin:CaptureOrigin;snapshot_path:string|null;stale?:boolean;stale_reason?:string;
};
export type CaptureVersion={
  version_id:string;name:string;created_at:string;actor:string;source_dataset_path:string;parent_source_dataset_path:string;
  scope:{project_id:string;source_dataset_path:string;task:string;labelset_id:string};activated:false;record_sha256:string;
  fixed_test_records:Array<{relative_path:string;sha256:string}>;fixed_test_sha256:string;review_policy_sha256:string;
  adopted:Array<{candidate_id:string;relative_path:string;source_sha256:string;truth_verdict:'UNKNOWN';usage_state:'not_used';workflow_state:'needs_review';origin:CaptureOrigin}>;
  training_readiness:string;lineage:{parent_source_dataset_path:string;task:string;labelset_id:string;parent_model_rule:string};
};
const json=(method:string,body:unknown):RequestInit=>({method,body:JSON.stringify(body)});
export function captureRoutingLabel(value:CaptureRouting):string{return {unknown:'정답 미확인',duplicate:'중복',failed:'실패'}[value];}
export const captureIntake={
  list:()=>request<{candidates:CaptureCandidate[];total:number}>('/api/capture-intake'),
  register:(job_ids?:string[])=>request<{candidates:CaptureCandidate[];total:number}>('/api/capture-intake/register',json('POST',{job_ids:job_ids?.length?job_ids:null,limit:100})),
  preview:(id:string)=>request<{candidate_id:string;width:number;height:number;data_url:string}>(`/api/capture-intake/candidates/${encodeURIComponent(id)}/preview`),
  review:(row:CaptureCandidate,actor:string,decision:'adopt'|'reject',note:string)=>request<CaptureCandidate>(`/api/capture-intake/candidates/${encodeURIComponent(row.candidate_id)}/review`,json('POST',{expected_revision:row.revision,actor,decision,note})),
  adopt:(candidate_ids:string[],actor:string,name:string)=>request<CaptureVersion>('/api/capture-intake/adopt',json('POST',{candidate_ids,actor,name})),
  versions:()=>request<{versions:CaptureVersion[];total:number}>('/api/capture-intake/versions'),
  version:(id:string)=>request<CaptureVersion>(`/api/capture-intake/versions/${encodeURIComponent(id)}`),
};
