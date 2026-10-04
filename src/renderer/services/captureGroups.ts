import {request} from './api';
export type CaptureJoinPolicy={revision:number;policy:{required_view_ids:string[];timestamp_basis:'shared_clock'|'trigger_offset';max_skew_ms:number;deadline_ms:number;late_window_ms?:number;completeness_policy?:'all_required'}};
export type CaptureGroupState={policy:CaptureJoinPolicy|null;groups:Array<{part_id:string;trigger_id:string;state:'OPEN'|'COMPLETE'|'INCOMPLETE'|'EXPIRED';verdict:'OK'|'NG'|'REVIEW'|null;missing_view_ids:string[];recipe_sha256:string;policy_revision:number;reason?:string;alarms?:string[]}>};
export const captureGroups={
  status:()=>request<CaptureGroupState>('/api/runtime-services/capture-groups'),
  save:(policy:CaptureJoinPolicy,expected_revision:number)=>request<CaptureJoinPolicy>('/api/runtime-services/capture-groups/policy',{method:'PUT',body:JSON.stringify({policy,expected_revision})}),
};
