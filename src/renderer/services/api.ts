/**
 * src/renderer/services/api.ts
 * Type-safe HTTP REST client for Python FastAPI backend with dynamic port resolution.
 */

import type { ApiRequestBody } from './generated/apiTypes';
import type {
  AnnotationItem,
  ClassSplitCounts,
  ErrorCatalogItem,
  EvaluationResults,
  FlowchartExecutionResult,
  FlowchartPipeline,
  FlowModelTask,
  ImageMeta,
  VisionTask,
} from '../types';
import type { BatchInspectionRow, BatchInspectionReport, InspectionHistoryRun, InspectionRunSummary } from '../components/inference/batchInspection';

export interface ComputeProfile {
  memory_budget_mb?:number|null;
  allow_sharing?:boolean;
  distributed_processes?:number;
  id: string;
  name: string;
  ssh_target: string;
  ssh_port: number;
  remote_root: string;
  runtime_kind: 'python' | 'docker';
  runtime_value: string;
  gpu_selector?: string | null;
}

export type ComputeProfileInput = Omit<ComputeProfile, 'id'> & { id?: string };

export interface FlowModelCatalogItem {
  score_spec?: import('../types').ScoreSpec | null;
  class_semantics?: {version:1;roles:import('../utils/classSemantics').ClassRoles;basis?:Record<string,string>};
  class_names?: string[];
  class_ids?: number[];
  job_id: string;
  task: FlowModelTask;
  label: string;
  preset: string | null;
  created_at: string | null;
  best_metric: number | null;
  source_dataset_path: string;
  capabilities?: { role: 'preprocess' | 'inspection'; operation?: 'learned_rotation' | 'enhancement'; task?: FlowModelTask; native_source_coordinates?: boolean };
  threshold_settings?: Partial<Record<'optimal_threshold' | 'threshold' | 'probability_threshold' | 'size_threshold' | 'min_defect_area_px', number>>;
  training_labelset_id?: string | null;
  parent_job_id?: string | null;
}

export interface SavedFlowVersion {
  version_id: string;
  pipeline_id: string;
  pipeline_hash?: string;
  name: string;
  recipe_task: FlowModelTask | 'mixed';
  source_dataset_path: string;
  created_at: string;
  saved_at: string;
  node_count: number;
  model_count: number;
  is_latest: boolean;
  is_active: boolean;
}

export interface ComputeProbeResult {
  ready: boolean;
  runtime_ready?: boolean;
  device_name?: string | null;
  device_type?: string | null;
  checks?: Record<string, unknown> | Array<unknown>;
  message?: string | null;
  protocol_version?: string | number | null;
  free_space_bytes?: number | null;
}

export interface TrainingConfigOverrides extends Record<string, unknown> {
  anomaly_method?: 'padim' | 'patchcore' | 'dino_synthetic';
  anomaly_backbone?: 'dinov3_vits16' | 'dinov3_vitb16' | 'dinov3_vitl16';
  patch_size?: number;
  stride?: number;
  patches_per_image?: number;
  inference_batch_size?: number;
  pretrained_checkpoint?: string;
  pretrained_sha256?: string;
}

export interface ProjectConfig {
  id: string;
  name: string;
  task: VisionTask;
  project_dir: string;
  dataset_dir: string;
  models_dir: string;
  reports_dir: string;
  annotations_dir: string;
  active_labelset_id: string;
  source_dataset_dir: string | null;
  description: string;
  active_preset: 'fast' | 'precision';
  created_at: string;
  updated_at: string;
}

export interface ProjectLabelSet {
  id: string;
  name: string;
  source_id: string | null;
  created_at: string;
}

export interface ProjectBackupResult {
  archive_path: string;
  source_included: boolean;
  file_count: number;
  total_bytes: number;
  source_bytes: number;
}

export type RecentProject = Pick<ProjectConfig, 'id' | 'name' | 'task' | 'project_dir' | 'updated_at'>;

/** A durable dataset import (S3-01): a ledger job that builds one prepared, completely validated revision. */
export interface DatasetRevisionReceipt {
  revision_id: string; state: 'prepared' | 'rejected'; manifest_sha256: string;
  image_count: number; valid_count: number; error_count: number; invalid_policy: 'reject' | 'exclude'; skipped_links: number;
  unreadable_folders: number; reused_entries: number; verified_all: boolean;
  /** Absent or null in receipts sealed before source annotations and duplicates were recorded (index schema 2). */
  annotated?: number | null; annotation_errors?: number | null; duplicate_groups?: number | null; duplicate_images?: number | null;
  conflicting_duplicates?: number | null; cross_split_duplicates?: number | null;
  /** Whether annotation errors exclude images: only for tasks whose training reads the annotation files. */
  annotations_bind?: boolean | null;
}
export interface DatasetImportView {
  job_id: string; state: string; revision: number; attempts: number; cancel_requested: boolean;
  progress: { phase?: string; processed?: number; total?: number | null; total_known?: boolean } | null;
  result: { revision?: DatasetRevisionReceipt; reason?: string; error?: { message: string } } | null;
  idempotent_replay?: boolean;
  /** What the import read: the registered source (artifact null) or an uploaded archive extracted into the project. */
  source?: { root: string; artifact: ArtifactRef | null };
}
export interface DatasetRevisionRow {
  revision_id: string; source_root: string; task: string; invalid_policy: 'reject' | 'exclude'; state: 'prepared' | 'rejected';
  manifest_sha256: string; image_count: number; valid_count: number; error_count: number; skipped_links: number;
  unreadable_folders: number; reused_entries: number; verified_all: number; follow_links: number; created_ns: number; active: boolean;
  publication_key: string | null; parent_revision: string | null;
  /** False for revisions sealed before schema 3: their annotation and duplicate counts are unknown (null). */
  annotations_scanned: boolean; annotated: number | null; annotation_errors: number | null; duplicate_groups: number | null;
  duplicate_images: number | null; conflicting_duplicates: number | null; cross_split_duplicates: number | null;
  annotations_bind: number | null;
}
export interface DatasetRevisionImage {
  relative_path: string; image_uuid: string; sha256: string | null; size: number; width: number | null; height: number | null;
  label: string | null; split: string | null; valid: number; error_code: string | null; error_detail: string | null; via_link: number;
  annotation_format: 'labelme' | 'coco' | 'yolo' | null; annotation_labels: string[];
  annotation_files: Array<{ path: string; sha256: string }>; annotation_error: string | null;
}
/** One image of a validated revision as the library serves it (identity, content digest, validity, ledger fields). */
export interface LibraryImage {
  relative_path: string; image_uuid: string; sha256: string | null; size: number; width: number | null; height: number | null;
  label: string | null; split: string | null; valid: boolean; error_code: string | null; error_detail: string | null;
  file_name: string; file_path: string; annotation_labels: string[]; annotation_error: string | null; tags: string[];
  product: string | null; lot: string | null; workflow_state: 'unworked' | 'needs_review' | 'approved'; usage_state: 'active' | 'not_used';
}
export interface LibraryQuery {
  q?: string; label?: string; split?: 'train' | 'val' | 'test'; state?: 'valid' | 'invalid'; annotation_label?: string; tag?: string;
  product?: string; lot?: string; workflow_state?: 'unworked' | 'needs_review' | 'approved'; usage_state?: 'active' | 'not_used';
  cursor?: string | null; limit?: number; revision_id?: string;
}
/** A saved image choice: kept by identity and content digest, never by path alone. */
export interface LibrarySelection { image_uuid: string; sha256: string | null; relative_path: string }
export interface LibraryResolution extends LibrarySelection {
  /** unreadable: the image is in the revision but could not be read in that build (kept, not usable until it is). */
  status: 'found' | 'changed' | 'unreadable' | 'moved' | 'missing';
  current: { relative_path: string; image_uuid: string; sha256: string | null; valid: number; file_path: string } | null;
  candidates: Array<{ relative_path: string; image_uuid: string; sha256: string | null; valid: number; file_path: string }>;
}
/** A verified, project-scoped reference to stored bytes (an uploaded archive, a model, ...). */
export interface ArtifactRef { id: string; revision: number; sha256: string }
/** A resumable upload: the server holds `offset` committed bytes of `size_bytes`. */
export interface ArtifactUpload {
  id: string; kind: string; sha256: string; size_bytes: number; offset: number; state: string; artifact_id: string | null;
  expires_at: number | null;
}
/** Valid images of one revision with the same bytes (``members`` is the full count; ``items`` at most 50). */
export interface DatasetDuplicateGroup {
  sha256: string; members: number; conflicting: boolean; cross_split: boolean; items: DatasetRevisionImage[];
}
/** What the quick folder inspection decoded; only `complete` speaks for every image of the inventory. */
export interface DatasetQuickValidation { requested: boolean; checked_images: number; complete: boolean; scope: string }

