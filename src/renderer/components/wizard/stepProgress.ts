/** Which of the first five steps are done, from what the stores already hold. Step 6 is the ongoing work itself. */
export interface StepProgressInput {
  /** A dataset folder is loaded for the current project and task. */
  hasData: boolean;
  /** Images in the source (or listed when the statistics have not arrived yet). */
  images: number;
  /** Images without a label in the active label set. */
  unlabeled: number;
  /** A training run for the current data completed. */
  trainingDone: boolean;
  /** An evaluation with per-image results is loaded. */
  evaluated: boolean;
  /** The flow on screen is a saved, active version without unsaved edits. */
  flowSaved: boolean;
}

export function completedSteps(input: StepProgressInput): number[] {
  const done: number[] = [];
  const hasImages = input.hasData && input.images > 0;
  if (hasImages) done.push(1);
  if (hasImages && input.unlabeled === 0) done.push(2);
  // An evaluation needs a trained model, so a loaded evaluation also means step 3 has a result; after a reload the
  // training status is not restored while the latest evaluation is.
  if (input.trainingDone || input.evaluated) done.push(3);
  if (input.evaluated) done.push(4);
  if (input.flowSaved) done.push(5);
  return done;
}

type EvidenceRow = Record<string, unknown>;
/** Steps the saved project evidence proves, for the step bar after a reload: a model trained on the current data
 *  (3), an evaluation of that exact checkpoint (4), and a saved flow whose whole-flow evaluation is current (5).
 *  Read from the impact report the footer already fetches; it never adds a request. */
export function stepsFromEvidence(impact: {models?: EvidenceRow[]; model_evaluations?: EvidenceRow[]; flows?: EvidenceRow[]} | null | undefined, task: string): number[] {
  if (!impact) return [];
  const models = (impact.models || []).filter(row => row.task === task && row.scope_matches === true && row.checkpoint_state === 'current' && row.data_state === 'current');
  const done: number[] = [];
  if (models.length) done.push(3);
  if ((impact.model_evaluations || []).some(row => row.state === 'current' && models.some(model => model.job_id === row.job_id))) done.push(4);
  if ((impact.flows || []).some(row => row.state === 'current' && row.scope_matches === true)) done.push(5);
  return done;
}

/** The step bar: what the stores hold plus what the impact report proves. Once a report exists for this scope, step 5
 *  is the report's (a saved flow whose whole-flow evaluation is stale or missing is not done), so a saved flow alone
 *  never keeps the checkmark after its models' data changed. Without a report the local signal stands. */
export function mergeStepProgress(local: number[], evidence: number[] | null): number[] {
  const kept = evidence ? local.filter(step => step !== 5) : local;
  return [...new Set([...kept, ...(evidence || [])])].sort((a, b) => a - b);
}
