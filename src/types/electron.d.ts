export interface BackendStatus {
  port: number | null;
  healthy: boolean;
  pid: number | null;
  device?: string;
  deviceName?: string;
}

export interface CrashEventData {
  exitCode: number | null;
  signal: string | null;
  recentStderr: string[];
  recentStdout: string[];
  restartsAttempted: number;
  message: string;
}

export interface ElectronAPI {
  getDistributionStatus:()=>Promise<DistributionState>;
  configureUpdateChannel:(configuration:UpdateChannel|null)=>Promise<DistributionState>;
  checkForUpdate:()=>Promise<DistributionState>;
  downloadUpdate:()=>Promise<ManualDelivery>;
  verifyOfflineUpdate:()=>Promise<ManualDelivery|null>;
  selectPortableUpdateHome:()=>Promise<PortableUpdateState|null>;
  inspectPortableUpdate:()=>Promise<PortableUpdateState>;
  previewPortableUpdate:(channel:'stable'|'beta',canary:PortableCanaryPins)=>Promise<PortableUpdateReview|null>;
  applyPortableUpdate:(reviewId:string)=>Promise<PortableUpdateState>;
  recoverPortableUpdate:(action:PortableRecoveryAction,expected:{installation_id:string;update_id:string})=>Promise<PortableUpdateState>;
  launchPortableUpdate:(expected:PortableLaunchExpected)=>Promise<PortableLaunchState>;
  inspectPortableLaunch:(expected:PortableLaunchExpected)=>Promise<PortableLaunchState>;
  getSharedConnection:()=>Promise<SharedConnection|null>;
  loginSharedServer:(input:{server_url:string;username:string;password:string})=>Promise<SharedConnection>;
  selectSharedProject:(projectId:string)=>Promise<SharedConnection>;
  disconnectSharedServer:()=>Promise<void>;
  getBackendPort: () => Promise<number | null>;
  getBackendStatus: () => Promise<BackendStatus>;
  selectFolder: (options?: { title?: string; defaultPath?: string }) => Promise<string | null>;
  selectFile: (options?: {
    title?: string;
    defaultPath?: string;
    filters?: Array<{ name: string; extensions: string[] }>;
  }) => Promise<string | null>;
  openExternal: (urlOrPath: string) => Promise<boolean>;
  onBackendStatusChange: (callback: (status: BackendStatus) => void) => () => void;
  onBackendCrashed: (callback: (data: CrashEventData) => void) => () => void;
  platform: 'darwin' | 'win32' | 'linux';
  /** The built-in assistant (main process only holds keys and calls providers); absent in a browser and in older shims. */
  assistant?: AssistantBridge;
}

/** Built-in assistant contract (docs/product-direction-easy-labeling-agent.md §4). The renderer never receives a key:
 *  only whether one is stored. Provider calls run in the Electron main process. */
export type AssistantApiKeyProviderId = 'openai-compatible' | 'anthropic';
export type AssistantProviderId = AssistantApiKeyProviderId | 'chatgpt';
/** OpenAI-compatible request format: Chat Completions or Responses. */
export type AssistantApiFormat = 'chat' | 'responses';
export interface AssistantProviderPublic {provider:AssistantProviderId;baseUrl:string;model:string;format:AssistantApiFormat|null;hasKey:boolean;supportsImages:boolean;label:string;ready?:boolean;connectionAuthority?:string}
export interface AssistantChatgptAccount {recordId:string;label:string;model:string;signedIn:boolean;planUsageEnabled:boolean;planUsageAcknowledged?:boolean;usagePaused?:boolean}
export interface AssistantChatgptRecoveryState {available:boolean;reason:'none'|'active_owner'|'unverifiable_owner'|'unsafe_store'|'closed'|'encryption_unavailable'|'stale_owner'|'unavailable_authority';ticket?:string}
export interface AssistantChatgptState {accounts:AssistantChatgptAccount[];active:string|null;encryptionAvailable:boolean;status:'idle'|'waiting'|'exchanging'|'connected'|'error';error?:AssistantError;remoteRevocationConfirmed?:boolean;authority?:string;recovery?:AssistantChatgptRecoveryState}
export interface AssistantConfigState {providers:AssistantProviderPublic[];active:AssistantProviderId|null;encryptionAvailable:boolean;chatgpt?:AssistantChatgptState}
export interface AssistantProviderInput {provider:AssistantApiKeyProviderId;baseUrl:string;model:string;format?:AssistantApiFormat;supportsImages:boolean;
  /** Sent once from the key field straight to the main process; never returned. */ key?:string;activate?:boolean}
