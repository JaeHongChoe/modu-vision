/** The assistant's run log in the open project (`<project>/assistant/runs.jsonl`, backend/api/routes_assistant.py).
 * Entries carry names, counts and hashes only; the backend refuses anything that looks like a path, address or key. */
import { request } from './api';
import type {AssistantProviderId} from '../../types/electron';

export type RunTier = 'read' | 'navigate' | 'write' | 'forbidden' | 'conversation';
export type RunResult = 'ok' | 'refused' | 'error' | 'cancelled';
export interface RunEntry {
  run_id: string; tool: string; tier: RunTier; summary: string; args_sha256: string; confirmed: boolean; result: RunResult;
  error_code?: string; provider?: AssistantProviderId; link?: string;
}
export interface RunRecord extends RunEntry { schema_version: 1; at: string; actor: string }
export interface RunListing { runs: RunRecord[]; total: number; archives: number }

export const assistantLog = {
  append: (entry: RunEntry) => request<RunRecord>('/api/assistant/runs', { method: 'POST', body: JSON.stringify(entry) }),
  list: (limit = 200) => request<RunListing>(`/api/assistant/runs?limit=${limit}`),
  /** "기록 비우기": the backend moves the log into assistant/archive/; nothing is deleted. */
  archive: () => request<{ archived: string | null; count: number }>('/api/assistant/runs/archive', { method: 'POST' }),
};
