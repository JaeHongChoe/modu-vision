import {request} from './api';

export interface WholeFlowPolicy {
  policy_id:string; revision:number; minimum_normal:number; minimum_defect:number;
  maximum_escape_rate:number; maximum_overkill_rate:number; maximum_review_rate:number;
}
export interface WholeFlowApproval {
  revision_id:string; evaluation_id:string; version_id:string; graph_sha256:string;
  policy:WholeFlowPolicy; reviewer:string; reason:string;
  package_qualified:false; device_accepted:false; validity:{valid:boolean;reasons:string[]};
}
export interface RuntimeFlowPreview {
  base_revision_id:string; manifest_sha256:string; runtime_acceptance_sha256:string;
  review_revision_id:string|null; review_valid:boolean; device_accepted:false;
  policy:WholeFlowPolicy;
  metrics:{escape_rate:number;overkill_rate:number;review_rate:number;normal_count:number;defect_count:number};
  outputs:Array<{relative_path:string;image_sha256:string;truth:'OK'|'NG';decision:'OK'|'NG'|'REVIEW';output_sha256:string}>;
}
export const wholeFlowApproval={
  active:()=>request<WholeFlowApproval|null>('/api/flow-evaluations/approvals/active'),
  approve:(body:{evaluation_id:string;policy:WholeFlowPolicy;reviewer:string;reason:string;
    holdout_reviewed:boolean;expected_revision:string|null})=>request<WholeFlowApproval>(
      '/api/flow-evaluations/approvals',{method:'POST',body:JSON.stringify(body)}),
  select:(revision:string,body:{expected_revision:string|null;reviewer:string;reason:string})=>request<WholeFlowApproval>(
    `/api/flow-evaluations/approvals/${encodeURIComponent(revision)}/select`,{method:'PUT',body:JSON.stringify(body)}),
  runtimePreview:(revision:string,packagePath:string)=>request<RuntimeFlowPreview>(
    `/api/flow-evaluations/approvals/${encodeURIComponent(revision)}/runtime-preview`,
    {method:'POST',body:JSON.stringify({package_path:packagePath,device:'openvino:CPU'})}),
  reviewRuntime:(revision:string,body:{package_path:string;device:'openvino:CPU';reviewer:string;reason:string;
    holdout_reviewed:true;expected_revision:string|null})=>request<{revision_id:string;device_accepted:false}>(
      `/api/flow-evaluations/approvals/${encodeURIComponent(revision)}/runtime-review`,
      {method:'POST',body:JSON.stringify(body)}),
};
