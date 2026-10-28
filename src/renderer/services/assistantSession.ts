/** One conversation with the built-in assistant: the question → provider → tools → provider loop (§4.1, §4.5, §4.6).
 *
 * The provider is called through the main process (the host adapter's assistant bridge, send); this side owns the tools. A question
 * freezes its screen context and its trimmed history, so every provider call of that question sees the same prefix
 * (Anthropic only accepts its replayed thinking blocks under an unchanged prefix). Read and navigate tools run at once;
 * a write tool stops the loop at its preview until the person confirms or declines in the panel; a forbidden tool is
 * answered "직접 하세요". Every provider call and every tool call is one line in the project's run log. An image
 * question may opt into bounded tool followups. Each stateless followup obtains a fresh one-shot grant for the same selected pixels; later questions retain only a note.
 *
 * Dependencies are injected (bridge, tools, context, log) so node tests drive it with fakes. */
import type { AssistantBridge, AssistantError, AssistantImageInput, AssistantMessage, AssistantProviderId, AssistantReasoningEffort, AssistantToolCall } from '../../types/electron';
import { argumentSummary, canonicalJson, randomHex, sha256Hex, trimConversation } from './assistantContext';
import { ToolRefusal, type AssistantToolRegistry, type ToolOutcome, type ToolPreview, type ToolTier } from './assistantTools';
import {diagnosticError} from './assistantDiagnostics';
import { AssistantReadWaitError, runBoundedAssistantRead } from './assistantReadWait';
import type { RunEntry } from './assistantLog';

export type SessionEvent =
  | { kind: 'assistant'; text: string }
  | { kind: 'tool'; name: string; tier: ToolTier | 'unknown'; summary: string; ok: boolean }
  | { kind: 'error'; error: AssistantError }
  | { kind: 'notice'; text: string }
  | { kind: 'busy'; busy: boolean }
  /** A confirmed write is executing: it cannot be stopped and its outcome is recorded before any reset. */
  | { kind: 'writing'; writing: boolean }
  | { kind: 'confirm'; pending: PendingWrite | null };

export interface PendingWrite { call: AssistantToolCall; preview: ToolPreview; runId: string; binding: string }

export interface SessionDeps {
  bridge: Pick<AssistantBridge, 'send' | 'cancel' | 'beginRun' | 'endRun'> & Partial<Pick<AssistantBridge,'imageIntent'>>;
  tools: AssistantToolRegistry;
  context(): Record<string, unknown>;
  /** Local authority only; never sent to the provider or run log. */
  scope(): string;
  /** The project part of scope() (no provider settings): the run log a write outcome belongs to. */
  projectScope(): string;
  writeBinding(): string;
  log(entry: RunEntry): Promise<unknown>;
  provider(): AssistantProviderId | null;
  /** Controlled-fixture deadline; production uses the shared 15-second read deadline. */
  readTimeoutMs?: number;
}

export interface ConversationQuestionOptions {reasoningEffort?:AssistantReasoningEffort;allowImageTools?:boolean;context?:Record<string,unknown>;executionIntent?:boolean}

interface Turn { version: number; scope: string; project: string }
class StaleTurn extends Error {}

const MAX_PROVIDER_CALLS = 6;
const MAX_TOOL_RESULT_CHARS = 4_000;
const newRunId = () => `run_${randomHex(12)}`;
/** A too-large result is explicitly omitted, never cut inside JSON. Normal tools bound their own DTOs. */
const boundedResult = (text:string,tool:string) => text.length<=MAX_TOOL_RESULT_CHARS?text:JSON.stringify({
  truncated:true,tool,reason:'tool_result_too_large',
  next_action:'진단은 read_app_diagnostics의 section을 골라 작게 다시 읽으세요. 작업 상태는 read_task_status로 확인하세요. 생략된 결과를 추측하지 마세요.',
});

export class AssistantConversation {
  /** Finished turns, as sent to the provider (images replaced by a note). */
  readonly messages: AssistantMessage[] = [];
  pending: PendingWrite | null = null;
  busy = false;
  writing = false;
  private history: AssistantMessage[] = [];
  private current: AssistantMessage[] = [];
  private frozenContext: Record<string, unknown> = {};
  private queue: AssistantToolCall[] = [];
  private calls = 0;
  private inFlight: string | null = null;
  private image: AssistantImageInput | undefined;
  private imageQuestion = false;
  private allowImageTools = false;
  private reasoningEffort:AssistantReasoningEffort|undefined;
  private executionIntent=false;
  private logWarned = false;
  private version = 0;
  private turn: Turn | null = null;
  private historyScope: string | null = null;
  private readWait: AbortController | null = null;

