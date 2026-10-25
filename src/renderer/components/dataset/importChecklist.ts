/** "학습 전 점검": what the step-1 import result already tells a first-time user before training, as plain checks.
 *  Pure, from the import response the dataset store holds (no extra request). Blocking checks are the ones that make
 *  training fail or step 4 refuse; warnings say what will be unreliable or what field approval will need later. */

export type ImportCheckLevel = 'ok' | 'warn' | 'block';
export interface ImportCheck { id: string; level: ImportCheckLevel; text: string; action?: 'apply_split' | 'reimport' | 'label' }

export interface ImportCheckInput {
  task: string;
  totalImages: number;
  classes: Record<string, number>;
  split: { train: number; val: number; test: number };
  splitState: 'saved' | 'source_folders' | 'automatic' | 'stale' | null;
  classSplitCounts: Record<string, { train: number; val: number; test: number }> | null;
  corrupted: number;
  excludedUndecodable: { excluded: string[]; recorded: boolean } | null;
  labelsMissing: boolean;
  expectedLayout: string;
  splitLocked: boolean;
}

/** Field approval compares two models on at least this many confirmed OK and NG test images each. */
export const APPROVAL_TEST_IMAGES_PER_ROLE = 8;
/** Below this many test images, one image moves accuracy by several points. */
export const RELIABLE_TEST_IMAGES = 30;
/** Fewest images a classification class with images may have (F-03; backend MIN_CLASSIFICATION_IMAGES_PER_CLASS and the
 *  error catalog's ERR_002). A one-image class lands in only one of train, val and test, so it is never learned or never
 *  checked during training. Two images do not promise a validation image; the `class_splits` warning covers that. */
export const MIN_IMAGES_PER_CLASS = 2;

const NORMAL = new Set(['ok', 'good', 'normal', 'pass', '정상', '양품', '합격']);
const name = (value: string) => value.trim().toLowerCase();

type SplitCounts = { train: number; val: number; test: number };

/** Per-class gaps for classification (F-03), as "NG 1장" or "NG (학습 4 · 검증 0 · 시험 1)". `short`: classes with
 *  images but fewer than MIN_IMAGES_PER_CLASS. From the saved split, when known: `untrained` classes have no train
 *  image (training start refuses), `thin` classes have no val or no test image while that split has some (a warning:
 *  training never checks the class, or step 4 cannot measure it). The stratified split of step 1 puts every class in
 *  all three splits once every class has 8 images at 80 / 10 / 10, 6 at 70 / 20 / 10 or 4 at 60 / 20 / 20 (measured with
 *  split_dataset over 200 seeds; its per-class rounding gives the same floors). Below them a class has at most one image
 *  left after train, so it misses val or test (or both), and the split's second pass then shuffles every class
 *  without strata. */
export function classGaps(classes: Record<string, number>, classSplitCounts: Record<string, SplitCounts> | null,
  split: SplitCounts): { short: string[]; untrained: string[]; thin: string[] } {
  const short = Object.entries(classes).filter(([, count]) => count > 0 && count < MIN_IMAGES_PER_CLASS).map(([cls, count]) => `${cls} ${count}장`);
  const rows = Object.entries(classSplitCounts || {}).filter(([cls]) => (classes[cls] ?? 0) >= MIN_IMAGES_PER_CLASS);
  const named = ([cls, counts]: [string, SplitCounts]) => `${cls} (학습 ${counts.train} · 검증 ${counts.val} · 시험 ${counts.test})`;
  return { short, untrained: rows.filter(([, counts]) => split.train > 0 && counts.train <= 0).map(([cls]) => cls),
    thin: rows.filter(([, counts]) => counts.train > 0 && ((split.val > 0 && counts.val <= 0) || (split.test > 0 && counts.test <= 0))).map(named) };
}

// The two class-rule sentences (F-03). The step-1 checks, the step-3 start notice and the footer show them word for word,
// and training start refuses with the same sentences (backend routes_training.py). "한 장뿐인" follows from the minimum 2.
const classMinimumText = (short: string[]) => `분류 학습에는 클래스마다 이미지가 ${MIN_IMAGES_PER_CLASS}장 이상 필요합니다. 부족한 클래스: ${short.join(', ')}. 한 장뿐인 클래스는 학습·검증·시험 중 한 곳에만 들어가 학습되지 않거나 학습 중 확인되지 않습니다. 이 클래스에 이미지를 더 넣고 1단계에서 다시 불러오세요.`;
const classTrainText = (untrained: string[]) => `학습 분할에 이미지가 없는 클래스가 있습니다: ${untrained.join(', ')}. 모델이 이 클래스를 배우지 못합니다. 1단계에서 '이 비율로 나누기'로 다시 나눈 뒤 학습하세요.`;

