import { request } from './api';

export interface RetentionPolicy {
  retention_days: number;
  trash_days: number;
  quota_bytes: number | null;
}
export interface RetentionSummary {
  policy: RetentionPolicy;
  active_bytes: number;
  trash_bytes: number;
  total_bytes: number;
  over_quota: boolean;
  permanent_deletion_supported: false;
  pins: Array<{ owner: string; relative_path: string; reason: string; created_at: number }>;
  trash: Array<{ trash_id: string; relative_path: string; state: string; size_bytes: number }>;
  backups: Array<{ backup_id: string; status: string; restore_verified: boolean }>;
  restores: Array<{ restore_id: string; status: string }>;
}
export interface TrashResult {
  dry_run: boolean;
  preview_sha256: string;
  eligible: Array<{ relative_path: string; size_bytes: number }>;
  trashed: Array<{ trash_id: string; relative_path: string; size_bytes: number }>;
}
export const artifactRetention = {
  summary: () => request<RetentionSummary>('/api/project/retention'),
  policy: (policy: RetentionPolicy) => request<RetentionPolicy>('/api/project/retention/policy', { method: 'PUT', body: JSON.stringify(policy) }),
  preview: (paths: string[]) => request<TrashResult>('/api/project/retention/trash', { method: 'POST', body: JSON.stringify({ paths, dry_run: true }) }),
  move: (paths: string[], preview_sha256: string) => request<TrashResult>('/api/project/retention/trash', { method: 'POST', body: JSON.stringify({ paths, dry_run: false, expected_preview_sha256: preview_sha256 }) }),
  restore: (trash_id: string) => request<{ state: string }>('/api/project/retention/restore-trash', { method: 'POST', body: JSON.stringify({ trash_id }) }),
};
