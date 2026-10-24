/** Electron-main SIWC connection. Credentials, PKCE and callback URLs never cross IPC.
 * The only default destinations are the documented OpenAI auth and API origins.
 * A browser opens only after begin({termsAccepted:true}); inference always needs a separate send.
 */
import http, {type Server} from 'node:http';
import {createHash, type JsonWebKey} from 'node:crypto';
import type {AssistantChatgptRecoveryState, AssistantError, AssistantMessage, AssistantToolSpec} from '../../types/electron';
import {ProviderError, assistantError, type Completion} from './providers';
import {acceptAuthorizationTokens, buildSiwcTextRequest, createAuthorizationAttempt, readSiwcResponse, siwcResponseError,
  SiwcProtocolError, SiwcTokenSession, type SiwcIdentity, type SiwcTokens} from './siwcProtocol';

const AUTH = 'https://auth.openai.com', API = 'https://api.openai.com/v1';
const DISCOVERY = `${AUTH}/.well-known/openid-configuration`, TOKEN = `${AUTH}/api/accounts/oauth/token`;
type Account = {recordId:string;label:string;model:string;signedIn:boolean;planUsageEnabled:boolean;planUsageAcknowledged?:boolean;usagePaused?:boolean};
export interface SiwcConnectionStore {
  state():{accounts:Account[];active:string|null;encryptionAvailable:boolean};
  peekState?():{accounts:Account[];active:string|null;encryptionAvailable:boolean};
  hostId():string;registration(id:string):SiwcIdentity|null;credentials(id?:string):SiwcTokens|null;
  save(tokens:SiwcTokens, options?:{recordId?:string;label?:string;activate?:boolean}):Account;
  clearCredentials(id:string):void;setActive(id:string|null):void;setModel(id:string, model:string):void;
  acknowledgePlanUsage(id:string):void;setUsagePaused(id:string,paused:boolean):void;
  recoveryStatus?():AssistantChatgptRecoveryState;recover?(input:{ticket:string;confirmed:true}):void;
  close?():void;
}
export interface SiwcConnectionState {
  accounts:Account[];active:string|null;encryptionAvailable:boolean;
  status:'idle'|'waiting'|'exchanging'|'connected'|'error';error?:AssistantError;remoteRevocationConfirmed?:boolean;recovery?:AssistantChatgptRecoveryState;
}
type Discovery = {issuer:string;jwks_uri:string;revocation_endpoint?:string};
type Attempt = {server:Server;controller:AbortController;timer:ReturnType<typeof setTimeout>;generation:number};
type Session = {value:SiwcTokenSession;refresh:Promise<SiwcTokens>|null};
const obj = (v:unknown):Record<string,any> => v && typeof v==='object' && !Array.isArray(v) ? v as Record<string,any> : {};
const validRecord = (v:unknown):v is string => typeof v==='string' && /^[A-Za-z0-9_-]{1,100}$/.test(v);
const validModel = (v:unknown):v is string => typeof v==='string' && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(v);
const protocol = (code:string, recovery:'sign_in'|'retry_later'|'fix_request'='fix_request') => new SiwcProtocolError(code,recovery);
const requestId = (response:Response):string|undefined => {const value=response.headers.get('x-request-id');return value&&/^[A-Za-z0-9_-]{1,200}$/.test(value)?value:undefined;};

