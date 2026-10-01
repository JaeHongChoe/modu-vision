export type FlowVerdict = 'OK' | 'NG' | 'REVIEW';
export type TruthVerdict = 'OK' | 'NG' | 'UNKNOWN';
export type TruthScope = {
  project_id: string; source_dataset_path: string; labelset_id: string; task: string; classes: string[];
  class_semantics: {version: number; roles: Record<string,'normal'|'defect'|'unknown'>; basis: Record<string,string>};
  participating_tasks?: string[];
};
export type ImageTruth = {
  image_path: string; relative_path: string; image_uuid: string; image_revision: number; truth_revision: number;
  scope: TruthScope; verdict: TruthVerdict; defect_classes: string[]; reviewer: string | null;
  invalidated: boolean; unknown_reason: string | null; truth_sha256: string;
  participating_tasks?: string[];
};
export type SavedEvaluationFlow = {version_id: string; name: string; saved_at: string; is_active: boolean};
export type FlowEvaluationCohort = {
  cohort_id: string; name: string; created_at: string; scope: TruthScope; count: number; split: 'test';
  record_sha256: string; input_sha256: string; truth_sha256: string; split_sha256: string;
  participating_tasks?: string[];
};
export type FlowNodeEvidence = {
  node_id: string; name: string; status: string; branch_verdict?: FlowVerdict;
  input_count?: number; output_count?: number; selected_edge_ids: string[]; skip_reason?: string;
  artifacts: Array<Record<string,unknown>>;
};
export type FlowImageEvidence = {
  relative_path: string; image_path: string; input_sha256: string; truth_sha256: string;
  truth: TruthVerdict; truth_reason: string | null; decision: FlowVerdict; execution_status: string;
  rejection_reason: string; node_evidence: FlowNodeEvidence[]; roi_evidence: Array<Record<string,unknown>>;
  error?: string | null;
};
export type FlowEvaluation = {
  evaluation_id: string; created_at: string; status: string; version_id: string; cohort_id: string; scope: TruthScope;
  graph_sha256: string; cohort_sha256: string; truth_sha256: string; input_sha256: string; model_sha256: string;
  record_sha256: string; device: string;
  coverage: {total: number; known: number; unknown: number; invalidated: number; known_fraction: number};
  confusion: Record<'OK'|'NG',Record<FlowVerdict,number>>;
  metrics: {escape_rate: number | null; overkill_rate: number | null; review_rate: number;
    escape_unavailable_reason: string | null; overkill_unavailable_reason: string | null; normal_count: number; defect_count: number};
  validity: {valid: boolean; reasons: string[]};
  records: FlowImageEvidence[]; escapes: FlowImageEvidence[]; overkills: FlowImageEvidence[];
  unknown_truth: FlowImageEvidence[]; errors: FlowImageEvidence[];
};
export type FlowEvaluationHistory = Omit<FlowEvaluation,'records'|'escapes'|'overkills'|'unknown_truth'|'errors'>;
