/** S1-10: the host features the renderer needs, separated by host.
 *
 *  The desktop app reaches native dialogs, the operating system's file browser and the shared-server session through
 *  the preload bridge. A plain browser has no native dialogs or OS file browser:
 *  picking a path and opening a local folder or file refuse with an explicit message
 *  instead of silently doing nothing, while links open in a new tab and files are uploaded and downloaded the same way
 *  on both hosts (archive uploads and anchor downloads). HTTPS browsers use same-origin HttpOnly account cookies.
 * Components ask this module, never the bridge itself.
 */
import { browserSession } from './browserSession';
import type { ElectronAPI, SharedConnection } from '../../types/electron';

export type HostKind = 'desktop' | 'browser';
export type HostCapability = 'pickPaths' | 'openPaths' | 'sharedSessions' | 'updates';

/** A host feature the current host does not have; its message says what to do instead. */
export class HostUnavailable extends Error {
  constructor(readonly capability: HostCapability, message: string) {
    super(message);
    this.name = 'HostUnavailable';
  }
}

const UNAVAILABLE: Record<HostCapability, string> = {
  pickPaths: '브라우저에서는 폴더·파일 선택 창을 열 수 없습니다. 백엔드가 읽을 수 있는 경로를 직접 입력하세요.',
  openPaths: '브라우저에서는 이 컴퓨터의 폴더나 파일을 열 수 없습니다.',
  sharedSessions: '공유 서버 로그인은 데스크톱 앱에서만 사용할 수 있습니다.',
  updates: '업데이트 확인은 데스크톱 앱에서만 사용할 수 있습니다.',
};

function bridge(): Partial<ElectronAPI> | null {
  return typeof window !== 'undefined' && window.api ? window.api : null;
}

function required<K extends keyof ElectronAPI>(capability: HostCapability, method: K): NonNullable<ElectronAPI[K]> {
  const value = bridge()?.[method];
  if (value === undefined || value === null) throw new HostUnavailable(capability, UNAVAILABLE[capability]);
  return value as NonNullable<ElectronAPI[K]>;
}

export const host = {
  kind(): HostKind {
    return bridge() ? 'desktop' : 'browser';
  },

  can(capability: HostCapability): boolean {
    const api = bridge();
    if (!api) return capability === 'sharedSessions' && browserSession.available();
    switch (capability) {
      case 'pickPaths': return typeof api.selectFolder === 'function' && typeof api.selectFile === 'function';
      case 'openPaths': return typeof api.openExternal === 'function';
      case 'sharedSessions': return typeof api.loginSharedServer === 'function';
      case 'updates': return typeof api.getDistributionStatus === 'function';
    }
  },

  // Every host call is async, so a missing feature always arrives as a rejected promise (never a synchronous throw that
  // a caller's .catch would not see).

  /** A folder path chosen in the native dialog; null when the user cancels. */
  async selectFolder(options?: { title?: string; defaultPath?: string }): Promise<string | null> {
    return required('pickPaths', 'selectFolder')(options);
  },

  /** A file path chosen in the native dialog; null when the user cancels. */
  async selectFile(options?: Parameters<ElectronAPI['selectFile']>[0]): Promise<string | null> {
    return required('pickPaths', 'selectFile')(options);
  },

  /** Open a web link: through the desktop shell, or in a new browser tab without access to this page. */
  async openLink(url: string): Promise<boolean> {
    const api = bridge();
    if (api?.openExternal) return api.openExternal(url);
    if (typeof window === 'undefined' || !/^https?:\/\//i.test(url)) return false;
    return window.open(url, '_blank', 'noopener,noreferrer') !== null;
  },

  /** Open a local folder or file in the operating system's file browser (desktop only). */
  async openPath(path: string): Promise<boolean> {
    return required('openPaths', 'openExternal')(path);
  },

  /** The desktop app's release channel and manual update delivery (desktop only). */
  updates: {
    status: async () => required('updates', 'getDistributionStatus')(),
    configure: async (configuration: Parameters<ElectronAPI['configureUpdateChannel']>[0]) => required('updates', 'configureUpdateChannel')(configuration),
    check: async () => required('updates', 'checkForUpdate')(),
    download: async () => required('updates', 'downloadUpdate')(),
    verifyOffline: async () => required('updates', 'verifyOfflineUpdate')(),
  },

  shared: {
    requestOptions: (url: string, method: string) => browserSession.requestOptions(url, method),
    async connection(): Promise<SharedConnection | null> {
      const api = bridge();
      return api?.getSharedConnection ? api.getSharedConnection() : browserSession.connection();
    },
    async login(input: { server_url: string; username: string; password: string }): Promise<SharedConnection> {
      if (!bridge() && browserSession.available()) return browserSession.login(input);
      return required('sharedSessions', 'loginSharedServer')(input);
    },
    async select(projectId: string): Promise<SharedConnection> {
      if (!bridge() && browserSession.available()) return browserSession.select(projectId);
      return required('sharedSessions', 'selectSharedProject')(projectId);
    },
    async disconnect(): Promise<void> {
      const api = bridge();
      if (api?.disconnectSharedServer) await api.disconnectSharedServer();
      else if (!api) await browserSession.disconnect();
    },
  },
};

/** The message to show for a failed host call: the host's own explanation, or the error's text. */
export function hostErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HostUnavailable) return error.message;
  return error instanceof Error && error.message ? error.message : fallback;
}
