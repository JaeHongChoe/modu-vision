/** Offline SIWC protocol building blocks, deliberately NOT imported by providers/service/IPC/UI.
 * No fetch, listener, browser, credential files or automatic retries. All transport and trusted JWKS are supplied.
 * Live integration, discovery/JWKS acquisition, protected persistence and user consent remain separate work.
 * Contracts checked 2026-10-24 against official OpenAI documentation:
 * https://developers.openai.com/siwc/token-sharing-open-source/sign-in
 * https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
 * https://developers.openai.com/siwc/token-sharing-open-source/token-reference
 * https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
 * https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
 * https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
 * https://developers.openai.com/api/docs/guides/tools-tool-search (namespace call shape only, no tool_search)
 * https://developers.openai.com/api/reference/resources/responses/methods/create (phase and reasoning defaults)
 */
import crypto, {type JsonWebKey} from 'node:crypto';
import type {AssistantErrorDiagnostics, AssistantToolSpec} from '../../types/electron';
import {redactOutgoingText, redactOutgoingValue} from './redact';

const ISSUER = 'https://auth.openai.com', RESOURCE = 'https://api.openai.com/v1';
const TOKEN_ENDPOINT = `${ISSUER}/api/accounts/oauth/token`;
type Json = Record<string, any>;
type Recovery = 'sign_in' | 'configure_client' | 'retry_later' | 'manage_usage' | 'fix_request' | 'inspect_credentials';
export class SiwcProtocolError extends Error {
  constructor(readonly code: string, readonly recovery: Recovery = 'fix_request', readonly status?: number, readonly diagnostics?:AssistantErrorDiagnostics) {
    super(`SIWC protocol: ${code}`);this.name = 'SiwcProtocolError';
  }
}
const fail = (code: string): never => {throw new SiwcProtocolError(code);};
const object = (value: unknown): Json => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Json : {};
const nonempty = (value: unknown, max = 16384): value is string => typeof value === 'string' && value.length > 0 && value.length <= max;
const client = (value: unknown): value is string => typeof value === 'string' && /^oaiapp_[A-Za-z0-9_-]{1,200}$/.test(value);
const equal = (a: string, b: string) => Buffer.byteLength(a) === Buffer.byteLength(b) && crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
const clone = <T>(value: T): T => structuredClone(value);

