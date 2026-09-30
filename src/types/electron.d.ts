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

export interface SharedConnection {server_url:string;expires_at:number;user:{id:string;username:string;administrator:boolean|number};project_id?:string}

declare global {
  interface Window {
    api: ElectronAPI;
  }
}
