/** The main-process side of the built-in assistant (§4.1–4.6): provider settings, one provider call per renderer
 * request, cancellation and the per-image confirmation grant.
 *
 * The conversation and the tool loop live in the renderer, which owns the app's stores and asks the person before any
 * write. This side is the trust boundary for what leaves the computer: it bounds every request, rebuilds the system
 * instructions itself (the forbidden actions cannot be edited away by the renderer), runs every outgoing text through
 * redact.ts, attaches at most one image and only against a fresh grant the panel showed the person, and is the only
 * place a key is decrypted. Nothing here runs unless the person configured a provider and pressed send. */
import crypto from 'node:crypto';
import type {
  AssistantConfigState, AssistantError, AssistantImageGrant, AssistantMessage, AssistantModelList, AssistantProviderId,
  AssistantProviderInput, AssistantSendInput, AssistantSendResult, AssistantToolSpec,
} from '../../types/electron';
import {AssistantKeyStore} from './keyStore';
import {ProviderError, assistantError, complete, listModels, providerLabel, type ImagePart, type ProviderConfig} from './providers';
import {redactOutgoingText, redactOutgoingValue} from './redact';

const REQUEST_ID = /^req_[a-f0-9]{16,32}$/;
const TOOL_NAME = /^[a-z][a-z0-9_]{1,63}$/;
const LIMITS = {messages: 60, messageChars: 12_000, totalChars: 60_000, tools: 40, toolChars: 24_000, contextChars: 12_000,
  replayChars: 200_000, imageBytes: 5 * 1024 * 1024, concurrent: 2, grantMs: 120_000};
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

export const SYSTEM_INSTRUCTIONS = [
  '당신은 비전 검사 프로그램 Vision AI Studio에 들어 있는 도우미입니다. 사용자는 대부분 처음 쓰는 현장 담당자입니다.',
  '단계: 1 데이터 준비 → 2 라벨 달기 → 3 모델 학습 → 4 결과 평가 → 5 검사 흐름 만들기 → 6 검사 실행.',
  '규칙:',
  '- 아래 "현재 화면 상태"와 읽기 도구의 결과만 근거로, 막힌 이유와 다음 행동 하나를 짧은 한국어로 설명합니다. 모르면 추측하지 말고 읽기 도구로 확인하거나 모른다고 답합니다.',
  '- 도구는 주어진 목록만 씁니다. 읽기·화면 이동 도구는 바로 실행되고, 쓰기 도구는 사람이 미리보기를 보고 확인해야 실행됩니다. 확인 없이 실행되었다고 말하지 않습니다.',
  '- 모델·흐름 승인, 배포·서비스 등록, 원본 이미지나 라벨 파일 삭제, 정답 확정, 초안 채택, 계정·권한 변경, 외부로 파일 보내기는 하지 않습니다. 요청받으면 해당 금지 도구를 불러 "직접 하세요"와 위치를 알려 줍니다.',
  '- 모델이나 도우미가 만든 라벨은 사람이 확인하기 전까지 초안이며 정답이 아닙니다.',
  '- 경로·서버 주소·사람 이름은 받지 않았으니 묻지 말고, 파일 이름과 수로 말합니다.',
].join('\n');

export function systemPrompt(context:Record<string,unknown>):string {
  const json = JSON.stringify(redactOutgoingValue(context ?? {}));
  if (json.length > LIMITS.contextChars) throw new ProviderError(assistantError('invalid_input', {detail: 'context_too_large'}));
  return `${SYSTEM_INSTRUCTIONS}\n\n현재 화면 상태(JSON):\n${json}`;
}

const toError = (cause:unknown):AssistantError => cause instanceof ProviderError ? cause.detail
  : assistantError('invalid_input', {detail: cause instanceof Error ? cause.message.slice(0, 200) : 'invalid'});

