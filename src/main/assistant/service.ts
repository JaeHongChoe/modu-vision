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
  AssistantApiKeyProviderId, AssistantChatgptState, AssistantConfigState, AssistantError, AssistantImageGrant, AssistantMessage, AssistantModelList, AssistantProviderId,
  AssistantProviderInput, AssistantSendInput, AssistantSendResult, AssistantToolSpec,
} from '../../types/electron';
import {AssistantKeyStore} from './keyStore';
import {ProviderError, assistantError, complete, listModels, providerLabel, type Completion, type ImagePart, type ProviderConfig} from './providers';
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
        return {id: call.id, name: call.name, arguments: redactOutgoingValue(call.arguments) as Record<string,unknown>};
      }) : (() => {throw new ProviderError(assistantError('invalid_input', {detail: 'tool_calls'}));})();
      if (raw.replay !== undefined && JSON.stringify(raw.replay).length > LIMITS.replayChars) throw new ProviderError(assistantError('invalid_input', {detail: 'replay'}));
      return {role: 'assistant', text: redactOutgoingText(text(raw.text ?? '')), ...(toolCalls ? {toolCalls} : {}), ...(raw.replay !== undefined ? {replay: raw.replay} : {})};
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

/** Separate main-only connection: no credential accessor is part of the service contract. */
export interface AssistantChatgptConnection {
  state():AssistantChatgptState; authority():string;
  begin(input:{termsAccepted:true;recordId?:string;label?:string;enablePlanUsage?:true}):Promise<AssistantChatgptState>;
  cancel():unknown; selectAccount(id:string|null):unknown; selectModel(id:string,model:string):unknown;
  acknowledgePlanUsage(id:string):unknown;resumeUsage(id:string):unknown;
  recover(input:{ticket:string;confirmed:true}):unknown;
  signOut(id:string):Promise<unknown>; dispose():unknown;
  /** Called only after a login was saved and activated. */
  onConnected?(listener:(recordId:string)=>void):unknown;
  listModels():Promise<{id:string;label:string}[]>;
  complete(input:{system:string;messages:AssistantMessage[];tools:AssistantToolSpec[];signal?:AbortSignal;timeoutMs?:number}):Promise<Completion>;
}
export interface AssistantServiceOptions {store:Pick<AssistantKeyStore,'state'|'save'|'removeKey'|'removeProvider'|'setActive'|'recipient'|'setRecipient'|'config'|'key'>;chatgpt?:AssistantChatgptConnection;fetchImpl?:typeof fetch;now?:()=>number;timeoutMs?:number}

export class AssistantService {
  private readonly running = new Map<string, AbortController>();
  private generation=0;
  private readonly grants = new Map<string, {receiver:string;imageIdentity:string;expires:number}>();
  /** The person's explicit recipient choice is persisted (store.recipient), so a failed login or a restart cannot change it.
   *  A started login is not a choice: only a saved, activated connection replaces an API choice. */
  constructor(private readonly options:AssistantServiceOptions) {
    options.chatgpt?.onConnected?.(() => {this.retire();this.options.store.setRecipient('chatgpt');});
  }

  private receiver(config:ProviderConfig):string {return JSON.stringify([config.provider,config.baseUrl,config.model,config.format,config.supportsImages]);}

  private now():number {return this.options.now ? this.options.now() : Date.now();}

