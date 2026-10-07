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
export interface PortableCanaryReview {schema_version:1;protocol:1;required:true;policy:'same_reviewed_source_runtime_worker_v1';status:'missing_pins'|'source_ready'|'requires_target';supported:boolean;pins:PortableCanaryPins|null;capability_sha256:string|null;candidate_runtime_source_sha256:string|null;reason:string|null;candidate_main_launch_verified:false;native_application_verified:false;frozen_backend_verified:false;owned_backend_execution_origin_verified:false;worker_process_tree_exit_verified:false;model_quality_verified:false;release_ready:false}
export interface PortableUpdateReview {status:'reviewed';root:string;review_id:string;installation_id:string;plan_sha256:string;source_sha256:string;envelope_sha256:string;authority_sha256:string;version:string;current_version:string;publisher:string;channel:'stable'|'beta';application_file_count:number;application_layout:'portable/v1'|'darwin-app/v2';application_link_count:number;pack_count:number;artifact_bytes:number;database_fence:number;copied_session_policy:'revoked';application_started:false;preactivation_canary:PortableCanaryReview;installable:boolean}

export interface SharedConnection {server_url:string;expires_at:number;user:{id:string;username:string;administrator:boolean|number};project_id?:string}

declare global {
  interface Window {
    /** The desktop preload bridge; absent in a plain browser. Components use services/hostAdapter.ts instead. */
    api?: ElectronAPI;
  }
}