/** Errors contain application-owned Korean text only. No raw provider body, URL, code, token or claims. */
function connectionError(error:unknown):AssistantError {
  if (error instanceof ProviderError) return error.detail;
  const code = obj(error).code, status = typeof obj(error).status==='number' ? obj(error).status : undefined;
  if (code==='cancelled') return assistantError('cancelled');
  if (code==='timeout' || code==='attempt_expired') return assistantError('timeout',{reason:'ChatGPT 연결 또는 응답 시간이 초과되었습니다.',action:'다시 연결하거나 잠시 뒤 다시 보내세요.'});
  if (code==='storage_unavailable' || code==='store_locked' || code==='store_corrupt' || code==='storage_failed') return assistantError('storage_unavailable',{reason:code==='store_locked'?'ChatGPT 보호 저장소가 잠겼습니다. 다른 앱 실행 중이거나 비정상 종료된 연결일 수 있습니다.':'ChatGPT 계정을 보호 저장소에 저장하거나 읽지 못했습니다.',action:'다른 앱 실행을 닫거나 OS 보호 저장소를 확인한 뒤 다시 연결하세요.'});
  if (code==='terms_required') return assistantError('invalid_input',{reason:'연결 전에 이용약관과 전송 안내를 확인하세요.',action:'설정의 안내를 확인한 뒤 직접 ChatGPT로 계속 버튼을 누르세요.'});
  if (code==='recovery_changed') return assistantError('storage_unavailable',{reason:'복구를 준비한 뒤 연결 상태가 바뀌었습니다.',action:'연결 상태를 다시 확인하고 새 복구 안내를 확인하세요.'});
  if (code==='recovery_unavailable'||code==='recovery_confirmation_required') return assistantError('storage_unavailable',{reason:'이 연결은 지금 안전하게 복구할 수 없습니다.',action:'다른 실행을 종료하고 연결 상태를 다시 확인하세요. 복구 안내를 읽고 직접 확인해야 합니다.'});
  if (code==='access_denied') return assistantError('forbidden',{reason:'ChatGPT 연결을 허용하지 않았습니다.',action:'필요할 때 설정에서 다시 연결하세요.'});
  if (code==='plan_scope_missing') return assistantError('forbidden',{reason:'계정 로그인은 확인됐지만 ChatGPT 요금제 사용 권한이 허용되지 않았습니다.',action:'연결 권한을 확인하거나 API 키 공급자를 사용하세요.'});
  if (code==='subscription_sharing_usage_limit_exceeded' || code==='usage_paused') return assistantError('rate_limited',{reason:'ChatGPT 플랜 요청을 사용 한도 때문에 일시 중단했습니다.',action:'ChatGPT 사용량을 확인한 뒤 설정에서 직접 요청 재개를 허용하세요. 자동으로 재전송하지 않습니다.',status});
  if (code==='subscription_sharing_invalid_user') return assistantError('forbidden',{reason:'이 ChatGPT 계정의 요금제 사용을 확인할 수 없습니다.',action:'선택한 계정과 연결 권한을 확인하세요.',status});
  if (code==='invalid_client') return assistantError('bad_request',{reason:'ChatGPT 앱 등록 설정을 확인해야 합니다.',action:'앱 등록 설정과 클라이언트 구성을 확인하세요. 같은 요청이나 로그인을 반복하지 마세요.',status});
  if (code==='subscription_sharing_user_not_eligible') return assistantError('forbidden',{reason:'선택한 사용자·작업 공간의 정책에서 ChatGPT 플랜 사용을 허용하지 않습니다.',action:'지원 범위와 작업 공간 정책을 확인하세요. 같은 요청이나 로그인을 반복하지 마세요.',status});
  if (['chatpass_v2_scope_not_authorized','chatpass_v2_invalid_authorization_context'].includes(code)) return assistantError('forbidden',{reason:'ChatGPT 연결의 플랜 사용 권한을 확인하지 못했습니다.',action:'선택한 계정·앱 등록·허용한 권한을 확인하세요. 다른 결제 방식으로 자동 전환하지 않습니다.',status});
  if (code==='subscription_sharing_unsupported_capability' || code==='subscription_sharing_route_not_supported') return assistantError('bad_request',{reason:'이 ChatGPT 연결에서 지원하지 않는 요청입니다.',action:'기술 상세의 입력·도구·모델 또는 요청 경로를 확인하세요. 같은 요청을 그대로 재시도하지 마세요.',status});
  if (['signed_out','reconnect_required','invalid_grant','invalid_refresh_token','token_expired','refresh_token_expired','refresh_token_invalidated','refresh_token_reused'].includes(code)) return assistantError('no_key',{reason:'ChatGPT에 다시 연결해야 합니다.',action:'설정에서 해당 계정으로 다시 연결하세요.'});
  if (code==='no_account') return assistantError('no_provider',{reason:'ChatGPT 계정이 선택되지 않았습니다.',action:'설정에서 계정을 연결하고 선택하세요.'});
  if (code==='model_required' || code==='model_not_listed') return assistantError('not_found',{reason:'사용할 ChatGPT 모델을 확인해야 합니다.',action:'모델 목록을 새로 읽고 사용할 모델을 직접 선택하세요.'});
  if (['invalid_id_token','invalid_state','invalid_callback_uri','duplicate_callback_parameter','invalid_client_binding','identity_mismatch','invalid_discovery','invalid_jwks','authorization_failed','invalid_token_response'].includes(code)) return assistantError('bad_response',{reason:'ChatGPT 연결의 보안 확인을 완료하지 못했습니다.',action:'연결을 새로 시작하세요. 이전 연결 정보로 요청을 보내지 않습니다.'});
  if (status===401) return assistantError('forbidden',{reason:'ChatGPT 계정의 서명 또는 플랜 사용 권한이 받아들여지지 않았습니다.',action:'선택한 계정과 허용한 권한을 확인하세요. 철회나 갱신 실패가 확인되기 전에는 로그인 정보를 지우지 않습니다.',status});
  if (status===403) return assistantError('forbidden',{reason:'정책이나 권한 제한으로 ChatGPT 요청을 보낼 수 없습니다.',action:'선택한 계정·허용 지역·앱 연결 정책을 확인하세요.',status});
  if (status===429) return assistantError('rate_limited',{reason:'ChatGPT 사용 한도 또는 요청 빈도 제한에 걸렸습니다.',action:'ChatGPT 사용량을 확인한 뒤 직접 다시 보내세요.',status});
  if (status && status>=500 || ['transport_failed','subscription_sharing_usage_unavailable','subscription_sharing_user_unavailable'].includes(code)) return assistantError('network',{reason:'ChatGPT 연결을 완료하지 못했습니다.',action:'인터넷 연결을 확인하고 잠시 뒤 다시 시도하세요.',status});
  return assistantError('bad_response',{reason:'ChatGPT 응답을 안전하게 읽지 못했습니다.',action:'대화를 새로 시작하거나 설정의 연결 상태를 확인하세요.',status});
}
export function siwcConnectionError(error:unknown):AssistantError {
  const result=connectionError(error);
  return error instanceof SiwcProtocolError&&error.diagnostics?{...result,diagnostics:{...error.diagnostics}}:result;
}

