/** 5.2 원클릭 자동 진행. The footer's readiness already names, for every step, the one next action. This runs that chain
 * on the person's behalf for the actions a machine may take — save the default split, start training with the settings
 * step 3 shows, evaluate the model on the test images, save a one-model inspection flow and evaluate it, inspect the test
 * images — and stops where a person must decide: the project and folder, ground-truth labels, draft review, confirming
 * truth, class problems, approval and deployment.
 *
 * It keeps no progress of its own: every pass reads the readiness again, so a finished step is never run twice and
 * pressing again after a stop or a failure resumes where it stopped. Dependency-free, so the rules are tested alone. */
import type { AssistSnapshot } from '../../stores/assistContext';

export type AutoAction = 'split' | 'train' | 'evaluate' | 'saveFlow' | 'evaluateFlow' | 'inspect';
export type Snapshot = Omit<AssistSnapshot, 'errors'>;
export type AutoDecision =
  | { kind: 'advance'; step: number }
  | { kind: 'act'; action: AutoAction }
  | { kind: 'wait'; refresh?: boolean; training?: boolean }
  | { kind: 'stop'; reason: string; step: number; target?: string }
  | { kind: 'done'; reason: string };

/** What each automatic action does, in the words the dialog lists before the start and logs after it. */
export const ACTION_TEXT: Record<AutoAction, string> = {
  split: '기본 비율로 학습·검증·시험 분할 저장',
  train: '3단계에 보이는 설정으로 학습',
  evaluate: '시험 이미지로 모델 평가 (4단계)',
  saveFlow: '모델 하나로 검사 흐름 저장 (5단계)',
  evaluateFlow: '저장한 검사 흐름 평가 (5단계)',
  inspect: '시험 이미지 검사 (6단계)',
};
/** Where the run always stops, said before it starts. */
export const PERSON_STOPS = ['프로젝트와 이미지 폴더 고르기', '정답 라벨 그리기', '초안 검토와 채택', '평가 정답 확인', '승인·패키지·배포'];

const START_TRAINING = new Set(['학습 시작 버튼으로 이동 (3단계)', '학습 결과 평가 (4단계)']);
const SPLIT = new Set(['분할 다시 적용 (1단계)', '학습·시험 분할 확인 (1단계)']);
const SAVE_FLOW = new Set(['검사 흐름 저장·평가 (5단계)', '보이는 검사 흐름 저장·평가 (5단계)']);
const DATA_CHANGED = '바뀐 내용과 되돌리기';

/** The next move for one readiness picture. Pure: the same picture always gives the same move. */
export function decide(ctx: Snapshot): AutoDecision {
  const { stage, status, nextAction: next } = ctx;
  const label = next.label;
  const stop = (reason: string, step = next.step, target = next.target): AutoDecision => ({ kind: 'stop', reason, step, ...(target ? { target } : {}) });
  if (label === '프로젝트 만들기·열기') return stop('프로젝트를 먼저 만들거나 여세요.');
  if (label === '데이터 가져오기 (1단계)') return stop('검사 이미지 폴더를 먼저 여세요. 어떤 폴더를 쓸지는 사람이 고릅니다.');
  if (label === '준비도 다시 확인') return { kind: 'wait', refresh: true };
  if (label === '학습 진행 상태 보기') return { kind: 'wait', training: true };
  if (status === 'checking' || label === '데이터 변경 상태 보기') return { kind: 'wait' };
  if (stage === 1) return label === '라벨 확인하기 (2단계)' ? { kind: 'advance', step: 2 } : stop(ctx.blocker || label);
  if (stage === 2) {
    if (next.target === 'draft-review-queue') return stop(`${ctx.blocker || '초안 검토 대기'} · 초안 채택은 사람이 합니다.`);
    if (status === 'blocked') return stop(`${ctx.blocker || '정답 라벨이 더 필요합니다'} · 정답 라벨은 사람이 그립니다.`);
    // Images without a label are left out of training; labelling or drafting them stays the person's choice.
    return { kind: 'advance', step: 3 };
  }
  if (stage === 3) {
    if (label === '라벨 확인하기 (2단계)') return stop(`${ctx.blocker || '검수 기준을 통과한 라벨 필요'} · 정답 라벨은 사람이 확인합니다.`);
    if (SPLIT.has(label) || (label === '데이터 다시 확인 (1단계)' && /분할/.test(ctx.blocker || ''))) return { kind: 'act', action: 'split' };
    if (label === '데이터 다시 확인 (1단계)') return stop(`${ctx.blocker || '데이터 확인 필요'} · 클래스 폴더는 사람이 정리합니다.`);
    // A finished model on the current data (from the run or the impact report) is the step's end, whatever the button says.
    if (ctx.progress && 'completed' in ctx.progress && ctx.progress.completed) return { kind: 'advance', step: 4 };
    if (START_TRAINING.has(label)) return { kind: 'act', action: 'train' };
    return stop(ctx.blocker || label);
  }
  if (stage === 4) {
    if (label === '검사 흐름 만들기 (5단계)') return { kind: 'advance', step: 5 };
    if (label === '시험 이미지로 평가하기 (4단계)') return { kind: 'act', action: 'evaluate' };
    // No current model (a data edit cleared it, or the data changed after training): step 3 trains one.
    if (label === '학습 모델 확인 (3단계)' || label === DATA_CHANGED) return { kind: 'advance', step: 3 };
    return stop(ctx.blocker || label);
  }
  // The active flow's model was trained before the data changed: step 4 already put a current model through its
  // evaluation, so the flow is saved again around that model (a new version; the old one stays in the history).
  if (label === DATA_CHANGED) return stage === 5 ? { kind: 'act', action: 'saveFlow' } : { kind: 'advance', step: 5 };
  if (SAVE_FLOW.has(label)) return stage === 5 ? { kind: 'act', action: 'saveFlow' } : { kind: 'advance', step: 5 };
  if (label === '검사 흐름 평가 확인 (5단계)') return stage === 5 ? { kind: 'act', action: 'evaluateFlow' } : { kind: 'advance', step: 5 };
  if (stage === 5) return label === '검사 결과 확인 (6단계)' ? { kind: 'advance', step: 6 } : stop(ctx.blocker || label);
  if (label === '승인·패키지·대상 확인') {
    const inspected = ctx.progress && 'inspected' in ctx.progress && ctx.progress.total > 0 && ctx.progress.inspected >= ctx.progress.total;
    return inspected ? { kind: 'done', reason: '검사 결과까지 왔습니다. 결과를 확인하고, 현장에 쓰려면 승인·패키지·대상 확인을 직접 진행하세요.' }
      : { kind: 'act', action: 'inspect' };
  }
  return stop(ctx.blocker || label);
}

