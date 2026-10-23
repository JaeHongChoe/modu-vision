/** One conversation with the built-in assistant: the question → provider → tools → provider loop (§4.1, §4.5, §4.6).
 *
 * The provider is called through the main process (the host adapter's assistant bridge, send); this side owns the tools. A question
 * freezes its screen context and its trimmed history, so every provider call of that question sees the same prefix
 * (Anthropic only accepts its replayed thinking blocks under an unchanged prefix). Read and navigate tools run at once;
 * a write tool stops the loop at its preview until the person confirms or declines in the panel; a forbidden tool is
 * answered "직접 하세요". Every provider call and every tool call is one line in the project's run log. An image
 * question is a single call without tools, so its pixels leave exactly once for the one confirmation the person gave.
 *
 * Dependencies are injected (bridge, tools, context, log) so node tests drive it with fakes. */
import type { AssistantBridge, AssistantError, AssistantImageInput, AssistantMessage, AssistantProviderId, AssistantToolCall } from '../../types/electron';
import { argumentSummary, canonicalJson, randomHex, sha256Hex, trimConversation } from './assistantContext';
import { ToolRefusal, type AssistantToolRegistry, type ToolOutcome, type ToolPreview, type ToolTier } from './assistantTools';
import type { RunEntry } from './assistantLog';

export type SessionEvent =
  | { kind: 'assistant'; text: string }
  | { kind: 'tool'; name: string; tier: ToolTier | 'unknown'; summary: string; ok: boolean }
  | { kind: 'error'; error: AssistantError }
  | { kind: 'notice'; text: string }
  | { kind: 'busy'; busy: boolean }
  | { kind: 'confirm'; pending: PendingWrite | null };

export interface PendingWrite { call: AssistantToolCall; preview: ToolPreview; runId: string }

export interface SessionDeps {
  bridge: Pick<AssistantBridge, 'send' | 'cancel' | 'beginRun' | 'endRun'>;
  tools: AssistantToolRegistry;
  context(): Record<string, unknown>;
  log(entry: RunEntry): Promise<unknown>;
  provider(): AssistantProviderId | null;
}

const MAX_PROVIDER_CALLS = 6;
const MAX_TOOL_RESULT_CHARS = 4_000;
const newRunId = () => `run_${randomHex(12)}`;
const clipResult = (text: string) => (text.length > MAX_TOOL_RESULT_CHARS ? `${text.slice(0, MAX_TOOL_RESULT_CHARS - 1)}…` : text);

export class AssistantConversation {
  /** Finished turns, as sent to the provider (images replaced by a note). */
  readonly messages: AssistantMessage[] = [];
  pending: PendingWrite | null = null;
  busy = false;
  private history: AssistantMessage[] = [];
  private current: AssistantMessage[] = [];
  private frozenContext: Record<string, unknown> = {};
  private queue: AssistantToolCall[] = [];
  private calls = 0;
  private inFlight: string | null = null;
  private image: AssistantImageInput | undefined;
  private imageQuestion = false;
  private logWarned = false;

  constructor(private readonly deps: SessionDeps, private readonly emit: (event: SessionEvent) => void) {}

  private setBusy(busy: boolean) { this.busy = busy; this.emit({ kind: 'busy', busy }); }

  private async record(entry: Omit<RunEntry, 'provider'>): Promise<void> {
    const provider = this.deps.provider();
    try { await this.deps.log({ ...entry, ...(provider ? { provider } : {}) }); }
    catch (cause) {
      if (!this.logWarned) {
        this.logWarned = true;
        this.emit({ kind: 'notice', text: `실행 기록을 남기지 못했습니다(${cause instanceof Error ? cause.message : String(cause)}). 프로젝트를 열었는지와 권한을 확인하세요.` });
      }
    }
  }

  /** A new question. `image` is passed only after the person confirmed sending it; the call then carries no tools. */
  async ask(text: string, image?: AssistantImageInput): Promise<void> {
    const question = text.trim();
    if (!question || this.busy || this.pending) return;
    this.history = trimConversation(this.messages);
    this.frozenContext = this.deps.context();
    this.current = [{ role: 'user', text: question }];
    this.calls = 0;this.queue = [];this.image = image;this.imageQuestion = Boolean(image);this.logWarned = false;
    await this.loop();
  }

  /** Stops a provider call in flight; the unfinished question is dropped and can be asked again. */
  async stop(): Promise<void> { if (this.inFlight) await this.deps.bridge.cancel(this.inFlight); }

  private commit() {
    const turn = this.current.map(message => message.role === 'user' && this.imageQuestion && message === this.current[0]
      ? { role: 'user' as const, text: `${message.text}\n(이 질문에는 이미지 1장을 함께 보냈습니다.)` } : message);
    this.messages.push(...turn);
    this.current = [];this.image = undefined;this.imageQuestion = false;
  }