export class SiwcConnection {
  private readonly store:SiwcConnectionStore;
  private readonly fetchImpl:typeof fetch;
  private readonly now:()=>number;
  private readonly timeoutMs:number;
  private readonly attemptTimeoutMs:number;
  private status:SiwcConnectionState['status']='idle';
  private error:AssistantError|undefined;
  private revocation:boolean|undefined;
  private revision=0;
  private generation=0;
  private disposed=false;
  private attempt:Attempt|null=null;
  private operations=new Set<AbortController>();
  private sessions=new Map<string,Session>();
  private blocked=new Set<string>();
  private listed=new Map<string,Set<string>>();
  /** Receipts bind exact validated provider output to this recipient authority. Renderer-supplied opaque data is never trusted. */
  private replayReceipts=new Map<string,string>();
  private discovered:Discovery|null=null;
  private jwks:{keys:JsonWebKey[]}|null=null;
  constructor(private readonly deps:{store:SiwcConnectionStore;openExternal:(url:string)=>Promise<unknown>|unknown;fetchImpl?:typeof fetch;now?:()=>number;timeoutMs?:number;attemptTimeoutMs?:number}) {
    this.store=deps.store;this.fetchImpl=deps.fetchImpl||fetch;this.now=deps.now||Date.now;
    this.timeoutMs=deps.timeoutMs??15000;this.attemptTimeoutMs=deps.attemptTimeoutMs??300000;
    if (!Number.isInteger(this.timeoutMs)||this.timeoutMs<1||this.timeoutMs>120000||!Number.isInteger(this.attemptTimeoutMs)||this.attemptTimeoutMs<1||this.attemptTimeoutMs>300000) throw new Error('Invalid SIWC timeout');
  }
  state():SiwcConnectionState {
    if(this.disposed)return {accounts:[],active:null,encryptionAvailable:false,status:'idle'};
    try {const raw=this.store.peekState?.()??this.store.state(),state={...raw,accounts:raw.accounts.map(a=>this.blocked.has(a.recordId)?{...a,signedIn:false,planUsageEnabled:false}:a)},signed=state.accounts.some(a=>a.recordId===state.active&&a.signedIn);
      return {...state,status:this.status==='idle'&&signed?'connected':this.status,...(this.error?{error:{...this.error}}:{}),...(this.revocation!==undefined?{remoteRevocationConfirmed:this.revocation}:{})};
    } catch(error) {let recovery:AssistantChatgptRecoveryState|undefined;try{recovery=this.store.recoveryStatus?.();}catch{}return {accounts:[],active:null,encryptionAvailable:false,status:'error',error:siwcConnectionError(error),...(recovery?{recovery}:{})};}
  }
  /** Opaque recipient binding; no user identity, token or stable subject appears in this value. */
  authority():string {const state=this.state(),active=state.accounts.find(a=>a.recordId===state.active);return `${this.revision}:${state.active||''}:${active?.model||''}:${active?.signedIn===true}`;}
  private assertOpen() {if(this.disposed)throw protocol('cancelled');}
  private stopRequests() {for(const operation of this.operations)operation.abort();this.operations.clear();}
  private closeAttempt() {const attempt=this.attempt;this.attempt=null;if(!attempt)return;clearTimeout(attempt.timer);attempt.controller.abort();attempt.server.close();attempt.server.closeIdleConnections();}
  private invalidate() {this.revision++;this.generation++;this.replayReceipts.clear();this.closeAttempt();this.stopRequests();this.error=undefined;this.revocation=undefined;}
  private replayFingerprint(replay:unknown):string {if(!Array.isArray(replay))throw protocol('invalid_input');return createHash('sha256').update(JSON.stringify(replay)).digest('hex');}
  private receipt(replay:unknown, binding:string) {const hash=this.replayFingerprint(replay);this.replayReceipts.set(hash,binding);if(this.replayReceipts.size>128)this.replayReceipts.delete(this.replayReceipts.keys().next().value!);}
  private failure(error:unknown) {this.status='error';this.error=siwcConnectionError(error);}
  private async operation<T>(run:(signal:AbortSignal)=>Promise<T>, options:{signal?:AbortSignal;timeoutMs?:number}={}):Promise<T> {
    this.assertOpen();const ms=options.timeoutMs??this.timeoutMs;if(!Number.isInteger(ms)||ms<1||ms>120000)throw protocol('invalid_timeout');
    if(options.signal?.aborted)throw protocol('cancelled');
    const controller=new AbortController();this.operations.add(controller);
    let timer:ReturnType<typeof setTimeout>|undefined;let listener:(()=>void)|undefined;
    try {return await new Promise<T>((resolve,reject)=>{let finished=false;const end=(error:unknown,value?:T)=>{if(finished)return;finished=true;error?reject(error):resolve(value as T);};
      listener=()=>{controller.abort();end(protocol('cancelled'));};options.signal?.addEventListener('abort',listener,{once:true});
      controller.signal.addEventListener('abort',()=>end(protocol('cancelled')),{once:true});
      timer=setTimeout(()=>{end(protocol('timeout','retry_later'));controller.abort();},ms);
      Promise.resolve().then(()=>run(controller.signal)).then(value=>end(null,value),error=>end(error));
    });} finally {if(timer)clearTimeout(timer);if(listener)options.signal?.removeEventListener('abort',listener);this.operations.delete(controller);}
  }
  private checkDestination(value:string) {const url=new URL(value);if(url.username||url.password||url.hash||url.search||url.protocol!=='https:'||![AUTH,new URL(API).origin].includes(url.origin))throw protocol('invalid_destination');return url.href;}
  private async request(url:string, init:RequestInit, signal:AbortSignal):Promise<Response> {
    this.checkDestination(url);if(signal.aborted)throw protocol('cancelled');let response:Response;
    try {response=await this.fetchImpl(url,{...init,signal,redirect:'error',cache:'no-store',credentials:'omit'});} catch(error) {if(signal.aborted)throw protocol('cancelled');if(error instanceof SiwcProtocolError)throw error;throw protocol('transport_failed','retry_later');}
    if(response.redirected||response.status>=300&&response.status<400){try{await response.body?.cancel();}catch{}throw protocol('redirect_refused');}
    return response;
  }
  private async json(response:Response, signal:AbortSignal, limit=262144):Promise<unknown> {
    if(Number(response.headers.get('content-length'))>limit)throw protocol('response_too_large');
    const reader=response.body?.getReader();if(!reader)throw protocol('bad_response');let bytes=0;const chunks:Uint8Array[]=[];
    try {while(true){if(signal.aborted)throw protocol('cancelled');const result=await reader.read();if(result.done)break;bytes+=result.value.byteLength;if(bytes>limit)throw protocol('response_too_large');chunks.push(result.value);}return JSON.parse(Buffer.concat(chunks).toString('utf8'));}
    catch(error) {if(error instanceof SiwcProtocolError)throw error;throw protocol('bad_response');}
    finally {try{await reader.cancel();}catch{}}
  }
  private httpError(status:number, body:unknown) {return siwcResponseError(status,body);}
  private async discovery(signal:AbortSignal):Promise<Discovery> {
    if(this.discovered)return this.discovered;
    const response=await this.request(DISCOVERY,{method:'GET',headers:{Accept:'application/json'}},signal);const value=obj(await this.json(response,signal));
    if(response.status!==200)throw this.httpError(response.status,value);
    if(value.issuer!==AUTH||value.authorization_endpoint!==`${AUTH}/api/accounts/authorize`||value.token_endpoint!==TOKEN||typeof value.jwks_uri!=='string')throw protocol('invalid_discovery');
    const trusted=(u:unknown)=>{if(typeof u!=='string')return undefined;try{const url=new URL(u);if(url.origin!==AUTH||url.username||url.password||url.search||url.hash)return undefined;return url.href;}catch{return undefined;}};
    const jwks=trusted(value.jwks_uri),revocation=trusted(value.revocation_endpoint);if(!jwks||value.revocation_endpoint!==undefined&&!revocation)throw protocol('invalid_discovery');
    const result={issuer:AUTH,jwks_uri:jwks,...(revocation?{revocation_endpoint:revocation}:{})};if(signal.aborted)throw protocol('cancelled');this.discovered=result;return result;
  }
  private async keys(signal:AbortSignal, fresh=false) {if(this.jwks&&!fresh)return this.jwks;const d=await this.discovery(signal),response=await this.request(d.jwks_uri,{method:'GET',headers:{Accept:'application/json'}},signal),value=obj(await this.json(response,signal));
    if(response.status!==200)throw this.httpError(response.status,value);if(!Array.isArray(value.keys)||!value.keys.length||value.keys.length>100)throw protocol('invalid_jwks');const result={keys:value.keys as JsonWebKey[]};if(signal.aborted)throw protocol('cancelled');this.jwks=result;return result;}
  private form=async(request:{url:string;form:URLSearchParams;signal:AbortSignal})=>{const response=await this.request(request.url,{method:'POST',headers:{Accept:'application/json','Content-Type':'application/x-www-form-urlencoded'},body:request.form.toString()},request.signal);
    // A valid revocation often returns an empty HTTP 200 body.
    if(request.url!==TOKEN){try{await response.body?.cancel();}catch{}return {status:response.status};}
    return {status:response.status,body:await this.json(response,request.signal)};};

