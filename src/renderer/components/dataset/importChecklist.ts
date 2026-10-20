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

const NORMAL = new Set(['ok', 'good', 'normal', 'pass', '정상', '양품', '합격']);
const name = (value: string) => value.trim().toLowerCase();

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
  if (input.task === 'classification') {
    if (present.length < 2) {
      const empty = Object.entries(input.classes).filter(([, count]) => count <= 0).map(([cls]) => cls);
      checks.push({ id: 'classes', level: 'block', action: 'reimport',
        text: `분류에는 이미지가 있는 클래스가 2개 이상 필요합니다. 지금은 ${present.map(([cls]) => cls).join(', ') || '없음'}${empty.length ? ` (빈 클래스: ${empty.join(', ')})` : ''}.` });
    } else {
      checks.push({ id: 'classes', level: 'ok', text: `클래스 ${present.length}개: ${present.map(([cls, count]) => `${cls} ${count}장`).join(' · ')}` });
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
  if (splitKnown && input.split.test <= 0) {
    checks.push({ id: 'test', level: 'block', action: 'apply_split',
      text: '시험 이미지가 0장이라 4단계에서 평가할 수 없습니다. 시험 비율이 있는 분할(예: 70 / 20 / 10)을 저장하세요.' });
  } else if (splitKnown && input.split.test < RELIABLE_TEST_IMAGES) {
    checks.push({ id: 'test', level: 'warn',
      text: `시험 이미지가 ${input.split.test}장뿐입니다. 한 장마다 정확도가 ${(100 / input.split.test).toFixed(0)}%p씩 움직여 결과가 크게 흔들립니다 (${RELIABLE_TEST_IMAGES}장 이상 권장).` });
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
