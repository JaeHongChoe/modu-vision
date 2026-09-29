type TrainingJobState = {
  jobId: string | null;
  status: string;
  isCurrentData: boolean;
};

type EvaluationJobState = {
  jobId: string | null;
  allowLatestRecovery: boolean;
};

/** Resolve a model only when the current data can legitimately use it. */
export function selectInferenceJobId(
  training: TrainingJobState,
  evaluation: EvaluationJobState,
): string | null {
  if (training.isCurrentData) {
    return training.status === 'completed' ? training.jobId : null;
  }
  if (training.jobId) return null;
  return evaluation.allowLatestRecovery ? evaluation.jobId : null;
}
