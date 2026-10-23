/** Assistant provider clients for the Electron main process (docs/product-direction-easy-labeling-agent.md §4.2–4.4).
 *
 * Two families: an OpenAI-compatible server (Chat Completions or Responses at a base URL the person gives, e.g.
 * https://api.openai.com/v1 or a local http://127.0.0.1:11434/v1) and the Anthropic Messages API. Each call is one
 * non-streaming request with the provider's native function/tool format; the tool loop runs in the renderer, which
 * executes the app's own tools and sends their results back. Keys are added to headers here and nowhere else; errors
 * map to a Korean reason and next action, never to the provider's raw text (which can echo request content).
 *
 * Formats checked against the official references on 2026-10-23: Anthropic `POST /v1/messages` with `x-api-key` and
 * `anthropic-version: 2023-06-01`, tools `{name, description, input_schema}`, `tool_use`/`tool_result` blocks, thinking
 * blocks returned unchanged; OpenAI Responses tools `{type:'function', name, description, parameters}`,
 * `function_call`/`function_call_output` items keyed by `call_id`; Chat Completions `tools[].function` with
 * `tool_calls` and `role:'tool'` results (the long-standing shape the compatible servers implement). */
import type {
  AssistantApiFormat, AssistantError, AssistantErrorCode, AssistantMessage, AssistantProviderId, AssistantToolCall,
  AssistantToolSpec,
} from '../../types/electron';

export interface ProviderConfig {provider:AssistantProviderId;baseUrl:string;model:string;format:AssistantApiFormat|null;supportsImages:boolean}
export interface ImagePart {mediaType:'image/png'|'image/jpeg'|'image/webp';base64:string}
export interface CompletionRequest {
  config:ProviderConfig;key:string|null;system:string;messages:AssistantMessage[];tools:AssistantToolSpec[];
  /** Attached to the last person message only (one image a request, after the person confirmed it). */ image?:ImagePart;
  signal?:AbortSignal;timeoutMs?:number;fetchImpl?:typeof fetch;
}
export interface Completion {text:string;toolCalls:AssistantToolCall[];replay?:unknown;stop:'end'|'tool'|'length';requestId?:string}

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]']);
const ANTHROPIC_VERSION = '2023-06-01';
const ANTHROPIC_MAX_TOKENS = 2048;
const MAX_RESPONSE_CHARS = 4 * 1024 * 1024;

export class ProviderError extends Error {
  constructor(readonly detail:AssistantError) {super(detail.reason);this.name='ProviderError';}
}

const ERROR_TEXT: Record<AssistantErrorCode, [string, string]> = {
  no_provider: ['도우미 공급자가 설정되지 않았습니다.', '도우미 설정에서 공급자와 모델을 고르세요.'],
  no_key: ['이 공급자의 키가 저장되어 있지 않습니다.', '도우미 설정에서 키를 넣으세요.'],
  storage_unavailable: ['이 컴퓨터의 보호 저장소를 쓸 수 없어 키를 저장하거나 읽지 않습니다.', 'OS 키체인(Windows 자격 증명, Linux 비밀 저장소)을 켠 뒤 다시 저장하세요. 키를 평문으로 저장하지 않습니다.'],
  bad_key: ['키가 맞지 않거나 만료되었습니다.', '도우미 설정에서 키를 다시 넣으세요.'],
  forbidden: ['이 키로는 이 모델이나 기능을 쓸 수 없습니다.', '공급자 콘솔에서 권한을 확인하거나 다른 모델을 고르세요.'],
  billing: ['결제나 크레딧 문제로 공급자가 요청을 거절했습니다.', '공급자 콘솔에서 결제 정보를 확인하세요.'],
  not_found: ['기본 주소나 모델 이름을 찾지 못했습니다.', '도우미 설정에서 기본 주소와 모델 이름을 확인하세요.'],
  bad_request: ['공급자가 요청 형식을 받지 않았습니다.', '다른 모델을 고르거나 새 대화로 다시 물어보세요.'],
  rate_limited: ['사용 한도나 요청 빈도 제한에 걸렸습니다.', '잠시 뒤 다시 보내거나 공급자 콘솔에서 한도를 확인하세요.'],
  overloaded: ['공급자 서버가 지금 붐빕니다.', '잠시 뒤 다시 보내세요.'],
  server: ['공급자 서버에 일시적인 문제가 있습니다.', '잠시 뒤 다시 보내세요.'],
  network: ['공급자에 연결하지 못했습니다(인터넷이나 주소 문제).', '인터넷 연결과 기본 주소를 확인하세요. 도우미 없이도 모든 단계를 쓸 수 있습니다.'],
  timeout: ['응답이 제한 시간 안에 오지 않았습니다.', '다시 보내거나 질문을 짧게 줄이세요.'],
  cancelled: ['보내기를 취소했습니다.', '필요하면 다시 보내세요.'],
  bad_response: ['공급자 응답 형식을 읽지 못했습니다.', '요청 형식(Chat Completions/Responses)과 모델을 확인하세요.'],
  invalid_input: ['보낼 내용이 허용 범위를 벗어났습니다.', '대화를 새로 시작하거나 질문을 줄이세요.'],
  image_refused: ['이 이미지는 보내지 않았습니다.', '이미지를 받는 모델로 표시했는지 확인하고, 이미지마다 보내기를 다시 확인하세요.'],
};