  /** Settings calls throw a plain Error whose message is the Korean reason and action, for the panel to show. */
  private settings<T>(action:()=>T):T {
    try {return action();}
    catch (cause) {
      if (cause instanceof ProviderError) throw new Error(`${cause.detail.reason} ${cause.detail.action}`);
      throw cause;
    }
  }
  state():AssistantConfigState {
    return this.settings(() => {
      const state = this.options.store.state(), connection = this.options.chatgpt;
      if (!connection) return state;
      let chatgpt:AssistantChatgptState;
      try {chatgpt = {...connection.state(), authority: connection.authority()};}
      catch {chatgpt = {accounts: [], active: null, encryptionAvailable: state.encryptionAvailable, status: 'error',
        error: {code: 'storage_unavailable', reason: 'ChatGPT 보호 저장소를 열지 못했습니다.', action: '다른 앱 인스턴스와 OS 보호 저장소를 확인하세요. API 키 방식은 계속 쓸 수 있습니다.'}};}
      const account = chatgpt.accounts.find(item => item.recordId === chatgpt.active), recipient = this.options.store.recipient();
      // Settings saved before the choice was persisted: an active account was the choice; an unreadable store still fails closed.
      if (!recipient && account) try {this.options.store.setRecipient('chatgpt');} catch {/* read again on the next call */}
      const selected = recipient ? recipient === 'chatgpt' : Boolean(account) || chatgpt.status === 'error' || !chatgpt.encryptionAvailable;
      return {...state, chatgpt, ...(selected ? {active: 'chatgpt' as const, providers: [...state.providers,
        ...(account ? [
        {provider: 'chatgpt' as const, baseUrl: 'https://api.openai.com/v1', model: account.model, format: 'responses' as const,
          label: `ChatGPT · ${account.label}`, hasKey: false, supportsImages: false,
          ready: account.signedIn && account.planUsageEnabled && !account.usagePaused && Boolean(account.model) && chatgpt.status !== 'waiting' && chatgpt.status !== 'exchanging' && chatgpt.status !== 'error', connectionAuthority: chatgpt.authority}] : [])]} : {})};
    });
  }
  private retire():void {this.generation++;this.grants.clear();for (const controller of this.running.values()) controller.abort();}
  /** A credential owner may revoke grants and sends in sibling web tabs without changing their selections. */
  invalidateAuthority():void {this.retire();}
  private chooseApiRecipient():void {
    this.options.store.setRecipient('api');
    // The explicit API choice remains usable even when the separate OAuth store is locked or unavailable.
    try {this.options.chatgpt?.selectAccount(null);} catch {this.options.chatgpt?.cancel();}
  }
  save(input:AssistantProviderInput):AssistantConfigState {return this.settings(() => {this.retire();this.options.store.save(input);if (input.activate !== false) this.chooseApiRecipient();return this.state();});}
  removeKey(provider:AssistantProviderId):AssistantConfigState {return this.settings(() => {this.retire();if (provider !== 'chatgpt') this.options.store.removeKey(provider);return this.state();});}
  removeProvider(provider:AssistantProviderId):AssistantConfigState {return this.settings(() => {this.retire();if (provider !== 'chatgpt') this.options.store.removeProvider(provider);return this.state();});}
  setActive(provider:AssistantProviderId):AssistantConfigState {return this.settings(() => {this.retire();if (provider !== 'chatgpt') {this.options.store.setActive(provider);this.chooseApiRecipient();}else {const connection=this.connection(),id=connection.state().active;if (!id) throw new Error('먼저 ChatGPT 계정을 직접 선택하세요.');connection.selectAccount(id);this.options.store.setRecipient('chatgpt');}return this.state();});}
  private connection():AssistantChatgptConnection {if (!this.options.chatgpt) throw new Error('이 앱 버전에서는 ChatGPT 연결을 쓸 수 없습니다.');return this.options.chatgpt;}
  async chatgptBegin(input:{termsAccepted:true;recordId?:string;label?:string;enablePlanUsage?:true}):Promise<AssistantConfigState> {this.retire();await this.connection().begin(input);return this.state();}
  async chatgptAcknowledgePlanUsage(id:string):Promise<AssistantConfigState> {await this.connection().acknowledgePlanUsage(id);return this.state();}
  async chatgptResumeUsage(id:string):Promise<AssistantConfigState> {this.retire();await this.connection().resumeUsage(id);return this.state();}
  async chatgptRecover(input:{ticket:string;confirmed:true}):Promise<AssistantConfigState> {await this.connection().recover(input);this.retire();return this.state();}
  async chatgptCancel():Promise<AssistantConfigState> {this.retire();await this.connection().cancel();return this.state();}
  async chatgptSelectAccount(id:string|null):Promise<AssistantConfigState> {this.retire();await this.connection().selectAccount(id);this.options.store.setRecipient(id === null ? 'api' : 'chatgpt');return this.state();}
  async chatgptSelectModel(id:string,model:string):Promise<AssistantConfigState> {this.retire();await this.connection().selectModel(id,model);this.options.store.setRecipient('chatgpt');return this.state();}
  async chatgptSignOut(id:string):Promise<AssistantConfigState> {this.retire();await this.connection().signOut(id);return this.state();}
  dispose():void {this.retire();this.options.chatgpt?.dispose();}

  /** The configuration and key for one call; a key is optional only for a server on this computer. */
  private credentials(provider?:AssistantApiKeyProviderId|null):{config:ProviderConfig;key:string|null} {
    const config = this.options.store.config(provider);
    if (!config) throw new ProviderError(assistantError('no_provider'));
    const key = this.options.store.key(config.provider);
    const local = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(config.baseUrl);
    if (!key && !local) throw new ProviderError(assistantError('no_key'));
    return {config, key};
  }