  async begin(input:{termsAccepted:boolean;recordId?:string;label?:string;enablePlanUsage?:true}):Promise<SiwcConnectionState> {
    this.assertOpen();if(input?.termsAccepted!==true)throw new ProviderError(siwcConnectionError(protocol('terms_required')));
    if(!this.store.state().encryptionAvailable)throw new ProviderError(siwcConnectionError(protocol('storage_unavailable')));
    if(input.recordId!==undefined&&!validRecord(input.recordId))throw new ProviderError(assistantError('invalid_input'));
    const registration=input.recordId?this.store.registration(input.recordId):null;if(input.recordId&&!registration)throw new ProviderError(assistantError('invalid_input'));
    if(input.enablePlanUsage!==undefined&&(input.enablePlanUsage!==true||!input.recordId||this.store.state().accounts.find(a=>a.recordId===input.recordId)?.planUsageEnabled!==false))throw new ProviderError(assistantError('invalid_input'));
    const label=input.label?.trim();if(label!==undefined&&(!label||label.length>100))throw new ProviderError(assistantError('invalid_input'));
    this.invalidate();const generation=this.generation;this.status='waiting';
    let server:Server|undefined;
    try {
      server=http.createServer();server.maxHeadersCount=20;server.requestTimeout=5000;server.headersTimeout=5000;server.keepAliveTimeout=1000;
      await new Promise<void>((resolve,reject)=>{const error=()=>reject(protocol('listener_failed'));server!.once('error',error);server!.listen(0,'127.0.0.1',()=>{server!.off('error',error);resolve();});});
      if(this.disposed||generation!==this.generation){server.close();throw protocol('cancelled');}
      const address=server.address();if(!address||typeof address==='string')throw protocol('listener_failed');const redirectUri=`http://127.0.0.1:${address.port}/auth/callback`;
      const auth=createAuthorizationAttempt({hostId:this.store.hostId(),appName:'Modu Vision',redirectUri,clientId:registration?.clientId,promptConsent:input.enablePlanUsage===true,now:this.now});
      const controller=new AbortController();const timer=setTimeout(()=>{if(this.attempt?.generation===generation){this.closeAttempt();this.failure(protocol('attempt_expired'));}},this.attemptTimeoutMs);timer.unref?.();
      this.attempt={server,controller,timer,generation};server.unref();
      server.on('request',(req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.setHeader('Cache-Control','no-store');res.setHeader('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'");res.setHeader('Referrer-Policy','no-referrer');
        if(req.method!=='GET'||req.headers.host!==`127.0.0.1:${address.port}`||!req.url||req.url.length>12288||!req.url.startsWith('/auth/callback?')){res.statusCode=404;res.end('요청을 찾을 수 없습니다.');return;}
        if(this.attempt?.generation!==generation||this.status!=='waiting'){res.statusCode=410;res.end('연결 시도가 끝났습니다. 앱으로 돌아가세요.');return;}
        this.status='exchanging';void (async()=>{try {const grant=auth.acceptCallback(redirectUri+req.url!.slice('/auth/callback'.length));
          const tokens=await this.operation(async signal=>{await this.discovery(signal);const [reply,jwks]=await Promise.all([this.form({url:TOKEN,form:grant.form,signal}),this.keys(signal,true)]);if(reply.status!==200)throw this.httpError(reply.status,reply.body);
            return acceptAuthorizationTokens(reply.body,{clientId:grant.clientId,nonce:grant.nonce,jwks,nowMs:this.now(),...(registration?{expectedSubject:registration.subject}:{})});},{signal:controller.signal});
          if(this.disposed||this.attempt?.generation!==generation||controller.signal.aborted)throw protocol('cancelled');
          if(registration&&(tokens.issuer!==registration.issuer||tokens.subject!==registration.subject||tokens.clientId!==registration.clientId))throw protocol('identity_mismatch');
          const saved=this.store.save(tokens,{...(input.recordId?{recordId:input.recordId}:{}),...(label?{label}:{}),activate:true});this.sessions.delete(saved.recordId);this.blocked.delete(saved.recordId);this.listed.delete(saved.recordId);this.revision++;this.status='connected';this.error=undefined;
          res.statusCode=200;res.end('ChatGPT 연결을 확인했습니다. 이 창을 닫고 Modu Vision으로 돌아가세요.');this.closeAttempt();
        }catch(error){if(generation===this.generation&&!this.disposed){this.failure(error);this.closeAttempt();}res.statusCode=400;res.end('ChatGPT 연결을 완료하지 못했습니다. 앱에서 연결 상태를 확인하세요.');}})();
      });
      const authUrl=new URL(auth.authorizationUrl),hint=input.recordId?this.store.credentials(input.recordId)?.idToken:null;if(hint)authUrl.searchParams.set('id_token_hint',hint);
      await this.deps.openExternal(authUrl.href);if(this.disposed||generation!==this.generation)throw protocol('cancelled');return this.state();
    }catch(error){server?.close();if(generation===this.generation&&!this.disposed){this.closeAttempt();this.failure(error);}throw new ProviderError(siwcConnectionError(error));}
  }
  cancel():SiwcConnectionState {this.invalidate();this.status='idle';return this.state();}
  selectAccount(id:string|null):SiwcConnectionState {this.assertOpen();if(id!==null&&(!validRecord(id)||!this.store.registration(id)))throw new ProviderError(assistantError('invalid_input'));this.invalidate();this.store.setActive(id);this.status='idle';return this.state();}
  selectModel(recordId:string,model:string):SiwcConnectionState {this.assertOpen();if(!validRecord(recordId)||!this.store.registration(recordId)||!validModel(model)||!this.listed.get(recordId)?.has(model))throw new ProviderError(siwcConnectionError(protocol('model_not_listed')));this.invalidate();this.store.setModel(recordId,model);this.status='idle';return this.state();}
  acknowledgePlanUsage(recordId:string):SiwcConnectionState {const account=this.account();if(account.recordId!==recordId)throw new ProviderError(assistantError('invalid_input'));this.store.acknowledgePlanUsage(recordId);return this.state();}
  resumeUsage(recordId:string):SiwcConnectionState {const account=this.account();if(account.recordId!==recordId||!account.usagePaused)throw new ProviderError(assistantError('invalid_input'));this.invalidate();this.store.setUsagePaused(recordId,false);this.status='idle';return this.state();}
  recover(input:{ticket:string;confirmed:true}):SiwcConnectionState {
    this.assertOpen();if(input?.confirmed!==true||typeof input.ticket!=='string'||!/^[a-f0-9]{64}$/.test(input.ticket)||!this.store.recover)throw new ProviderError(siwcConnectionError(protocol('recovery_confirmation_required')));
    const recovery=this.store.recoveryStatus?.();if(!recovery?.available||recovery.ticket!==input.ticket)throw new ProviderError(siwcConnectionError(protocol('recovery_unavailable')));
    this.invalidate();this.sessions.clear();this.listed.clear();this.blocked.clear();
    try{this.store.recover(input);this.status='idle';return this.state();}catch(error){this.failure(error);throw new ProviderError(siwcConnectionError(error));}
  }
  private account(requireModel=false):Account {this.assertOpen();const state=this.store.state(),account=state.accounts.find(a=>a.recordId===state.active);
    if(!account)throw protocol('no_account');if(!account.signedIn||this.blocked.has(account.recordId))throw protocol('reconnect_required','sign_in');
    if(!account.planUsageEnabled)throw protocol('plan_scope_missing');if(requireModel&&account.usagePaused)throw protocol('usage_paused');if(requireModel&&!validModel(account.model))throw protocol('model_required');return account;}
  private async access(recordId:string,signal:AbortSignal,timeoutMs:number):Promise<SiwcTokens> {
    if(this.blocked.has(recordId))throw protocol('reconnect_required','sign_in');const tokens=this.store.credentials(recordId);if(!tokens)throw protocol('signed_out','sign_in');
    if(!tokens.planUsageEnabled)throw protocol('plan_scope_missing');if(tokens.expiresAt>this.now()+30000)return tokens;
    let session=this.sessions.get(recordId);
    if(!session){const jwks=await this.keys(signal);if(signal.aborted)throw protocol('cancelled');
      // Another concurrent caller may already own the same rotating refresh.
      session=this.sessions.get(recordId);if(!session){const transport=async(request:Parameters<typeof this.form>[0])=>{const response=await this.form(request),token=obj(response.body).id_token;
          // A decoded header only selects a trusted-key reload; it never establishes identity or bypasses signature verification.
          if(response.status===200&&typeof token==='string'&&token.length<=65536){let kid:unknown;try{const segment=token.split('.')[0];if(segment.length<=4096)kid=obj(JSON.parse(Buffer.from(segment,'base64url').toString('utf8'))).kid;}catch{}
            if(typeof kid==='string'&&kid.length<=200&&!jwks.keys.some(key=>key.kid===kid)){const fresh=await this.keys(request.signal,true);jwks.keys=fresh.keys;}}
          return response;};session={value:new SiwcTokenSession(tokens,{transport,jwks,now:this.now}),refresh:null};this.sessions.set(recordId,session);}}
    const owned=session;
    if(!owned.refresh){owned.refresh=(async()=>{try {const next=await owned.value.refresh({timeoutMs});
        if(this.disposed||this.sessions.get(recordId)!==owned)throw protocol('cancelled');
        // Persist the replacement before any caller receives its access token. An inactive account stays inactive.
        this.store.save(next,{recordId,activate:false});if(!next.planUsageEnabled)this.revision++;return next;
      }catch(error){if(this.sessions.get(recordId)===owned){
          const terminal=new Set(['invalid_grant','invalid_refresh_token','token_expired','refresh_token_expired','refresh_token_invalidated','refresh_token_reused']);
          if(error instanceof SiwcProtocolError&&error.status!==undefined&&error.status!==200&&!terminal.has(error.code)){this.sessions.delete(recordId);throw error;}
          this.blocked.add(recordId);this.revision++;this.listed.delete(recordId);
          // An uncertain network outcome may have rotated remotely. Never retry the retained refresh after restart.
          try{this.store.clearCredentials(recordId);}catch{throw protocol('storage_failed');}}
        throw error;
      }finally{owned.refresh=null;}})();}
    const next=await owned.refresh;if(signal.aborted)throw protocol('cancelled');if(!next.planUsageEnabled)throw protocol('plan_scope_missing');return next;
  }
  async listModels():Promise<{id:string;label:string}[]> {
    let responseId:string|undefined;
    try {const account=this.account(),binding=this.authority();return await this.operation(async signal=>{const token=await this.access(account.recordId,signal,this.timeoutMs);
        if(signal.aborted||this.authority()!==binding)throw protocol('cancelled');const response=await this.request(`${API}/models`,{method:'GET',headers:{Accept:'application/json',Authorization:`Bearer ${token.accessToken}`}},signal);responseId=requestId(response);const raw=await this.json(response,signal,524288);
        if(response.status!==200)throw this.httpError(response.status,raw);const list=obj(raw).models;if(!Array.isArray(list)||list.length>1000)throw protocol('bad_response');
        const names=new Set<string>(),models:{id:string;label:string}[]=[];
        for(const value of list){const row=obj(value);if(row.visibility!=='list')continue;if(!validModel(row.slug)||names.has(row.slug))throw protocol('bad_response');names.add(row.slug);
          const label=typeof row.display_name==='string'&&row.display_name.trim().length>0&&row.display_name.length<=200?row.display_name.replace(/[\u0000-\u001f\u007f]/g,' ').trim():row.slug;models.push({id:row.slug,label});}
        if(signal.aborted||this.authority()!==binding)throw protocol('cancelled');this.listed.set(account.recordId,names);return models;
      });}catch(error){throw new ProviderError({...siwcConnectionError(error),...(responseId?{requestId:responseId}:{})});}
  }
  async complete(input:{system:string;messages:AssistantMessage[];tools:AssistantToolSpec[];signal?:AbortSignal;timeoutMs?:number}):Promise<Completion> {
    let responseId:string|undefined,accountId:string|undefined;
    try {if(obj(input).image!==undefined)throw new ProviderError(assistantError('image_refused'));const account=this.account(true),binding=this.authority(),timeoutMs=input.timeoutMs??this.timeoutMs;
      accountId=account.recordId;const history:unknown[]=[];
      for(const message of input.messages){if(message.role==='user')history.push({role:'user',content:message.text});else if(message.role==='tool')history.push({type:'function_call_output',call_id:message.toolCallId,output:message.content});
        else if(message.replay!==undefined){if(this.replayReceipts.get(this.replayFingerprint(message.replay))!==binding)throw protocol('invalid_input');history.push(...structuredClone(message.replay as unknown[]));}
        else {if(message.text)history.push({role:'assistant',content:message.text});for(const call of message.toolCalls||[])history.push({type:'function_call',namespace:'modu_vision',name:call.name,call_id:call.id,arguments:JSON.stringify(call.arguments)});}}
      const body=buildSiwcTextRequest({model:account.model,instructions:input.system,input:history,tools:input.tools});
      return await this.operation(async signal=>{const token=await this.access(account.recordId,signal,timeoutMs);if(signal.aborted||this.authority()!==binding)throw protocol('cancelled');
        const response=await this.request(`${API}/responses`,{method:'POST',headers:{Accept:'text/event-stream','Content-Type':'application/json',Authorization:`Bearer ${token.accessToken}`},body:JSON.stringify(body)},signal);
        responseId=requestId(response);
        if(response.status!==200)throw this.httpError(response.status,await this.json(response,signal));
        if(!response.headers.get('content-type')?.toLowerCase().startsWith('text/event-stream')||!response.body){try{await response.body?.cancel();}catch{}throw protocol('bad_stream');}
        const stream=response.body;
        async function* source(){const reader=stream.getReader();try{while(true){const part=await reader.read();if(part.done)return;yield part.value;}}finally{try{void reader.cancel().catch(()=>undefined);}catch{}}}
        const result=await readSiwcResponse(source(),{tools:input.tools,signal,timeoutMs,httpStatus:response.status});if(signal.aborted||this.authority()!==binding)throw protocol('cancelled');
        this.receipt(result.replay,binding);
        return {text:result.text,toolCalls:result.toolCalls,replay:result.replay,stop:result.toolCalls.length?'tool':'end'};
      },{signal:input.signal,timeoutMs});
    }catch(error){if(accountId&&error instanceof SiwcProtocolError&&error.code==='subscription_sharing_usage_limit_exceeded'){this.store.setUsagePaused(accountId,true);this.stopRequests();}throw new ProviderError({...siwcConnectionError(error),...(responseId?{requestId:responseId}:{})});}
  }
  async signOut(recordId:string):Promise<SiwcConnectionState> {
    this.assertOpen();if(!validRecord(recordId)||!this.store.registration(recordId))throw new ProviderError(assistantError('invalid_input'));
    this.invalidate();const generation=this.generation;this.status='idle';this.blocked.add(recordId);this.listed.delete(recordId);
    const saved=this.store.credentials(recordId),existing=this.sessions.get(recordId);this.sessions.delete(recordId);
    // Clear disk first; neither a late callback nor a rotating token can rehydrate this record.
    try{this.store.clearCredentials(recordId);}catch(error){this.failure(error);throw new ProviderError(siwcConnectionError(error));}
    let confirmed=false;
    try {if(saved||existing){const result=await this.operation(async signal=>{const discovery=await this.discovery(signal);if(signal.aborted)throw protocol('cancelled');
          const session=existing?.value||new SiwcTokenSession(saved!,{transport:this.form,jwks:{keys:[]},now:this.now});return session.signOut(discovery,{signal,timeoutMs:this.timeoutMs});});confirmed=result.remoteRevocationConfirmed;}}
    catch{/* Local credentials are already cleared. Remote revocation stays explicitly unconfirmed. */}
    if(!this.disposed&&generation===this.generation){this.revocation=confirmed;this.revision++;}return this.state();
  }
  dispose():void {
    if(this.disposed)return;
    let safeToRelease=true;
    try {
      this.invalidate();
      // A rotating refresh can finish remotely after shutdown. Never leave its old token reusable on disk.
      for(const [id,session] of this.sessions) if(session.refresh) {
        try {this.store.clearCredentials(id);} catch {safeToRelease=false;}
      }
    } finally {
      this.disposed=true;this.sessions.clear();this.listed.clear();
      // Failed clearing retains the exclusive lease as a fail-closed recovery fence, rather than reopening stale tokens.
      if(safeToRelease)this.store.close?.();
    }
  }
}
