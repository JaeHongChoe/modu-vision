/** Pure rules behind the data step's imbalance warning and its 3-way split calibrator. */

export type SplitCounts = { train: number; val: number; test: number };

/** True when the largest class outnumbers the smallest present class by more than `ratio` to 1.
 *  Classes with no images are not "small": they are missing, which the class list already shows. */
export function severeClassImbalance(classes: Record<string, number>, ratio = 20): boolean {
  const counts = Object.values(classes).filter(count => count > 0);
  if (counts.length < 2) return false;
  return Math.max(...counts) / Math.min(...counts) > ratio;
}

function percentages(counts: SplitCounts): SplitCounts {
  const total = counts.train + counts.val + counts.test;
  if (total <= 0) return { train: 0, val: 0, test: 0 };
  const train = Math.round((counts.train / total) * 100);
  const val = Math.round((counts.val / total) * 100);
  return { train, val, test: 100 - train - val };
}

/** What the calibrator shows: the saved split until the user changes a preset or the slider (or when the task's
 *  split cannot be changed), otherwise a preview of the chosen ratios over the current image count. */
export function calibratorView(input: { touched: boolean; locked: boolean; applied: SplitCounts; totalImages: number; ratios: SplitCounts }):
  { mode: 'applied' | 'preview'; counts: SplitCounts; ratios: SplitCounts } {
  const appliedTotal = input.applied.train + input.applied.val + input.applied.test;
  if (appliedTotal > 0 && (input.locked || !input.touched)) {
    return { mode: 'applied', counts: input.applied, ratios: percentages(input.applied) };
  }
  const train = Math.round(input.totalImages * (input.ratios.train / 100));
  const val = Math.round(input.totalImages * (input.ratios.val / 100));
  return { mode: 'preview', counts: { train, val, test: Math.max(0, input.totalImages - train - val) }, ratios: input.ratios };
}