export interface AssistantToolSpec {name:string;description:string;parameters:Record<string,unknown>}
export interface AssistantToolCall {id:string;name:string;arguments:Record<string,unknown>;argumentsError?:boolean}
export type AssistantMessage =
  | {role:'user';text:string}
  | {role:'assistant';text:string;toolCalls?:AssistantToolCall[];
     /** The provider's own blocks for this turn (Anthropic thinking/tool_use), sent back unchanged; opaque here. */ replay?:unknown}
  | {role:'tool';toolCallId:string;name:string;content:string;isError?:boolean};
export interface AssistantImageInput {grantId:string;imageIdentity:string;mediaType:'image/png'|'image/jpeg'|'image/webp';base64:string}
export interface AssistantSendInput {requestId:string;context:Record<string,unknown>;messages:AssistantMessage[];tools:AssistantToolSpec[];image?:AssistantImageInput}
export type AssistantErrorCode = 'no_provider'|'no_key'|'storage_unavailable'|'bad_key'|'forbidden'|'billing'|'not_found'|'bad_request'
  |'rate_limited'|'overloaded'|'server'|'network'|'timeout'|'cancelled'|'bad_response'|'invalid_input'|'image_refused';
export interface AssistantErrorDiagnostics {providerCode?:string;param?:string;bodyShape:'error_object'|'oauth_error'|'detail'|'other'|'response_error'|'stream_error'}
export interface AssistantError {code:AssistantErrorCode;reason:string;action:string;status?:number;requestId?:string;detail?:string;diagnostics?:AssistantErrorDiagnostics}
export type AssistantSendResult =
  | {ok:true;text:string;toolCalls:AssistantToolCall[];replay?:unknown;stop:'end'|'tool'|'length';requestId?:string}
  | {ok:false;error:AssistantError};
export type AssistantModelList = {ok:true;models:{id:string;label:string}[]} | {ok:false;error:AssistantError};
export type AssistantImageGrant = {ok:true;grantId:string;message:string;providerLabel:string;expiresAt:number} | {ok:false;error:AssistantError};
export interface AssistantBridge {
  getState:()=>Promise<AssistantConfigState>;
  saveProvider:(input:AssistantProviderInput)=>Promise<AssistantConfigState>;
  removeKey:(provider:AssistantProviderId)=>Promise<AssistantConfigState>;
  removeProvider:(provider:AssistantProviderId)=>Promise<AssistantConfigState>;
  setActive:(provider:AssistantProviderId)=>Promise<AssistantConfigState>;
  listModels:(provider:AssistantProviderId)=>Promise<AssistantModelList>;
  send:(input:AssistantSendInput)=>Promise<AssistantSendResult>;
  cancel:(requestId:string)=>Promise<boolean>;
  /** Step one of an image question: names the provider for the person's confirmation; no pixels move yet. */
  imageIntent:(input:{fileName:string;imageIdentity:string})=>Promise<AssistantImageGrant>;
  /** Arms the run-id header on the one backend write a confirmed tool makes; endRun disarms it. */
  beginRun:(runId:string,tool:string)=>Promise<boolean>;
  endRun:(runId:string)=>Promise<void>;
  /** Optional in older desktop bridges. Tokens and authorization URLs never cross IPC. */
  chatgpt?: {
    begin:(input:{termsAccepted:true;recordId?:string;label?:string;enablePlanUsage?:true})=>Promise<AssistantConfigState>;
    acknowledgePlanUsage:(recordId:string)=>Promise<AssistantConfigState>;
    resumeUsage:(recordId:string)=>Promise<AssistantConfigState>;
    recover:(input:{ticket:string;confirmed:true})=>Promise<AssistantConfigState>;
    cancel:()=>Promise<AssistantConfigState>;
    selectAccount:(recordId:string|null)=>Promise<AssistantConfigState>;
    selectModel:(recordId:string,model:string)=>Promise<AssistantConfigState>;
    signOut:(recordId:string)=>Promise<AssistantConfigState>;
  };
}

