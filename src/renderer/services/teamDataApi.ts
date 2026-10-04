import {request} from './api';
import type {ImageReviewMetadata} from './datasetWorkflow';
export interface BookCategory {id:number;name:string;color:string;definition:string;inclusion:string;exclusion:string;annotation_guidance:string;examples:{relative_path:string;content_hash:string;caption:string}[]}
export interface LabelBook {id:string;version:number;sha256:string;title:string;categories:BookCategory[];actor:string;published_at:number}
export interface TeamSettings {revision:number;editing_enabled:boolean;review_enabled:boolean;required_reviews:1|2;prevent_self_review:boolean;approved_only_training:boolean}
export interface TeamImageState {assignment:{assignee:string;priority:number;assigned_by:string;assigned_at:number}|null;edit_lease:{owner:string;expires_at:number}|null;reviews:{actor:string;decision:'approve'|'reject';reason:string;at:number;binding_sha256:string}[];review_status:'pending'|'approved'|'rejected'|'disputed';annotation_actor:string|null;history:Record<string,unknown>[];adjudication?:{actor:string;decision:'approve'|'reject';reason:string;at:number;binding_sha256:string}}
export interface TeamWorkspace {scope:{project_id:string;source:string;labelset_id:string};book:LabelBook|null;book_history:LabelBook[];settings:TeamSettings;members:{id:string;name:string;role:string}[];actor?:{id:string;name:string;role:string}|null}
export interface TeamReadiness {ready:boolean;counts:{approved:number;pending:number;rejected:number;disputed:number;unused:number;total:number;eligible:number};blockers:string[];book_version:number|null;book_sha256:string|null;policy_sha256:string;eligibility_sha256:string;eligible_image_uuids:string[]}
export interface EditLease {image_uuid:string;token:string;owner:string;expires_at:number}
export interface TeamQueue {items:ImageReviewMetadata[];total:number;offset:number;limit:number}
type ImageMutation={image:ImageReviewMetadata;lease_token?:string};
/** E05: gold-sample label review. */
export interface QualityProfile {retired?:{actor:string;at:string};profile_id:string;created_at:string;actor:string;task:'detection'|'segmentation';reference_labelset:string;candidate_labelset:string;reference_snapshot:{relative_path:string;annotation_sha256:string;mask_sha256:string|null}[];guideline_revision:{book_id:string;version:number;sha256:string}|null;class_match:string;shape_metric:string;tolerance:number;profile_sha256:string}
export interface QualityConflict {conflict_id:string;kind:'missing'|'extra'|'class'|'geometry';reference_object?:string;candidate_object?:string;label?:string;reference_label?:string;candidate_label?:string;overlap?:number;bbox:number[];candidate_bbox?:number[]}
export type QualityCounts=Record<'missing'|'extra'|'class'|'geometry',number>&{not_comparable?:number};
export interface QualityReport {report_id:string;profile_id:string;created_at:string;task:string;images:{relative_path:string;image_path:string;labeled:boolean;conflicts:QualityConflict[];error?:string}[];counts:QualityCounts;agreeing_images:number;limitations:string[];stale:boolean;stale_reasons:string[];current:boolean;candidate_changes:string[];passes:boolean;approval_eligible:boolean}
export interface QualityReportSummary {report_id:string;profile_id:string|null;created_at:string|null;task:string|null;counts:QualityCounts|null;agreeing_images:number|null;images:number|null;passes?:boolean;integrity_error?:string}
const json=(method:string,body:unknown):RequestInit=>({method,body:JSON.stringify(body)});
const imageAction=(image:ImageReviewMetadata,action:string,actor:string,extra:Record<string,unknown>={})=>request<ImageMutation>(`/api/team-data/images/${encodeURIComponent(image.image_uuid)}/${action}`,json('POST',{expected_revision:image.revision,actor,...extra}));
export const teamDataApi={
 workspace:()=>request<TeamWorkspace>('/api/team-data'),
 image:(id:string)=>request<ImageMutation>(`/api/team-data/images/${encodeURIComponent(id)}`),
 readiness:()=>request<TeamReadiness>('/api/team-data/readiness'),
 queue:(filters:{assignee?:string;state?:string;offset?:number;limit?:number}={})=>{const q=new URLSearchParams();Object.entries(filters).forEach(([k,v])=>{if(v!==undefined&&v!=='')q.set(k,String(v));});return request<TeamQueue>(`/api/team-data/queue?${q}`);},
 publish:(expectedVersion:number,actor:string,title:string,categories:BookCategory[])=>request<LabelBook>('/api/team-data/books',json('POST',{expected_version:expectedVersion,actor,title,categories})),
 settings:(settings:TeamSettings,actor:string,changes:Partial<Omit<TeamSettings,'revision'>>)=>request<TeamSettings>('/api/team-data/settings',json('PUT',{expected_revision:settings.revision,actor,changes})),
 assign:(image:ImageReviewMetadata,actor:string,assignee:string|null,priority:number)=>imageAction(image,'assign',actor,{assignee,priority}),
 acquire:(image:ImageReviewMetadata,actor:string)=>imageAction(image,'lease/acquire',actor,{ttl_seconds:120}),
 renew:(image:ImageReviewMetadata,actor:string,token:string)=>imageAction(image,'lease/renew',actor,{lease_token:token,ttl_seconds:120}),
 release:(image:ImageReviewMetadata,actor:string,token:string)=>imageAction(image,'lease/release',actor,{lease_token:token}),
 review:(image:ImageReviewMetadata,actor:string,decision:'approve'|'reject',reason:string)=>imageAction(image,'review',actor,{decision,reason}),
 adjudicate:(image:ImageReviewMetadata,actor:string,decision:'approve'|'reject',reason:string)=>imageAction(image,'adjudicate',actor,{decision,reason}),
 qualityProfiles:()=>request<{profiles:QualityProfile[];gold_policy:{include_gold_in_training:boolean}}>('/api/team-data/quality/profiles'),
 createQualityProfile:(body:{task:'detection'|'segmentation';reference_labelset:string;candidate_labelset:string;gold_images:string[];tolerance:number;actor?:string})=>request<QualityProfile>('/api/team-data/quality/profiles',json('POST',body)),
 runQualityReport:(profileId:string)=>request<QualityReport>(`/api/team-data/quality/profiles/${encodeURIComponent(profileId)}/reports`,{method:'POST'}),
 qualityReports:(profileId?:string)=>request<{reports:QualityReportSummary[]}>(`/api/team-data/quality/reports${profileId?`?profile_id=${encodeURIComponent(profileId)}`:''}`),
 qualityReport:(reportId:string)=>request<QualityReport>(`/api/team-data/quality/reports/${encodeURIComponent(reportId)}`),
 setGoldPolicy:(include:boolean,actor?:string)=>request<{include_gold_in_training:boolean}>('/api/team-data/quality/gold-policy',json('PUT',{include_gold_in_training:include,...(actor?{actor}:{})})),
 retireQualityProfile:(profileId:string,actor:string)=>request<{profile_id:string;actor:string;at:string}>(`/api/team-data/quality/profiles/${encodeURIComponent(profileId)}/retire`,json('POST',{actor})),
};
