import type {ParentCandidate} from '../types/parentCandidate';
import { request } from './api';
import {submitModelTraining} from './modelExecution';
import type {TrainingSchedulingOptions} from '../stores/useTrainingStore';

export type ModelFamily = 'classification' | 'segmentation' | 'detection' | 'anomaly' |
  'patch_classification' | 'ocr' | 'rotated_detection' | 'rotation' | 'defect_gan' | 'enhancement';
export type LocalTrainingDevice = 'cpu' | 'mps' | 'cuda';
export type PreparedDataset = {dataset_path: string; sample_count?: number; patch_count?: number;
  patch_size?: number; stride?: number; classes?: string[]; normal_class?: string;
  provenance: {split_counts?: Record<string, number>; source_dataset_path?: string; dataset_sha256?: string}};
export type FamilyModel = {job_id: string; checkpoint_path?: string; metadata: Record<string, unknown> & {dataset_path?: string; source_dataset_path?: string;training_provenance?:{family_dataset_path?:string}}};
export type ProgramJob = {execution_job_id?:string;model_id?:string;compute_profile_id?:string;job_id: string; status: string; task?: string; dataset_path?: string; source_dataset_path?: string;
  epoch?: number; epochs?: number; current_epoch?: number; total_epochs?: number; loss?: number;
  current_train_loss?: number; output_dir?: string; error?: string | {message?: string};
  training_provenance?: {labelset_id?: string}; result?: Record<string, unknown>};
export type RotationRow = {image: string; correction_deg: number; split: 'train' | 'val' | 'test'; source_sha256?: string};
export type RotationEvaluation = {angular_mae_deg: number; within_10_deg: number; sample_count: number; evaluation_id?: string};
export type RotationPrediction = {correction_deg: number; output_size: number[]; transform: number[][]; aligned_image_base64?: string};
export type TrialCapability = {architectures: string[]; metric_key: string; direction: 'min' | 'max';
  search_defaults: Record<string, Array<string | number>>; prepared_input: boolean; architecture_key?: string};
export type MeasuredTrial = {trial_id: string; config: Record<string, unknown>; status: string; metrics: Record<string, number>;
  latency_ms?: number; latency_scope?: string; objective?: number; checkpoint_path?: string; checkpoint_sha256?: string; error?: string;
  progress?:{epoch?:number;epochs?:number;batch?:number;batches?:number;loss?:number}};
export type AutomatedTrainingJob = {search_id: string; task: ModelFamily; status: string; mode: string;compute_profile_id?:string;
  dataset_path: string; source_dataset_path?: string; created_at: number; trials: MeasuredTrial[]; winner?: MeasuredTrial;
  training_provenance?: {labelset_id?: string}; stop_reason?: string; error?: string; epochs_consumed?: number; duration_seconds?: number; device?: string; memory_scope?:string; memory_used_mb?:number; budget?: {max_trials:number;max_total_epochs:number;max_seconds:number;max_memory_mb?:number}};

const post = <T>(path: string, body: unknown) => request<T>(path, {method: 'POST', body: JSON.stringify(body)});
const query = (values: Record<string, string | number>) => new URLSearchParams(Object.entries(values).map(([key, value]) => [key, String(value)])).toString();
// A job in any of these states is still working (a server job also transfers and syncs); a new start waits for it.
export const activeProgramJob = (status: string) => ['queued', 'preparing', 'transferring', 'running', 'stopping', 'cancelling', 'syncing'].includes(status);
export const programError = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);

