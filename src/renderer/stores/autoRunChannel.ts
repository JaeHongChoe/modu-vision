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