/** A saved split that leaves out labeled images (newly labeled, or adopted from a draft): the import then counts none
 *  of its partitions until the split is applied again, so say that instead of "no training images". */
export const SPLIT_MISSES_LABELS_TEXT = "저장된 분할에 없는 라벨 이미지가 있습니다(새로 라벨했거나 초안에서 채택한 이미지). 1단계에서 '이 비율로 나누기'으로 분할을 다시 적용하세요.";
export function splitMissesLabels(input: { totalImages: number; split: { train: number; val: number; test?: number };
  splitState: ImportCheckInput['splitState'] | undefined }): boolean {
  return input.splitState === 'saved' && input.totalImages > 0 && input.split.train === 0 && input.split.val === 0 && !input.split.test;
}

/** Why classification training cannot start for its classes, or null; the step-3 start notice and footer use it. */
export function classStartBlocker(classes: Record<string, number>, classSplitCounts: Record<string, SplitCounts> | null,
  split: SplitCounts): string | null {
  const populated = Object.values(classes).filter(count => count > 0).length;
  if (populated < 2) return null;  // the two-class rule speaks first
  const gaps = classGaps(classes, classSplitCounts, split);
  if (gaps.short.length) return classMinimumText(gaps.short);
  if (gaps.untrained.length) return classTrainText(gaps.untrained);
  return null;
}

/** Test images of normal-looking and defect classes, by class name (the approval rule counts confirmed OK and NG). */
export function roleTestCounts(classSplitCounts: Record<string, { test: number }>): { normal: number; defect: number } {
  const roles = Object.entries(classSplitCounts);
  return { normal: roles.filter(([cls]) => NORMAL.has(name(cls))).reduce((sum, [, counts]) => sum + counts.test, 0),
    defect: roles.filter(([cls]) => !NORMAL.has(name(cls))).reduce((sum, [, counts]) => sum + counts.test, 0) };
}