export const modelTrainingProgram = {
  patch: {
    datasets: () => request<{datasets: PreparedDataset[]}>('/api/patch-classification/datasets'),
    prepare: (options: {patch_size: number; stride: number; normal_class: string; minimum_overlap: number}) => post<PreparedDataset>('/api/patch-classification/prepare', options),
    manifest: (dataset_path: string) => request<PreparedDataset>(`/api/patch-classification/manifest?${query({dataset_path})}`),
    train: (options: {dataset_path: string; backbone: string; epochs: number; batch_size: number; image_size: number;
      learning_rate: number; device: LocalTrainingDevice; warm_start_job_id?: string; pretrained_checkpoint?: string} & TrainingSchedulingOptions) => submitModelTraining('patch_classification',options,()=>post<ProgramJob>('/api/patch-classification/train', options)),
    status: (job_id: string) => request<ProgramJob>(`/api/training/status?${query({job_id})}`),
    jobs: () => request<{jobs: ProgramJob[]}>('/api/training/jobs'),
    cancel: (job_id: string) => post<ProgramJob>('/api/training/stop', {job_id}),
    reconnect: (job_id: string) => post<ProgramJob>('/api/training/reconnect', {job_id}),
    parents: (dataset_path: string, backbone: string) => request<{parents: ParentCandidate[]}>(`/api/training/warm-start-parents?${query({dataset_path, task: 'patch_classification', preset: 'fast', backbone})}`),
    models: (source_dataset_path: string) => request<{models: FamilyModel[]}>(`/api/evaluation/model-comparisons/models?${query({source_dataset_path, task: 'patch_classification'})}`),
    evaluate: (job_id: string) => post<Record<string, unknown>>('/api/patch-classification/evaluate', {job_id, force_recompute: true}),
    export: (job_id: string) => post<{package_path?: string; package_dir?: string}>('/api/export/runtime', {job_id, export_format: 'torchscript', package_name: 'patch_classifier'}),
  },
  rotation: {
    datasets: () => request<{datasets: PreparedDataset[]}>('/api/rotation/datasets'),
    prepare: (source_dataset_path: string, samples: RotationRow[]) => post<PreparedDataset>('/api/rotation/prepare', {source_dataset_path, samples}),
    manifest: (dataset_path: string) => request<PreparedDataset & {samples: RotationRow[]}>(`/api/rotation/manifest?${query({dataset_path})}`),
    train: (options: {dataset_path: string; epochs: number; batch_size: number; image_size: number; width: number;
      learning_rate: number; device: LocalTrainingDevice; warm_start_job_id?: string}) => submitModelTraining('rotation',options,()=>post<ProgramJob>('/api/rotation/train', {...options, background: true})),
    jobs: () => request<{jobs: ProgramJob[]}>('/api/rotation/jobs'),
    status: (job_id: string) => request<ProgramJob>(`/api/rotation/jobs/${encodeURIComponent(job_id)}`),
    cancel: (job_id: string) => post<ProgramJob>(`/api/rotation/jobs/${encodeURIComponent(job_id)}/cancel`, {}),
    parents: (dataset_path: string, image_size: number, width: number) => request<{parents: ParentCandidate[]}>(`/api/rotation/warm-start-parents?${query({dataset_path, image_size, width})}`),
    models: () => request<{models: FamilyModel[]}>('/api/rotation/models'),
    evaluate: (job_id: string, dataset_path: string, device: LocalTrainingDevice) => post<RotationEvaluation>('/api/rotation/evaluate', {job_id, dataset_path, device, split: 'test'}),
    predict: (job_id: string, image_path: string, device: LocalTrainingDevice) => post<RotationPrediction>('/api/rotation/predict', {job_id, image_path, device, include_aligned: true}),
    export: (job_id: string) => post<{package_dir: string}>('/api/rotation/export', {job_id}),
  },
  automated: {
    capabilities: () => request<{tasks: Partial<Record<ModelFamily, TrialCapability>>}>('/api/automated-training/capabilities'),
    jobs: () => request<{jobs: AutomatedTrainingJob[]}>('/api/automated-training/jobs'),
    status: (search_id: string) => request<AutomatedTrainingJob>(`/api/automated-training/jobs/${encodeURIComponent(search_id)}`),
    cancel: (search_id: string) => post<AutomatedTrainingJob>(`/api/automated-training/jobs/${encodeURIComponent(search_id)}/cancel`, {}),
    start: (options: {task: ModelFamily; dataset_path: string; family_dataset_path?: string; device: LocalTrainingDevice;
      mode: 'quick' | 'search' | 'fast_retrain'; epochs_per_trial: number; parent_job_id?: string;compute_profile_id?:string;seed?:number;reuse_search_id?:string;
      objective: 'val_loss' | 'loss_latency'; latency_weight: number;
      budget: {max_trials: number; max_total_epochs: number; max_seconds: number;max_memory_mb?:number};
      search_space: Record<string, Array<string | number>>; base_config: Record<string, unknown>}) => post<AutomatedTrainingJob>('/api/automated-training/start', {...options, background: true}),
  },
};