export interface DatasetVersionSummary {
  id: string;
  name: string;
  note: string;
  kind: 'manual' | 'auto_backup';
  created_at: string;
  source_dataset_dir: string;
  image_count: number;
  label_file_count: number;
  total_image_bytes: number;
  copied_label_bytes: number;
  dataset_fingerprint: string;
  status: 'verified' | 'changed' | 'not_checked' | 'corrupt';
}

export interface DatasetVersionVerification {
  id: string;
  status: 'verified' | 'changed';
  changed_files: string[];
  editable_changed_files: string[];
  checked_file_count: number;
  checked_at: string;
}

export interface LabelSuggestionModel {
  job_id: string;
  task: VisionTask;
  classes: string[];
  created_at: string | null;
  checkpoint_path: string;
  source_dataset_path: string;
}

export interface OCRLabelRow {
  image: string;
  text: string;
  split: 'train' | 'val' | 'test';
  source_sha256?: string;
}

export interface OCRModelSummary {
  job_id: string;
  model_sha256: string;
  metadata: { best_epoch?: number; training_samples?: number; validation_samples?: number };
}

export interface OCREvaluation {
  sample_count: number;
  exact_match_accuracy: number;
  character_error_rate: number;
  samples: Array<{ image: string; reference_text: string; predicted_text: string }>;
}

export interface RotatedBox {
  cx: number;
  cy: number;
  width: number;
  height: number;
  angle_deg: number;
}

export interface RotatedSampleRow {
  direction_deg?: number;
  image: string;
  split: 'train' | 'val' | 'test';
  label: string;
  box: RotatedBox;
  source_sha256?: string;
}

export interface RotatedModelSummary {
  job_id: string;
  model_sha256: string;
  dataset_sha256: string;
  class_name: string;
  validation: { mean_oriented_iou: number; mean_angle_error_deg: number; sample_count: number };
}

export interface RotatedJob {
  execution_job_id?:string;compute_profile_id?:string;
  job_id: string;
  status: 'running' | 'stopping' | 'completed' | 'aborted' | 'failed' | 'interrupted';
  epochs_completed: number;
  total_epochs: number;
  result: { checkpoint_sha256: string; dataset_sha256: string } | null;
  error: string | null;
}

export interface RotatedEvaluation {
  mean_direction_error_deg?: number | null;
  direction_matched_count?: number;
  sample_count: number;
  mean_oriented_iou: number;
  mean_angle_error_deg: number;
  dataset_sha256: string;
  model_sha256: string;
}

export interface RotatedPrediction {
  direction_deg?: number;
  label: string;
  box: RotatedBox;
  polygon: Array<[number, number]>;
  image_size: [number, number];
  source_sha256: string;
  model_sha256: string;
  preview_data_url: string;
  preview_size: [number, number];
}

export interface DefectGANCropRow {
  image: string;
  bbox: [number, number, number, number];
  split: 'train' | 'val' | 'test';
  source_sha256?: string;
}

export interface DefectGANModelSummary {
  job_id: string;
  checkpoint_sha256: string;
  sample_count: number;
  epochs: number;
  quality_status: 'unvalidated';
}

export interface DefectGANCandidate {
  id: string;
  path: string;
  sha256: string;
  status: 'synthetic_unreviewed' | 'synthetic_adopted' | 'synthetic_rejected';
  preview_data_url: string;
}

export interface LabelSuggestionCandidate {
  id: string;
  confidence: number;
  annotation: AnnotationItem;
}

export interface LabelSuggestion {
  id: string;
  status: 'pending' | 'accepted' | 'rejected';
  created_at: string;
  reviewed_at?: string;
  image_path: string;
  image_id: string;
  image_width: number;
  image_height: number;
  job_id: string;
  task: VisionTask;
  threshold: number;
  confidence: number;
  latency_ms: number;
  candidates: LabelSuggestionCandidate[];
  accepted_candidate_ids: string[];
  backup_version_id: string | null;
  batch_id?: string;
}

export interface LabelSuggestionBatchEntry {
  image_path: string;
  image_id: string;
  status: 'queued' | 'generated' | 'zero_candidates' | 'failed';
  proposal_id?: string;
  candidate_count?: number;
  error?: string;
}

export interface LabelSuggestionBatch {
  id: string;
  status: 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed' | 'interrupted';
  created_at: string;
  finished_at?: string | null;
  source_dataset_dir: string;
  job_id: string;
  task: VisionTask;
  threshold: number;
  total: number;
  processed: number;
  generated: number;
  zero_candidates: number;
  failed: number;
  entries: LabelSuggestionBatchEntry[];
  error?: string;
}

export interface ModelComparisonModel {
  score_spec?: import('../types').ScoreSpec | null;
  job_id: string;
  task: FlowModelTask;
  training_dataset_fingerprint: string;
  created_at: string | null;
  preset: string | null;
}

export interface ModelComparisonSummary {
  selected_images: number;
  comparable_images: number;
  error_images: number;
  disagreements: number;
  incumbent_verdicts: Record<'OK' | 'NG' | 'REVIEW', number>;
  candidate_verdicts: Record<'OK' | 'NG' | 'REVIEW', number>;
  ng_to_ok: number;
  ok_to_ng: number;
  new_missed_ng: number;
  new_overkill_ok: number;
  known_ok_images: number;
  known_ng_images: number;
  unknown_truth_images: number;
}

export interface ModelComparisonOutcome {
  verdict: 'OK' | 'NG' | 'REVIEW' | null;
  defective_roi_count: number | null;
  max_defect_score: number | null;
  reason: string;
  error: string | null;
}

export interface ModelComparisonRow {
  image_id: string;
  file_name: string;
  file_path: string;
  image_sha256: string;
  ground_truth_label: string | null;
  ground_truth_verdict: 'OK' | 'NG' | null;
  incumbent: ModelComparisonOutcome;
  candidate: ModelComparisonOutcome;
  disagrees: boolean;
}

export interface ModelComparisonRecord {
  comparison_id: string;
  created_at: string;
  project_id: string;
  source_dataset_path: string;
  task: FlowModelTask;
  incumbent_job_id: string;
  candidate_job_id: string;
  status: 'completed' | 'completed_with_errors';
  summary: ModelComparisonSummary;
  dataset_fingerprint: string;
  selected_image_count: number;
  total_test_images: number;
}

export interface ModelComparisonReport extends ModelComparisonRecord {
  schema_version: number;
  incumbent_training_dataset_fingerprint: string;
  candidate_training_dataset_fingerprint: string;
  model_sha256: { incumbent: string; candidate: string };
  image_selection: string;
  limitations: string[];
  images: ModelComparisonRow[];
  intake_lineage?: {
    cohort_id: string;
    cohort_sha256: string;
    truth_sha256: string;
    ancestor_source_dataset_path: string;
    truth_source_dataset_path: string;
    version_ids: string[];
    version_sha256: string[];
  };
}