  async listModels(provider:AssistantProviderId):Promise<AssistantModelList> {
    const generation=this.generation;
    try {
      let models:{id:string;label:string}[];
      if (provider === 'chatgpt') models=await this.connection().listModels();
      else {const {config, key} = this.credentials(provider);models=await listModels(config, key, {fetchImpl: this.options.fetchImpl});}
      if(generation!==this.generation)throw new ProviderError(assistantError('cancelled'));
      return {ok:true,models};
    } catch (cause) {return {ok: false, error: toError(cause)};}
  }

  /** Step one of an image question: the panel shows `message` and sends the image only after the person confirms. */
  imageIntent(input:{fileName?:unknown;imageIdentity?:unknown}):AssistantImageGrant {
    try {
      if (this.state().active === 'chatgpt') throw new ProviderError(assistantError('image_refused', {reason: 'ChatGPT 연결은 현재 텍스트 질문만 지원합니다.', action: '이미지 질문은 이미지 지원 API 키 공급자를 선택하고 전송을 확인하세요.'}));
      const config = this.options.store.config();
      if (!config) throw new ProviderError(assistantError('no_provider'));
      if (!config.supportsImages) throw new ProviderError(assistantError('image_refused', {detail: 'model_not_marked_for_images'}));
      if (typeof input?.imageIdentity !== 'string' || !/^[a-f0-9]{64}$/.test(input.imageIdentity))
        throw new ProviderError(assistantError('image_refused', {detail: 'image_identity_required'}));
      const now = this.now();
      for (const [id, grant] of this.grants) if (grant.expires <= now) this.grants.delete(id);
      const grantId = `img_${crypto.randomBytes(12).toString('hex')}`;
      const label = providerLabel(config);
      this.grants.set(grantId, {receiver: this.receiver(config), imageIdentity: input.imageIdentity, expires: now + LIMITS.grantMs});
      const name = typeof input?.fileName === 'string' ? redactOutgoingText(input.fileName).slice(0, 120) : '';
      return {ok: true, grantId, providerLabel: label, expiresAt: now + LIMITS.grantMs,
        message: `이 이미지 1장${name ? `(${name})` : ''}을 ${label}로 보냅니다. 보낸 이미지는 공급자의 정책에 따라 처리되며 사용량이 더 듭니다.`};
    } catch (cause) {return {ok: false, error: toError(cause)};}
  }

  private takeImage(image:unknown, config:ProviderConfig):ImagePart {
    const value = image as {grantId?:unknown;imageIdentity?:unknown;mediaType?:unknown;base64?:unknown};
    const grant = typeof value?.grantId === 'string' ? this.grants.get(value.grantId) : undefined;
    if (typeof value?.grantId === 'string') this.grants.delete(value.grantId);  // one image per confirmation, used or not
    if (!grant || grant.expires <= this.now() || grant.receiver !== this.receiver(config) || grant.imageIdentity !== value.imageIdentity)
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
      const messages = sanitizedMessages(input.messages);
      const tools = sanitizedTools(input.tools ?? []);
      const system = systemPrompt(input.context);
      const state = this.state();
      if (state.active === 'chatgpt') {
        const receiver = state.providers.find(item => item.provider === 'chatgpt');
        if (!receiver?.ready) throw new ProviderError(assistantError('no_provider', {reason: 'ChatGPT 플랜 사용과 모델 선택이 아직 준비되지 않았습니다.', action: '도우미 설정에서 로그인·플랜 동의와 모델 선택을 확인하세요.'}));
        if (input.image !== undefined) throw new ProviderError(assistantError('image_refused'));
        const connection = this.connection(), authority = connection.authority();
        const result = await connection.complete({system, messages, tools, signal: controller.signal, timeoutMs: this.options.timeoutMs});
        if (controller.signal.aborted || authority !== connection.authority()) throw new ProviderError(assistantError('cancelled'));
        return {ok: true, ...result};
      }
      const {config, key} = this.credentials();
      const image = input.image !== undefined ? this.takeImage(input.image, config) : undefined;
      if (image && messages[messages.length - 1]?.role !== 'user') throw new ProviderError(assistantError('image_refused', {detail: 'image_needs_question'}));
      const result = await complete({config, key, system, messages, tools, image, signal: controller.signal,
        timeoutMs: this.options.timeoutMs, fetchImpl: this.options.fetchImpl});
      if (controller.signal.aborted) throw new ProviderError(assistantError('cancelled'));
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
