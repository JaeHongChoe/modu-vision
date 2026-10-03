// S2-01: build the example project through the same calls a user's clicks make: the project store creates the
// project, the dataset store imports the folder, the training store starts a short CPU training, the flow API saves a
// single-model flow and the batch inspection runner inspects the test images into the saved history. Only the example
// dataset itself comes from the onboarding API (drawn on this computer from a seed).
import { nextExampleName, type DemoResult, type DemoState, type DemoStepId } from './firstRun';

export const DEMO_PROJECT_NAME = '예제 · 표면 긁힘 분류';
/** A small network-free model: no pretrained weights are downloaded. Measured on the example (CPU, about 16 s here):
 *  from scratch it needs a higher learning rate and every epoch (no early stop) to separate the scratches and blots; with
 *  4 epochs or early stopping it stayed at chance (validation loss 0.69) and judged every test image OK. */
export const DEMO_TRAINING = { backbone: 'resnet18', pretrained: false, epochs: 20, image_size: 64, learning_rate: 0.003,
  augmentation_profile: 'photometric', patience: 20 } as const;
const TERMINAL = new Set(['completed', 'failed', 'aborted', 'cancelled', 'stopped', 'disconnected', 'interrupted']);
/** Status reads that fail in a row before the example stops (a brief backend restart or network hiccup is not fatal). */
const STATUS_RETRIES = 5;
/** Names tried after a collision before the example stops, and half-second waits for other project work to finish. */
const NAME_ATTEMPTS = 20;
const BUSY_WAITS = 40;
/** The project store's refusals while other work is still finishing (data loading, a training the store has not yet
 *  seen end): transient, so the step waits instead of failing. */
const STILL_RUNNING = /진행 중입니다/;
const message = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);

export interface DemoDeps {
  exampleDataset: () => Promise<{ folder: string; images: number; task: string }>;
  /** Why the example cannot run here (a selected compute server or a team connection), or null. */
  serverReason: () => string | null;
  createProject: (data: { name: string; task: 'classification' }) => Promise<boolean>;
  projectError: () => string | null;
  /** Another project action (an open, a save, a data load) is still running. */
  projectBusy: () => boolean;
  importFolder: (folder: string) => Promise<{ error: string | null; folder: string | null }>;
  markExample: () => Promise<unknown>;
  startTraining: (folder: string) => Promise<string | null>;
  trainingError: () => string | null;
  jobStatus: (jobId: string) => Promise<{ status: string; error?: unknown }>;
  /** The job ended: the training store reads it again, so it no longer counts as running (a project switch checks it). */
  trainingEnded: (jobId: string) => Promise<void>;
  saveFlow: (jobId: string, folder: string) => Promise<{ version_id: string }>;
  inspect: (folder: string) => Promise<{ run_id: string | null; counts: Record<string, number>; images: number }>;
  projectId: () => string | null;
  sleep: (ms: number) => Promise<void>;
  now: () => number;
}

