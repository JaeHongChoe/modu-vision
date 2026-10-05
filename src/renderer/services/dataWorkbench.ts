import {request} from './api';
import type {AnnotationItem} from '../types';
export interface DiagnosticItem {image_uuid:string;file_path:string;relative_path:string;source_sha256:string;revision:number;issues:string[];blur_score?:number;dark_fraction?:number;bright_fraction?:number;split:string;current_split?:string}
export interface DataDiagnostic {decision:'ready'|'needs_review';stale?:boolean;items:DiagnosticItem[];issue_counts:Record<string,number>;near_duplicates:{images:string[];distance:number;exact_bytes:boolean;cross_split:boolean;splits:string[];current_cross_split?:boolean;current_splits?:string[]}[];summary:{total:number;labeling:Record<string,{count:number;ratio:number}>;assignments:Record<string,{count:number;ratio:number}>;classes?:Record<string,{count:number;ratio:number}>};task_schema?:{task:string;valid_count:number;invalid_count:number;items:{relative_path:string;valid:boolean;error:string|null}[];limits:string};measurement_limits:string}
export interface DerivedVersion {id:string;parent_id:string|null;source_path:string;source_sha256:string;derived_sha256:string;created_at:number;actor:string;operation:Record<string,unknown>;size:[number,number];annotations:AnnotationItem[];file_path:string;dataset_path:string;image_url:string;omitted_annotation_ids:string[]}
export interface SavedReviewQueue {id:string;revision:number;cursor:number;created_at:number;stale?:boolean;error?:string;origin:{evaluation_id?:string;comparison_id?:string;job_id:string|null;step:4;evidence_sha256:string};scope:{source:string;task:string;labelset_id:string};items:{relative_path:string;file_path:string;source_sha256:string;reasons:string[];priority:number;state:'pending'|'reviewed'|'skipped'}[]}
export interface ReviewEvaluation {id:string;kind:'evaluation'|'comparison';evaluation_id?:string;comparison_id?:string;created_at:number;job_id:string|null;sample_count:number}
type WorkbenchScope={projectDir:string|null;transportRevision?:number;selectedProfileId?:string|null;apiTransportIdentity?:string;task?:string;project?:{id?:string;task?:string;source_dataset_dir?:string|null;active_labelset_id?:string}|null};
export function dataWorkbenchPersistenceScope(state:WorkbenchScope):string{return JSON.stringify([state.projectDir,state.project?.id,state.project?.task||state.task,state.project?.source_dataset_dir,state.project?.active_labelset_id||'default',state.apiTransportIdentity||'local',state.selectedProfileId||'local']);}
export function dataWorkbenchScope(state:WorkbenchScope):string{return JSON.stringify([dataWorkbenchPersistenceScope(state),state.transportRevision||0]);}
const post=(body:unknown):RequestInit=>({method:'POST',body:JSON.stringify(body)});
export const dataWorkbench={
 savedDiagnostics:()=>request<DataDiagnostic|{report:null}>('/api/data-workbench/diagnostics'),
 diagnose:(thresholds:{blur_threshold:number;exposure_fraction:number;near_distance:number})=>request<DataDiagnostic>('/api/data-workbench/diagnostics',post(thresholds)),
 derivedHistory:(imagePath:string)=>request<{versions:DerivedVersion[]}>(`/api/data-workbench/derived?image_path=${encodeURIComponent(imagePath)}`),
 derive:(body:{image_path:string;expected_sha256:string;expected_revision:number;actor:string;operation:Record<string,unknown>;parent_id?:string})=>request<DerivedVersion>('/api/data-workbench/derived',post(body)),
 queues:()=>request<{queues:SavedReviewQueue[]}>('/api/data-workbench/review-queues'),
 queue:(id:string)=>request<SavedReviewQueue>(`/api/data-workbench/review-queues/${encodeURIComponent(id)}`),
 evaluations:()=>request<{evaluations:ReviewEvaluation[]}>('/api/data-workbench/review-evaluations'),
 createQueue:(source:ReviewEvaluation,threshold:number,margin:number)=>request<SavedReviewQueue>('/api/data-workbench/review-queues',post({[source.kind==='comparison'?'comparison_id':'evaluation_id']:source.id,threshold,margin})),
 advance:(queue:SavedReviewQueue,state:'reviewed'|'skipped',actor:string)=>request<SavedReviewQueue>(`/api/data-workbench/review-queues/${encodeURIComponent(queue.id)}/advance`,post({expected_revision:queue.revision,relative_path:queue.items[queue.cursor].relative_path,state,actor})),
};