export interface NativeSignature {status:'verified'|'unsigned'|'invalid'|'unavailable'|'development';reason:string;publisher?:string;platform:string;checked_at:string}
export interface UpdateChannel {channel:'stable'|'beta';manifest_url:string}
export interface UpdateRelease {version:string;channel:'stable'|'beta';platform:string;arch:string;url:string;sha256:string;size:number}
export interface DeliveryRecovery {schema_version:1;status:string;manifest_sha256:string;candidate_sha256:string;installed_sha256:string|null;candidate_path?:string;version:string;checked_at:string;error?:string}
export interface DistributionBackend {status:'development'|'inventory_bound'|'missing'|'invalid';build_identity_sha256?:string;executable_sha256?:string;startup_acceptance?:'passed'|'unverified';signature?:NativeSignature;offline?:{network_downloads_required_for_uncached_training:boolean;inference_requires_exported_package_weights:boolean;optional_features_unavailable:string[]};prerequisites:string[];reason?:string}
export interface DistributionState {backend?:DistributionBackend;app_version:string;version_source:'electron';platform:string;architecture:string;signature:NativeSignature;update:{configured:boolean;configuration:UpdateChannel|null;status:'not_configured'|'configured'|'available'|'up_to_date';release:UpdateRelease|null;automatic_update_available:false;prerequisite:string;recovery?:DeliveryRecovery|null}}
export interface ManualDelivery {offline?:boolean;artifacts_verified?:number;version:string;path:string;sha256:string;integrity_verified:true;signature:NativeSignature;publisher_matches_installed:boolean;handoff_ready:boolean;prerequisite:string}

export type PortableRecoveryAction='finish'|'forward'|'abort';
export interface PortableLaunchExpected {installation_id:string;update_id:string;database_fence:number}
export interface PortableLaunchState extends PortableLaunchExpected {schema_version:1;root:string;status:'absent'|'reserved'|'starting'|'ready'|'recovery_required'|'exited';nonce:string|null;bootstrap_binding_verified:boolean;readiness:'unverified'|'authenticated_controller_binding_only';native_app_handshake_verified:false;backend_handshake_verified:false;actual_application_inference_verified:false;release_ready:false;reason?:string}
export interface PortableUpdateState {status:'ready'|'committed'|'recovery_required';root:string;installation_id:string;version:string;update_id:string|null;database_fence:number;allowed_recovery:PortableRecoveryAction[];application_started:false;native_signature_acceptance:'unqualified';model_quality_acceptance:'required';launch_state?:PortableLaunchState}
export interface PortableCanaryPins {workspace_id:string;project_id:string;plan_sha256:string}
interface PortableCanaryReviewBase {schema_version:1;required:true;supported:boolean;pins:PortableCanaryPins|null;capability_sha256:string|null;candidate_runtime_source_sha256:string|null;reason:string|null;candidate_main_launch_verified:false;native_application_verified:false;frozen_backend_verified:false;owned_backend_execution_origin_verified:false;worker_process_tree_exit_verified:false;model_quality_verified:false;release_ready:false}
export interface PortableSourceCanaryReview extends PortableCanaryReviewBase {protocol:1;policy:'same_reviewed_source_runtime_worker_v1';status:'missing_pins'|'source_ready'|'requires_target'}
export interface PortableFrozenCanaryWorker {protocol:1;executable_path:string;executable_sha256:string;build_receipt_path:string;build_receipt_sha256:string;build_identity_sha256:string;runtime_source_sha256:string;resource_inventory_sha256:string}
export interface PortableFrozenCanaryReview extends PortableCanaryReviewBase {protocol:2;policy:'same_reviewed_frozen_runtime_worker_v1';status:'missing_pins'|'frozen_ready'|'requires_target';worker_binding:PortableFrozenCanaryWorker|null}
export type PortableCanaryReview=PortableSourceCanaryReview|PortableFrozenCanaryReview;
export interface PortableUpdateReview {status:'reviewed';root:string;review_id:string;installation_id:string;plan_sha256:string;source_sha256:string;envelope_sha256:string;authority_sha256:string;version:string;current_version:string;publisher:string;channel:'stable'|'beta';application_file_count:number;application_layout:'portable/v1'|'darwin-app/v2';application_link_count:number;pack_count:number;artifact_bytes:number;database_fence:number;copied_session_policy:'revoked';application_started:false;preactivation_canary:PortableCanaryReview;installable:boolean}

export interface SharedConnection {server_url:string;expires_at:number;user:{id:string;username:string;administrator:boolean|number};project_id?:string}

declare global {
  interface Window {
    /** The desktop preload bridge; absent in a plain browser. Components use services/hostAdapter.ts instead. */
    api?: ElectronAPI;
  }
}
