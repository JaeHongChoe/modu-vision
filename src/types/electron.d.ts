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
export interface ManualDelivery {version:string;path:string;sha256:string;integrity_verified:true;signature:NativeSignature;publisher_matches_installed:boolean;handoff_ready:boolean;prerequisite:string}

export interface SharedConnection {server_url:string;expires_at:number;user:{id:string;username:string;administrator:boolean|number};project_id?:string}

declare global {
  interface Window {
    api: ElectronAPI;
  }
}
