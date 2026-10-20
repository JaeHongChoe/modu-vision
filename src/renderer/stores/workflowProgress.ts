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
export function subscribeWorkflowProgress(listener: Listener): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }
