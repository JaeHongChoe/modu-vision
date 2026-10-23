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

/** Where the split counts come from, as the import reports it (backend `split_state`). */
export type SplitState = 'saved' | 'source_folders' | 'automatic' | 'stale';

/** The calibrator heading and its one-line explanation. "Saved" is said only when the backend says a split is saved:
 *  an automatic split (the loader's per-run split, which step 4 refuses to evaluate) and a saved split that no longer
 *  matches the folder are called out instead of being shown as saved. */
export function splitStatus(input: { mode: 'applied' | 'preview'; splitState: SplitState | null; locked: boolean }):
  { label: string; description: string; tone: 'ok' | 'warn'; foldable: boolean } {
  if (input.splitState === 'stale') {
    return { label: '다시 나눠야 함', tone: 'warn', foldable: false,
      description: "폴더의 이미지가 저장한 분할과 달라졌습니다(추가·삭제·이름 변경). 비율을 고르고 '이 비율로 나누기'로 다시 저장하세요." };
  }
  if (input.mode === 'preview') {
    return { label: '예상', tone: 'ok', foldable: false,
      description: "아래는 바뀔 분할의 예상입니다. '이 비율로 나누기'를 눌러야 저장됩니다." };
  }
  if (input.locked || input.splitState === 'source_folders') {
    return { label: '원본 폴더 구성', tone: 'ok', foldable: true, description: '원본 폴더의 train·val·test 구성을 그대로 씁니다.' };
  }
  if (input.splitState === 'automatic') {
    return { label: '자동 · 저장 안 됨', tone: 'warn', foldable: false,
      description: "학습할 때마다 새로 나뉘어 4단계에서 평가할 수 없습니다. 비율을 고르고 '이 비율로 나누기'를 눌러 저장하세요." };
  }
  return { label: '저장됨', tone: 'ok', foldable: true, description: '지금 저장된 분할입니다. 바꾸려면 아래에서 비율을 고르고 적용을 누르세요.' };
}