export function pkceChallenge(verifier: string): string {
  if (!/^[A-Za-z0-9._~-]{43,128}$/.test(verifier)) fail('invalid_verifier');
  return crypto.createHash('sha256').update(verifier).digest('base64url');
}
export interface SiwcCodeGrant {clientId: string; nonce: string; form: URLSearchParams}
export function createAuthorizationAttempt(options: {hostId: string; appName: string; redirectUri: string; clientId?: string; promptConsent?:boolean; now?: () => number}) {
  const redirect = new URL(options.redirectUri);
  if (redirect.protocol !== 'http:' || redirect.hostname !== '127.0.0.1' || !redirect.port || redirect.pathname !== '/auth/callback'
    || redirect.username || redirect.password || redirect.search || redirect.hash) fail('invalid_callback_uri');
  if (!/^urn:uuid:[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(options.hostId)) fail('invalid_host_id');
  if (!nonempty(options.appName, 100) || (options.clientId !== undefined && !client(options.clientId))) fail('invalid_registration');
  const now = options.now || Date.now, created = now(), state = crypto.randomBytes(32).toString('base64url');
  const nonce = crypto.randomBytes(32).toString('base64url'), verifier = crypto.randomBytes(32).toString('base64url');
  const url = new URL(`${ISSUER}/api/accounts/authorize`);
  url.search = new URLSearchParams({client_id: options.clientId || 'dynamic_agent_client', ext_agent_host_id: options.hostId,
    ...(!options.clientId ? {agent_name_hint: options.appName} : {}), ...(options.promptConsent===true?{prompt:'consent'}:{}), response_type: 'code', redirect_uri: redirect.href,
    scope: 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct', resource: RESOURCE,
    state, nonce, code_challenge_method: 'S256', code_challenge: pkceChallenge(verifier)}).toString();
  let used = false;
  return {authorizationUrl: url.href, nonce, acceptCallback(callbackUrl: string): SiwcCodeGrant {
    if (used) fail('attempt_used');
    // Five minutes is this module's local attempt lifetime, not an asserted provider token lifetime.
    if (now() - created > 300000 || now() < created) fail('attempt_expired');
    const callback = new URL(callbackUrl);
    if (callback.origin !== redirect.origin || callback.pathname !== redirect.pathname || callback.username || callback.password || callback.hash) fail('invalid_callback_uri');
    used = true; // a callback reaching this attempt is consumed, including a failed state/client/code validation
    for (const name of ['state', 'code', 'client_id', 'error']) if (callback.searchParams.getAll(name).length > 1) fail('duplicate_callback_parameter');
    const returnedState = callback.searchParams.get('state');
    if (!returnedState || !equal(returnedState, state)) fail('invalid_state');
    if (callback.searchParams.has('error')) {used = true;fail(callback.searchParams.get('error') === 'access_denied' ? 'access_denied' : 'authorization_failed');}
    const returnedClient = callback.searchParams.get('client_id'), issued = options.clientId || returnedClient;
    if (!client(issued)) return fail('invalid_client_binding');
    if (options.clientId && returnedClient && returnedClient !== options.clientId) fail('invalid_client_binding');
    const code = callback.searchParams.get('code');if (!nonempty(code, 8192)) return fail('missing_code');
    used = true;
    return {clientId: issued, nonce, form: new URLSearchParams({grant_type: 'authorization_code', client_id: issued,
      code, code_verifier: verifier, redirect_uri: redirect.href, resource: RESOURCE})};
  }};
}

export interface SiwcIdentity {issuer: string; subject: string; clientId: string}
interface IdExpectation {clientId: string; nonce?: string; nowMs?: number; expectedSubject?: string}
/** JWKS must come from a trusted issuer discovery flow. This function verifies a signature, never trusts decoded claims alone.
 * Only RS256 and ES256 are implemented here; another published algorithm needs explicit support before live use. */
export function verifySiwcIdToken(token: string, jwks: {keys: JsonWebKey[]}, expected: IdExpectation): SiwcIdentity {
  try {
    if (!nonempty(token, 65536) || !client(expected.clientId)) return fail('invalid_id_token');
    const parts = token.split('.');if (parts.length !== 3 || parts.some(part => !/^[A-Za-z0-9_-]+$/.test(part))) return fail('invalid_id_token');
    const header = object(JSON.parse(Buffer.from(parts[0], 'base64url').toString())), claims = object(JSON.parse(Buffer.from(parts[1], 'base64url').toString()));
    if (!['RS256', 'ES256'].includes(header.alg) || !nonempty(header.kid, 200) || header.crit) return fail('invalid_id_token');
    const matching = jwks.keys.filter(key => key.kid === header.kid && (!key.alg || key.alg === header.alg) && (!key.use || key.use === 'sig'));
    if (matching.length !== 1) return fail('invalid_id_token');
    const key = matching[0];
    if ((header.alg === 'RS256' && (key.kty !== 'RSA' || !key.n || Buffer.from(key.n, 'base64url').length < 256))
      || (header.alg === 'ES256' && (key.kty !== 'EC' || key.crv !== 'P-256'))) return fail('invalid_id_token');
    const publicKey = crypto.createPublicKey({key, format: 'jwk'});
    if (!crypto.verify('sha256', Buffer.from(`${parts[0]}.${parts[1]}`), {key: publicKey, dsaEncoding: 'ieee-p1363'}, Buffer.from(parts[2], 'base64url'))) return fail('invalid_id_token');
    const now = (expected.nowMs ?? Date.now()) / 1000;
    const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (claims.iss !== ISSUER || !audiences.includes(expected.clientId) || (audiences.length > 1 && claims.azp !== expected.clientId)
      || !Number.isFinite(claims.exp) || claims.exp <= now || !Number.isFinite(claims.iat) || claims.iat > now
      || (claims.nbf !== undefined && (!Number.isFinite(claims.nbf) || claims.nbf > now)) || !nonempty(claims.sub, 1000)
      || (expected.nonce !== undefined && claims.nonce !== expected.nonce)
      || (expected.expectedSubject !== undefined && claims.sub !== expected.expectedSubject)) return fail('invalid_id_token');
    return {issuer: ISSUER, subject: claims.sub, clientId: expected.clientId};
  } catch {return fail('invalid_id_token');}
}
export interface SiwcTokens extends SiwcIdentity {
  accessToken: string; refreshToken: string; idToken: string; scopes: string[]; expiresAt: number; planUsageEnabled: boolean;
}
function tokenFields(raw: unknown, nowMs: number) {
  const value = object(raw);
  if (!nonempty(value.access_token) || !nonempty(value.refresh_token) || String(value.token_type).toLowerCase() !== 'bearer'
    || typeof value.expires_in !== 'number' || !Number.isFinite(value.expires_in) || value.expires_in <= 0 || value.expires_in > 86400
    || typeof value.scope !== 'string' || value.scope.length > 8192) return fail('invalid_token_response');
  const scopes = [...new Set(value.scope.split(/\s+/).filter(Boolean))];
  // earliest_refresh_at is intentionally neither interpreted nor used to schedule anything.
  return {accessToken: value.access_token, refreshToken: value.refresh_token, scopes, expiresAt: nowMs + value.expires_in * 1000,
    planUsageEnabled: scopes.includes('chatgpt.tokens.use.direct')};
}
export function acceptAuthorizationTokens(raw: unknown, options: IdExpectation & {nonce: string; jwks: {keys: JsonWebKey[]}}): SiwcTokens {
  if (!nonempty(options.nonce, 1000)) fail('invalid_nonce');
  const value = object(raw), nowMs = options.nowMs ?? Date.now();
  const identity = verifySiwcIdToken(value.id_token, options.jwks, {...options, nowMs});
  return {...identity, ...tokenFields(value, nowMs), idToken: value.id_token};
}
export interface SiwcFormRequest {url: string; method: 'POST'; form: URLSearchParams; signal: AbortSignal}
export interface SiwcFormResponse {status: number; body?: unknown}
export type SiwcFormTransport = (request: SiwcFormRequest) => Promise<SiwcFormResponse>;
interface OperationOptions {signal?: AbortSignal; timeoutMs?: number}
const TERMINAL_REFRESH = new Set(['invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused']);
const PROVIDER_CODES=new Set([...TERMINAL_REFRESH,'invalid_client','access_denied','subscription_sharing_usage_limit_exceeded','subscription_sharing_invalid_user','subscription_sharing_usage_unavailable','subscription_sharing_user_unavailable','subscription_sharing_user_not_eligible','subscription_sharing_unsupported_capability','subscription_sharing_route_not_supported','chatpass_v2_scope_not_authorized','chatpass_v2_invalid_authorization_context','server_error','invalid_request_error','rate_limit_exceeded','insufficient_quota','model_not_found','permission_denied']);
const PARAM_PATH=/^(?:model|tools|input|instructions|store|stream|include|previous_response_id|service_tier|reasoning|text|background|conversation|max_output_tokens|max_tool_calls|metadata|moderation|multi_agent|prompt|prompt_cache_retention|safety_identifier|temperature|top_logprobs|top_p|truncation|user)(?:\[\d{1,3}\]|\.(?:type|name|namespace|tools|function|parameters|properties|content|role|text|format|strict|effort|summary|encrypted_content|arguments|output|call_id)){0,8}$/;
export function siwcResponseError(status: number, body: unknown, shape?:AssistantErrorDiagnostics['bodyShape']): SiwcProtocolError {
  const raw = object(body), value = typeof raw.error === 'string' ? raw.error : object(raw.error).code;
  const code = typeof value === 'string' && PROVIDER_CODES.has(value) ? value : 'http_error';
  const param=object(raw.error).param;
  const diagnostics:AssistantErrorDiagnostics={bodyShape:shape??(typeof raw.error==='string'?'oauth_error':raw.error&&typeof raw.error==='object'?'error_object':typeof raw.detail==='string'?'detail':'other'),
    ...(code!=='http_error'?{providerCode:code}:{}),...(typeof param==='string'&&param.length<=200&&PARAM_PATH.test(param)?{param}:{})};
  const recovery: Recovery = TERMINAL_REFRESH.has(code) ? 'sign_in' : code === 'invalid_client' ? 'configure_client'
    : code === 'subscription_sharing_usage_limit_exceeded' ? 'manage_usage' : code === 'subscription_sharing_invalid_user' ? 'inspect_credentials'
      : ['subscription_sharing_usage_unavailable', 'subscription_sharing_user_unavailable'].includes(code) || status >= 500 || status === 429 ? 'retry_later' : 'fix_request';
  return new SiwcProtocolError(code, recovery, status, diagnostics);
}
/** Bound cancellation even when a faulty injected transport ignores its AbortSignal. No background retry. */
function bounded<T>(run: (signal: AbortSignal) => Promise<T>, options: OperationOptions = {}): Promise<T> {
  const ms = options.timeoutMs ?? 15000;
  if (!Number.isInteger(ms) || ms < 1 || ms > 120000) return Promise.reject(new SiwcProtocolError('invalid_timeout'));
  if (options.signal?.aborted) return Promise.reject(new SiwcProtocolError('cancelled'));
  return new Promise<T>((resolve, reject) => {
    const controller = new AbortController();let finished = false;
    const done = (error?: unknown, value?: T) => {if (finished) return;finished = true;clearTimeout(timer);options.signal?.removeEventListener('abort', cancel);error ? reject(error) : resolve(value as T);};
    const cancel = () => {controller.abort();done(new SiwcProtocolError('cancelled'));};
    const timer = setTimeout(() => {controller.abort();done(new SiwcProtocolError('timeout', 'retry_later'));}, ms);
    options.signal?.addEventListener('abort', cancel, {once: true});
    const rejected = (error: unknown) => done(error instanceof SiwcProtocolError ? error : new SiwcProtocolError('transport_failed', 'retry_later'));
    try {Promise.resolve(run(controller.signal)).then(value => done(undefined, value), rejected);} catch (error) {rejected(error);}
  });
}
export class SiwcTokenSession {
  private tokens: SiwcTokens | null;
  private flight: Promise<SiwcTokens> | null = null;
  private closed = false;
  private rotationUncertain = false;
  private readonly registration: SiwcIdentity;
  constructor(tokens: SiwcTokens, private readonly deps: {transport: SiwcFormTransport; jwks: {keys: JsonWebKey[]}; now?: () => number; stopRequests?: () => void}) {
    this.tokens = clone(tokens);this.registration = {issuer: tokens.issuer, subject: tokens.subject, clientId: tokens.clientId};
  }
  state() {return {...this.registration, signedIn: Boolean(this.tokens), planUsageEnabled: this.tokens?.planUsageEnabled === true};}
  /** Main-process-only credential copy; this module does not expose an IPC or persist it. */
  credentials(): SiwcTokens | null {return this.tokens ? clone(this.tokens) : null;}
  async refresh(options: OperationOptions = {}): Promise<SiwcTokens> {
    if (this.closed || !this.tokens) throw new SiwcProtocolError('signed_out', 'sign_in');
    if (options.signal?.aborted) throw new SiwcProtocolError('cancelled');
    if (!this.flight) {
      const prior = this.tokens;
      this.flight = bounded(signal => this.deps.transport({url: TOKEN_ENDPOINT, method: 'POST', signal,
        form: new URLSearchParams({grant_type: 'refresh_token', client_id: prior.clientId, refresh_token: prior.refreshToken, resource: RESOURCE})}), {timeoutMs: options.timeoutMs})
        .then(response => {
          if (response.status !== 200) throw siwcResponseError(response.status, response.body);
          const value = object(response.body), fields = tokenFields(value, this.deps.now?.() ?? Date.now());
          if (value.id_token !== undefined) verifySiwcIdToken(value.id_token, this.deps.jwks, {clientId: prior.clientId, expectedSubject: prior.subject, nowMs: this.deps.now?.() ?? Date.now()});
          const next = {...prior, ...fields, idToken: value.id_token ?? prior.idToken};
          if (!this.closed) this.tokens = next;return next;
        }).catch(error => {
          if (error instanceof SiwcProtocolError && TERMINAL_REFRESH.has(error.code)) {if (!this.closed) this.tokens = null;}
          else if (!(error instanceof SiwcProtocolError) || error.status===undefined || error.status===200) this.rotationUncertain = true;
          // A definitive HTTP error did not rotate tokens. A lost or unusable successful reply remains uncertain.
          throw error;
        })
        .finally(() => {this.flight = null;});
    }
    // A caller may stop waiting without aborting another caller's rotating refresh or dropping its replacement.
    const next = await bounded(() => this.flight!, options);
    if (this.closed) throw new SiwcProtocolError('signed_out', 'sign_in');
    return clone(next);
  }
  async signOut(discovery: {revocation_endpoint?: unknown}, options: OperationOptions = {}) {
    this.closed = true;let latest = this.tokens;this.tokens = null;
    try {this.deps.stopRequests?.();} catch {/* credential clearing is still required */}
    let uncertainRefresh = this.rotationUncertain;
    if (this.flight) {try {latest = await bounded(() => this.flight!, options);} catch {uncertainRefresh = true;}}
    let confirmed = false;
    if (latest) {
      try {
        const endpoint = new URL(String(discovery.revocation_endpoint));
        if (endpoint.origin !== ISSUER || endpoint.username || endpoint.password || endpoint.hash) fail('invalid_revocation_endpoint');
        const response = await bounded(signal => this.deps.transport({url: endpoint.href, method: 'POST', signal,
          form: new URLSearchParams({token: latest!.refreshToken, token_type_hint: 'refresh_token', client_id: latest!.clientId})}), options);
        confirmed = response.status === 200 && !uncertainRefresh;
      } catch {/* local sign-out remains complete; remote revocation is explicitly unconfirmed */}
    }
    return {localCleared: true, remoteRevocationConfirmed: confirmed, registrationRetained: true};
  }
}

const NAMESPACE = 'modu_vision';
const nameOK = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_]{1,63}$/.test(value);
function toolNames(tools: AssistantToolSpec[]) {
  const names = new Set<string>();
  for (const tool of tools) {if (!nameOK(tool.name) || names.has(tool.name) || object(tool.parameters).type !== 'object') fail('invalid_tools');names.add(tool.name);}
  return names;
}
function toolCall(item: Json, names: Set<string>, ids: Set<string>) {
  if (item.namespace !== NAMESPACE || !names.has(item.name) || !nameOK(item.name) || !nonempty(item.call_id, 200) || ids.has(item.call_id) || typeof item.arguments !== 'string') fail('invalid_tool_call');
  let args: unknown;try {args = JSON.parse(item.arguments);} catch {fail('invalid_tool_call');}
  if (!args || typeof args !== 'object' || Array.isArray(args)) fail('invalid_tool_call');
  ids.add(item.call_id);return {id: item.call_id as string, namespace: NAMESPACE, name: item.name as string, arguments: args as Record<string, unknown>};
}
const textField = (value:unknown, max=100000):value is string => typeof value==='string' && value.length<=max;
function fields(row:Json, allowed:string[]) {
  if(Object.keys(row).some(key=>!allowed.includes(key)))fail('invalid_input');
  if(row.id!==undefined&&!nonempty(row.id,200)||row.status!==undefined&&row.status!=='completed')fail('invalid_input');
  if(row.phase!==undefined&&row.phase!==null&&row.phase!=='commentary'&&row.phase!=='final_answer')fail('invalid_input');
}
/** Validate every nested field before retaining provider replay. Opaque reasoning is a bounded string or null;
 * its bytes stay unchanged because the provider must verify it on replay. Ordinary text is always redacted. */