  constructor(private readonly deps: SessionDeps, private readonly emit: (event: SessionEvent) => void) {}

  private setBusy(busy: boolean) { this.busy = busy; this.emit({ kind: 'busy', busy }); }
  private setWriting(writing: boolean) { this.writing = writing; this.emit({ kind: 'writing', writing }); }

  /** Cancels only this conversation's request. Late completions cannot change the next turn. */
  invalidate(reason?: string, clearHistory = true): void {
    const request = this.inFlight;
    this.version++; this.turn = null; this.inFlight = null;
    const readWait = this.readWait; this.readWait = null; readWait?.abort();
    this.pending = null; this.queue = []; this.current = []; this.image = undefined; this.imageQuestion = false;
    if (clearHistory) { this.messages.length = 0; this.history = []; this.historyScope = null; }
    this.emit({ kind: 'confirm', pending: null }); this.setBusy(false);
    if (reason) this.emit({ kind: 'notice', text: reason });
    if (request) void this.deps.bridge.cancel(request).catch(() => {});
  }

  private isCurrent(turn: Turn, binding?: string): boolean {
    return turn.version === this.version && this.deps.scope() === turn.scope
      && (binding === undefined || binding === this.deps.writeBinding());
  }
  private check(turn: Turn, binding?: string): void {
    if (this.isCurrent(turn, binding)) return;
    if (turn.version === this.version) this.invalidate('프로젝트·이미지 또는 설정이 바뀌어 이전 요청을 멈췄습니다. 현재 화면에서 다시 요청하세요.');
    throw new StaleTurn();
  }

  private logFailed(cause: unknown) {
    if (this.logWarned) return;
    this.logWarned = true;
    this.emit({ kind: 'notice', text: `실행 기록을 남기지 못했습니다(${cause instanceof Error ? cause.message : String(cause)}). 프로젝트를 열었는지와 권한을 확인하세요.` });
  }
  private async record(turn: Turn, entry: Omit<RunEntry, 'provider'>): Promise<void> {
    this.check(turn);
    const provider = this.deps.provider();
    try { await this.deps.log({ ...entry, ...(provider ? { provider } : {}) }); }
    catch (cause) { this.check(turn); this.logFailed(cause); }
  }

  /** A confirmed write returned, so the backend may have changed: its outcome is shown and logged whenever the project is
   *  unchanged, even if the turn or the provider settings changed meanwhile. Only another project withholds it (its log). */
  private async recordWrite(turn: Turn, call: AssistantToolCall, runId: string, outcome: ToolOutcome, provider: AssistantProviderId | null, disarmed: boolean): Promise<void> {
    const entry: RunEntry = { run_id: runId, tool: call.name, tier: 'write', summary: `${call.name}(${argumentSummary(call.arguments)})`.slice(0, 280),
      args_sha256: await sha256Hex(canonicalJson(call.arguments)), confirmed: true, result: outcome.ok ? 'ok' : outcome.errorCode === 'tool_error' ? 'error' : 'refused',
      ...(outcome.errorCode ? { error_code: outcome.errorCode } : {}), ...(outcome.link ? { link: outcome.link } : {}), ...(provider ? { provider } : {}) };
    if (this.deps.projectScope() !== turn.project) {
      const text = '실행하는 동안 프로젝트가 바뀌어 결과를 지금 프로젝트의 실행 기록에 남기지 않았습니다. 이전 프로젝트를 다시 열어 상태를 확인하세요.';
      if (turn.version === this.version) this.invalidate(text); else this.emit({ kind: 'notice', text });
      return;
    }
    if (this.isCurrent(turn)) this.answer(call, outcome);
    this.emit({ kind: 'tool', name: call.name, tier: 'write', summary: outcome.summary, ok: outcome.ok });
    if (!disarmed) this.emit({kind: 'notice', text: '실행 결과는 아래 기록에 남깁니다. 실행 확인 연결을 정리하지 못했으니 같은 작업을 다시 누르기 전에 현재 상태를 확인하세요.'});
    try { await this.deps.log(entry); }
    catch (cause) { if (this.deps.projectScope() === turn.project) this.logFailed(cause); }
  }

