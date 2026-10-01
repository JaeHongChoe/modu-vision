import { request } from './api';

/** Existing approval revision that still verifies for one exact flow checkpoint. */
export interface ReleaseCandidate {
  revision_id: string;
  action: string;
  reviewer: string;
  reason: string;
  created_at: string;
  comparison_id: string;
  checkpoint_sha256: string;
  is_active: boolean;
}

export interface ReleaseModel {
  job_id: string;
  task: string;
  node_ids: string[];
  checkpoint_sha256: string;
  candidates: ReleaseCandidate[];
  selected_revision_id: string | null;
  reason: 'select_verified_revision' | 'no_verified_approval' | null;
}

export interface FlowApprovalPrerequisites {
  status: 'ready' | 'selection_required' | 'blocked';
  /** Preselected only where the task's active revision verifies; other models need an explicit choice. */
  approval_revision_ids: Record<string, string>;
  models: ReleaseModel[];
  approval_created: false;
}

export type FlowParityStatus = 'not_run' | 'passed' | 'mismatch' | 'failed';

export interface FlowParityImage {
  index: number;
  image_path: string;
  image_id?: string | null;
  image_sha256: string;
  status: 'passed' | 'mismatch' | 'failed' | 'not_run';
  mismatched_fields?: string[];
  error?: string;
}

export interface FlowParityReport {
  status: FlowParityStatus;
  contract?: 'flow_parity_v1';
  scope?: 'cohort' | 'single_image';
  limitation?: string | null;
  device?: string;
  image_count?: number;
  completed_count?: number;
  cohort_sha256?: string;
  manifest_sha256?: string;
  compared_fields?: string[];
  mismatched_fields?: string[];
  verdict_counts?: Record<string, number>;
  images?: FlowParityImage[];
  error?: string | null;
  packaged_runtime?: { kind: 'isolated_python_runner' | 'frozen_package_dispatcher'; independent_process: boolean };
  image_path?: string;
  final_verdict?: string;
  roi_count?: number;
}

export interface FlowExportBody {
  source_dataset_path: string;
  recipe_task: string;
  package_name: string;
  version_id?: string;
  verification_image_path?: string;
  verification_image_id?: string;
  parity_images?: Array<{ path: string; image_id?: string }>;
  parity_device?: string;
  approval_revision_ids?: Record<string, string>;
  deployment_profile?: 'standard' | 'edge_cpu' | 'edge_cuda';
  target_os?: 'linux' | 'windows' | 'macos';
  target_arch?: 'x86_64' | 'arm64';
  runtime_config?: { deadline_ms: number | null; cpu_threads: number; device: string };
}

export interface FlowExportResult {
  package_path: string;
  package_name: string;
  pipeline_id: string;
  model_job_ids: string[];
  total_files: number;
  deployment?: { profile: 'edge_cpu' | 'edge_cuda'; device: string; target: { os: 'linux' | 'windows' | 'macos'; architecture: 'x86_64' | 'arm64' } };
  parity: FlowParityReport;
}

export const flowPackageExport = {
  prerequisites: (params: { source_dataset_path: string; recipe_task: string; version_id?: string }) => {
    const query = new URLSearchParams({ source_dataset_path: params.source_dataset_path, recipe_task: params.recipe_task });
    if (params.version_id) query.set('version_id', params.version_id);
    return request<FlowApprovalPrerequisites>(`/api/export/flow/approval-prerequisites?${query}`);
  },
  exportFlow: (body: FlowExportBody) => request<FlowExportResult>('/api/export/flow', { method: 'POST', body: JSON.stringify(body) }),
};