/** Checks shape and bounds and returns a copy whose person-made texts are redacted. */
export function sanitizedMessages(messages:unknown):AssistantMessage[] {
  if (!Array.isArray(messages) || !messages.length || messages.length > LIMITS.messages) throw new ProviderError(assistantError('invalid_input', {detail: 'message_count'}));
  let total = 0;
  const text = (value:unknown) => {
    if (typeof value !== 'string' || value.length > LIMITS.messageChars) throw new ProviderError(assistantError('invalid_input', {detail: 'message_text'}));
    total += value.length;return value;
  };
  const result = messages.map((raw:any):AssistantMessage => {
    if (raw?.role === 'user') return {role: 'user', text: redactOutgoingText(text(raw.text))};
    if (raw?.role === 'assistant') {
      const toolCalls = raw.toolCalls === undefined ? undefined : Array.isArray(raw.toolCalls) && raw.toolCalls.length <= 16 ? raw.toolCalls.map((call:any) => {
        if (typeof call?.id !== 'string' || call.id.length > 128 || !TOOL_NAME.test(call?.name) || !call.arguments || typeof call.arguments !== 'object' || Array.isArray(call.arguments))
          throw new ProviderError(assistantError('invalid_input', {detail: 'tool_call'}));
        return {id: call.id, name: call.name, arguments: call.arguments};
      }) : (() => {throw new ProviderError(assistantError('invalid_input', {detail: 'tool_calls'}));})();
      if (raw.replay !== undefined && JSON.stringify(raw.replay).length > LIMITS.replayChars) throw new ProviderError(assistantError('invalid_input', {detail: 'replay'}));
      return {role: 'assistant', text: text(raw.text ?? ''), ...(toolCalls ? {toolCalls} : {}), ...(raw.replay !== undefined ? {replay: raw.replay} : {})};
    }
    if (raw?.role === 'tool' && typeof raw.toolCallId === 'string' && raw.toolCallId.length <= 128 && TOOL_NAME.test(raw.name))
      return {role: 'tool', toolCallId: raw.toolCallId, name: raw.name, content: redactOutgoingText(text(raw.content)), ...(raw.isError ? {isError: true} : {})};
    throw new ProviderError(assistantError('invalid_input', {detail: 'message_role'}));
  });
  if (total > LIMITS.totalChars) throw new ProviderError(assistantError('invalid_input', {detail: 'conversation_too_long'}));
  return result;
}

export function sanitizedTools(tools:unknown):AssistantToolSpec[] {
  if (!Array.isArray(tools) || tools.length > LIMITS.tools) throw new ProviderError(assistantError('invalid_input', {detail: 'tools'}));
  const result = tools.map((tool:any) => {
    if (!TOOL_NAME.test(tool?.name) || typeof tool.description !== 'string' || tool.description.length > 1024 || !tool.parameters || typeof tool.parameters !== 'object')
      throw new ProviderError(assistantError('invalid_input', {detail: 'tool_spec'}));
    return {name: tool.name, description: tool.description, parameters: tool.parameters};
  });
  if (JSON.stringify(result).length > LIMITS.toolChars) throw new ProviderError(assistantError('invalid_input', {detail: 'tools_too_large'}));
  return result;
}

export interface AssistantServiceOptions {store:AssistantKeyStore;fetchImpl?:typeof fetch;now?:()=>number;timeoutMs?:number}

export class AssistantService {
  private readonly running = new Map<string, AbortController>();
  private readonly grants = new Map<string, {provider:AssistantProviderId;model:string;expires:number}>();
  constructor(private readonly options:AssistantServiceOptions) {}

  private now():number {return this.options.now ? this.options.now() : Date.now();}

  /** Settings calls throw a plain Error whose message is the Korean reason and action, for the panel to show. */
  private settings<T>(action:()=>T):T {
    try {return action();}
    catch (cause) {
      if (cause instanceof ProviderError) throw new Error(`${cause.detail.reason} ${cause.detail.action}`);
      throw cause;
    }
  }
  state():AssistantConfigState {return this.settings(() => this.options.store.state());}
  save(input:AssistantProviderInput):AssistantConfigState {return this.settings(() => this.options.store.save(input));}
  removeKey(provider:AssistantProviderId):AssistantConfigState {return this.settings(() => this.options.store.removeKey(provider));}
  removeProvider(provider:AssistantProviderId):AssistantConfigState {return this.settings(() => this.options.store.removeProvider(provider));}
  setActive(provider:AssistantProviderId):AssistantConfigState {return this.settings(() => this.options.store.setActive(provider));}

  /** The configuration and key for one call; a key is optional only for a server on this computer. */
  private credentials(provider?:AssistantProviderId|null):{config:ProviderConfig;key:string|null} {
    const config = this.options.store.config(provider);
    if (!config) throw new ProviderError(assistantError('no_provider'));
    const key = this.options.store.key(config.provider);
    const local = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(config.baseUrl);
    if (!key && !local) throw new ProviderError(assistantError('no_key'));
    return {config, key};
  }

  async listModels(provider:AssistantProviderId):Promise<AssistantModelList> {
    try {
      const {config, key} = this.credentials(provider);
      return {ok: true, models: await listModels(config, key, {fetchImpl: this.options.fetchImpl})};
    } catch (cause) {return {ok: false, error: toError(cause)};}
  }

  /** Step one of an image question: the panel shows `message` and sends the image only after the person confirms. */
  imageIntent(input:{fileName?:unknown}):AssistantImageGrant {
    try {
      const config = this.options.store.config();
      if (!config) throw new ProviderError(assistantError('no_provider'));
      if (!config.supportsImages) throw new ProviderError(assistantError('image_refused', {detail: 'model_not_marked_for_images'}));
      const now = this.now();
      for (const [id, grant] of this.grants) if (grant.expires <= now) this.grants.delete(id);
      const grantId = `img_${crypto.randomBytes(12).toString('hex')}`;
      const label = providerLabel(config);
      this.grants.set(grantId, {provider: config.provider, model: config.model, expires: now + LIMITS.grantMs});
      const name = typeof input?.fileName === 'string' ? redactOutgoingText(input.fileName).slice(0, 120) : '';
      return {ok: true, grantId, providerLabel: label, expiresAt: now + LIMITS.grantMs,
        message: `이 이미지 1장${name ? `(${name})` : ''}을 ${label}로 보냅니다. 보낸 이미지는 공급자의 정책에 따라 처리되며 사용량이 더 듭니다.`};
    } catch (cause) {return {ok: false, error: toError(cause)};}
  }

  private takeImage(image:unknown, config:ProviderConfig):ImagePart {
    const value = image as {grantId?:unknown;mediaType?:unknown;base64?:unknown};
    const grant = typeof value?.grantId === 'string' ? this.grants.get(value.grantId) : undefined;
    if (typeof value?.grantId === 'string') this.grants.delete(value.grantId);  // one image per confirmation, used or not
    if (!grant || grant.expires <= this.now() || grant.provider !== config.provider || grant.model !== config.model)
      throw new ProviderError(assistantError('image_refused', {detail: 'confirmation_missing_or_expired'}));
    if (!config.supportsImages) throw new ProviderError(assistantError('image_refused', {detail: 'model_not_marked_for_images'}));
    if (typeof value.mediaType !== 'string' || !IMAGE_TYPES.has(value.mediaType) || typeof value.base64 !== 'string'
        || !/^[A-Za-z0-9+/]+={0,2}$/.test(value.base64) || Math.floor(value.base64.length * 3 / 4) > LIMITS.imageBytes)
      throw new ProviderError(assistantError('image_refused', {detail: 'image_format_or_size'}));
    return {mediaType: value.mediaType as ImagePart['mediaType'], base64: value.base64};
  }

  async send(input:AssistantSendInput):Promise<AssistantSendResult> {
    const requestId = typeof input?.requestId === 'string' && REQUEST_ID.test(input.requestId) ? input.requestId : null;
    if (!requestId) return {ok: false, error: assistantError('invalid_input', {detail: 'request_id'})};
    if (this.running.size >= LIMITS.concurrent || this.running.has(requestId)) return {ok: false, error: assistantError('invalid_input', {detail: 'busy'})};
    const controller = new AbortController();
    this.running.set(requestId, controller);
    try {
      const {config, key} = this.credentials();
      const messages = sanitizedMessages(input.messages);
      const tools = sanitizedTools(input.tools ?? []);
      const system = systemPrompt(input.context);
      const image = input.image !== undefined ? this.takeImage(input.image, config) : undefined;
      if (image && messages[messages.length - 1]?.role !== 'user') throw new ProviderError(assistantError('image_refused', {detail: 'image_needs_question'}));
      const result = await complete({config, key, system, messages, tools, image, signal: controller.signal,
        timeoutMs: this.options.timeoutMs, fetchImpl: this.options.fetchImpl});
      return {ok: true, text: result.text, toolCalls: result.toolCalls, stop: result.stop,
        ...(result.replay !== undefined ? {replay: result.replay} : {}), ...(result.requestId ? {requestId: result.requestId} : {})};
    } catch (cause) {
      return {ok: false, error: toError(cause)};
    } finally {
      this.running.delete(requestId);
    }
  }

  cancel(requestId:unknown):boolean {
    const controller = typeof requestId === 'string' ? this.running.get(requestId) : undefined;
    controller?.abort();
    return Boolean(controller);
  }
}