export interface ModelDeploymentAssessment {
  status: 'ready' | 'needs_review';
  reasons: string[];
  comparison_id: string;
  comparison_sha256: string;
  candidate_job_id: string;
  candidate_checkpoint_sha256: string;
  dataset_fingerprint: string;
  known_ok_images: number;
  known_ng_images: number;
  minimum_each_class: number;
  current_revision_id: string | null;
  training_dataset_fingerprint: string | null;
}

export interface ModelDeploymentRevision {
  revision_id: string;
  source_dataset_path: string;
  task: FlowModelTask;
  job_id: string;
  checkpoint_sha256: string;
  training_dataset_fingerprint: string;
  evaluation_dataset_fingerprint: string;
  comparison_id: string;
  comparison_sha256: string;
  parent_revision_id: string | null;
  restored_from_revision_id: string | null;
  action: 'approve' | 'rollback';
  reviewer: string;
  reason: string;
  created_at: string;
  valid?: boolean;
  is_active?: boolean;
}

export interface ProjectContext {
  workspace_id: string;
  project_id: string;
  actor_id: string;
  mode: 'local' | 'team';
}
export interface ArtifactRef { id: string; revision: number; sha256: string }
export interface ContextRequestOptions extends RequestInit {
  /** null discovers the server's current selection; an object pins this request. */
  projectContext?: ProjectContext | null;
  responseType?: 'json' | 'blob';
}
let boundProjectContext:ProjectContext|null=null;
let contextGeneration=0;
type ContextCandidate={context:ProjectContext;generation:number;transport:string|null};
const contextCandidates=new WeakMap<object,ContextCandidate>();
const contextListeners=new Set<(context:ProjectContext|null)=>void>();
function sameContext(left:ProjectContext|null,right:ProjectContext|null):boolean {
  return left===right||Boolean(left&&right&&left.workspace_id===right.workspace_id&&left.project_id===right.project_id
    &&left.actor_id===right.actor_id&&left.mode===right.mode);
}
function notifyContext():void {
  for(const listener of contextListeners)listener(getProjectContext());
}
export function subscribeProjectContext(listener:(context:ProjectContext|null)=>void):()=>void {
  contextListeners.add(listener);return ()=>{contextListeners.delete(listener);};
}
function validContext(value:unknown):value is ProjectContext {
  if(!value||typeof value!=='object')return false;
  const context=value as ProjectContext;
  return [context.workspace_id,context.project_id,context.actor_id].every(id=>typeof id==='string'&&/^[A-Za-z0-9_.:-]{1,128}$/.test(id))
    && (context.mode==='local'||context.mode==='team');
}
export function setProjectContext(context:ProjectContext|null):void {
  if(context&&!validContext(context))throw new Error('프로젝트 요청 문맥을 확인하세요.');
  if(sameContext(boundProjectContext,context))return;
  boundProjectContext=context?{...context}:null;contextGeneration++;notifyContext();
}
export function getProjectContext():ProjectContext|null {
  return boundProjectContext?{...boundProjectContext}:null;
}
/** Accepted authority epoch; also changes when leaving and returning to a namespace. */
export function getProjectContextGeneration():number {
  return contextGeneration;
}
function artifactQuery(ref:ArtifactRef):string {
  if(!/^[A-Za-z0-9_.:-]{1,128}$/.test(ref.id)||!Number.isSafeInteger(ref.revision)||ref.revision<1||!/^[a-f0-9]{64}$/.test(ref.sha256))
    throw new Error('파일 참조 ID·버전·해시를 확인하세요.');
  return new URLSearchParams({revision:String(ref.revision),sha256:ref.sha256}).toString();
}
let cachedPort: number | null = null;
let sharedBase:string|null=null;
export function setSharedApiBase(base:string|null):void {
  if(sharedBase===base)return;
  sharedBase=base;boundProjectContext=null;contextGeneration++;notifyContext();
}
/** Commit only an accepted server candidate; discovery alone never changes write authority.
 * The optional UI setter is synchronous. Validation precedes it, and telemetry
 * observers are notified after the UI setter and authority commit succeed.
 */
function acceptProjectContext(project:ProjectConfig,apply?:()=>void):void {
  const candidate=contextCandidates.get(project);
  // Existing servers/test adapters without the additive header retain their
  // legacy protocol. They cannot provide a new explicit namespace authority.
  if(!candidate){apply?.();return;}
  const unchanged=sameContext(boundProjectContext,candidate.context);
  if(candidate.transport!==sharedBase||(!unchanged&&candidate.generation!==contextGeneration)
      ||candidate.context.project_id!==project.id)
    throw new Error('프로젝트 선택 문맥이 바뀌었습니다. 현재 서버의 프로젝트를 다시 확인하세요.');
  const previous=boundProjectContext;
  boundProjectContext={...candidate.context};
  try{apply?.();}catch(error){boundProjectContext=previous;throw error;}
  if(!unchanged){contextGeneration++;notifyContext();}
}
/** Stable storage binding; credentials, tokens and the local process port are excluded. */
export function getApiPersistenceIdentity():string {
  if(!sharedBase)return 'local';
  try {const url=new URL(sharedBase);return `shared:${url.origin}${url.pathname.replace(/\/+$/,'')}`;}
  catch {throw new Error('공유 서버 주소를 확인하세요.');}
}

export async function getBackendPort(): Promise<number> {
  if (cachedPort) return cachedPort;
  if (typeof window !== 'undefined' && window.api?.getBackendPort) {
    try {
      const port = await window.api.getBackendPort();
      if (port) {
        cachedPort = port;
        return port;
      }
    } catch {
      // fallback
    }
  }
  if (typeof window !== 'undefined') {
    const urlParams = new URLSearchParams(window.location.search);
    const portParam = urlParams.get('port');
    if (portParam && !isNaN(Number(portParam))) {
      cachedPort = Number(portParam);
      return cachedPort;
    }
  }
  return 8000;
}

export function setCachedPort(port: number | null): void {
  cachedPort = port;
}

export async function getApiBaseUrl(): Promise<string> {
  if(sharedBase)return sharedBase;
  const port = await getBackendPort();
  return `http://127.0.0.1:${port}`;
}

export function resolveApiUrl(path: string, port?: number): string {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:') || path.startsWith('blob:')) {
    return path;
  }
  const effectivePort = port || cachedPort || 8000;
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  if(sharedBase)return `${sharedBase}${cleanPath}`;
  return `http://127.0.0.1:${effectivePort}${cleanPath}`;
}

