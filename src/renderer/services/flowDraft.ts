import type { FlowchartPipeline } from '../types';
import { request } from './api';

export interface FlowDraftContext {
  project_id: string;
  source_dataset_path: string | null;
  labelset_id: string;
}
export interface FlowDraft {
  context: FlowDraftContext;
  pipeline: FlowchartPipeline;
  draft_sha256: string;
  active_version_id: string | null;
  /** The active version the editor started from ("none": there was none); missing in drafts of older editors. */
  base_version_id?: string | null;
}
export const flowDraft = {
  get: () => request<FlowDraft>('/api/flowchart/draft'),
  save: (pipeline: FlowchartPipeline, context: FlowDraftContext, baseVersionId?: string) => request<FlowDraft>('/api/flowchart/draft', {
    method: 'PUT', body: JSON.stringify(baseVersionId === undefined ? { pipeline, context } : { pipeline, context, base_version_id: baseVersionId }),
  }),
};
