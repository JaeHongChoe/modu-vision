/** One readable picture of where the person is: the step, its state, what blocks it, the one next action, the
 * step's progress and the panels that are currently showing an error. The footer publishes the first part on every
 * readiness change and panels report their errors; the footer's details list them in one place, and a later
 * assistant can read the same object instead of scraping the screen.
 *
 * Dependency-free like workflowEvidence.ts, so a panel compiled alone in a test does not pull the store graph. */
type Listener = () => void;

export type AssistStatus = 'ready' | 'blocked' | 'checking' | 'unknown';
/** The step's number line, one shape per step (told apart by `stage`): step 2 confirmed/drafts/remaining, step 3 the
 * epoch counter and remaining seconds or a finished model, step 4 the evaluation's counts with the accuracy and its 95%
 * range in whole percents (null without any image to score; dataChanged: counted before the model's data changed;
 * unconfirmed: the impact report does not back the model or its evaluation as current), step 5 the saved version (reevaluate: its evaluation must be redone), step 6 the inspection counts (failed: images the run could not judge, only when some failed). Mirrors
 * ReadinessProgress in components/wizard/workflowReadiness.ts (kept here so this module stays dependency-free; the
 * publish site type-checks the two against each other). */
export type AssistProgress =
  | { confirmed: number; drafts: number; remaining: number }
  | { epoch: number; totalEpochs: number; etaSeconds: number | null; completed: boolean }
  | { samples: number; escapes: number; overkills: number; accuracy: number | null; low: number | null; high: number | null; dataChanged?: boolean; unconfirmed?: boolean }
  | { versionId: string; evaluated: boolean; unsaved: boolean; reevaluate?: boolean }
  | { inspected: number; total: number; ng: number; failed?: number };
export interface AssistSnapshot {
  scope: string;
  stage: number;
  status: AssistStatus;
  blocker: string | null;
  nextAction: { label: string; step: number; target?: string };
  progress: AssistProgress | null;
  errors: { source: string; text: string }[];
}

const listeners = new Set<Listener>();
let snapshot: Omit<AssistSnapshot, 'errors'> | null = null;
const errors = new Map<string, string>();
const ownedErrors = new Map<symbol, {source: string; text: string}>();

const notify = () => { for (const listener of listeners) listener(); };

export function publishAssistContext(next: Omit<AssistSnapshot, 'errors'>): void {
  const previous = snapshot;
  if (previous && previous.scope === next.scope && previous.stage === next.stage && previous.status === next.status
    && previous.blocker === next.blocker && previous.nextAction.label === next.nextAction.label
    && previous.nextAction.step === next.nextAction.step && previous.nextAction.target === next.nextAction.target
    && JSON.stringify(previous.progress) === JSON.stringify(next.progress)) return;
  snapshot = { ...next, nextAction: { ...next.nextAction }, progress: next.progress ? { ...next.progress } : null };
  notify();
}

/** A panel's current error (null clears it). The panel keeps its own detailed banner; this is the one-line summary. */
export function reportPanelError(source: string, text: string | null | undefined): void {
  const clean = typeof text === 'string' ? text.trim() : '';
  if (clean ? errors.get(source) === clean : !errors.has(source)) return;
  if (clean) errors.set(source, clean); else errors.delete(source);
  notify();
}

/** Cleanup belongs to this mounted panel, even if another panel uses the same display name. */
export function registerPanelError(source: string, text: string | null | undefined): () => void {
  const clean = text?.trim();
  if (!clean) return () => {};
  const owner = Symbol(source);
  ownedErrors.set(owner, {source, text: clean});
  notify();
  return () => { if (ownedErrors.delete(owner)) notify(); };
}

export function currentAssistContext(): AssistSnapshot | null {
  return snapshot ? { ...snapshot, errors: assistPanelErrors() } : null;
}

export function assistPanelErrors(): { source: string; text: string }[] {
  return [...errors].map(([source, text]) => ({ source, text })).concat([...ownedErrors.values()].map(error => ({...error})));
}

export function subscribeAssistContext(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
