// S2-01: the first-run guide. It shows the order of a first project (how to work, checking this computer or a
// server, creating a project, bringing data) and offers a small example that runs end to end on this computer's
// CPU without network, GPU or accounts. The example's results are never a quality approval of a real process.

export type DemoStepId = 'dataset' | 'project' | 'import' | 'train' | 'flow' | 'inspect';
export type DemoStepStatus = 'pending' | 'running' | 'done' | 'failed';
export type DemoResult = {
  project_id: string; project_name: string; job_id: string; flow_version_id: string; run_id: string;
  counts: Record<string, number>; images: number;
};
export type DemoState = {
  state: 'idle' | 'running' | 'ready' | 'failed';
  steps: Partial<Record<DemoStepId, { status: DemoStepStatus; detail?: string | null }>>;
  error: string | null;
  result: DemoResult | null;
};
export type ExampleProjectRef = { id: string; name: string; project_dir: string };
export type OnboardingState = { version: 1; dismissed: boolean; demo: DemoState; example_projects?: ExampleProjectRef[]; team?: boolean };

export const DEMO_STEPS: Array<[DemoStepId, string]> = [
  ['dataset', '합성 예제 이미지 만들기'],
  ['project', '예제 프로젝트 만들기'],
  ['import', '데이터 가져오기'],
  ['train', '이 컴퓨터 CPU로 짧게 학습'],
  ['flow', '검사 플로우 저장'],
  ['inspect', '테스트 이미지 검사·기록'],
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
  return `예제 검사 ${result.images}장 · ${counts}`;
}
