import { request } from './api';

/** E07: what a saved flow version needs on a deployment target, node by node. */
export type PreflightTarget =
  | { kind: 'this_computer'; device: string }
  | { kind:'selected_compute'; compute_profile_id:string; device:'cpu'|'cuda:0'; compute_profile_name?:string; execution_profile_sha256?:string; compute_gpu_selector?:string|null }
  | { kind: 'edge'; profile: 'edge_cpu' | 'edge_cuda'; os: string; architecture: string; device: string };

export interface PreflightRequirement {
  node_id: string | null;
  kind: 'model' | 'calibration' | 'runtime' | 'device';
  artifact_ref: string;
  version_range: string | null;
  platform: string | null;
  license_ref: string | null;
  state: 'ready' | 'missing' | 'mismatch' | 'unavailable' | 'unverified';
  evidence_ref: string | null;
  remedy: string | null;
}

export interface PreflightReport {
  runtime?:{device:string;process_id:number;torch_version:string;gpu_uuid?:string|null};
  report_sha256?:string;
  report_id: string;
  checked_at: string;
  status: 'ready' | 'blocked' | 'unverified';
  recipe_release: { kind: string; version_id?: string; recipe_task?: string; pipeline_sha256?: string };
  target_identity: PreflightTarget;
  environment_hash: string | null;
  requirements: PreflightRequirement[];
  counts: Record<PreflightRequirement['state'], number>;
  blocked_nodes: Record<string, string[]>;
  decision_blocked: boolean;
  stale: boolean;
  stale_reasons: string[];
}

export type PreflightSummary = Pick<PreflightReport, 'report_id' | 'checked_at' | 'status' | 'recipe_release' | 'target_identity' | 'counts'>;

export const flowPreflight = {
  run: (body: { source_dataset_path: string; recipe_task: string; version_id: string; target: PreflightTarget }) =>
    request<PreflightReport>('/api/export/flow/preflight', { method: 'POST', body: JSON.stringify(body) }),
  list: (versionId: string) => request<{ reports: PreflightSummary[] }>(`/api/export/flow/preflights?version_id=${encodeURIComponent(versionId)}`),
  read: (reportId: string, sourceDatasetPath: string, target: PreflightTarget) =>
    request<PreflightReport>(`/api/export/flow/preflights/${encodeURIComponent(reportId)}?${new URLSearchParams({
      source_dataset_path: sourceDatasetPath, target: JSON.stringify(target) }).toString()}`),
};