export async function request<T>(path: string, options: ContextRequestOptions = {}): Promise<T> {
  const {projectContext,responseType='json',...fetchOptions}=options;
  const selecting=['/api/project/current','/api/project/create','/api/project/open','/api/project/restore'].includes(path)
    && projectContext===undefined;
  const generation=contextGeneration,transport=sharedBase;
  const selected=projectContext===null||selecting?null:projectContext??boundProjectContext;
  if(selected&&!validContext(selected))throw new Error('프로젝트 요청 문맥을 확인하세요.');
  const captured=selected?{...selected}:null;
  const headers=new Headers(fetchOptions.headers);
  if(!headers.has('Content-Type'))headers.set('Content-Type','application/json');
  if(captured){
    headers.set('X-Vision-Project',captured.project_id);
    headers.set('X-Vision-Context',JSON.stringify(captured));
  }
  // Capture both transport and project before a port lookup or selection can yield.
  const base = transport||`http://127.0.0.1:${await getBackendPort()}`;
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`;
  const response = await fetch(url, {
    ...fetchOptions,
    headers,
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`;
    try {
      const errData = await response.json();
      if (errData.detail) {
        if (typeof errData.detail === 'object') {
          // Structured details (FastAPI validation lists, coded errors) keep their shape and gain the HTTP status, so
          // callers can tell a refusal from a transient failure.
          throw Object.assign(errData.detail, { status: response.status });
        }
        errorDetail = errData.detail;
      }
    } catch (e) {
      if (typeof e === 'object' && e !== null && !(e instanceof SyntaxError)) {
        throw e;
      }
    }
    throw Object.assign(new Error(errorDetail), { status: response.status });
  }

  const encoded=response.headers?.get('X-Vision-Context');
  let receivedContext:ProjectContext|null=null;
  if(encoded){
    let received:unknown;
    try{received=JSON.parse(encoded);}catch{throw new Error('서버 프로젝트 문맥이 유효하지 않습니다.');}
    if(!validContext(received))throw new Error('서버 프로젝트 문맥이 유효하지 않습니다.');
    if(captured&&!sameContext(received,captured))throw new Error('요청과 응답의 프로젝트 문맥이 다릅니다.');
    receivedContext={...received};
  }
  if (response.status === 204) return undefined as T;
  if(responseType==='blob')return response.blob() as Promise<T>;
  const value:T=await response.json();
  if(receivedContext&&value&&typeof value==='object')contextCandidates.set(value,{context:receivedContext,generation,transport});
  return value;
}

