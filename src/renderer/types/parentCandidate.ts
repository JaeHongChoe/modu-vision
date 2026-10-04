export type ParentCandidate = {
  job_id: string; checkpoint_sha256: string; semantics?: string;
  summary?: {model_recorded_at: string | null; completed_at: string | null; training_metrics: Record<string, number>};
};