  /** A new question. The explicit Send action authorizes the selected image in this bounded turn; tool followups renew scoped grants. */
  async ask(text: string, image?: AssistantImageInput, options:ConversationQuestionOptions={}): Promise<void> {
    const question = text.trim();
    if (!question || this.busy || this.pending || this.writing) return;
    const scope = this.deps.scope();
    if (this.historyScope !== scope) this.messages.length = 0;
    this.historyScope = scope;
    const turn = { version: ++this.version, scope, project: this.deps.projectScope() }; this.turn = turn;
    this.history = trimConversation(this.messages);
    this.frozenContext = options.context ?? this.deps.context();
    this.reasoningEffort=options.reasoningEffort;this.allowImageTools=options.allowImageTools===true;this.executionIntent=options.executionIntent===true;
    this.current = [{ role: 'user', text: question }];
    this.calls = 0;this.queue = [];this.image = image;this.imageQuestion = Boolean(image);this.logWarned = false;
    await this.loop(turn);
  }

  /** Stops a provider call in flight; the unfinished question is dropped and can be asked again. A confirmed write that is
   *  executing is not stopped halfway (the panel offers no stop then): its outcome is recorded first. */
  async stop(): Promise<void> { if (!this.writing) this.invalidate(undefined, false); }

  private commit() {
    const turn = this.current.map(message => message.role === 'user' && this.imageQuestion && message === this.current[0]
      ? { role: 'user' as const, text: `${message.text}\n(이 질문에는 이미지 1장을 함께 보냈습니다.)` } : message);
    this.messages.push(...turn);
    this.current = [];this.image = undefined;this.imageQuestion = false;
  }

  private async loop(turn: Turn): Promise<void> {
    this.check(turn);
    this.setBusy(true);
    try {
      while (this.calls < MAX_PROVIDER_CALLS) {
        this.check(turn);
        this.calls++;
        const requestId = `req_${randomHex(12)}`;
        this.inFlight = requestId;
        const messages = [...this.history, ...this.current];
        let image=this.image;
        if(image&&this.calls>1){
          const grant=await this.deps.bridge.imageIntent?.({fileName:'현재 질문의 첨부 이미지',imageIdentity:image.imageIdentity,context:this.frozenContext});
          this.check(turn);
          if(!grant?.ok){this.emit({kind:'error',error:grant&&!grant.ok?grant.error:{code:'image_refused',reason:'이미지 문맥을 이어서 보내지 못했습니다.',action:'연결을 확인한 뒤 다시 질문하세요.'}});this.current=[];this.image=undefined;this.imageQuestion=false;return;}
          image={...image,grantId:grant.grantId};
        }
        const result = await this.deps.bridge.send({ requestId, context: this.frozenContext, messages,
          tools: this.imageQuestion&&!this.allowImageTools ? [] : this.deps.tools.specs().filter(spec=>!this.executionIntent||this.deps.tools.tier(spec.name)!=='write'), ...(image?{image}:{}), ...(this.reasoningEffort?{reasoningEffort:this.reasoningEffort}:{}) });
        this.check(turn);
        this.inFlight = null;
        const lastUser = [...this.current].reverse().find(message => message.role === 'user') as { text: string } | undefined;
        await this.record(turn, { run_id: newRunId(), tool: 'conversation', tier: 'conversation',
          summary: this.calls === 1 ? `질문 ${lastUser?.text.length ?? 0}자${this.image ? ' · 이미지 1장' : ''}` : `도구 결과 뒤 ${this.calls}번째 요청`,
          args_sha256: await sha256Hex(canonicalJson({ requestId, count: messages.length })), confirmed: Boolean(this.image && this.calls === 1),
          result: result.ok ? 'ok' : result.error.code === 'cancelled' ? 'cancelled' : 'error', ...(result.ok ? {} : { error_code: result.error.code }) });
        this.check(turn);
        if (!result.ok) {
          this.emit({ kind: 'error', error: result.error });
          // The unanswered question is not kept: the next one starts from the last finished turn.
          this.current = [];this.image = undefined;this.imageQuestion = false;
          return;
        }
        if (result.text) this.emit({ kind: 'assistant', text: result.text });
        if (this.imageQuestion && !this.allowImageTools && result.toolCalls.length) {
          // An unsolicited provider call cannot turn one image confirmation into a tool-enabled conversation.
          this.current.push({role: 'assistant', text: result.text});
          this.emit({kind: 'notice', text: '이미지 질문에서는 도구를 실행하지 않습니다. 필요한 작업은 별도 질문으로 요청하세요.'});
          this.commit(); return;
        }
        this.current.push({ role: 'assistant', text: result.text, ...(result.toolCalls.length ? { toolCalls: result.toolCalls } : {}),
          ...(result.replay !== undefined ? { replay: result.replay } : {}) });
        if (!result.toolCalls.length) { this.commit(); return; }
        this.queue = [...result.toolCalls];
        if (await this.drain(turn)) return;  // waiting for the person
      }
      this.emit({ kind: 'notice', text: '도우미가 도구를 여러 번 불러 이번 질문을 멈췄습니다. 결과를 확인하고 다시 물어보세요.' });
      this.commit();
    } catch (cause) {
      if (!(cause instanceof StaleTurn) && this.isCurrent(turn)) {
        this.emit({ kind: 'error', error: {code: 'network', reason: '도우미 연결을 완료하지 못했습니다.', action: '연결 상태를 확인한 뒤 다시 보내세요.'} });
        this.current = []; this.queue = []; this.image = undefined; this.imageQuestion = false;
      }
    } finally {
      if (turn.version === this.version) {
        this.inFlight = null;
        if (!this.pending) this.setBusy(false);
      }
    }
  }