function replayItem(raw:unknown, names:Set<string>, ids:Set<string>):Json {
  const row=object(raw);
  if(row.type==='reasoning') {
    fields(row,['type','id','summary','encrypted_content','status','content']);
    if(!Array.isArray(row.summary)||row.summary.length>128||row.encrypted_content!==undefined&&row.encrypted_content!==null&&!textField(row.encrypted_content))fail('invalid_input');
    if(row.content!==undefined&&(!Array.isArray(row.content)||row.content.length))fail('invalid_input');
    const summary=row.summary.map((raw:unknown)=>{const part=object(raw);fields(part,['type','text']);if(part.type!=='summary_text'||!textField(part.text))fail('invalid_input');return {type:'summary_text',text:redactOutgoingText(part.text)};});
    return {...clone(row),summary};
  }
  if(row.type==='message') {
    fields(row,['type','role','content','id','status','phase']);if(row.role!=='assistant'||!Array.isArray(row.content)||row.content.length>128)fail('invalid_input');
    const content=row.content.map((raw:unknown)=>{const part=object(raw);
      if(part.type==='output_text'){fields(part,['type','text','annotations','logprobs']);if(!textField(part.text))fail('invalid_input');
        // This text-only client requests no hosted tools or log probabilities. An empty provider-default field is safe.
        for(const field of ['annotations','logprobs'])if(part[field]!==undefined&&part[field]!==null&&(!Array.isArray(part[field])||part[field].length))fail('invalid_input');
        return {...clone(part),text:redactOutgoingText(part.text)};
      }
      if(part.type==='refusal'){fields(part,['type','refusal']);if(!textField(part.refusal))fail('invalid_input');return {type:'refusal',refusal:redactOutgoingText(part.refusal)};}
      return fail('invalid_input');
    });return {...clone(row),content};
  }
  if(row.type==='function_call') {
    fields(row,['type','id','namespace','name','call_id','arguments','status']);const call=toolCall(row,names,ids);
    return {...clone(row),arguments:JSON.stringify(redactOutgoingValue(call.arguments))};
  }
  return fail('invalid_input');
}
/** Text/history subset only. No images, hosted tools, custom arbitrary fields or execution is enabled here. */
export function buildSiwcTextRequest(input: {model: string; instructions?: string; input: unknown[]; tools: AssistantToolSpec[]}) {
  const names = toolNames(input.tools), ids = new Set<string>(), outstanding = new Set<string>();
  if (!nonempty(input.model, 200) || !Array.isArray(input.input) || !input.input.length) fail('invalid_request');
  if (input.instructions !== undefined && !nonempty(input.instructions, 100000)) fail('invalid_request');
  const sanitized:unknown[]=[];
  for (const item of input.input) {
    const row = object(item);
    if (row.type === 'function_call') {
      const safe=replayItem(row,names,ids);outstanding.add(safe.call_id);sanitized.push(safe);
    } else if (row.type === 'function_call_output') {
      if (!outstanding.delete(row.call_id) || !nonempty(row.output, 100000) || Object.keys(row).some(key => !['type', 'call_id', 'output'].includes(key))) fail('invalid_input');
      sanitized.push({...clone(row),output:redactOutgoingText(row.output)});
    } else if (row.type === 'reasoning') {
      sanitized.push(replayItem(row,names,ids));
    } else {
      const text = typeof row.content === 'string' && nonempty(row.content, 100000);
      const replayText = row.role === 'assistant' && row.type === 'message' && Array.isArray(row.content);
      if (!['user', 'assistant', 'developer'].includes(row.role) || (!text && !replayText) || (row.type !== undefined && row.type !== 'message')) fail('invalid_input');
      fields(row,['type','role','content','id','status',...(row.role==='assistant'?['phase']:[])]);sanitized.push(replayText?replayItem(row,names,ids):{...clone(row),content:redactOutgoingText(row.content)});
    }
  }
  if (outstanding.size) fail('invalid_input');
  return {model: input.model, ...(input.instructions ? {instructions: redactOutgoingText(input.instructions)} : {}), input: sanitized, store: false, stream: true, include: ['reasoning.encrypted_content'],
    ...(input.tools.length ? {tools: [{type: 'namespace', name: NAMESPACE, description: 'Modu Vision application tools',
      tools: clone(input.tools).map(tool => ({type: 'function', name: tool.name, description: tool.description, parameters: tool.parameters}))}]} : {})};
}
function completedResponse(response: unknown, tools: AssistantToolSpec[]) {
  const value = object(response), names = toolNames(tools), calls: {id: string; namespace: string; name: string; arguments: Record<string, unknown>}[] = [], ids = new Set<string>();let text = '';
  if (value.status !== 'completed' || !Array.isArray(value.output)) fail('bad_stream');
  const replay:Json[]=[];
  for (const raw of value.output) {
    const item = replayItem(raw,names,ids);replay.push(item);
    if (item.type === 'message') {
      if (!Array.isArray(item.content)) fail('bad_stream');
      for (const rawPart of item.content) {const part = object(rawPart);if (part.type === 'output_text') text += part.text;else if(part.type==='refusal')text+=part.refusal;}
    } else if (item.type === 'function_call') {
      calls.push({id:item.call_id,namespace:NAMESPACE,name:item.name,arguments:JSON.parse(item.arguments)});
    } else if (item.type !== 'reasoning') fail('unsupported_output');
  }
  return {text, toolCalls: calls, replay};
}
/** Consume injected byte chunks, require a complete terminal event, and never execute tool calls or expose deltas as success. */
export async function readSiwcResponse(source: AsyncIterable<Uint8Array>, options: OperationOptions & {tools: AssistantToolSpec[]; maxBytes?: number; maxEventBytes?: number;httpStatus?:number}) {
  const maxBytes = options.maxBytes ?? 2 * 1024 * 1024, maxEventBytes = options.maxEventBytes ?? 256 * 1024;
  if (!Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > 2 * 1024 * 1024 || !Number.isInteger(maxEventBytes) || maxEventBytes < 1 || maxEventBytes > 256 * 1024) fail('invalid_limits');
  const allowedTools = clone(options.tools);toolNames(allowedTools);const iterator = source[Symbol.asyncIterator]();
  try {
    return await bounded(async signal => {
      const decoder = new TextDecoder('utf-8', {fatal: true});let bytes = 0, pending = '', data: string[] = [], eventName = '', frameBytes = 0;
      const dispatch = () => {
        const content = data.join('\n'), named = eventName;data = [];eventName = '';frameBytes = 0;
        if (!content || content === '[DONE]') return null;
        let value: Json;try {value = object(JSON.parse(content));} catch {return fail('bad_stream');}
        if (!nonempty(value.type, 200) || (named && value.type !== named)) fail('bad_stream');
        if (value.type === 'response.failed') throw siwcResponseError(options.httpStatus??200, {error: object(value.response).error},'response_error');
        if (value.type === 'error') throw siwcResponseError(options.httpStatus??200, {error: value},'stream_error');
        if (value.type === 'response.incomplete') fail('response_incomplete');
        if (value.type === 'response.completed') return completedResponse(value.response, allowedTools);
        return null;
      };
      const lines = (final = false) => {
        let index: number;
        while ((index = pending.search(/[\r\n]/)) >= 0) {
          if (!final && pending[index] === '\r' && index === pending.length - 1) break;
          const line = pending.slice(0, index), width = pending[index] === '\r' && pending[index + 1] === '\n' ? 2 : 1;pending = pending.slice(index + width);
          if (!line) {const result = dispatch();if (result) return result;continue;}
          frameBytes += Buffer.byteLength(line, 'utf8');if (frameBytes > maxEventBytes) fail('response_too_large');
          if (line.startsWith(':')) continue;
          const colon = line.indexOf(':'), field = colon < 0 ? line : line.slice(0, colon);let value = colon < 0 ? '' : line.slice(colon + 1);if (value.startsWith(' ')) value = value.slice(1);
          if (field === 'data') data.push(value);else if (field === 'event') eventName = value;
        }
        if (frameBytes + Buffer.byteLength(pending, 'utf8') > maxEventBytes) fail('response_too_large');
        return null;
      };
      while (!signal.aborted) {
        const part = await iterator.next();
        if (signal.aborted) fail('cancelled');
        if (part.done) {try {pending += decoder.decode();} catch {fail('bad_stream');}const result = lines(true);if (result) return result;fail('stream_truncated');}
        if (!(part.value instanceof Uint8Array)) fail('bad_stream');bytes += part.value.byteLength;if (bytes > maxBytes) fail('response_too_large');
        try {pending += decoder.decode(part.value, {stream: true});} catch {fail('bad_stream');}
        const result = lines();if (result) return result;
      }
      return fail('cancelled');
    }, options);
  } finally {
    // A broken iterator may ignore return too. Cleanup is best effort and cannot delay cancellation indefinitely.
    try {void Promise.resolve(iterator.return?.()).catch(() => undefined);} catch {/* already closed */}
  }
}
