/** The last gallery thumbnail reads and how each ended, for the technical details (audit B5 / F-22).
 *  A read that fails, or whose answer arrives after the project context changed, leaves a line with the file name,
 *  the project it was read for and the reason, so a missing thumbnail can be traced without a debugger. Kept in
 *  memory only (never saved or sent) and dependency-free like workflowEvidence.ts. */
export type ThumbnailOutcome = 'ok' | 'failed' | 'stale' | 'aborted';
export type ThumbnailRecord = { at: string; file: string; project: string | null; outcome: ThumbnailOutcome; detail?: string };

const LIMIT = 50;
type Listener = () => void;
const listeners = new Set<Listener>();
let records: ThumbnailRecord[] = [];
const counts: Record<ThumbnailOutcome, number> = { ok: 0, failed: 0, stale: 0, aborted: 0 };

/** The file name of a thumbnail address, never its full local path. */
export function thumbnailFile(path: string): string {
  try { return decodeURIComponent(new URL(path, 'http://thumbnail.invalid').pathname.split('/').pop() || ''); } catch { return ''; }
}

export function recordThumbnailRead(path: string, project: string | null, outcome: ThumbnailOutcome, detail?: string): void {
  counts[outcome] += 1;
  // Successful reads only count; the list keeps what needs explaining.
  if (outcome !== 'ok') records = [{ at: new Date().toISOString(), file: thumbnailFile(path), project, outcome, ...(detail ? { detail } : {}) }, ...records].slice(0, LIMIT);
  for (const listener of listeners) listener();
}
export function thumbnailDiagnostics(): { counts: Record<ThumbnailOutcome, number>; records: ThumbnailRecord[] } { return { counts: { ...counts }, records: [...records] }; }
export function subscribeThumbnailDiagnostics(listener: Listener): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }
