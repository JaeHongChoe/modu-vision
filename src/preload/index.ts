import { contextBridge, ipcRenderer } from 'electron';
import type { BackendStatus, CrashEventData, ElectronAPI } from '../types/electron';

const api: ElectronAPI = {
  getDistributionStatus:()=>ipcRenderer.invoke('distribution:get-status'),
  configureUpdateChannel:configuration=>ipcRenderer.invoke('distribution:configure-channel',configuration),
  checkForUpdate:()=>ipcRenderer.invoke('distribution:check-update'),
  downloadUpdate:()=>ipcRenderer.invoke('distribution:download-update'),
  verifyOfflineUpdate:()=>ipcRenderer.invoke('distribution:verify-offline-update'),
  selectPortableUpdateHome:()=>ipcRenderer.invoke('distribution:select-portable-home'),
  inspectPortableUpdate:()=>ipcRenderer.invoke('distribution:inspect-portable'),
  previewPortableUpdate:channel=>ipcRenderer.invoke('distribution:preview-portable',channel),
  applyPortableUpdate:id=>ipcRenderer.invoke('distribution:apply-portable',id),
  recoverPortableUpdate:(action,expected)=>ipcRenderer.invoke('distribution:recover-portable',action,expected),
  launchPortableUpdate:expected=>ipcRenderer.invoke('distribution:launch-portable',expected),
  inspectPortableLaunch:expected=>ipcRenderer.invoke('distribution:inspect-portable-launch',expected),
  getSharedConnection:()=>ipcRenderer.invoke('shared:get'),
  loginSharedServer:input=>ipcRenderer.invoke('shared:login',input),
  selectSharedProject:id=>ipcRenderer.invoke('shared:select',id),
  disconnectSharedServer:()=>ipcRenderer.invoke('shared:disconnect'),
  getBackendPort: (): Promise<number | null> => {
    return ipcRenderer.invoke('get-backend-port');
  },
  getBackendStatus: (): Promise<BackendStatus> => {
    return ipcRenderer.invoke('get-backend-status');
  },
  selectFolder: (options?: { title?: string; defaultPath?: string }): Promise<string | null> => {
    return ipcRenderer.invoke('dialog:select-folder', options);
  },
  selectFile: (options?: {
    title?: string;
    defaultPath?: string;
    filters?: Array<{ name: string; extensions: string[] }>;
  }): Promise<string | null> => {
    return ipcRenderer.invoke('dialog:select-file', options);
  },
  openExternal: (urlOrPath: string): Promise<boolean> => {
    return ipcRenderer.invoke('shell:open-external', urlOrPath);
  },
  onBackendStatusChange: (callback: (status: BackendStatus) => void): (() => void) => {
    const listener = (_event: unknown, status: BackendStatus) => callback(status);
    ipcRenderer.on('backend:status-changed', listener);
    return () => {
      ipcRenderer.removeListener('backend:status-changed', listener);
    };
  },
  onBackendCrashed: (callback: (data: CrashEventData) => void): (() => void) => {
    const listener = (_event: unknown, data: CrashEventData) => callback(data);
    ipcRenderer.on('backend:crashed', listener);
    return () => {
      ipcRenderer.removeListener('backend:crashed', listener);
    };
  },
  platform: process.platform as 'darwin' | 'win32' | 'linux',
};

// Safely bridge to renderer process
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('api', api);
  } catch (error) {
    console.error('Failed to expose electron API via contextBridge:', error);
  }
} else {
  (window as unknown as { api: ElectronAPI }).api = api;
}

export type { ElectronAPI };