export function assistantError(code:AssistantErrorCode, extra:Partial<AssistantError>={}):AssistantError {
  const [reason, action] = ERROR_TEXT[code];
  return {code, reason, action, ...extra};
}

/** A base URL the person typed: HTTPS anywhere, plain HTTP only on this computer (a local or mock server); never
 * credentials, a query or a fragment in it. Returns the normalized base without a trailing slash. */
export function validateBaseUrl(value:unknown):string {
  if (typeof value !== 'string' || !value.trim() || value.length > 2048) throw new Error('기본 주소를 입력하세요. 예: https://api.openai.com/v1');
  let url:URL;
  try {url = new URL(value.trim());} catch {throw new Error('기본 주소 형식이 올바르지 않습니다. https://로 시작하는 주소를 넣으세요.');}
  if (url.username || url.password || url.search || url.hash) throw new Error('기본 주소에는 계정 정보·질의·# 부분을 넣지 마세요. 키는 키 칸에 따로 넣으세요.');
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOOPBACK.has(url.hostname))) throw new Error('공급자 주소는 https://만 쓸 수 있습니다. 이 컴퓨터의 로컬 서버(127.0.0.1, localhost)만 http://를 쓸 수 있습니다.');
  return url.origin + url.pathname.replace(/\/+$/, '');
}

export function providerLabel(config:Pick<ProviderConfig,'provider'|'baseUrl'>):string {
  if (config.provider === 'anthropic') return 'Anthropic';
  let host = '';
  try {host = new URL(config.baseUrl).host;} catch {/* label without a host */}
  return host === 'api.openai.com' ? 'OpenAI' : `OpenAI 호환 서버(${host || '주소 미설정'})`;
}

const anthropicBase = (baseUrl:string) => baseUrl.replace(/\/v1$/, '');

// ---- request builders (pure: the key is added by `send`) ----

type Json = Record<string, unknown>;
const imageDataUrl = (image:ImagePart) => `data:${image.mediaType};base64,${image.base64}`;
const lastUserIndex = (messages:AssistantMessage[]) => {
  for (let index = messages.length - 1; index >= 0; index--) if (messages[index].role === 'user') return index;
  return -1;
};

