// S2-01: the first-run guide. It shows the order of a first project (how to work, checking this computer or a
// server, creating a project, bringing data) and offers a small example that runs end to end on this computer's
// CPU without network, GPU or accounts. The example's results are never a quality approval of a real process.

export type DemoStepId = 'dataset' | 'project' | 'import' | 'labels' | 'train' | 'evaluate_model' | 'flow' | 'evaluate' | 'inspect';
export type DemoStepStatus = 'pending' | 'running' | 'done' | 'failed';
export type DemoResult = {
  project_id: string; project_name: string; job_id: string; flow_version_id: string; run_id: string;
  counts: Record<string, number>; images: number;
  /** The saved flow's evaluation on the held-out test images (what the readiness check asks for before step 6). */
  evaluation?: { evaluation_id: string; known: number; escapes: number; overkills: number } | null;
};
export type DemoState = {
  state: 'idle' | 'running' | 'ready' | 'failed';
  steps: Partial<Record<DemoStepId, { status: DemoStepStatus; detail?: string | null }>>;
  error: string | null;
  result: DemoResult | null;
};
export type ExampleProjectRef = { id: string; name: string; project_dir: string };
export type OnboardingState = { version: 1; dismissed: boolean; demo: DemoState; example_projects?: ExampleProjectRef[]; team?: boolean };

/** The example walks the first-project path in the order the guide lists it (product direction 5.1 #2): folder →
 *  structure, task and split → labels → training with the default settings → evaluation → saved flow → inspection. */
export const DEMO_STEPS: Array<[DemoStepId, string]> = [
  ['dataset', '합성 예제 이미지 만들기'],
  ['project', '예제 프로젝트 만들기'],
  ['import', '1단계 · 폴더 열기 → 구조·작업 종류·분할 확인'],
  ['labels', '2단계 · 라벨 확인 (분류는 폴더 이름이 정답)'],
  ['train', '3단계 · 추천 설정으로 학습 (이 컴퓨터 CPU)'],
  ['evaluate_model', '4단계 · 시험 이미지로 모델 평가'],
  ['flow', '5단계 · 검사 흐름 저장'],
  ['evaluate', '5단계 · 저장한 흐름 평가'],
  ['inspect', '6단계 · 시험 이미지 검사·기록'],
];

/** The guide opens by itself on start until it is dismissed, while no project has data yet, never on a team server. */
export function shouldOpenFirstRun(onboarding: OnboardingState | null, hasProjectData: boolean): boolean {
  return Boolean(onboarding && !onboarding.dismissed && !onboarding.team && !hasProjectData && onboarding.demo.state !== 'running');
}

/** Each demo step with its label and state, in order. */
export function demoStepRows(demo: DemoState): Array<{ id: DemoStepId; label: string; status: DemoStepStatus; detail: string | null }> {
  return DEMO_STEPS.map(([id, label]) => ({ id, label, status: demo.steps[id]?.status ?? 'pending', detail: demo.steps[id]?.detail ?? null }));
}

export const EXAMPLE_NOTICE = '합성 예제 데이터로 만든 결과입니다. 실제 공정의 품질 승인이나 배포 근거로 쓸 수 없습니다.';

/** The name of a new example project: numbered after the names already taken, so it never collides. */
export function nextExampleName(base: string, existing: Array<{ name: string }> | undefined): string {
  const names = new Set((existing || []).map(row => row.name));
  if (!names.has(base)) return base;
  for (let index = 2; ; index += 1) if (!names.has(`${base} ${index}`)) return `${base} ${index}`;
}

/** The ready demo's summary line, with the example notice kept next to the numbers. */
export function demoSummary(result: DemoResult): string {
  const counts = ['OK', 'NG', 'REVIEW'].filter(key => key in result.counts).map(key => `${key} ${result.counts[key]}`).join(' · ');
  const evaluation = !result.evaluation ? '' : result.evaluation.known > 0
    ? ` · 평가 ${result.evaluation.known}장: 미검 ${result.evaluation.escapes} · 과검 ${result.evaluation.overkills}`
    : ' · 흐름 평가는 정답 확인 후';
  return `예제 검사 ${result.images}장 · ${counts}${evaluation}`;
}
