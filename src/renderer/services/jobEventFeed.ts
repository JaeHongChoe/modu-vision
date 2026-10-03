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

/** Pull until the stream has caught up (bounded), re-reading the current job when the events concern it or the stream
 *  was reset. A failed pull leaves the stream where it was; the caller decides when to try again. */
export async function catchUpJobEvents(stream: Pick<JobEventStream, 'pull'>, currentJobId: () => string | null,
  refreshJob: () => Promise<void>, rounds = 10): Promise<void> {
  for (let round = 0; round < rounds; round += 1) {
    const pulled = await stream.pull();
    if (jobNeedsRefresh(currentJobId(), pulled)) await refreshJob();
    if (pulled.stale || !pulled.more) return;
  }
}
