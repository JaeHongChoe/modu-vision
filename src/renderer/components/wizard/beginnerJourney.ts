import type { ReadinessAction, WorkflowStep } from './workflowReadiness';

export type BeginnerGoal = {
  id: 'photos' | 'teach' | 'check' | 'use';
  nameKo: string;
  nameEn: string;
  steps: readonly WorkflowStep[];
};

/** Presentation groups only. The six workflow steps and their save/authorization guards remain authoritative. */
export const BEGINNER_GOALS: readonly BeginnerGoal[] = [
  { id: 'photos', nameKo: '사진 준비', nameEn: 'Prepare photos', steps: [1] },
  { id: 'teach', nameKo: 'AI에게 가르치기', nameEn: 'Teach the AI', steps: [2, 3] },
  { id: 'check', nameKo: '처음 보는 사진으로 확인', nameEn: 'Check unseen photos', steps: [4] },
  { id: 'use', nameKo: '검사에 사용', nameEn: 'Use for inspection', steps: [5, 6] },
];

export function beginnerGoalForStep(step: WorkflowStep): BeginnerGoal {
  return BEGINNER_GOALS.find(goal => goal.steps.includes(step))!;
}

export function beginnerGoalComplete(goal: BeginnerGoal, completedSteps: readonly number[]): boolean {
  return goal.steps.every(step => completedSteps.includes(step));
}

export function beginnerGoalEntry(goal: BeginnerGoal, activeStep: WorkflowStep, completedSteps: readonly number[]): WorkflowStep {
  if (goal.steps.includes(activeStep)) return activeStep;
  return goal.steps.find(step => !completedSteps.includes(step)) ?? goal.steps[0];
}

const STEP_NAVIGATION: Record<WorkflowStep, readonly [string, string]> = {
  1: ['사진 준비 화면 열기', 'Open photo preparation'],
  2: ['라벨 확인 화면 열기', 'Open label review'],
  3: ['학습 화면 열기', 'Open training'],
  4: ['평가 화면 열기', 'Open evaluation'],
  5: ['검사 흐름 화면 열기', 'Open inspection workflow'],
  6: ['검사 결과·사용 준비 열기', 'Open inspection and use setup'],
};
const TARGET_NAVIGATION: Record<string, readonly [string, string]> = {
  'workflow-project': ['프로젝트 만들기·열기', 'Create or open a project'],
  'workflow-data': STEP_NAVIGATION[1],
  'workflow-label-review': ['라벨 검토 화면 열기', 'Open label review'],
  'draft-review-queue': ['초안 검토 화면 열기', 'Open draft review'],
  'draft-create': ['초안 만들기 설정 열기', 'Open draft settings'],
  'labeling-canvas': ['라벨 달기 화면 열기', 'Open labeling'],
  'image-class-picker': ['사진 종류 선택 열기', 'Open photo class selection'],
  'workflow-training': STEP_NAVIGATION[3],
  'evaluation-refresh': ['평가 새로고침 버튼 확인', 'Find evaluation refresh'],
  'workflow-missed-defects': ['놓친 불량 사진 확인', 'Check missed defects'],
  'flow-area-edit': ['검사 흐름 편집 열기', 'Open workflow editing'],
  'flow-area-evaluate': ['검사 흐름 평가 열기', 'Open workflow evaluation'],
  'workflow-package': STEP_NAVIGATION[6],
};

/** The footer opens/focuses controls; it never starts training, evaluates, saves a flow or inspects images itself.
 * Keep readiness labels untouched because the existing runner and assistant also consume that contract. */
export function navigationActionLabel(action: ReadinessAction['nextAction'], ko: boolean): string {
  const pick = (labels: readonly [string, string]) => labels[ko ? 0 : 1];
  if (action.kind === 'refresh') return pick(['준비도 다시 확인', 'Check readiness again']);
  if (action.kind === 'details') {
    if (action.label.includes('학습 오류')) return pick(['학습 오류 확인', 'Check training error']);
    if (action.label.includes('바뀐 내용')) return pick(['바뀐 내용과 복구 방법 확인', 'Check changes and recovery options']);
    return pick(['필요한 작업 자세히 확인', 'Check required work']);
  }
  if (action.step === 1 && action.label.includes('분할')) return pick(['사진 나누기 설정 열기', 'Open image split settings']);
  if (action.target === 'workflow-training' && action.label.includes('진행')) return pick(['학습 진행 확인', 'Check training progress']);
  return pick((action.target && TARGET_NAVIGATION[action.target]) || STEP_NAVIGATION[action.step]);
}