export function buildChatBody(request:CompletionRequest):Json {
  const imageAt = request.image ? lastUserIndex(request.messages) : -1;
  const messages:Json[] = [{role: 'system', content: request.system}];
  request.messages.forEach((message, index) => {
    if (message.role === 'user') {
      messages.push(index === imageAt && request.image
        ? {role: 'user', content: [{type: 'text', text: message.text}, {type: 'image_url', image_url: {url: imageDataUrl(request.image)}}]}
        : {role: 'user', content: message.text});
    } else if (message.role === 'assistant') {
      messages.push({role: 'assistant', content: message.text || (message.toolCalls?.length ? null : ''), ...(message.toolCalls?.length ? {tool_calls: message.toolCalls.map(call => ({
        id: call.id, type: 'function', function: {name: call.name, arguments: JSON.stringify(call.arguments)}}))} : {})});
    } else {
      messages.push({role: 'tool', tool_call_id: message.toolCallId, content: message.content});
    }
  });
  // No max_tokens: newer OpenAI models reject it in favour of max_completion_tokens, which compatible servers may not know.
  return {model: request.config.model, messages, ...(request.tools.length ? {tools: request.tools.map(tool => ({
    type: 'function', function: {name: tool.name, description: tool.description, parameters: tool.parameters}})), tool_choice: 'auto'} : {})};
}

// The provider's own items of a tool-using turn, sent back unchanged: Anthropic content blocks (thinking must come back
// exactly as received) and Responses output items (reasoning items, with their encrypted content because store:false
// keeps nothing on the server, belong next to their function calls).
const REPLAY_BLOCKS = new Set(['text', 'thinking', 'redacted_thinking', 'tool_use']);
const REPLAY_ITEMS = new Set(['reasoning', 'message', 'function_call']);
function validReplay(value:unknown, kinds:Set<string>):value is Json[] {
  return Array.isArray(value) && value.length > 0 && value.length <= 64
    && value.every(block => block && typeof block === 'object' && kinds.has(String((block as Json).type)));
}

export function buildResponsesBody(request:CompletionRequest):Json {
  const imageAt = request.image ? lastUserIndex(request.messages) : -1;
  const input:Json[] = [];
  request.messages.forEach((message, index) => {
    if (message.role === 'user') {
      input.push({role: 'user', content: [{type: 'input_text', text: message.text},
        ...(index === imageAt && request.image ? [{type: 'input_image', image_url: imageDataUrl(request.image)}] : [])]});
    } else if (message.role === 'assistant') {
      if (validReplay(message.replay, REPLAY_ITEMS)) {input.push(...message.replay);return;}
      if (message.text) input.push({role: 'assistant', content: message.text});
      // Without stored items they go without ids: with store:false the server keeps nothing, and call_id pairs the output.
      for (const call of message.toolCalls || []) input.push({type: 'function_call', call_id: call.id, name: call.name, arguments: JSON.stringify(call.arguments)});
    } else {
      input.push({type: 'function_call_output', call_id: message.toolCallId, output: message.content});
    }
  });
  return {model: request.config.model, instructions: request.system, input, store: false, include: ['reasoning.encrypted_content'],
    ...(request.tools.length ? {tools: request.tools.map(tool => ({type: 'function', name: tool.name, description: tool.description, parameters: tool.parameters}))} : {})};
}

export function buildAnthropicBody(request:CompletionRequest):Json {
  const imageAt = request.image ? lastUserIndex(request.messages) : -1;
  const turns:{role:'user'|'assistant';content:Json[]}[] = [];
  const push = (role:'user'|'assistant', blocks:Json[]) => {
    const last = turns[turns.length - 1];
    // The Messages API alternates roles: tool results and the next question share one user turn.
    if (last && last.role === role && role === 'user') last.content.push(...blocks);
    else turns.push({role, content: blocks});
  };
  request.messages.forEach((message, index) => {
    if (message.role === 'user') {
      push('user', [{type: 'text', text: message.text}, ...(index === imageAt && request.image
        ? [{type: 'image', source: {type: 'base64', media_type: request.image.mediaType, data: request.image.base64}}] : [])]);
    } else if (message.role === 'assistant') {
      // Thinking blocks must come back exactly as received when the turn used a tool, so the stored blocks win.
      const blocks = validReplay(message.replay, REPLAY_BLOCKS) ? message.replay : [
        ...(message.text ? [{type: 'text', text: message.text}] : []),
        ...(message.toolCalls || []).map(call => ({type: 'tool_use', id: call.id, name: call.name, input: call.arguments})),
      ];
      if (blocks.length) push('assistant', blocks);
    } else {
      push('user', [{type: 'tool_result', tool_use_id: message.toolCallId, content: message.content, ...(message.isError ? {is_error: true} : {})}]);
    }
  });
  return {model: request.config.model, max_tokens: ANTHROPIC_MAX_TOKENS, system: request.system, messages: turns,
    ...(request.tools.length ? {tools: request.tools.map(tool => ({name: tool.name, description: tool.description, input_schema: tool.parameters}))} : {})};
}

