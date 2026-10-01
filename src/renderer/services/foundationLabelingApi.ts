import {request, type LabelSuggestion} from './api';
export interface VLMConfiguration {enabled:boolean;endpoint:string;model:string;api_key_env?:string|null;timeout_seconds?:number;max_tokens?:number}
export interface FoundationConfiguration {vlm?:VLMConfiguration;model_dir?:string|null;mask_model_dir?:string|null;feature_backbone?:string;feature_checkpoint?:string|null;feature_sha256?:string|null}
export interface ProviderReadiness {ready:boolean;error:string|null;limits:string;dependency_available:boolean;model_dir?:string|null;mask_model_dir?:string|null}
export interface FoundationSetup {can_configure_vlm?:boolean;configuration:FoundationConfiguration;providers:{foundation:ProviderReadiness;grounding_dino:ProviderReadiness;vlm?:ProviderReadiness};labelset_id:string;labelset_version:string}
export interface RegionExample {image_path:string;roi:[number,number,number,number]}
export interface FoundationPoint {x:number;y:number;label:0|1}
export interface SizeControls {min_area:number;max_area?:number;min_width:number;max_width?:number;min_height:number;max_height?:number}
export interface FoundationOptions extends SizeControls {label:string;prompt:string;device:string;threshold:number;text_threshold:number;max_candidates:number;output_geometry:'mask'|'polygon'|'bbox';positive_examples:RegionExample[];negative_examples:RegionExample[];points:FoundationPoint[];boxes:number[][];suggestion_model_id?:string;class_ids?:Record<string,number>}
export type FoundationProposal=Omit<LabelSuggestion,'candidates'> & {backend?:string;labelset_id?:string;labelset_version?:string;prompt?:string;device?:string;candidates:(LabelSuggestion['candidates'][number]&{area?:number;source?:string;provenance?:Record<string,unknown>;mask_rle?:string})[]};
export type LabelingJobStatus='queued'|'running'|'cancelling'|'completed'|'stopped'|'failed'|'interrupted';
export interface FoundationBatch {id:string;status:LabelingJobStatus;created_at:string;total:number;processed:number;generated:number;zero_candidates:number;failed:number;error?:string;entries:{image_path:string;proposal_id?:string;candidate_count?:number;status:string;error?:string}[];request:FoundationOptions}
export interface FeatureJob {id:string;status:LabelingJobStatus;model_id?:string;error?:string;loss?:number;training_regions?:number}
export interface FeatureModel {id:string;classes:string[];checkpoint_sha256:string;labelset_id:string;labelset_version:string;parent_model_id:string|null;training_regions:number;feature_metadata:Record<string,unknown>;loss_history:number[]}
export interface FeatureTrainingOptions {device:string;backbone:string;pretrained_checkpoint?:string;pretrained_sha256?:string;epochs:number;learning_rate:number;parent_model_id?:string;image_paths?:string[]}
export interface DicomView {view_id:string;display_url:string;source_path:string;source_sha256:string;view_sha256:string;width:number;height:number;frames:number;frame_index:number;modality:string;photometric_interpretation:string;bits_allocated:number;pixel_spacing_mm:number[];window_center:number;window_width:number}
const json=(body:unknown):RequestInit=>({method:'POST',body:JSON.stringify(body)});
export const foundationLabelingApi={
  setup:()=>request<FoundationSetup>('/api/label-candidates/setup'),
  configure:(body:Partial<FoundationConfiguration>)=>request<FoundationSetup>('/api/label-candidates/setup',{method:'PUT',body:JSON.stringify(body)}),
  generate:(body:Record<string,unknown>)=>request<FoundationProposal>('/api/label-candidates/generate',json(body)),
  startBatch:(body:Record<string,unknown>)=>request<FoundationBatch>('/api/label-candidates/batches',json(body)),
  batches:()=>request<{batches:FoundationBatch[]}>('/api/label-candidates/batches'),
  batch:(id:string)=>request<FoundationBatch>(`/api/label-candidates/batches/${encodeURIComponent(id)}`),
  cancelBatch:(id:string)=>request<FoundationBatch>(`/api/label-candidates/batches/${encodeURIComponent(id)}/cancel`,json({})),
  featureModels:()=>request<{models:FeatureModel[]}>('/api/label-suggestions/feature-models'),
  featureJobs:()=>request<{jobs:FeatureJob[]}>('/api/label-suggestions/feature-jobs'),
  train:(body:FeatureTrainingOptions)=>request<FeatureJob>('/api/label-suggestions/feature-train',json(body)),
  training:(id:string)=>request<FeatureJob>(`/api/label-suggestions/feature-train/${encodeURIComponent(id)}`),
  cancelTraining:(id:string)=>request<FeatureJob>(`/api/label-suggestions/feature-train/${encodeURIComponent(id)}/cancel`,json({})),
  dicomView:(body:{image_path:string;window_center?:number;window_width?:number;frame_index?:number})=>request<DicomView>('/api/dataset/dicom/view',json(body)),
};
export const labelingJobActive=(status?:LabelingJobStatus)=>!!status&&['queued','running','cancelling'].includes(status);
