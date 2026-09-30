import {request,type DefectGANCropRow,type DefectGANCandidate} from './api';
import type {PreparedDataset,LocalTrainingDevice} from './modelTrainingProgram';
export type GANRegion={id:string;bbox:[number,number,number,number];opacity:number;feather_px:number;mask_polygon?:Array<[number,number]>};
export type GANPreview={source_size:[number,number];source_sha256:string;preview_data_url:string};
export type ExplicitGANRow=DefectGANCropRow & {label?:string};
type GANPrepared=PreparedDataset & {source_dataset_path:string;samples:ExplicitGANRow[];sample_count:number;provenance:PreparedDataset['provenance'] & {source_map?:Record<string,{source_relative_path:string;source_sha256:string}>}};
export const ganWorkflow={
  openReview:(job_id:string,review_id:string)=>request<{job_id:string;review_dir:string;candidates:DefectGANCandidate[];source_image_sha256?:string;regions?:GANRegion[];seed:number}>(`/api/defect-gan/reviews/${encodeURIComponent(job_id)}/${encodeURIComponent(review_id)}`),
  datasets:()=>request<{datasets:PreparedDataset[]}>('/api/defect-gan/datasets'),
  prepare:(source_dataset_path:string,samples:ExplicitGANRow[])=>request<GANPrepared>('/api/defect-gan/prepare',{method:'POST',body:JSON.stringify({source_dataset_path,samples})}),
  manifest:(dataset_path:string)=>request<GANPrepared>(`/api/defect-gan/manifest?dataset_path=${encodeURIComponent(dataset_path)}`),
  preview:(image_path:string)=>request<GANPreview>(`/api/defect-gan/source-preview?image_path=${encodeURIComponent(image_path)}`),
  generate:(job_id:string,count:number,seed:number,device:LocalTrainingDevice,composition?:{source_image_path:string;source_sha256:string;regions:GANRegion[]})=>request<{job_id:string;review_dir:string;quality_status:string;candidates:DefectGANCandidate[];regions?:GANRegion[];source_image_sha256?:string;generation_mode?:string}>('/api/defect-gan/generate',{method:'POST',body:JSON.stringify({job_id,count,seed,device,...composition})}),
};
