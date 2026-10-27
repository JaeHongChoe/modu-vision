/** How 원클릭 자동 진행 presses a step's own start button: the step screen that owns the settings (3단계 학습, 6단계 일괄 검사)
 * registers a handler while it is shown, and the run asks it to start with exactly what the screen shows. Nothing here
 * copies those settings, so what the dialog promised ("3단계에 보이는 설정") is what runs.
 *
 * Dependency-free like assistContext.ts, so a panel compiled alone in a test does not pull the store graph. */
export type AutoRunKind = 'train' | 'inspect';
/** Resolves null when the work ran (training: when it started), else the screen's own reason it cannot. */
type Handler = () => Promise<string | null>;

const handlers = new Map<AutoRunKind, Handler>();

/** Register the shown screen's start; the returned function removes it (only if it is still the registered one). */
export function registerAutoRun(kind: AutoRunKind, handler: Handler): () => void {
  handlers.set(kind, handler);
  return () => { if (handlers.get(kind) === handler) handlers.delete(kind); };
}

export async function requestAutoRun(kind: AutoRunKind): Promise<string | null> {
  const handler = handlers.get(kind);
  if (!handler) return kind === 'train' ? '3단계 학습 화면이 아직 열리지 않았습니다.' : '6단계 검사 화면이 아직 열리지 않았습니다.';
  return handler();
}

const flowSavedListeners = new Set<() => void>();

/** A flow 자동 진행 saved through the API (not the step-5 screen's own save): the screen reads its version list again so
 *  the new active version is the one it shows and evaluates. */
export function announceFlowSaved(): void {
  for (const listener of flowSavedListeners) listener();
}

export function subscribeFlowSaved(listener: () => void): () => void {
  flowSavedListeners.add(listener);
  return () => { flowSavedListeners.delete(listener); };
}

/** Approved tasks use a separate guarded screen entry point so ordinary auto progression stays compatible. */
export interface TaskAutoRunControl { isCurrent(): boolean; isPaused(): boolean; isAuthorityCurrent?(): boolean; checkpoint?(receipt: Record<string, unknown>): Promise<void> }
export interface TaskAutoRunResult { error: string | null; receipt?: Record<string, unknown> }
type TaskHandler = (control: TaskAutoRunControl) => Promise<TaskAutoRunResult>;
const taskHandlers = new Map<AutoRunKind, TaskHandler>();
const taskSettings = new Map<AutoRunKind, Record<string, unknown>>();
export function registerTaskAutoRun(kind: AutoRunKind, handler: TaskHandler, settings?: Record<string, unknown>): () => void {
  taskHandlers.set(kind, handler);
  if (settings) taskSettings.set(kind, JSON.parse(JSON.stringify(settings))); else taskSettings.delete(kind);
  return () => { if (taskHandlers.get(kind) === handler) {taskHandlers.delete(kind); taskSettings.delete(kind);} };
}
export async function requestTaskAutoRun(kind: AutoRunKind, control: TaskAutoRunControl): Promise<TaskAutoRunResult> {
  if (control.isAuthorityCurrent?.() === false || !control.isCurrent()) return {error: '프로젝트·자료·연결 또는 설정이 바뀌었습니다. 현재 작업에서 다시 확인하세요.'};
  if (control.isPaused()) return {error: '작업 진행을 일시정지했습니다.'};
  const handler = taskHandlers.get(kind);
  if (!handler) return {error: kind === 'train' ? '3단계 학습 화면의 설정을 아직 확인하지 못했습니다. 화면을 연 뒤 이어 가세요.' : '6단계 시험 검사 화면을 아직 확인하지 못했습니다. 화면을 연 뒤 이어 가세요.'};
  return handler(control);
}

/** Exact mounted-screen settings stay local; callers receive an independent snapshot. */
export function taskAutoRunSettings(kind: AutoRunKind): Record<string, unknown> | null {
  const settings = taskSettings.get(kind);
  return settings ? JSON.parse(JSON.stringify(settings)) as Record<string, unknown> : null;
}