// ---- response parsers ----

function parseArguments(raw:unknown):{arguments:Record<string,unknown>;argumentsError?:boolean} {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return {arguments: raw as Record<string,unknown>};
  if (typeof raw === 'string') {
    try {
      const value = raw.trim() ? JSON.parse(raw) : {};
      if (value && typeof value === 'object' && !Array.isArray(value)) return {arguments: value};
    } catch {/* reported below */}
  }
  return {arguments: {}, argumentsError: true};
}

export function parseChat(value:any):Completion {
  const choice = value?.choices?.[0];
  const message = choice?.message;
  if (!message || typeof message !== 'object') throw new ProviderError(assistantError('bad_response'));
  const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls
    .filter((call:any) => call?.type === 'function' && typeof call.id === 'string' && typeof call.function?.name === 'string')
    .map((call:any) => ({id: call.id, name: call.function.name, ...parseArguments(call.function.arguments)})) : [];
  const text = typeof message.content === 'string' ? message.content : '';
  return {text, toolCalls, stop: toolCalls.length ? 'tool' : choice.finish_reason === 'length' ? 'length' : 'end'};
}

export function parseResponses(value:any):Completion {
  if (!value || !Array.isArray(value.output)) throw new ProviderError(assistantError('bad_response'));
  const texts:string[] = [];const toolCalls:AssistantToolCall[] = [];
  for (const item of value.output) {
    if (item?.type === 'message' && Array.isArray(item.content)) {
      for (const part of item.content) if (part?.type === 'output_text' && typeof part.text === 'string') texts.push(part.text);
    } else if (item?.type === 'function_call' && typeof item.call_id === 'string' && typeof item.name === 'string') {
      toolCalls.push({id: item.call_id, name: item.name, ...parseArguments(item.arguments)});
    }
  }
  const truncated = value.status === 'incomplete' && value.incomplete_details?.reason === 'max_output_tokens';
  const replay = value.output.filter((item:any) => REPLAY_ITEMS.has(item?.type));
  return {text: texts.join('\n'), toolCalls, replay: toolCalls.length ? replay : undefined, stop: toolCalls.length ? 'tool' : truncated ? 'length' : 'end'};
}

export function parseAnthropic(value:any):Completion {
  if (!value || !Array.isArray(value.content)) throw new ProviderError(assistantError('bad_response'));
  const texts:string[] = [];const toolCalls:AssistantToolCall[] = [];
  for (const block of value.content) {
    if (block?.type === 'text' && typeof block.text === 'string') texts.push(block.text);
    else if (block?.type === 'tool_use' && typeof block.id === 'string' && typeof block.name === 'string') toolCalls.push({id: block.id, name: block.name, ...parseArguments(block.input)});
  }
  const replay = value.content.filter((block:any) => REPLAY_BLOCKS.has(block?.type));
  return {text: texts.join('\n'), toolCalls, replay: toolCalls.length ? replay : undefined,
    stop: value.stop_reason === 'tool_use' || toolCalls.length ? 'tool' : value.stop_reason === 'max_tokens' ? 'length' : 'end'};
}

// ---- transport ----

export function mapHttpError(status:number, body:any, requestId?:string):AssistantError {
  const type = typeof body?.error?.type === 'string' ? body.error.type : typeof body?.error?.code === 'string' ? body.error.code : undefined;
  const detail = type ? String(type).slice(0, 80) : `HTTP ${status}`;
  const code:AssistantErrorCode = status === 401 ? 'bad_key' : status === 402 ? 'billing' : status === 403 ? 'forbidden'
    : status === 404 ? 'not_found' : status === 429 ? 'rate_limited' : status === 529 || type === 'overloaded_error' ? 'overloaded'
    : status >= 500 ? 'server' : 'bad_request';
  return assistantError(code, {status, detail, ...(requestId ? {requestId} : {})});
}

