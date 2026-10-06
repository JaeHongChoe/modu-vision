import { api, request, type JobObservation, type RotatedSampleRow, type RotatedPrediction, type DefectGANCandidate } from './api';
import {executeModelRecipe} from './modelExecution';
export type MultiRotatedSample = Omit<RotatedSampleRow, 'label'|'box'> & { label?: string; box?: RotatedSampleRow['box']; objects?: Array<{label: string; direction_deg?: number; box: RotatedSampleRow['box']}> };
export type MultiRotatedPrediction = Partial<RotatedPrediction> & { image_size: number[]; source_sha256: string; model_sha256: string; preview_data_url: string; detections?: Array<{label: string; confidence: number; direction_deg?: number; box: RotatedSampleRow['box']; polygon: number[][]}> };
export type SpecializedTrainingJob = {observation?:JobObservation;execution_job_id?:string;model_id?:string;compute_profile_id?:string;job_id:string;task:string;status:'queued'|'preparing'|'running'|'stopping'|'stopped'|'completed'|'failed'|'interrupted'|'transferring'|'syncing'|'aborted'|'disconnected';epoch:number;epochs:number;batch:number;batches:number;loss?:number;error?:string;source_dataset_path:string;training_provenance:{dataset_version_id:string;labelset_id:string};events:Array<{at:number;status:string;epoch:number;batch:number}>};
export type SpecializedTrainingFamily = 'ocr'|'defect-gan';
export const specializedApi = {
  startTraining:(family:SpecializedTrainingFamily,dataset_path:string,epochs:number,warm_start_job_id?:string,device:'cpu'|'cuda'|'mps'='cpu') => request<SpecializedTrainingJob>(`/api/${family}/train`,{method:'POST',body:JSON.stringify({dataset_path,epochs,background:true,device,...(warm_start_job_id ? {warm_start_job_id} : {})})}),
  trainingJobs:(family:SpecializedTrainingFamily) => request<{jobs:SpecializedTrainingJob[]}>(`/api/${family}/jobs`),
  trainingJob:(family:SpecializedTrainingFamily,job:string) => request<SpecializedTrainingJob>(`/api/${family}/jobs/${job}`),
  cancelTraining:(family:SpecializedTrainingFamily,job:string) => request<SpecializedTrainingJob>(`/api/${family}/jobs/${job}/cancel`,{method:'POST'}),
  rotated: {
    ...api.rotated,
    manifest: (path: string) => request<{dataset_path:string;sample_count:number;split_counts:Record<string,number>;samples:MultiRotatedSample[]}>(`/api/rotated-detection/manifest?dataset_path=${encodeURIComponent(path)}`),
    saveManifest: (path: string, samples: MultiRotatedSample[]) => request<{dataset_path:string;sample_count:number;split_counts:Record<string,number>;samples:MultiRotatedSample[]}>('/api/rotated-detection/manifest',{method:'POST',body:JSON.stringify({dataset_path:path,samples})}),
    predict: (job_id: string, image_path: string) => request<MultiRotatedPrediction>('/api/rotated-detection/predict',{method:'POST',body:JSON.stringify({job_id,image_path})}),
  },
  ganReviews: () => request<{reviews:Array<{job_id:string;review_id:string;review_dir:string;unreviewed_count:number}>}>('/api/defect-gan/reviews'),
  openGANReview: (job_id:string,review_id:string) => request<{job_id:string;review_dir:string;candidates:DefectGANCandidate[]}>('/api/defect-gan/reviews/'+job_id+'/'+review_id),
  evaluateGAN: (job_id:string,dataset_path:string,device:'cpu'|'cuda'|'mps'='cpu') => executeModelRecipe<{real_sample_count:number;generated_count:number;rgb_statistics_mmd:number;metric_backend:string;quality_status:string}>('defect_gan','evaluate',{job_id,dataset_path,device},()=>request('/api/defect-gan/evaluate',{method:'POST',body:JSON.stringify({job_id,dataset_path})})),
  exportGAN: (job_id:string) => request<{package_path:string;generator_sha256:string}>('/api/defect-gan/export',{method:'POST',body:JSON.stringify({job_id})}),
  adopt: (job_id: string, review_dir: string, decisions: Array<{candidate_id:string;decision:'adopt'|'reject';label?:string;reviewer:string;reason:string}>) => request<{dataset_path:string;adopted_count:number;real_image_count:number}>('/api/defect-gan/adopt',{method:'POST',body:JSON.stringify({job_id,review_dir,decisions})}),
};
