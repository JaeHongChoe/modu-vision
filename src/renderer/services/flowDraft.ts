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
}
export const flowDraft = {
  get: () => request<FlowDraft>('/api/flowchart/draft'),
  save: (pipeline: FlowchartPipeline, context: FlowDraftContext) => request<FlowDraft>('/api/flowchart/draft', {
    method: 'PUT', body: JSON.stringify({ pipeline, context }),
  }),
};
