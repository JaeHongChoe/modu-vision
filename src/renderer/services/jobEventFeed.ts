/** S1-10: the app's job event stream, bound to the committed project and API server. */
import { getApiPersistenceIdentity, getProjectContext, request } from './api';
import { JobEventStream, type JobEventPage } from './jobEvents';

/** One stream per committed project on one API server; a project switch starts a new stream without a cursor. */
export function jobEventStreamKey(): string | null {
  const context = getProjectContext();
  return context ? JSON.stringify([getApiPersistenceIdentity(), context.workspace_id, context.project_id]) : null;
}

export function createJobEventStream(): JobEventStream {
  return new JobEventStream(
    (after) => request<JobEventPage>(`/api/job-events?${new URLSearchParams(after ? { after, limit: '200' } : { limit: '200' })}`),
    jobEventStreamKey,
  );
}

/** Whether a pull means the given job's state must be read again from the server. */
export function jobNeedsRefresh(jobId: string | null, pulled: { events: Array<{ job_id: string }>; reset: boolean; stale: boolean }): boolean {
  if (!jobId || pulled.stale) return false;
  return pulled.reset || pulled.events.some((event) => event.job_id === jobId);
}

/** The app's catch-up after a (re)connection: pulls until caught up and re-reads the current job when concerned. A failed
 *  catch-up is retried once after a pause; if that fails too, the next connection catches up again (no endless retry). */
export function createCatchUp(stream: Pick<JobEventStream, 'pull'>, currentJobId: () => string | null, refreshJob: () => Promise<void>,
  schedule: (run: () => void, ms: number) => unknown = setTimeout): () => Promise<void> {
  let retryPending = false;
  const run = async (retrying: boolean): Promise<void> => {
    try {
      await catchUpJobEvents(stream, currentJobId, refreshJob);
    } catch {
      if (retrying || retryPending) return;
      retryPending = true;
      schedule(() => { retryPending = false; void run(true); }, 5000);
    }
  };
  return () => run(false);
}

/** Pull until the stream has caught up (bounded), re-reading the current job when the events concern it or the stream
 *  was reset. A failed pull leaves the stream where it was; the caller decides when to try again. */
export async function catchUpJobEvents(stream: Pick<JobEventStream, 'pull'>, currentJobId: () => string | null,
  refreshJob: () => Promise<void>, rounds = 10): Promise<void> {
  for (let round = 0; round < rounds; round += 1) {
    const pulled = await stream.pull();
    if (jobNeedsRefresh(currentJobId(), pulled)) await refreshJob();
    if (pulled.stale || !pulled.more) return;
  }
  // Still more after the bound: the current job's events may be among those not read yet, so it is read again.
  if (currentJobId()) await refreshJob();
}