export const api = {
  context: {
    get: (context?:ProjectContext) => request<{version:1;project_context:ProjectContext}>('/api/context',{projectContext:context}),
    registerArtifact: (data:{kind:'source'|'label'|'split'|'model'|'evaluation'|'flow'|'package'|'result';relative_path:string;sha256:string;expected_revision?:number},context?:ProjectContext) =>
      request<{project_context:ProjectContext;artifact_ref:ArtifactRef}>('/api/context/artifacts',{method:'POST',body:JSON.stringify(data),projectContext:context}),
    artifact: (ref:ArtifactRef,context?:ProjectContext) => request<{project_context:ProjectContext;artifact_ref:ArtifactRef}>(
      `/api/context/artifacts/${encodeURIComponent(ref.id)}?${artifactQuery(ref)}`,{projectContext:context}),
    artifactContent: (ref:ArtifactRef,context?:ProjectContext) => request<Blob>(
      `/api/context/artifacts/${encodeURIComponent(ref.id)}/content?${artifactQuery(ref)}`,{projectContext:context,responseType:'blob'}),
  },
  health: {
    check: () => request<{ status: string; version: string; device: string; device_name: string }>('/health'),
  },

  compute: {
    listProfiles: () => request<{ profiles: ComputeProfile[] }>('/api/compute/profiles'),
    saveProfile: (profile: ComputeProfileInput) =>
      request<{ profile: ComputeProfile }>('/api/compute/profiles', {
        method: 'POST', body: JSON.stringify(profile),
      }),
    deleteProfile: (id: string) =>
      request<void>(`/api/compute/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    getSelection: () => request<{ compute_profile_id: string | null }>('/api/compute/selection'),
    selectProfile: (computeProfileId: string | null) =>
      request<{ compute_profile_id: string | null }>('/api/compute/selection', {
        method: 'PUT', body: JSON.stringify({ compute_profile_id: computeProfileId }),
      }),
    probeProfile: (id: string) =>
      request<ComputeProbeResult>(`/api/compute/profiles/${encodeURIComponent(id)}/probe`, { method: 'POST' }),
  },

  project: {
    acceptContext: acceptProjectContext,
    getCurrent: () => request<ProjectConfig>('/api/project/current'),
    list: () => request<{ projects: RecentProject[] }>('/api/project/list'),
    create: (data: { name: string; task: VisionTask; project_dir?: string; description?: string }) =>
      request<ProjectConfig>('/api/project/create', { method: 'POST', body: JSON.stringify(data) }),
    open: (projectDir: string) =>
      request<ProjectConfig>('/api/project/open', { method: 'POST', body: JSON.stringify({ project_dir: projectDir }) }),
    update: (data: Partial<Pick<ProjectConfig, 'name' | 'task' | 'active_preset' | 'description' | 'source_dataset_dir'>>) =>
      request<ProjectConfig>('/api/project/update', { method: 'PUT', body: JSON.stringify(data) }),
    listLabelsets: () => request<{ active_id: string; labelsets: ProjectLabelSet[] }>('/api/project/labelsets'),
    createLabelset: (name: string) => request<ProjectLabelSet>('/api/project/labelsets', {
      method: 'POST', body: JSON.stringify({ name }),
    }),
    activateLabelset: (id: string) => request<ProjectConfig>(`/api/project/labelsets/${encodeURIComponent(id)}/activate`, {
      method: 'PUT',
    }),
    backup: (destinationDir: string) => request<ProjectBackupResult>('/api/project/backup', {
      method: 'POST', body: JSON.stringify({ destination_dir: destinationDir }),
    }),
    restore: (archivePath: string, targetDir: string) => request<ProjectConfig>('/api/project/restore', {
      method: 'POST', body: JSON.stringify({ archive_path: archivePath, target_dir: targetDir }),
    }),
  },

  library: {
    /** A page of the active (or named) validated revision; `next_cursor` continues it, `scanned_to` shows scan progress. */
    images: (query: LibraryQuery = {}) => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
      return request<{ revision_id: string; active: boolean; source_root: string; items: LibraryImage[]; next_cursor: string | null;
        scanned: number; scanned_to: string | null; complete_page: boolean }>(`/api/dataset/library/images${params.size ? `?${params}` : ''}`);
    },
    resolve: async (selections: LibrarySelection[], revisionId?: string) => {
      // Only the identity is sent (id and digest; a path is never matched and the shared server refuses path fields);
      // each result gets back the path it was saved with, for the notices.
      const answer = await request<{ revision_id: string; active: boolean; results: LibraryResolution[] }>('/api/dataset/library/resolve', {
        method: 'POST', body: JSON.stringify({
          selections: selections.map(({ image_uuid, sha256 }) => ({ image_uuid, sha256 })),
          ...(revisionId ? { revision_id: revisionId } : {}),
        }),
      });
      return { ...answer, results: answer.results.map((result, index) => ({ ...result, relative_path: selections[index]?.relative_path ?? '' })) };
    },
  },
  artifacts: {
    beginUpload: (data: { kind: 'source'; sha256: string; size_bytes: number }) =>
      request<{ upload: ArtifactUpload }>('/api/artifacts/uploads', { method: 'POST', body: JSON.stringify(data) }),
    uploadStatus: (uploadId: string) => request<{ upload: ArtifactUpload }>(`/api/artifacts/uploads/${encodeURIComponent(uploadId)}`),
    uploadChunk: (uploadId: string, offset: number, chunk: Blob, signal?: AbortSignal) =>
      request<{ upload: ArtifactUpload }>(`/api/artifacts/uploads/${encodeURIComponent(uploadId)}?offset=${offset}`, {
        method: 'PUT', body: chunk, headers: { 'Content-Type': 'application/octet-stream' }, signal,
      }),
    completeUpload: (uploadId: string) =>
      request<{ artifact_ref: ArtifactRef; state: string }>(`/api/artifacts/uploads/${encodeURIComponent(uploadId)}/complete`, { method: 'POST' }),
    cancelUpload: (uploadId: string) =>
      request<{ state: string }>(`/api/artifacts/uploads/${encodeURIComponent(uploadId)}`, { method: 'DELETE' }),
  },
  datasetImports: {
    /** One key per user action: a retried request returns the same job instead of starting another. */
    start: (data: { task: VisionTask; invalid_policy: 'reject' | 'exclude'; verify?: boolean; follow_links?: boolean }, idempotencyKey: string) =>
      request<DatasetImportView>('/api/dataset/imports', {
        method: 'POST', body: JSON.stringify(data), headers: { 'Idempotency-Key': idempotencyKey },
      }),
    get: (jobId: string) => request<DatasetImportView>(`/api/dataset/imports/${encodeURIComponent(jobId)}`),
    cancel: (jobId: string) => request<DatasetImportView>(`/api/dataset/imports/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
    accept: (jobId: string, revisionId: string, expectedActive: string | null) =>
      request<{ active_revision: string; job_id: string }>(`/api/dataset/imports/${encodeURIComponent(jobId)}/accept`, {
        method: 'POST', body: JSON.stringify({ revision_id: revisionId, expected_active: expectedActive }),
      }),
    revisions: () => request<{ active_revision: string | null; revisions: DatasetRevisionRow[] }>('/api/dataset/revisions'),
    gaps: (revisionId: string) => request<{ revision_id: string; gaps: Array<{ relative_path: string; reason: string }> }>(
      `/api/dataset/revisions/${encodeURIComponent(revisionId)}/gaps`),
    images: (revisionId: string, params: { cursor?: string | null; limit?: number; valid?: boolean; label?: string; annotationLabel?: string; annotationError?: boolean } = {}) => {
      const query = new URLSearchParams();
      if (params.cursor) query.set('cursor', params.cursor);
      if (params.limit) query.set('limit', String(params.limit));
      if (params.valid !== undefined) query.set('valid', String(params.valid));
      if (params.label) query.set('label', params.label);
      if (params.annotationLabel) query.set('annotation_label', params.annotationLabel);
      if (params.annotationError !== undefined) query.set('annotation_error', String(params.annotationError));
      return request<{ revision_id: string; items: DatasetRevisionImage[]; next_cursor: string | null }>(
        `/api/dataset/revisions/${encodeURIComponent(revisionId)}/images${query.size ? `?${query}` : ''}`);
    },
    /** Import an uploaded ZIP: the server extracts it into a project folder and validates it like a registered source. */
    startArchive: (data: { artifact: ArtifactRef; task: VisionTask; invalid_policy: 'reject' | 'exclude'; verify?: boolean }, idempotencyKey: string) =>
      request<DatasetImportView & { source_root: string }>('/api/dataset/imports/archive', {
        method: 'POST', body: JSON.stringify(data), headers: { 'Idempotency-Key': idempotencyKey },
      }),
    duplicates: (revisionId: string, params: { cursor?: string | null; limit?: number; kind?: 'conflicting' | 'cross_split' } = {}) => {
      const query = new URLSearchParams();
      if (params.cursor) query.set('cursor', params.cursor);
      if (params.limit) query.set('limit', String(params.limit));
      if (params.kind) query.set('kind', params.kind);
      return request<{ revision_id: string; groups: DatasetDuplicateGroup[]; next_cursor: string | null }>(
        `/api/dataset/revisions/${encodeURIComponent(revisionId)}/duplicates${query.size ? `?${query}` : ''}`);
    },
  },

  datasetVersions: {
    create: (data: { name: string; note?: string; dataset_path?: string }) =>
      request<DatasetVersionSummary>('/api/dataset/versions', {
        method: 'POST', body: JSON.stringify(data),
      }),
    list: () => request<{ versions: DatasetVersionSummary[] }>('/api/dataset/versions'),
    verify: (id: string) =>
      request<DatasetVersionVerification>(`/api/dataset/versions/${encodeURIComponent(id)}/verify`),
    restore: (id: string) =>
      request<{ restored_version_id: string; backup_version_id: string; source_dataset_dir: string; dataset_fingerprint: string }>(
        `/api/dataset/versions/${encodeURIComponent(id)}/restore`, { method: 'POST' },
      ),
  },

  labelSuggestions: {
    models: () => request<{ models: LabelSuggestionModel[] }>('/api/label-suggestions/models'),
    list: (imagePath?: string) => request<{ suggestions: LabelSuggestion[] }>(
      `/api/label-suggestions${imagePath ? `?image_path=${encodeURIComponent(imagePath)}` : ''}`,
    ),
    generate: (data: { job_id: string; image_path: string; threshold: number }) =>
      request<LabelSuggestion>('/api/label-suggestions/generate', { method: 'POST', body: JSON.stringify(data) }),
    startBatch: (data: { job_id: string; threshold: number; image_paths?: string[] }) =>
      request<LabelSuggestionBatch>('/api/label-suggestions/batches', { method: 'POST', body: JSON.stringify(data) }),
    listBatches: () => request<{ batches: LabelSuggestionBatch[] }>('/api/label-suggestions/batches'),
    getBatch: (id: string) => request<LabelSuggestionBatch>(`/api/label-suggestions/batches/${encodeURIComponent(id)}`),
    cancelBatch: (id: string) => request<LabelSuggestionBatch>(`/api/label-suggestions/batches/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    review: (id: string, decision: 'accept' | 'reject', candidateIds: string[] = []) =>
      request<LabelSuggestion>(`/api/label-suggestions/${encodeURIComponent(id)}/review`, {
        method: 'POST', body: JSON.stringify({ decision, candidate_ids: candidateIds }),
      }),
  },

  ocr: {
    manifest: (datasetPath: string) => request<{ sample_count: number; alphabet: string; samples: OCRLabelRow[] }>(
      `/api/ocr/manifest?dataset_path=${encodeURIComponent(datasetPath)}`,
    ),
    saveManifest: (datasetPath: string, samples: OCRLabelRow[]) => request<{ sample_count: number; alphabet: string; samples: OCRLabelRow[] }>(
      '/api/ocr/manifest', { method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, samples }) },
    ),
    models: () => request<{ models: OCRModelSummary[] }>('/api/ocr/models'),
    train: (datasetPath: string, epochs: number) => request<{ job_id: string; model_sha256: string; result: Record<string, unknown> }>(
      '/api/ocr/train', { method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, epochs }) },
    ),
    evaluate: (jobId: string, datasetPath: string) => request<OCREvaluation>(
      '/api/ocr/evaluate', { method: 'POST', body: JSON.stringify({ job_id: jobId, dataset_path: datasetPath, split: 'test' }) },
    ),
    predict: (jobId: string, imagePath: string) => request<{ text: string; confidence: number; model_sha256: string }>(
      '/api/ocr/predict', { method: 'POST', body: JSON.stringify({ job_id: jobId, image_path: imagePath }) },
    ),
  },

  rotated: {
    manifest: (datasetPath: string) => request<{ sample_count: number; class_name: string; split_counts: Record<string, number>; dataset_sha256: string; samples: RotatedSampleRow[] }>(
      `/api/rotated-detection/manifest?dataset_path=${encodeURIComponent(datasetPath)}`,
    ),
    saveManifest: (datasetPath: string, samples: RotatedSampleRow[]) => request<{ sample_count: number; class_name: string; split_counts: Record<string, number>; dataset_sha256: string; samples: RotatedSampleRow[] }>(
      '/api/rotated-detection/manifest', { method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, samples }) },
    ),
    models: () => request<{ models: RotatedModelSummary[] }>('/api/rotated-detection/models'),
    train: (datasetPath: string, epochs: number, warmStartJobId?: string) => request<RotatedJob>(
      '/api/rotated-detection/train', { method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, epochs,
        ...(warmStartJobId ? {warm_start_job_id: warmStartJobId} : {}) }) },
    ),
    job: (jobId: string) => request<RotatedJob>(`/api/rotated-detection/jobs/${encodeURIComponent(jobId)}`),
    cancel: (jobId: string) => request<RotatedJob>(`/api/rotated-detection/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
    evaluate: (jobId: string, datasetPath: string) => request<RotatedEvaluation>(
      '/api/rotated-detection/evaluate', { method: 'POST', body: JSON.stringify({ job_id: jobId, dataset_path: datasetPath, split: 'test' }) },
    ),
    predict: (jobId: string, imagePath: string) => request<RotatedPrediction>(
      '/api/rotated-detection/predict', { method: 'POST', body: JSON.stringify({ job_id: jobId, image_path: imagePath }) },
    ),
  },

  defectGAN: {
    manifest: (datasetPath: string) => request<{ sample_count: number; samples: DefectGANCropRow[]; split_counts: Record<string, number> }>(
      `/api/defect-gan/manifest?dataset_path=${encodeURIComponent(datasetPath)}`,
    ),
    saveManifest: (datasetPath: string, samples: DefectGANCropRow[]) => request<{ sample_count: number; samples: DefectGANCropRow[]; split_counts: Record<string, number> }>(
      '/api/defect-gan/manifest', { method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, samples }) },
    ),
    models: () => request<{ models: DefectGANModelSummary[] }>('/api/defect-gan/models'),
    train: (datasetPath: string, epochs: number) => request<{ job_id: string; result: { status: string; checkpoint_sha256: string } }>(
      '/api/defect-gan/train', { method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, epochs }) },
    ),
    generate: (jobId: string, count: number, seed: number) => request<{ job_id: string; review_dir: string; quality_status: string; candidates: DefectGANCandidate[] }>(
      '/api/defect-gan/generate', { method: 'POST', body: JSON.stringify({ job_id: jobId, count, seed }) },
    ),
  },

  dataset: {
    import: (data: { folder_path: string; task: VisionTask; validate_images?: boolean }) =>
      request<{
        status: string;
        total_images: number;
        source_images?: number;
        unlabeled_images?: number;
        split_supported?: boolean;
        split_unavailable_reason?: string | null;
        classes: Record<string, number>;
        split: { train: number; val: number; test?: number };
        corrupted_images?: any[];
        validation?: DatasetQuickValidation;
      }>('/api/dataset/import', { method: 'POST', body: JSON.stringify(data) }),

    generate: (data: {
      task: VisionTask | 'all';
      num_samples: number;
      output_dir: string;
      modality?: 'pcb' | 'wafer' | 'metal' | 'all';
      split_ratio?: number;
      normal_ratio?: number;
      seed?: number;
    }) =>
      request<{
        status: string;
        count: number;
        classes: string[];
        output_dir: string;
        train_count?: number;
        val_count?: number;
      }>('/api/dataset/generate', { method: 'POST', body: JSON.stringify(data) }),

    split: (data: { folder_path?: string; task?: VisionTask; train_ratio: number; val_ratio?: number; test_ratio?: number; seed?: number }) =>
      request<{ status: string; split: { train: number; val: number; test: number } }>('/api/dataset/split', {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    getImages: (params: { folder_path?: string; task?: VisionTask; limit?: number; offset?: number; split?: string; class_name?: string; label_status?: 'labeled' | 'unlabeled' }) => {
      const q = new URLSearchParams();
      if (params.folder_path) q.set('folder_path', params.folder_path);
      if (params.task) q.set('task', params.task);
      if (params.limit !== undefined) q.set('limit', String(params.limit));
      if (params.offset !== undefined) q.set('offset', String(params.offset));
      if (params.split) q.set('split', params.split);
      if (params.class_name) q.set('class_name', params.class_name);
      if (params.label_status) q.set('label_status', params.label_status);
      return request<{ total: number; limit: number; offset: number; items: ImageMeta[]; class_split_counts?: ClassSplitCounts | null }>(
        `/api/dataset/images?${q.toString()}`
      );
    },
  },

  annotations: {
    get: (imageId: string, dirPath?: string, filePath?: string) => {
      const q = new URLSearchParams();
      if (dirPath) q.set('dir_path', dirPath);
      if (filePath) q.set('file_path', filePath);
      const queryStr = q.toString() ? `?${q.toString()}` : '';
      return request<{
        image_id: string;
        annotations: AnnotationItem[];
        image_width?: number;
        image_height?: number;
        mask_file?: string;
      }>(`/api/annotations/${encodeURIComponent(imageId)}${queryStr}`);
    },
    save: (data: { image_id: string; image_path?: string; annotations: AnnotationItem[]; image_width?: number; image_height?: number; output_dir?: string }) =>
      request<{ status: string; count: number; mask_generated: boolean }>('/api/annotations/save', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    autoSelect: (data: { image_path?: string; image_id?: string; seed_x: number; seed_y: number; tolerance?: number }) =>
      request<{
        status: string;
        result: {
          polygon: Array<[number, number]>;
          bbox: [number, number, number, number];
          area: number;
          seed_point: [number, number];
        };
      }>('/api/annotations/auto-select', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    shapeConverter: (data: { image_path?: string; image_id?: string; bbox: [number, number, number, number]; sensitivity?: number }) =>
      request<{
        status: string;
        result: {
          polygon: Array<[number, number]>;
          area: number;
          source_bbox: [number, number, number, number];
        };
      }>('/api/annotations/shape-converter', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  training: {
    warmStartParents: (datasetPath: string, task: VisionTask, preset: 'fast' | 'precision', modelOverrides: Record<string, unknown> = {}) => {
      const query = new URLSearchParams({ dataset_path: datasetPath, task, preset });
      for (const name of ['backbone', 'model_name', 'anomaly_method', 'anomaly_backbone', 'patch_size', 'stride']) {
        const value = modelOverrides[name];
        if (typeof value === 'string' || (typeof value === 'number' && Number.isInteger(value))) query.set(name, String(value));
      }
      return request<{ parents: Array<{
        job_id: string; classes: string[]; architecture: string;
        checkpoint_sha256: string; dataset_fingerprint: string;
      }>; total: number }>(`/api/training/warm-start-parents?${query.toString()}`);
    },
    start: (data: {
      task: VisionTask;
      preset: 'fast' | 'precision';
      dataset_path: string;
      output_dir?: string;
      config_overrides?: TrainingConfigOverrides;
      compute_profile_id?: string;
      device?: string;
      warm_start_job_id?: string;
    }) => {
      // An explicit literal of the generated request type: a field the backend renames or removes fails typecheck here
      // (a variable or a spread would not be checked for extra fields). Undefined fields are left out as before.
      const body: ApiRequestBody['POST /api/training/start'] = {
        task: data.task, preset: data.preset, dataset_path: data.dataset_path,
        output_dir: data.output_dir, config_overrides: data.config_overrides, compute_profile_id: data.compute_profile_id,
        device: data.device, warm_start_job_id: data.warm_start_job_id,
      };
      return request<{ job_id: string; status: string; preset: string; task: string; compute_profile_id?: string | null; phase?: string; warm_start_parent_job_id?: string | null }>('/api/training/start', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },

    stop: (jobId?: string) =>
      request<{ status: string; job_id: string | null }>('/api/training/stop', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId } satisfies ApiRequestBody['POST /api/training/stop']),
        signal: AbortSignal.timeout(10000),
      }),

    reconnect: (jobId: string) =>
      request<{ job_id: string; status: string; compute_profile_id: string | null }>('/api/training/reconnect', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId }),
        signal: AbortSignal.timeout(10000),
      }),

    getStatus: (jobId?: string) => {
      const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : '';
      return request<any>(`/api/training/status${q}`, {
        signal: AbortSignal.timeout(5000),
      });
    },
  },

  modelDeployments: {
    assess: (comparisonId: string, sourceDatasetPath: string, task: FlowModelTask) => {
      const q = new URLSearchParams({ source_dataset_path: sourceDatasetPath, task });
      return request<ModelDeploymentAssessment>(
        `/api/model-deployments/assess/${encodeURIComponent(comparisonId)}?${q.toString()}`
      );
    },
    active: (sourceDatasetPath: string, task: FlowModelTask) => {
      const q = new URLSearchParams({ source_dataset_path: sourceDatasetPath, task });
      return request<{ active: ModelDeploymentRevision | null; field_runtime_applied: boolean }>(
        `/api/model-deployments/active?${q.toString()}`
      );
    },
    history: (sourceDatasetPath: string, task: FlowModelTask) => {
      const q = new URLSearchParams({ source_dataset_path: sourceDatasetPath, task });
      return request<{ revisions: ModelDeploymentRevision[] }>(
        `/api/model-deployments/history?${q.toString()}`
      );
    },
    approve: (data: { source_dataset_path: string; task: FlowModelTask; comparison_id: string;
      reviewer: string; reason: string; holdout_reviewed: true }) =>
      request<{ revision: ModelDeploymentRevision; field_runtime_applied: boolean }>(
        '/api/model-deployments/approve', { method: 'POST', body: JSON.stringify(data) }
      ),
    rollback: (data: { source_dataset_path: string; task: FlowModelTask; target_revision_id: string;
      reviewer: string; reason: string }) =>
      request<{ revision: ModelDeploymentRevision; field_runtime_applied: boolean }>(
        '/api/model-deployments/rollback', { method: 'POST', body: JSON.stringify(data) }
      ),
  },

  evaluation: {
    comparisonModels: (sourceDatasetPath: string, task: FlowModelTask) => {
      const q = new URLSearchParams({ source_dataset_path: sourceDatasetPath, task });
      return request<{ models: ModelComparisonModel[]; total: number }>(
        `/api/evaluation/model-comparisons/models?${q.toString()}`
      );
    },
    listComparisons: (sourceDatasetPath: string, task: FlowModelTask) => {
      const q = new URLSearchParams({ source_dataset_path: sourceDatasetPath, task });
      return request<{ comparisons: ModelComparisonRecord[]; total: number }>(
        `/api/evaluation/model-comparisons?${q.toString()}`
      );
    },
    getComparison: (comparisonId: string, sourceDatasetPath: string, task: FlowModelTask) => {
      const q = new URLSearchParams({ source_dataset_path: sourceDatasetPath, task });
      return request<ModelComparisonReport>(
        `/api/evaluation/model-comparisons/${encodeURIComponent(comparisonId)}?${q.toString()}`
      );
    },
    createComparison: (data: {
      source_dataset_path: string;
      task: FlowModelTask;
      incumbent_job_id: string;
      candidate_job_id: string;
      max_images: number;
    }) => request<ModelComparisonReport>('/api/evaluation/model-comparisons', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
    getResults: (jobId?: string, options?: {
      datasetPath?: string;
      forceRecompute?: boolean;
      sourceDatasetPath?: string;
      sourceTask?: VisionTask;
    }) => {
      const q = new URLSearchParams();
      if (jobId) q.set('job_id', jobId);
      if (options?.datasetPath) q.set('dataset_path', options.datasetPath);
      if (options?.forceRecompute) q.set('force_recompute', 'true');
      if (options?.sourceDatasetPath) q.set('source_dataset_path', options.sourceDatasetPath);
      if (options?.sourceTask) q.set('source_task', options.sourceTask);
      return request<EvaluationResults>(`/api/evaluation/results?${q.toString()}`);
    },

    getHeatmap: (imageId: string, jobId?: string, threshold?: number, filePath?: string, scoreSpec?: import('../types').ScoreSpec) => {
      const q = new URLSearchParams();
      if (jobId) q.set('job_id', jobId);
      if (threshold !== undefined) q.set('threshold', String(threshold));
      if (scoreSpec) q.set('score_spec', JSON.stringify(scoreSpec));
      q.set('format', 'base64');
      if (filePath) q.set('file_path', filePath);
      return request<{
        image_id: string;
        threshold: number;
        confidence_score: number;
        overlay_base64: string;
        predictions: any[];
        latency_ms: number;
      }>(`/api/evaluation/heatmap/${encodeURIComponent(imageId)}?${q.toString()}`);
    },

    getOverkillUnderkill: (params: {
      job_id?: string;
      target_max_underkill?: number;
      cost_escape?: number;
      cost_scrap?: number;
      current_threshold?: number;
    }) => {
      const q = new URLSearchParams();
      if (params.job_id) q.set('job_id', params.job_id);
      if (params.target_max_underkill !== undefined) q.set('target_max_underkill', String(params.target_max_underkill));
      if (params.cost_escape !== undefined) q.set('cost_escape', String(params.cost_escape));
      if (params.cost_scrap !== undefined) q.set('cost_scrap', String(params.cost_scrap));
      if (params.current_threshold !== undefined) q.set('current_threshold', String(params.current_threshold));
      return request<any>(`/api/evaluation/overkill-underkill?${q.toString()}`);
    },

    runBenchmark: (data: { job_id?: string; iterations?: number; resolution?: number }) =>
      request<any>('/api/evaluation/benchmark', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  flowchart: {
    modelCatalog: (sourceDatasetPath: string) =>
      request<{ models: FlowModelCatalogItem[]; total: number }>(
        `/api/flowchart/models/catalog?source_dataset_path=${encodeURIComponent(sourceDatasetPath)}`
      ),
    listPipelines: (sourceDatasetPath: string) =>
      request<{ pipelines: SavedFlowVersion[]; total: number }>(
        `/api/flowchart/pipelines?source_dataset_path=${encodeURIComponent(sourceDatasetPath)}`
      ),
    getPipelineVersion: (versionId: string) =>
      request<FlowchartPipeline>(`/api/flowchart/pipelines/${encodeURIComponent(versionId)}`),
    activatePipelineVersion: (versionId: string, sourceDatasetPath: string) =>
      request<{ status: 'active'; version_id: string; pipeline: FlowchartPipeline }>(
        `/api/flowchart/pipelines/${encodeURIComponent(versionId)}/activate?source_dataset_path=${encodeURIComponent(sourceDatasetPath)}`,
        { method: 'PUT' },
      ),
    getPipeline: (inspectionTask?: FlowModelTask | 'mixed', sourceDatasetPath?: string) => {
      const query = new URLSearchParams();
      if (inspectionTask) query.set('inspection_task', inspectionTask);
      if (sourceDatasetPath) query.set('source_dataset_path', sourceDatasetPath);
      return request<any>(`/api/flowchart/pipeline${query.size ? `?${query.toString()}` : ''}`);
    },
    getActivePipeline: (sourceDatasetPath: string) =>
      request<FlowchartPipeline>(`/api/flowchart/pipeline/active?source_dataset_path=${encodeURIComponent(sourceDatasetPath)}`),
    getSingleDetectionTemplate: (jobId?: string) =>
      request<any>(`/api/flowchart/templates/single-detection${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''}`),
    getSingleSegmentationTemplate: (jobId?: string, inspectionTask?: Exclude<FlowModelTask, 'detection'>) => {
      const query = new URLSearchParams();
      if (jobId) query.set('job_id', jobId);
      if (inspectionTask) query.set('inspection_task', inspectionTask);
      return request<any>(`/api/flowchart/templates/single-segmentation${query.size ? `?${query.toString()}` : ''}`);
    },
    getDetectorRoiTemplate: (inspectionTask: Exclude<FlowModelTask, 'detection'>) =>
      request<any>(`/api/flowchart/templates/detector-roi?inspection_task=${encodeURIComponent(inspectionTask)}`),
    getFixedRoiTemplate: (inspectionTask: Exclude<FlowModelTask, 'detection'>, jobId?: string) => {
      const query = new URLSearchParams({ inspection_task: inspectionTask });
      if (jobId) query.set('job_id', jobId);
      return request<FlowchartPipeline>(`/api/flowchart/templates/fixed-roi?${query.toString()}`);
    },
    getFiveModelChainTemplate: () =>
      request<FlowchartPipeline>('/api/flowchart/templates/five-model-chain'),
    getConditionalInspectionTemplate: () =>
      request<FlowchartPipeline>('/api/flowchart/templates/conditional-inspection'),
    verifyModels: (data: { source_dataset_path: string; models: Array<{ job_id: string; task: FlowModelTask }> }) =>
      request<{ verified_job_ids: string[] }>('/api/flowchart/models/verify', {
        method: 'POST', body: JSON.stringify(data),
      }),
    savePipeline: (data: FlowchartPipeline, recipeTask?: FlowModelTask | 'mixed', sourceDatasetPath?: string) => {
      const query = new URLSearchParams();
      if (recipeTask) query.set('recipe_task', recipeTask);
      if (sourceDatasetPath) query.set('source_dataset_path', sourceDatasetPath);
      return request<{ status: string; pipeline_id: string; node_count: number; version_id: string }>(`/api/flowchart/pipeline${query.size ? `?${query.toString()}` : ''}`, {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },
    run: (data: { image_path?: string; image_id?: string; pipeline?: any;
      execution_target?: 'local' | 'selected_compute' | 'model_compute'; device?: 'cpu' | 'mps' | 'cuda'; compute_profile_id?: string; project_id?: string; stop_node_id?: string }) =>
      request<any>('/api/flowchart/run', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  inspections: {
    reviewQueue: (sourceFolder: string, task: VisionTask) => {
      const q = new URLSearchParams({ source_folder: sourceFolder, task });
      return request<{ items: Array<{
        run_id: string; image: ImageMeta; model_verdict: 'REVIEW' | null;
        operator_verdict: 'OK' | 'NG' | 'REVIEW' | null;
        status: 'unreviewed' | 'still_review' | 'error'; can_review: boolean;
        error: string | null; created_at: string; pipeline_name: string; saved_version_id: string | null;
      }>; total: number; review_required: number; diagnostic_errors: number }>(
        `/api/inspections/review-queue?${q.toString()}`
      );
    },
    createRun: (report: BatchInspectionReport, pipeline: FlowchartPipeline, execution?: {
      execution_target: 'local' | 'selected_compute' | 'model_compute';
      device: 'cpu' | 'mps' | 'cuda'; compute_profile_id?: string; project_id: string;
    }) =>
      request<{
        run_id: string; status: string; saved_version_id: string | null;
        pipeline_hash: string; model_sha256: Record<string, string>;
      }>('/api/inspections/runs', {
        method: 'POST', body: JSON.stringify({
          source_folder: report.source_folder, task: report.task, scope: report.scope,
          pipeline, images: report.rows.map((row) => row.image), ...execution,
        }),
      }),
    executeRow: (runId: string, imagePath: string) =>
      request<FlowchartExecutionResult>(`/api/inspections/runs/${encodeURIComponent(runId)}/execute`, {
        method: 'POST', body: JSON.stringify({ image_path: imagePath }),
      }),
    recordRow: (runId: string, row: BatchInspectionRow) =>
      request<{ status: string }>(`/api/inspections/runs/${encodeURIComponent(runId)}/rows`, {
        method: 'PUT', body: JSON.stringify({
          image_path: row.image.file_path, state: row.state, result: row.result, error: row.error,
        }),
      }),
    finishRun: (runId: string, status: 'completed' | 'stopped') =>
      request<{ status: string }>(`/api/inspections/runs/${encodeURIComponent(runId)}/finish`, {
        method: 'PUT', body: JSON.stringify({ status }),
      }),
    listRuns: (sourceFolder: string, task: VisionTask) => {
      const query = new URLSearchParams({ source_folder: sourceFolder, task });
      return request<{ runs: InspectionRunSummary[] }>(`/api/inspections/runs?${query}`);
    },
    getRun: (runId: string) =>
      request<InspectionHistoryRun>(`/api/inspections/runs/${encodeURIComponent(runId)}`),
    reviewRow: (runId: string, data: {
      image_path: string; final_verdict: 'OK' | 'NG' | 'REVIEW'; reason: string; reviewer: string;
    }) => request<{ review_id: string }>(`/api/inspections/runs/${encodeURIComponent(runId)}/reviews`, {
      method: 'POST', body: JSON.stringify(data),
    }),
    exportRun: (runId: string, format: 'csv' | 'json') =>
      request<{ format: string; filename: string; content: string }>(
        `/api/inspections/runs/${encodeURIComponent(runId)}/export?format=${format}`
      ),
  },

  export: {
    edgeTargets: () => request<{
      profile: 'edge_cpu'; device: 'cpu';
      supported: Record<'linux' | 'windows' | 'macos', Array<'x86_64' | 'arm64'>>;
      host: { os: 'linux' | 'windows' | 'macos'; architecture: 'x86_64' | 'arm64' } | null;
      python: { minimum: string; maximum_exclusive: string };
    }>('/api/export/edge-targets'),
    flow: (data: {
      source_dataset_path: string;
      recipe_task: FlowModelTask | 'mixed';
      package_name: string;
      version_id?: string;
      verification_image_path?: string;
      verification_image_id?: string;
      approval_revision_ids?: Record<string,string>;
      parity_images?: Array<{path:string;image_id?:string}>;
      parity_device?: string;
      deployment_profile?: 'standard' | 'edge_cpu';
      target_os?: 'linux' | 'windows' | 'macos';
      target_arch?: 'x86_64' | 'arm64';
    }) => request<{
      status: string;
      package_path: string;
      package_name: string;
      pipeline_id: string;
      model_job_ids: string[];
      total_files: number;
      deployment?: {
        profile: 'edge_cpu'; device: 'cpu';
        target: { os: 'linux' | 'windows' | 'macos'; architecture: 'x86_64' | 'arm64' };
      };
      parity: {
        status: 'not_run' | 'passed' | 'mismatch' | 'failed';
        scope?: 'single_image' | 'cohort';
        device?: string;
        cohort_sha256?: string;
        manifest_sha256?: string;
        image_path?: string;
        final_verdict?: string;
        roi_count?: number;
        compared_fields?: string[];
      };
    }>('/api/export/flow', { method: 'POST', body: JSON.stringify(data) }),
    flowApprovalPrerequisites: (params:{source_dataset_path:string;recipe_task:FlowModelTask|'mixed';version_id?:string}) => {
      const query=new URLSearchParams({source_dataset_path:params.source_dataset_path,recipe_task:params.recipe_task});
      if(params.version_id)query.set('version_id',params.version_id);
      return request<import('./flowPackageExport').FlowApprovalPrerequisites>(`/api/export/flow/approval-prerequisites?${query}`);
    },
    runtime: (data: {
      job_id?: string;
      export_format?: string;
      resolution?: number;
      quantize_fp16?: boolean;
      package_name?: string;
    }) =>
      request<{
        status: string;
        package_name: string;
        package_path: string;
        manifest: Array<{ name: string; size_kb: number }>;
        total_files: number;
      }>('/api/export/runtime', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  report: {
    export: (data: { job_id?: string; format: 'html' | 'json'; include_images?: boolean; output_path?: string }) =>
      request<{ status: string; file_path: string; format: string; content?: string }>('/api/report/export', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  errors: {
    list: () => request<{ errors: ErrorCatalogItem[] }>('/api/errors'),
    get: (code: string) => request<ErrorCatalogItem>(`/api/errors/${code}`),
  },
};