export function importChecks(input: ImportCheckInput): ImportCheck[] {
  const checks: ImportCheck[] = [];
  if (input.totalImages <= 0) {
    checks.push({ id: 'no_images', level: 'block', action: 'reimport',
      text: `가져온 이미지가 없습니다.${input.expectedLayout ? ` 이렇게 준비하세요: ${input.expectedLayout}` : ''}` });
    return checks;
  }
  if (input.excludedUndecodable && input.excludedUndecodable.excluded.length) {
    const names = input.excludedUndecodable.excluded.slice(0, 5).join(', ');
    const more = input.excludedUndecodable.excluded.length > 5 ? ` 외 ${input.excludedUndecodable.excluded.length - 5}장` : '';
    checks.push({ id: 'undecodable', level: 'warn',
      text: `읽을 수 없는 이미지 ${input.excludedUndecodable.excluded.length}장을 학습·분할에서 뺐습니다 (원본 파일은 그대로): ${names}${more}` });
  } else if (input.corrupted > 0) {
    checks.push({ id: 'undecodable', level: 'block', action: 'reimport',
      text: `읽을 수 없는 이미지가 ${input.corrupted}장 있습니다. 프로젝트를 열고 다시 불러오면 학습에서 자동으로 빠집니다.` });
  }
  if (input.labelsMissing) {
    checks.push({ id: 'labels_missing', level: 'block', action: 'label',
      text: `라벨이 아직 없습니다. 2단계에서 영역을 그리거나, 이렇게 준비해 다시 불러오세요: ${input.expectedLayout}` });
  }
  const present = Object.entries(input.classes).filter(([, count]) => count > 0);
  const gaps = classGaps(input.classes, input.classSplitCounts, input.split);
  if (input.task === 'classification') {
    if (present.length < 2) {
      const empty = Object.entries(input.classes).filter(([, count]) => count <= 0).map(([cls]) => cls);
      checks.push({ id: 'classes', level: 'block', action: 'reimport',
        text: `분류에는 이미지가 있는 클래스가 2개 이상 필요합니다. 지금은 ${present.map(([cls]) => cls).join(', ') || '없음'}${empty.length ? ` (빈 클래스: ${empty.join(', ')})` : ''}.` });
    } else {
      checks.push({ id: 'classes', level: 'ok', text: `클래스 ${present.length}개: ${present.map(([cls, count]) => `${cls} ${count}장`).join(' · ')}` });
      if (gaps.short.length) {
        checks.push({ id: 'class_minimum', level: 'block', action: 'reimport', text: classMinimumText(gaps.short) });
      }
    }
  }
  if (!input.splitLocked && input.task === 'classification') {
    if (input.splitState === 'automatic') {
      checks.push({ id: 'split', level: 'block', action: 'apply_split',
        text: "분할이 저장되지 않았습니다. 저장하지 않고 학습하면 4단계에서 평가할 수 없습니다. '이 비율로 나누기'를 누르세요." });
    } else if (input.splitState === 'stale') {
      checks.push({ id: 'split', level: 'block', action: 'apply_split',
        text: "폴더의 이미지가 저장한 분할과 달라졌습니다. '이 비율로 나누기'로 다시 저장하세요." });
    }
  }
  const splitKnown = input.splitState === 'saved' || input.splitState === 'source_folders' || input.splitLocked;
  if (!input.splitLocked && splitMissesLabels(input)) {
    checks.push({ id: 'split', level: 'block', action: 'apply_split', text: SPLIT_MISSES_LABELS_TEXT });
  } else if (splitKnown && input.split.test <= 0) {
    checks.push({ id: 'test', level: 'block', action: 'apply_split',
      text: '시험 이미지가 0장이라 4단계에서 평가할 수 없습니다. 시험 비율이 있는 분할(예: 70 / 20 / 10)을 저장하세요.' });
  } else if (splitKnown && input.split.test < RELIABLE_TEST_IMAGES) {
    checks.push({ id: 'test', level: 'warn',
      text: `시험 이미지가 ${input.split.test}장뿐입니다. 한 장마다 정확도가 ${(100 / input.split.test).toFixed(0)}%p씩 움직여 결과가 크게 흔들립니다 (${RELIABLE_TEST_IMAGES}장 이상 권장).` });
  }
  if (splitKnown && input.task === 'classification' && input.classSplitCounts && present.length >= 2 && !gaps.short.length) {
    if (gaps.untrained.length) {
      checks.push({ id: 'class_train', level: 'block', action: 'apply_split', text: classTrainText(gaps.untrained) });
    }
    if (gaps.thin.length) {
      checks.push({ id: 'class_splits', level: 'warn', action: 'apply_split',
        text: `${gaps.thin.join(', ')}: 검증 이미지가 없는 클래스는 학습 중 확인되지 않고, 시험 이미지가 없는 클래스는 4단계에서 잴 수 없습니다. '이 비율로 나누기'는 모든 클래스가 80 / 10 / 10에서 8장, 70 / 20 / 10에서 6장, 60 / 20 / 20에서 4장 이상이면 클래스마다 학습·검증·시험에 한 장 이상씩 넣습니다. 이 클래스 이미지를 늘리거나 검증·시험 비율이 큰 분할로 다시 나누세요.` });
    }
  }
  if (splitKnown && input.task === 'classification' && input.classSplitCounts) {
    const { normal, defect } = roleTestCounts(input.classSplitCounts);
    if (normal < APPROVAL_TEST_IMAGES_PER_ROLE || defect < APPROVAL_TEST_IMAGES_PER_ROLE) {
      checks.push({ id: 'approval', level: 'warn',
        text: `현장 적용 승인에는 정상·불량 시험 이미지가 각각 ${APPROVAL_TEST_IMAGES_PER_ROLE}장 이상 필요합니다 (지금 정상 ${normal} · 불량 ${defect}).` });
    }
  }
  if (present.length >= 2) {
    const counts = present.map(([, count]) => count);
    if (Math.max(...counts) / Math.min(...counts) > 20) {
      checks.push({ id: 'imbalance', level: 'warn', text: '가장 많은 클래스가 가장 적은 클래스보다 20배 넘게 많습니다. 적은 클래스의 이미지를 더 모으세요.' });
    }
  }
  return checks;
}
