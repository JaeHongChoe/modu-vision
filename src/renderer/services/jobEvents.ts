/** S1-10: job events read after a cursor, so a client that was disconnected catches up with what the job ledger
 *  recorded instead of trusting the live messages it happened to receive.
 *
 *  A cursor belongs to one project stream. The server answers `reset` when the client has no cursor or its cursor can
 *  no longer be continued (another project or ledger, a restored ledger): the client then takes the new cursor first and
 *  reloads its authoritative snapshot, so nothing recorded after that cursor is missed. Events are deduplicated by id,
 *  and an answer that arrives after the project stream changed is discarded.
 *
 *  A pull commits its cursor and the ids it saw only when it completes: a failed page or a discarded (stale) pull leaves
 *  the stream where it was, so the next pull reads those events again instead of losing them. A pull requested while
 *  another is in flight gets one more read after it (the events recorded since its trigger), shared by every request
 *  that arrives meanwhile. */

export interface JobEvent {
  id: string;
  job_id: string;
  kind: string;
  seq: number;
  event: string;
  from_state: string | null;
  to_state: string;
  at: number;
  payload: unknown;
}

export interface JobEventPage {
  cursor: string;
  events: JobEvent[];
  reset: boolean;
  reason: 'initial' | 'cursor_expired' | null;
  more: boolean;
}

export interface JobEventPull {
  /** Newly seen events, in ledger order. */
  events: JobEvent[];
  /** The client must reload its snapshot: it had no cursor, or its cursor could not be continued. */
  reset: boolean;
  /** The project stream changed while the request was in flight; nothing was applied. */
  stale: boolean;
  /** The page limit was reached with more recorded: pull again to continue. */
  more: boolean;
}

/** Pages read in one pull before yielding; a client far behind continues at its next pull. */
const MAX_PAGES = 20;
/** Event ids remembered for deduplication. */
const SEEN_LIMIT = 5000;

export class JobEventStream {
  private cursor: string | null = null;
  private key: string | null = null;
  private seen = new Set<string>();
  private pulling: Promise<JobEventPull> | null = null;
  private followUp: Promise<JobEventPull> | null = null;

  constructor(
    private readonly fetchPage: (after: string | null) => Promise<JobEventPage>,
    private readonly streamKey: () => string | null,
  ) {}

  /** Read everything recorded after the cursor. A call during a pull gets one more read after it, shared by every call
   *  that arrives meanwhile, so an answer computed before its trigger is never its only answer. */
  pull(): Promise<JobEventPull> {
    // A queued follow-up is joined, so two reads never run at once (a read settling just before this call included).
    if (this.followUp) return this.followUp;
    if (!this.pulling) return this.start();
    this.followUp ??= this.pulling.catch(() => undefined).then(() => { this.followUp = null; return this.start(); });
    return this.followUp;
  }

  private start(): Promise<JobEventPull> {
    const run = this.read().finally(() => { if (this.pulling === run) this.pulling = null; });
    this.pulling = run;
    return run;
  }

  private async read(): Promise<JobEventPull> {
    const key = this.streamKey();
    if (key === null) return { events: [], reset: false, stale: true, more: false };
    if (key !== this.key) {
      // Another project stream: its cursor and seen events never carry over.
      this.key = key;
      this.cursor = null;
      this.seen.clear();
    }
    // Read into local copies; the stream moves only when the whole pull completed for this same project stream.
    let cursor = this.cursor;
    let seen = new Set(this.seen);
    const events: JobEvent[] = [];
    let reset = false;
    let more = false;
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const answer = await this.fetchPage(cursor);
      if (this.streamKey() !== key || this.key !== key) return { events: [], reset: false, stale: true, more: false };
      if (answer.reset) {
        reset = true;
        events.length = 0;
        seen = new Set();
      }
      for (const event of answer.events) {
        if (seen.has(event.id)) continue;
        remember(seen, event.id);
        events.push(event);
      }
      cursor = answer.cursor;
      more = answer.more;
      if (!answer.more) break;
    }
    this.cursor = cursor;
    this.seen = seen;
    return { events, reset, stale: false, more };
  }
}

function remember(seen: Set<string>, id: string): void {
  seen.add(id);
  if (seen.size > SEEN_LIMIT) {
    const oldest = seen.values().next().value;
    if (oldest !== undefined) seen.delete(oldest);
  }
}
