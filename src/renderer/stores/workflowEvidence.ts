/** A dependency-free signal: a flow evaluation or approval that landed bumps it, and the footer re-reads its evidence.
 * Kept apart from the stores so panels that are loaded alone in tests do not pull the whole store graph. */
type Listener = () => void;
const listeners = new Set<Listener>();
let revision = 0;

export function workflowEvidenceRevision(): number { return revision; }
export function bumpWorkflowEvidence(): void { revision += 1; for (const listener of listeners) listener(); }
export function subscribeWorkflowEvidence(listener: Listener): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }
