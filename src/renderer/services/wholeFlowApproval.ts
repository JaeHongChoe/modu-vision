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
export const wholeFlowApproval={
  active:()=>request<WholeFlowApproval|null>('/api/flow-evaluations/approvals/active'),
  approve:(body:{evaluation_id:string;policy:WholeFlowPolicy;reviewer:string;reason:string;
    holdout_reviewed:boolean;expected_revision:string|null})=>request<WholeFlowApproval>(
      '/api/flow-evaluations/approvals',{method:'POST',body:JSON.stringify(body)}),
  select:(revision:string,body:{expected_revision:string|null;reviewer:string;reason:string})=>request<WholeFlowApproval>(
    `/api/flow-evaluations/approvals/${encodeURIComponent(revision)}/select`,{method:'PUT',body:JSON.stringify(body)}),
};