export type AutoLogRow = { at: number; text: string; tone: 'done' | 'info' | 'stop' | 'error' };
export type AutoRunState = {
  state: 'idle' | 'running' | 'stopped' | 'done' | 'failed' | 'cancelled';
  log: AutoLogRow[];
  /** Where it stopped and the step/target that clears it (the footer's own action names the same place). */
  stop: { reason: string; step: number; target?: string; confirm?: TruthConfirm } | null;
};
/** A stop the person can clear right in the dialog: confirm the frozen test images' labels as truth (their press). */
export type TruthConfirm = { kind: 'truth'; cohortId: string; versionId: string; images: number };
/** An action's answer: null when it ran (or, for training, started), a refusal, a stop that needs a person, or an earlier
 *  step the run must go back to first (step 5's flow save with no model on the current data goes back to step 3). */
export type ActResult = null | string | { person: string; step: number; target?: string; confirm?: TruthConfirm } | { back: number; reason: string };

export interface AutoDeps {
  /** The readiness picture the footer published last (null until it has one). */
  snapshot: () => Snapshot | null;
  setStep: (step: number) => Promise<void>;
  /** Ask the readiness to read its evidence again. */
  refresh: () => void;
  act: (action: AutoAction) => Promise<ActResult>;
  sleep: (ms: number) => Promise<void>;
  cancelled: () => boolean;
  now: () => number;
}

