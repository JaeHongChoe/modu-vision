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
  if (input.trainingDone) done.push(3);
  if (input.evaluated) done.push(4);
  if (input.flowSaved) done.push(5);
  return done;
}
