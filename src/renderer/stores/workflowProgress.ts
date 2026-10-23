/** The steps the last impact report proved, per project scope, so the step bar keeps its checkmarks after a reload
 * without a second request. Dependency-free like workflowEvidence.ts, so panels loaded alone in tests stay light. */
type Listener = () => void;
const listeners = new Set<Listener>();
let current: {scope: string; steps: number[]} | null = null;

export function publishWorkflowProgress(scope: string, steps: number[]): void {
  if (current && current.scope === scope && current.steps.join() === steps.join()) return;
  current = {scope, steps: [...steps]};
  for (const listener of listeners) listener();
}
export function workflowProgressFor(scope: string): number[] { return current?.scope === scope ? current.steps : []; }
/** The report's steps for this scope, or null when no report has arrived for it yet. */
export function workflowProgressReport(scope: string): number[] | null { return current?.scope === scope ? current.steps : null; }
export function subscribeWorkflowProgress(listener: Listener): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }

/** The batch inspection the step-6 panel shows, as counts per source scope, for the footer's step-6 number line. The
 * panel publishes on every result change and clears its scope when the source changes or it closes (a clear for
 * another scope leaves the counts alone); the footer reads its own scope only. inspected counts the images the run is
 * done with, failed ones included (failed, present when there are any). */
export type InspectionProgress = {inspected: number; total: number; ng: number; failed?: number};
const inspectionListeners = new Set<Listener>();
let inspection: {scope: string; counts: InspectionProgress} | null = null;

export function publishInspectionProgress(scope: string, counts: InspectionProgress | null): void {
  if (!counts) { if (inspection?.scope !== scope) return; inspection = null; }
  else {
    if (inspection && inspection.scope === scope && inspection.counts.inspected === counts.inspected
      && inspection.counts.total === counts.total && inspection.counts.ng === counts.ng
      && (inspection.counts.failed ?? 0) === (counts.failed ?? 0)) return;
    inspection = {scope, counts: {...counts}};
  }
  for (const listener of inspectionListeners) listener();
}
/** The published counts for this scope, or null when the panel shows no run for it. */
export function inspectionProgressFor(scope: string): InspectionProgress | null { return inspection?.scope === scope ? inspection.counts : null; }
export function subscribeInspectionProgress(listener: Listener): () => void { inspectionListeners.add(listener); return () => { inspectionListeners.delete(listener); }; }