  private answer(call: AssistantToolCall, outcome: ToolOutcome) {
    this.current.push({ role: 'tool', toolCallId: call.id, name: call.name, content: boundedResult(outcome.content,call.name), ...(outcome.ok ? {} : { isError: true }) });
  }

  /** Runs the queued calls of one assistant turn; true when a write waits for the person. */
  private async drain(turn: Turn): Promise<boolean> {
    while (this.queue.length) {
      this.check(turn);
      const call = this.queue.shift()!;
      const tier = this.deps.tools.tier(call.name);
      const runId = newRunId();
      const args_sha256 = await sha256Hex(canonicalJson(call.arguments));
      this.check(turn);
      const summary = `${call.name}(${argumentSummary(call.arguments)})`.slice(0, 280);
      let outcome: ToolOutcome;
      if (call.argumentsError || !tier) {
        outcome = { ok: false, errorCode: call.argumentsError ? 'invalid_arguments' : 'unknown_tool', summary: '도구 호출을 읽지 못했습니다.',
          content: call.argumentsError ? '인자를 JSON 객체로 읽지 못했습니다. 다시 호출하세요.' : '그런 도구는 없습니다. 목록의 도구만 쓰세요.' };
      } else if (tier === 'forbidden') {
        outcome = this.deps.tools.refusal(call.name);
      } else if (tier === 'write' && this.executionIntent) {
        outcome={ok:false,errorCode:'use_task_executor',summary:'작업 실행 계획으로 진행합니다.',content:'이 실행 요청은 propose_task로 앱 작업 실행기에 맡기세요. 직접 쓰기 도구를 재호출하지 마세요.'};
      } else if (tier === 'write') {
        try {
          const binding = this.deps.writeBinding();
          const preview = await this.deps.tools.preview(call.name, call.arguments, () => this.isCurrent(turn, binding));
          this.check(turn, binding);
          this.pending = { call, preview, runId, binding };
          this.emit({ kind: 'confirm', pending: this.pending });
          return true;
        } catch (cause) {
          outcome = refused(cause);
        }
      } else {
        const binding = tier === 'read' ? this.deps.writeBinding() : undefined;
        try {
          if (tier === 'read') {
            const wait = new AbortController(); this.readWait = wait;
            try {
              outcome = await runBoundedAssistantRead(signal => this.deps.tools.runAutomatic(call.name, call.arguments,
                () => !signal.aborted && this.isCurrent(turn, binding)), { timeoutMs: this.deps.readTimeoutMs, signal: wait.signal });
            } finally { if (this.readWait === wait) this.readWait = null; }
          } else {
            outcome = await this.deps.tools.runAutomatic(call.name, call.arguments, () => this.isCurrent(turn, binding));
          }
          this.check(turn, binding);
        } catch (cause) {
          if (cause instanceof StaleTurn) throw cause;
          if (tier === 'read' && cause instanceof AssistantReadWaitError) {
            this.check(turn, binding);
            const subject = call.name === 'read_task_status' ? '작업 상태' : call.name === 'read_app_diagnostics' ? '진단 정보' : '상태 정보';
            const provider = this.deps.provider(), retiredVersion = turn.version + 1;
            const entry: RunEntry = { run_id: runId, tool: call.name, tier: 'read', summary, args_sha256, confirmed: false,
              result: cause.code === 'read_timeout' ? 'error' : 'cancelled', error_code: cause.code, ...(provider ? { provider } : {}) };
            // Start the audit under the still-current account/project, but its POST cannot hold the composer.
            let append: Promise<unknown>;
            try { append = Promise.resolve(this.deps.log(entry)); } catch (failure) { append = Promise.reject(failure); }
            void append.catch(failure => {
              if (this.version === retiredVersion && this.deps.scope() === turn.scope && this.deps.projectScope() === turn.project)
                this.logFailed(failure);
            });
            this.check(turn, binding);
            this.invalidate(`${subject}를 읽는 시간이 길어져 이번 질문을 멈췄습니다. 현재 화면에서 상태를 확인한 뒤 다시 묻거나 새 질문을 입력하세요.`, false);
            return true;
          }
          outcome = refused(cause);
        }
      }
      this.check(turn);
      this.answer(call, outcome);
      this.emit({ kind: 'tool', name: call.name, tier: tier ?? 'unknown', summary: outcome.summary, ok: outcome.ok });
      await this.record(turn, { run_id: runId, tool: call.name, tier: tier ?? 'read', summary, args_sha256, confirmed: false,
        result: outcome.ok ? 'ok' : tier === 'forbidden' || outcome.errorCode !== 'tool_error' ? 'refused' : 'error',
        ...(outcome.errorCode ? { error_code: outcome.errorCode } : {}), ...(outcome.link ? { link: outcome.link } : {}) });
    }
    return false;
  }