/** Run the example; ``onUpdate`` receives every state change. Failures name their step and never leave it 'running'. */
export async function runDemo(deps: DemoDeps, onUpdate: (state: DemoState) => void,
  options: { trainingTimeoutMs?: number; projectName?: string; takenNames?: string[] } = {}): Promise<DemoState> {
  let projectName = options.projectName || DEMO_PROJECT_NAME;
  let state: DemoState = { state: 'running', steps: {}, error: null, result: null };
  const publish = (next: Partial<DemoState>) => { state = { ...state, ...next }; onUpdate(state); };
  const mark = (step: DemoStepId, status: 'running' | 'done' | 'failed', detail?: string | null) =>
    publish({ steps: { ...state.steps, [step]: { status, detail: detail ?? null } } });
  let current: DemoStepId | null = null;
  const step = async <T>(id: DemoStepId, work: () => Promise<T>, detail?: (value: T) => string | null): Promise<T> => {
    current = id;
    mark(id, 'running');
    const value = await work();
    mark(id, 'done', detail ? detail(value) : null);
    return value;
  };
  publish({});
  try {
    const refused = deps.serverReason();
    if (refused) throw new Error(refused);
    const dataset = await step('dataset', deps.exampleDataset, value => `합성 이미지 ${value.images}장`);
    await step('project', async () => {
      // A project left by an earlier example that stopped, or one no longer in the recent list, can hold the name:
      // the next free name is tried instead.
      const tried = [...(options.takenNames || [])];
      let collisions = 0;
      let waits = 0;
      for (;;) {
        if (deps.projectBusy()) {
          if (++waits > BUSY_WAITS) throw new Error('다른 프로젝트 작업이 끝나지 않아 예제 프로젝트를 만들지 못했습니다. 잠시 뒤 다시 시작하세요.');
          await deps.sleep(500);
          continue;
        }
        if (await deps.createProject({ name: projectName, task: 'classification' })) return;
        const error = deps.projectError() || '';
        // Refused because other work started meanwhile, or is still finishing: wait and try the same name again.
        if ((deps.projectBusy() || STILL_RUNNING.test(error)) && ++waits <= BUSY_WAITS) {
          await deps.sleep(500);
          continue;
        }
        // Only this run's collisions count toward the budget, not the names already listed.
        if (!/already exists/i.test(error) || ++collisions >= NAME_ATTEMPTS) throw new Error(error || '예제 프로젝트를 만들지 못했습니다.');
        tried.push(projectName);
        projectName = nextExampleName(DEMO_PROJECT_NAME, tried.map(name => ({ name })));
      }
    }, () => projectName);
    const folder = await step('import', async () => {
      const imported = await deps.importFolder(dataset.folder);
      if (imported.error || !imported.folder) throw new Error(imported.error || '예제 데이터를 가져오지 못했습니다.');
      await deps.markExample();
      return imported.folder;
    });
    const jobId = await step('train', async () => {
      const started = await deps.startTraining(folder);
      if (!started) throw new Error(deps.trainingError() || '예제 학습을 시작하지 못했습니다.');
      const deadline = deps.now() + (options.trainingTimeoutMs ?? 15 * 60_000);
      let failures = 0;
      for (;;) {
        let job: { status: string; error?: unknown };
        try {
          job = await deps.jobStatus(started);
          failures = 0;
        } catch (cause) {
          failures += 1;
          if (failures >= STATUS_RETRIES) throw new Error(`예제 학습 상태를 확인하지 못했습니다(${message(cause)}). 작업 센터에서 작업 ${started}을 확인하세요.`);
          if (deps.now() > deadline) throw new Error('예제 학습이 제한 시간 안에 끝나지 않았습니다. 작업 센터에서 상태를 확인하세요.');
          await deps.sleep(1000);
          continue;
        }
        if (TERMINAL.has(job.status)) {
          try { await deps.trainingEnded(started); } catch { /* the job center still shows it; the next read settles it */ }
        }
        if (job.status === 'completed') return started;
        if (TERMINAL.has(job.status)) {
          const reason = typeof job.error === 'string' ? job.error : (job.error as { message?: string } | undefined)?.message;
          throw new Error(`예제 학습이 ${job.status} 상태로 끝났습니다${reason ? `: ${reason}` : ''}.`);
        }
        if (deps.now() > deadline) throw new Error('예제 학습이 제한 시간 안에 끝나지 않았습니다. 작업 센터에서 상태를 확인하세요.');
        await deps.sleep(1000);
      }
    }, value => `작업 ${value}`);
    const flow = await step('flow', () => deps.saveFlow(jobId, folder), value => `저장 버전 ${value.version_id.slice(0, 8)}`);
    const inspected = await step('inspect', () => deps.inspect(folder), value => `${value.images}장 검사`);
    const result: DemoResult = { project_id: deps.projectId() || '', project_name: projectName, job_id: jobId,
      flow_version_id: flow.version_id, run_id: inspected.run_id || '', counts: inspected.counts, images: inspected.images };
    publish({ state: 'ready', result });
  } catch (cause) {
    // A refusal before the first step marks no step; a step that failed shows its reason on its own row.
    if (current) mark(current, 'failed', message(cause));
    publish({ state: 'failed', error: message(cause) });
  }
  return state;
}
