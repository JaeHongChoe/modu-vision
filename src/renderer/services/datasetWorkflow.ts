import { request, type LabelSuggestion, type LabelSuggestionBatch } from './api';
import type { AnnotationItem } from '../types';
export type ReviewState = 'unworked' | 'needs_review' | 'approved';
export interface ImageReviewMetadata {
  image_uuid: string; file_path: string; relative_path: string; content_hash: string; content_version: number;
  width: number; height: number; revision: number; tags: string[]; product: string; lot: string; group: string;
  workflow_state: ReviewState; reviewer: string | null;
  review_history: { at: string; actor: string; state: ReviewState; revision: number }[];
  audit: { id: string; at: string; actor: string; action: string; revision: number; changes: Record<string, unknown> }[];
}
export interface DuplicateGroup { content_hash: string; images: string[]; splits: string[]; cross_split: boolean }
export interface GroupSplit { assignments: Record<string,string>; split: Record<string,number>; group_count: number; duplicates: DuplicateGroup[]; applied: boolean; apply_supported: boolean; apply_unavailable_reason?: string; backup_version_id?: string }
export interface ImportPreview { file_name: string; image_uuid: string; revision: number; existing_count: number; incoming_count: number; conflict: boolean }
export interface SemanticReadiness { ready: boolean; backend: string; model_dir: string | null; dependency_available: boolean; error: string | null; limits: string }
export type CandidateProposal = LabelSuggestion & { backend?: string; support_limits?: string; prompt?: string; reviewer?: string };
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
export const datasetWorkflow = {
  list: (filters: Record<string,string | number | undefined> = {}) => {
    const params = new URLSearchParams(); Object.entries(filters).forEach(([key,value]) => { if (value !== undefined && value !== '') params.set(key,String(value)); });
    return request<{ items: ImageReviewMetadata[]; total: number }>(`/api/dataset/metadata?${params}`);
  },
  image: (imagePath: string) => request<ImageReviewMetadata>(`/api/dataset/metadata/image?image_path=${encodeURIComponent(imagePath)}`),
  edit: (row: ImageReviewMetadata, actor: string, changes: Partial<Pick<ImageReviewMetadata,'tags'|'product'|'lot'|'group'|'workflow_state'>>) => request<ImageReviewMetadata>(`/api/dataset/metadata/${row.image_uuid}`,json('PATCH',{ expected_revision: row.revision, actor, changes })),
  bulkEdit: (rows: ImageReviewMetadata[], actor: string, changes: Record<string,unknown>) => request<{items:ImageReviewMetadata[];updated:number}>('/api/dataset/metadata/bulk',json('POST',{actor,items:rows.map(row=>({image_uuid:row.image_uuid,expected_revision:row.revision})),changes})),
  duplicates: () => request<{ duplicates: DuplicateGroup[] }>('/api/dataset/metadata/duplicates'),
  split: (groupBy: string[], ratios: number[], apply: boolean, actor: string) => request<GroupSplit>('/api/dataset/metadata/split',json('POST',{ group_by: groupBy, train_ratio: ratios[0], val_ratio: ratios[1], test_ratio: ratios[2], apply, actor })),
  import: (format: string, importDir: string, mode: 'preview'|'apply', policy: string, actor: string, revisions: Record<string,number> = {}) => request<{ preview: ImportPreview[]; applied: boolean; backup_version_id?: string }>('/api/dataset/formats/import',json('POST',{ format, import_dir: importDir, mode, conflict_policy: policy, actor, expected_revisions: revisions })),
  export: (format: string) => request<{ download_url: string; image_count: number; annotation_count: number }>('/api/dataset/formats/export',json('POST',{ format })),
  annotations: (imageId: string, imagePath: string) => request<{ annotations: AnnotationItem[]; image_id: string; image_width: number; image_height: number; mask_file?: string; metadata?: ImageReviewMetadata }>(`/api/annotations/${encodeURIComponent(imageId)}?file_path=${encodeURIComponent(imagePath)}`),
  saveAnnotations: (body: Record<string,unknown>) => request<{ status: string; mask_generated: boolean; metadata?: ImageReviewMetadata }>('/api/annotations/save',json('POST',body)),
  semanticSetup: () => request<SemanticReadiness>('/api/label-candidates/setup'),
  setSemanticModel: (modelDir: string) => request<SemanticReadiness>('/api/label-candidates/setup',json('PUT',{model_dir:modelDir})),
  generateCandidates: (body: Record<string,unknown>) => request<CandidateProposal>('/api/label-candidates/generate',json('POST',body)),
  generateModel: (jobId: string, imagePath: string, threshold: number, keywords: string[]) => request<CandidateProposal>('/api/label-suggestions/generate',json('POST',{job_id:jobId,image_path:imagePath,threshold,keywords})),
  startBatch: (body: Record<string,unknown>) => request<LabelSuggestionBatch>('/api/label-suggestions/batches',json('POST',body)),
  review: (id: string, decision: string, candidateIds: string[], actor: string) => request<CandidateProposal>(`/api/label-suggestions/${id}/review`,json('POST',{decision,candidate_ids:candidateIds,actor})),
};
export function workflowError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object') {
    const detail = (error as Record<string,unknown>).detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message);
  }
  return '요청을 처리하지 못했습니다.';
}