/** Run until a person is needed, the chain is done, an action fails, or the person stops it. */
export async function runAutoProgress(deps: AutoDeps, onUpdate: (state: AutoRunState) => void,
  options: { settleMs?: number; pollMs?: number; maxPasses?: number; afterActMs?: number } = {}): Promise<AutoRunState> {
  const settleMs = options.settleMs ?? 90_000, pollMs = options.pollMs ?? 1000, maxPasses = options.maxPasses ?? 400;
  const afterActMs = options.afterActMs ?? 20_000;
  let state: AutoRunState = { state: 'running', log: [], stop: null };
  const publish = (next: Partial<AutoRunState>) => { state = { ...state, ...next }; onUpdate(state); };
  const log = (text: string, tone: AutoLogRow['tone'] = 'info') => publish({ log: [...state.log, { at: deps.now(), text, tone }] });
  const finish = (next: Partial<AutoRunState>) => { publish(next); return state; };
  publish({});
  // An action runs at most twice in one run: if the readiness still asks for it after that, it is not something this
  // run can clear, and it stops with the readiness's own reason instead of looping.
  const attempts = new Map<AutoAction, number>();
  let expectedStep = deps.snapshot()?.stage ?? 1;
  let waitingSince: number | null = null;
  let refreshed = 0;
  // Right after an action the readiness may still name it (the evidence is read again a moment later): the same action
  // is not taken again inside this window.
  let lastAct: { action: AutoAction; at: number } | null = null;
  // A step entered again and again means the readiness and the actions disagree: stop instead of circling.
  const visits = new Map<number, number>();
  for (let pass = 0; pass < maxPasses; pass++) {
    if (deps.cancelled()) return finish({ state: 'cancelled', stop: null });
    const ctx = deps.snapshot();
    // The picture of the step just opened arrives a moment later: wait for it (a bounded wait, then stop).
    if (!ctx || ctx.stage !== expectedStep) {
      if (waitingSince === null) waitingSince = deps.now();
      if (deps.now() - waitingSince > settleMs) return finish({ state: 'failed', stop: { reason: `${expectedStep}단계 상태를 읽지 못했습니다. 화면을 확인한 뒤 다시 누르세요.`, step: expectedStep } });
      await deps.sleep(pollMs);
      continue;
    }
    const move = decide(ctx);
    if (move.kind === 'wait') {
      if (waitingSince === null) waitingSince = deps.now();
      // Training waits as long as the run takes (the footer shows its epochs); anything else is bounded.
      if (!move.training && deps.now() - waitingSince > settleMs) return finish({ state: 'failed', stop: { reason: `${ctx.stage}단계 확인이 끝나지 않았습니다: ${ctx.blocker || ctx.nextAction.label}`, step: ctx.stage } });
      if (move.refresh && refreshed++ < 3) deps.refresh();
      await deps.sleep(pollMs);
      continue;
    }
    waitingSince = null;
    refreshed = 0;
    if (move.kind === 'advance') {
      const entered = (visits.get(move.step) ?? 0) + 1;
      visits.set(move.step, entered);
      if (entered > 3) return finish({ state: 'stopped', stop: { reason: `${move.step}단계를 여러 번 다시 거쳤지만 ${ctx.blocker || ctx.nextAction.label}. 화면에서 확인하세요.`, step: ctx.stage } });
      log(move.step > ctx.stage ? `${ctx.stage}단계 끝 → ${move.step}단계` : `${ctx.stage}단계에서 ${move.step}단계로 돌아감: ${ctx.blocker || ctx.nextAction.label}`, move.step > ctx.stage ? 'done' : 'info');
      expectedStep = move.step;
      await deps.setStep(move.step);
      continue;
    }
    if (move.kind === 'stop') return finish({ state: 'stopped', stop: { reason: move.reason, step: move.step, ...(move.target ? { target: move.target } : {}) }, log: [...state.log, { at: deps.now(), text: move.reason, tone: 'stop' }] });
    if (move.kind === 'done') return finish({ state: 'done', stop: null, log: [...state.log, { at: deps.now(), text: move.reason, tone: 'done' }] });
    if (lastAct && lastAct.action === move.action && deps.now() - lastAct.at < afterActMs) {
      await deps.sleep(pollMs);
      continue;
    }
    const tried = (attempts.get(move.action) ?? 0) + 1;
    attempts.set(move.action, tried);
    if (tried > 2) return finish({ state: 'stopped', stop: { reason: `${ACTION_TEXT[move.action]}을(를) 했지만 아직 ${ctx.blocker || ctx.nextAction.label}. 화면에서 확인하세요.`, step: ctx.nextAction.step, ...(ctx.nextAction.target ? { target: ctx.nextAction.target } : {}) } });
    log(`${ACTION_TEXT[move.action]} 시작`);
    let result: ActResult;
    try { result = await deps.act(move.action); } catch (cause) { result = cause instanceof Error ? cause.message : String(cause); }
    // Going back counts as entering that step, so a back-and-forth still meets the circling stop above.
    if (result && typeof result === 'object' && 'back' in result) {
      const entered = (visits.get(result.back) ?? 0) + 1;
      visits.set(result.back, entered);
      if (entered > 3) return finish({ state: 'stopped', stop: { reason: `${result.back}단계를 여러 번 다시 거쳤지만 ${result.reason}. 화면에서 확인하세요.`, step: result.back } });
      log(`${ctx.stage}단계에서 ${result.back}단계로 돌아감: ${result.reason}`, 'info');
      expectedStep = result.back;
      await deps.setStep(result.back);
      continue;
    }
    if (result && typeof result === 'object') return finish({ state: 'stopped', stop: { reason: result.person, step: result.step, ...(result.target ? { target: result.target } : {}), ...(result.confirm ? { confirm: result.confirm } : {}) }, log: [...state.log, { at: deps.now(), text: result.person, tone: 'stop' }] });
    if (typeof result === 'string') return finish({ state: 'failed', stop: { reason: `${ACTION_TEXT[move.action]}: ${result}`, step: ctx.stage }, log: [...state.log, { at: deps.now(), text: `${ACTION_TEXT[move.action]}: ${result}`, tone: 'error' }] });
    log(move.action === 'train' ? `${ACTION_TEXT.train} 시작됨 · 끝날 때까지 기다립니다` : `${ACTION_TEXT[move.action]} 끝`, 'done');
    // The evidence the readiness reads changed: ask it to read again before the next pass.
    lastAct = { action: move.action, at: deps.now() };
    deps.refresh();
    await deps.sleep(pollMs);
  }
  return finish({ state: 'failed', stop: { reason: '자동 진행이 너무 오래 걸려 멈췄습니다. 다시 누르면 이어 갑니다.', step: expectedStep } });
}
