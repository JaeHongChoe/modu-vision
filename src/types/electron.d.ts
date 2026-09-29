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

declare global {
  interface Window {
    api: ElectronAPI;
  }
}
