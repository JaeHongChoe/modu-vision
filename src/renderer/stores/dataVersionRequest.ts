/** A request to open step 1's "데이터 버전" dialog at a given version, from anywhere (the footer's data-change notice).
 * The request waits until step 1 is mounted and takes it, so switching steps first never loses it. Dependency-free like
 * workflowEvidence.ts. */
type Listener = () => void;
const listeners = new Set<Listener>();
let pending: {versionId: string | null} | null = null;

export function requestDataVersionPanel(versionId: string | null): void {
  pending = {versionId};
  for (const listener of listeners) listener();
}
/** Take the waiting request (or null); a request is taken once. */
export function takeDataVersionRequest(): {versionId: string | null} | null { const value = pending; pending = null; return value; }
export function subscribeDataVersionRequest(listener: Listener): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }
