/** The run-id header on the one backend write a confirmed assistant tool makes (§4.6).
 *
 * The renderer's stores and services make the request as they always do; just before a confirmed write tool runs the
 * panel arms its run here, and the main process's header hook adds `X-Modu-Assistant-Run` to the matching request (same
 * method and API path, to the local backend or the connected shared server) until the tool ends or two minutes pass.
 * The backend records it as `via: "assistant"` beside the person's own actor. It is attribution, not a capability: the
 * person's identity and role decide what the request may do, so a mis-tagged request can only be mislabelled. */

const RUN_ID = /^run_[a-f0-9]{24}$/;
const TTL_MS = 120_000;

/** The backend write each write tool makes; a tool without an entry never gets the header. */
export const TOOL_WRITES:Readonly<Record<string, ReadonlyArray<{method:string;path:RegExp}>>> = {
  save_split: [{method: 'POST', path: /^\/api\/dataset\/split$/}],
  assign_class: [{method: 'PUT', path: /^\/api\/dataset\/metadata\/class-override$/}],
  generate_label_drafts: [{method: 'POST', path: /^\/api\/label-suggestions\/batches$/}],
  start_training: [{method: 'POST', path: /^\/api\/training\/start$/}],
};

export class AssistantRunRegistry {
  private readonly armed = new Map<string, {tool:string;expires:number}>();

  arm(runId:unknown, tool:unknown, now:number=Date.now()):boolean {
    if (typeof runId !== 'string' || !RUN_ID.test(runId) || typeof tool !== 'string' || !TOOL_WRITES[tool]) return false;
    this.prune(now);
    if (this.armed.size >= 4 && !this.armed.has(runId)) return false;
    this.armed.set(runId, {tool, expires: now + TTL_MS});
    return true;
  }

  disarm(runId:unknown):void {if (typeof runId === 'string') this.armed.delete(runId);}

  private prune(now:number):void {for (const [id, run] of this.armed) if (run.expires <= now) this.armed.delete(id);}

  /** Headers for one outgoing request; the caller only asks for requests to the local backend or the shared server. */
  headerFor(target:string, method:string|undefined, now:number=Date.now()):Record<string,string> {
    this.prune(now);
    if (!this.armed.size) return {};
    let pathname:string;
    try {pathname = new URL(target).pathname;} catch {return {};}
    const verb = (method || 'GET').toUpperCase();
    for (const [runId, run] of this.armed) {
      if (TOOL_WRITES[run.tool].some(write => write.method === verb && write.path.test(pathname))) return {'X-Modu-Assistant-Run': runId};
    }
    return {};
  }
}

export const assistantRuns = new AssistantRunRegistry();