  /** The person's answer to the write preview in the panel. Only `true` from this call runs the tool. */
  async decide(confirmed: boolean): Promise<void> {
    const pending = this.pending, turn = this.turn;
    if (!pending || !turn) return;
    try {
      this.check(turn, pending.binding);
      this.pending = null;
      this.emit({ kind: 'confirm', pending: null });
      const { call, runId } = pending;
      if (!confirmed) {
        const outcome: ToolOutcome = { ok: false, errorCode: 'declined_by_person', content: '사람이 실행하지 않기로 했습니다. 다른 방법을 안내하거나 그대로 두세요.', summary: '실행하지 않았습니다.' };
        this.check(turn);
        this.answer(call, outcome);
        this.emit({ kind: 'tool', name: call.name, tier: 'write', summary: outcome.summary, ok: outcome.ok });
        await this.record(turn, { run_id: runId, tool: call.name, tier: 'write', summary: `${call.name}(${argumentSummary(call.arguments)})`.slice(0, 280),
          args_sha256: await sha256Hex(canonicalJson(call.arguments)), confirmed: false, result: 'refused', error_code: 'declined_by_person' });
      } else {
        let acquired = false;
        try { acquired = await this.deps.bridge.beginRun(runId, call.name); }
        catch {
          // A rejected IPC response is not an execution grant; best-effort disarm this owned id.
          try { await this.deps.bridge.endRun(runId); } catch { /* no write was attempted */ }
          this.check(turn);
          await this.record(turn, {run_id: runId, tool: call.name, tier: 'write', summary: call.name,
            args_sha256: await sha256Hex(canonicalJson(call.arguments)), confirmed: true, result: 'refused', error_code: 'run_arm_failed'});
          this.check(turn);
          this.invalidate('실행 확인 연결에 실패해 실행하지 않았습니다. 현재 화면에서 다시 요청하세요.', false);
          return;
        }
        if (!acquired) {
          this.check(turn);
          this.invalidate('실행 확인을 등록하지 못해 실행하지 않았습니다. 현재 화면에서 다시 요청하세요.', false);
          return;
        }
        const provider = this.deps.provider();
        let outcome: ToolOutcome, disarmed = true;
        try {
          this.check(turn, pending.binding);
          this.setWriting(true);
          // Preflight is the full binding; after the mutation only the project matters (provider settings do not change the write).
          outcome = await this.deps.tools.execute(call.name, call.arguments, { confirmed: true, runId, preview: pending.preview,
            isCurrent: Object.assign(() => this.isCurrent(turn, pending.binding), {afterMutation: () => this.deps.projectScope() === turn.project}) });
        } catch (cause) { if (cause instanceof StaleTurn) throw cause; outcome = refused(cause); }
        finally {
          try { await this.deps.bridge.endRun(runId); } catch { disarmed = false; }
          if (this.writing) this.setWriting(false);
        }
        await this.recordWrite(turn, call, runId, outcome, provider, disarmed);
      }
      this.check(turn);
      if (await this.drain(turn)) return;
      await this.loop(turn);
    } catch (cause) { if (!(cause instanceof StaleTurn)) throw cause; }
    finally { if (turn.version === this.version && !this.pending) this.setBusy(false); }
  }
}

function refused(cause: unknown): ToolOutcome {
  if (cause instanceof ToolRefusal) return { ok: false, errorCode: cause.code, content: cause.message, summary: cause.message };
  const error=diagnosticError(cause);
  return {ok:false,errorCode:'tool_error',content:JSON.stringify({error,evidence:'untrusted_error',retry_policy:'verify_existing_result_before_retry'}),summary:`오류: ${error.message.slice(0,160)}`};
}
