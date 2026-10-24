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
  previewPortableUpdate:(channel,canary)=>ipcRenderer.invoke('distribution:preview-portable',channel,canary),
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
  // The built-in assistant: settings, provider calls and image grants run in the main process; no key comes back.
  assistant: {
    getState: () => ipcRenderer.invoke('assistant:get-state'),
    saveProvider: input => ipcRenderer.invoke('assistant:save-provider', input),
    removeKey: provider => ipcRenderer.invoke('assistant:remove-key', provider),
    removeProvider: provider => ipcRenderer.invoke('assistant:remove-provider', provider),
    setActive: provider => ipcRenderer.invoke('assistant:set-active', provider),
    listModels: provider => ipcRenderer.invoke('assistant:list-models', provider),
    send: input => ipcRenderer.invoke('assistant:send', input),
    cancel: requestId => ipcRenderer.invoke('assistant:cancel', requestId),
    imageIntent: input => ipcRenderer.invoke('assistant:image-intent', input),
    beginRun: (runId, tool) => ipcRenderer.invoke('assistant:run-begin', runId, tool),
    endRun: runId => ipcRenderer.invoke('assistant:run-end', runId),
    chatgpt: {
      begin: input => ipcRenderer.invoke('assistant:chatgpt-begin', input),
      cancel: () => ipcRenderer.invoke('assistant:chatgpt-cancel'),
      selectAccount: id => ipcRenderer.invoke('assistant:chatgpt-account', id),
      selectModel: (id, model) => ipcRenderer.invoke('assistant:chatgpt-model', id, model),
      signOut: id => ipcRenderer.invoke('assistant:chatgpt-signout', id),
    },
  },
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