  private async loop(): Promise<void> {
    this.setBusy(true);
    try {
      while (this.calls < MAX_PROVIDER_CALLS) {
        this.calls++;
        const requestId = `req_${randomHex(12)}`;
        this.inFlight = requestId;
        const messages = [...this.history, ...this.current];
        const result = await this.deps.bridge.send({ requestId, context: this.frozenContext, messages,
          tools: this.imageQuestion ? [] : this.deps.tools.specs(), ...(this.image && this.calls === 1 ? { image: this.image } : {}) });
        this.inFlight = null;
        const lastUser = [...this.current].reverse().find(message => message.role === 'user') as { text: string } | undefined;
        await this.record({ run_id: newRunId(), tool: 'conversation', tier: 'conversation',
          summary: this.calls === 1 ? `질문 ${lastUser?.text.length ?? 0}자${this.image ? ' · 이미지 1장' : ''}` : `도구 결과 뒤 ${this.calls}번째 요청`,
          args_sha256: await sha256Hex(canonicalJson({ requestId, count: messages.length })), confirmed: Boolean(this.image && this.calls === 1),
          result: result.ok ? 'ok' : result.error.code === 'cancelled' ? 'cancelled' : 'error', ...(result.ok ? {} : { error_code: result.error.code }) });
        if (!result.ok) {
          this.emit({ kind: 'error', error: result.error });
          // The unanswered question is not kept: the next one starts from the last finished turn.
          this.current = [];this.image = undefined;this.imageQuestion = false;
          return;
        }
        if (result.text) this.emit({ kind: 'assistant', text: result.text });
        this.current.push({ role: 'assistant', text: result.text, ...(result.toolCalls.length ? { toolCalls: result.toolCalls } : {}),
          ...(result.replay !== undefined ? { replay: result.replay } : {}) });
        if (!result.toolCalls.length) { this.commit(); return; }
        this.queue = [...result.toolCalls];
        if (await this.drain()) return;  // waiting for the person
      }
      this.emit({ kind: 'notice', text: '도우미가 도구를 여러 번 불러 이번 질문을 멈췄습니다. 결과를 확인하고 다시 물어보세요.' });
      this.commit();
    } finally {
      this.inFlight = null;
      if (!this.pending) this.setBusy(false);
    }
  }

  private answer(call: AssistantToolCall, outcome: ToolOutcome) {
    this.current.push({ role: 'tool', toolCallId: call.id, name: call.name, content: clipResult(outcome.content), ...(outcome.ok ? {} : { isError: true }) });
  }

  /** Runs the queued calls of one assistant turn; true when a write waits for the person. */
  private async drain(): Promise<boolean> {
    while (this.queue.length) {
      const call = this.queue.shift()!;
      const tier = this.deps.tools.tier(call.name);
      const runId = newRunId();
      const args_sha256 = await sha256Hex(canonicalJson(call.arguments));
      const summary = `${call.name}(${argumentSummary(call.arguments)})`.slice(0, 280);
      let outcome: ToolOutcome;
      if (call.argumentsError || !tier) {
        outcome = { ok: false, errorCode: call.argumentsError ? 'invalid_arguments' : 'unknown_tool', summary: '도구 호출을 읽지 못했습니다.',
          content: call.argumentsError ? '인자를 JSON 객체로 읽지 못했습니다. 다시 호출하세요.' : '그런 도구는 없습니다. 목록의 도구만 쓰세요.' };
      } else if (tier === 'forbidden') {
        outcome = this.deps.tools.refusal(call.name);
      } else if (tier === 'write') {
        try {
          const preview = await this.deps.tools.preview(call.name, call.arguments);
          this.pending = { call, preview, runId };
          this.emit({ kind: 'confirm', pending: this.pending });
          return true;
        } catch (cause) {
          outcome = refused(cause);
        }
      } else {
        try { outcome = await this.deps.tools.runAutomatic(call.name, call.arguments); }
        catch (cause) { outcome = refused(cause); }
      }
      this.answer(call, outcome);
      this.emit({ kind: 'tool', name: call.name, tier: tier ?? 'unknown', summary: outcome.summary, ok: outcome.ok });
      await this.record({ run_id: runId, tool: call.name, tier: tier ?? 'read', summary, args_sha256, confirmed: false,
        result: outcome.ok ? 'ok' : tier === 'forbidden' || outcome.errorCode !== 'tool_error' ? 'refused' : 'error',
        ...(outcome.errorCode ? { error_code: outcome.errorCode } : {}), ...(outcome.link ? { link: outcome.link } : {}) });
    }
    return false;
  }

  /** The person's answer to the write preview in the panel. Only `true` from this call runs the tool. */
  async decide(confirmed: boolean): Promise<void> {
    const pending = this.pending;
    if (!pending) return;
    this.pending = null;
    this.emit({ kind: 'confirm', pending: null });
    const { call, runId } = pending;
    let outcome: ToolOutcome;
    if (!confirmed) {
      outcome = { ok: false, errorCode: 'declined_by_person', content: '사람이 실행하지 않기로 했습니다. 다른 방법을 안내하거나 그대로 두세요.', summary: '실행하지 않았습니다.' };
    } else {
      await this.deps.bridge.beginRun(runId, call.name);
      try { outcome = await this.deps.tools.execute(call.name, call.arguments, { confirmed: true, runId }); }
      catch (cause) { outcome = refused(cause); }
      finally { await this.deps.bridge.endRun(runId); }
    }
    this.answer(call, outcome);
    this.emit({ kind: 'tool', name: call.name, tier: 'write', summary: outcome.summary, ok: outcome.ok });
    await this.record({ run_id: runId, tool: call.name, tier: 'write', summary: `${call.name}(${argumentSummary(call.arguments)})`.slice(0, 280),
      args_sha256: await sha256Hex(canonicalJson(call.arguments)), confirmed, result: outcome.ok ? 'ok' : confirmed && outcome.errorCode === 'tool_error' ? 'error' : 'refused',
      ...(outcome.errorCode ? { error_code: outcome.errorCode } : {}), ...(outcome.link ? { link: outcome.link } : {}) });
    if (await this.drain()) return;
    await this.loop();
  }
}

function refused(cause: unknown): ToolOutcome {
  if (cause instanceof ToolRefusal) return { ok: false, errorCode: cause.code, content: cause.message, summary: cause.message };
  const message = cause instanceof Error ? cause.message : String(cause);
  return { ok: false, errorCode: 'tool_error', content: `도구 실행 중 오류: ${message.slice(0, 400)}`, summary: `오류: ${message.slice(0, 160)}` };
}
