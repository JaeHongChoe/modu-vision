import type { Page } from '@playwright/test';

/**
 * Minimal stand-in for the Electron preload bridge in the browser project, so
 * the same renderer sees a healthy owned backend behind the harness server.
 * Native capabilities (dialogs, shell, updates, shared login) report that they
 * are unavailable instead of pretending to work. Shell requests are recorded.
 */
export async function installDesktopHostShim(page: Page, port: number): Promise<void> {
  await page.addInitScript(({ backendPort }) => {
    const status = { port: backendPort, healthy: true, pid: null, device: 'cpu', deviceName: 'browser harness' };
    const unavailable = async () => { throw new Error('Not available in the browser harness'); };
    const opened: string[] = [];
    (window as unknown as { __harnessOpened: string[] }).__harnessOpened = opened;
    (window as unknown as { api: unknown }).api = {
      getBackendPort: async () => backendPort,
      getBackendStatus: async () => status,
      onBackendStatusChange: (callback: (value: typeof status) => void) => { setTimeout(() => callback(status), 0); return () => undefined; },
      onBackendCrashed: () => () => undefined,
      selectFolder: async () => null,
      selectFile: async () => null,
      openExternal: async (target: string) => { opened.push(target); return false; },
      getDistributionStatus: unavailable,
      configureUpdateChannel: unavailable,
      checkForUpdate: unavailable,
      downloadUpdate: unavailable,
      getSharedConnection: async () => null,
      loginSharedServer: unavailable,
      selectSharedProject: unavailable,
      disconnectSharedServer: async () => undefined,
      platform: /Win/.test(navigator.platform) ? 'win32' : /Mac/.test(navigator.platform) ? 'darwin' : 'linux',
    };
  }, { backendPort: port });
}