function headersFor(config:ProviderConfig, key:string|null):Record<string,string> {
  if (config.provider === 'anthropic') return {'content-type': 'application/json', 'anthropic-version': ANTHROPIC_VERSION, ...(key ? {'x-api-key': key} : {})};
  return {'content-type': 'application/json', ...(key ? {authorization: `Bearer ${key}`} : {})};
}

async function call(url:string, init:{method:string;headers:Record<string,string>;body?:string}, options:{signal?:AbortSignal;timeoutMs:number;fetchImpl?:typeof fetch}):Promise<{value:any;requestId?:string}> {
  const timeout = AbortSignal.timeout(options.timeoutMs);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  let response:Response;
  try {
    // Redirects are refused: a redirect could carry the key header to another host.
    response = await (options.fetchImpl || fetch)(url, {...init, signal, redirect: 'error'});
  } catch {
    if (options.signal?.aborted) throw new ProviderError(assistantError('cancelled'));
    if (timeout.aborted) throw new ProviderError(assistantError('timeout'));
    throw new ProviderError(assistantError('network'));
  }
  const requestId = response.headers.get('request-id') || response.headers.get('x-request-id') || undefined;
  let text:string;
  try {text = await response.text();} catch {
    if (options.signal?.aborted) throw new ProviderError(assistantError('cancelled'));
    if (timeout.aborted) throw new ProviderError(assistantError('timeout'));
    throw new ProviderError(assistantError('network'));
  }
  if (text.length > MAX_RESPONSE_CHARS) throw new ProviderError(assistantError('bad_response', {requestId}));
  let value:any = null;
  try {value = text ? JSON.parse(text) : null;} catch {/* a non-JSON error page */}
  if (!response.ok) throw new ProviderError(mapHttpError(response.status, value, requestId));
  if (value === null) throw new ProviderError(assistantError('bad_response', {requestId}));
  return {value, requestId};
}

export async function complete(request:CompletionRequest):Promise<Completion> {
  const {config} = request;
  const timeoutMs = request.timeoutMs ?? 90_000;
  const headers = headersFor(config, request.key);
  if (config.provider === 'anthropic') {
    const {value, requestId} = await call(`${anthropicBase(config.baseUrl)}/v1/messages`, {method: 'POST', headers, body: JSON.stringify(buildAnthropicBody(request))}, {signal: request.signal, timeoutMs, fetchImpl: request.fetchImpl});
    return {...parseAnthropic(value), requestId};
  }
  if (config.format === 'responses') {
    const {value, requestId} = await call(`${config.baseUrl}/responses`, {method: 'POST', headers, body: JSON.stringify(buildResponsesBody(request))}, {signal: request.signal, timeoutMs, fetchImpl: request.fetchImpl});
    return {...parseResponses(value), requestId};
  }
  const {value, requestId} = await call(`${config.baseUrl}/chat/completions`, {method: 'POST', headers, body: JSON.stringify(buildChatBody(request))}, {signal: request.signal, timeoutMs, fetchImpl: request.fetchImpl});
  return {...parseChat(value), requestId};
}

/** The provider's model list where it offers one; the person can always type a model id instead. */
export async function listModels(config:ProviderConfig, key:string|null, options:{signal?:AbortSignal;timeoutMs?:number;fetchImpl?:typeof fetch}={}):Promise<{id:string;label:string}[]> {
  const url = config.provider === 'anthropic' ? `${anthropicBase(config.baseUrl)}/v1/models?limit=100` : `${config.baseUrl}/models`;
  const {value} = await call(url, {method: 'GET', headers: headersFor(config, key)}, {signal: options.signal, timeoutMs: options.timeoutMs ?? 15_000, fetchImpl: options.fetchImpl});
  if (!Array.isArray(value?.data)) throw new ProviderError(assistantError('bad_response'));
  return value.data.filter((row:any) => typeof row?.id === 'string' && row.id.length <= 200).slice(0, 500)
    .map((row:any) => ({id: row.id, label: typeof row.display_name === 'string' ? row.display_name : row.id}));
}
