/** Host features use the desktop preload or the authenticated localhost helper.
 * A browser keeps its cookie/CSRF transport and negotiates native capabilities;
 * other browser hosts retain typed-path and upload/download fallbacks.
 * Components ask this module, never the bridge itself.
 */
import type { FolderOptions, FileOptions } from './browserHost';
import { BrowserRemote } from './browserRemote';
import { setRemoteTransport,request,getProjectContext,getProjectContextGeneration } from './api';
import {LinkedSourceClient} from './linkedSources';
import { browserSession, localBrowserHost as browserHost } from './browserSession';
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

const browserRemote=new BrowserRemote({origin:()=>typeof window!=='undefined'&&!window.api?window.location?.origin??null:null,session:browserSession,host:()=>browserHost,fetch:(...args)=>fetch(...args),now:()=>Date.now()/1000});
setRemoteTransport(browserRemote);
const linkedSources=new LinkedSourceClient({
 available:()=>!bridge()&&!browserRemote.connection()&&!!browserHost?.available()&&!!browserHost.can('projects'),
 prepare:async()=>{if(!browserHost)throw new Error('로컬 보조 프로그램 연결을 확인하세요.');await browserHost.prepareRemote();},
 context:getProjectContext,generation:()=>`${getProjectContextGeneration()}:${browserHost?.generation()??0}`,request,
});

export const host = {
  linkedSources,
  localBrowser(): boolean { return !bridge() && !!browserHost?.available() && !!browserHost?.can('projects'); },

  unavailableReason(capability: HostCapability): string { return !bridge() && browserHost?.available() && capability !== 'sharedSessions' ? browserHost.reason(capability) : UNAVAILABLE[capability]; },

  kind(): HostKind {
    return bridge() ? 'desktop' : 'browser';
  },

  can(capability: HostCapability): boolean {
    const api = bridge();
    if (!api) return capability === 'sharedSessions' ? browserSession.available() : browserHost?.can(capability) ?? false;
    switch (capability) {
      case 'pickPaths': return typeof api.selectFolder === 'function' && typeof api.selectFile === 'function';
      case 'openPaths': return typeof api.openExternal === 'function';
      case 'sharedSessions': return typeof api.loginSharedServer === 'function';
      case 'updates': return typeof api.getDistributionStatus === 'function';
    }
  },

  /** Provider credentials and requests stay in the native service on either supported host. */
  assistant(): NonNullable<ElectronAPI['assistant']> | null {
    return bridge()?.assistant ?? browserHost?.assistant() ?? null;
  },

  // Every host call is async, so a missing feature always arrives as a rejected promise (never a synchronous throw that
  // a caller's .catch would not see).

  /** A folder path chosen in the native dialog; null when the user cancels. */
  async selectFolder(options?: FolderOptions): Promise<string | null> {
    if (!bridge() && browserHost?.available()) return browserHost.selectFolder(options);
    const nativeOptions=options?(({purpose:_purpose,...rest})=>rest)(options):undefined;
    return required('pickPaths', 'selectFolder')(nativeOptions);
  },

  /** A file path chosen in the native dialog; null when the user cancels. */
  async selectFile(options?: FileOptions): Promise<string | null> {
    if (!bridge() && browserHost?.available()) return browserHost.selectFile(options);
    const nativeOptions=options?(({purpose:_purpose,...rest})=>rest)(options):undefined;
    return required('pickPaths', 'selectFile')(nativeOptions);
  },

  /** Open a web link: through the desktop shell, or in a new browser tab without access to this page. */
  async openLink(url: string): Promise<boolean> {
    const api = bridge();
    if (api?.openExternal) return api.openExternal(url);
    if (typeof window === 'undefined' || !/^https?:\/\//i.test(url)) return false;
    return window.open(url, '_blank', 'noopener,noreferrer') !== null;
  },

  /** Open a local folder or file in the operating system's file browser (when the current host supports it). */
  async openPath(path: string): Promise<boolean> {
    if (!bridge() && browserHost?.available()) return browserHost.openPath(path);
    return required('openPaths', 'openExternal')(path);
  },

  /** The desktop app's release channel and manual update delivery (when the current host supports it). */
  updates: {
    status: async () => (!bridge() && browserHost?.available() ? browserHost.update('status', []) : required('updates', 'getDistributionStatus')()),
    configure: async (configuration: Parameters<ElectronAPI['configureUpdateChannel']>[0]) => (!bridge() && browserHost?.available() ? browserHost.update('configure', [configuration]) : required('updates', 'configureUpdateChannel')(configuration)),
    check: async () => (!bridge() && browserHost?.available() ? browserHost.update('check', []) : required('updates', 'checkForUpdate')()),
    download: async () => (!bridge() && browserHost?.available() ? browserHost.update('download', []) : required('updates', 'downloadUpdate')()),
    verifyOffline: async () => (!bridge() && browserHost?.available() ? browserHost.update('verifyOffline', []) : required('updates', 'verifyOfflineUpdate')()),
    selectPortableHome: async () => (!bridge() && browserHost?.available() ? browserHost.update('selectPortableHome', []) : required('updates', 'selectPortableUpdateHome')()),
    inspectPortable: async () => (!bridge() && browserHost?.available() ? browserHost.update('inspectPortable', []) : required('updates', 'inspectPortableUpdate')()),
    previewPortable: async (channel:'stable'|'beta',canary:Parameters<ElectronAPI['previewPortableUpdate']>[1]) => (!bridge() && browserHost?.available() ? browserHost.update('previewPortable', [channel,canary]) : required('updates', 'previewPortableUpdate')(channel,canary)),
    applyPortable: async (id:string) => (!bridge() && browserHost?.available() ? browserHost.update('applyPortable', [id]) : required('updates', 'applyPortableUpdate')(id)),
    recoverPortable: async (action:Parameters<ElectronAPI['recoverPortableUpdate']>[0],expected:Parameters<ElectronAPI['recoverPortableUpdate']>[1]) => (!bridge() && browserHost?.available() ? browserHost.update('recoverPortable', [action,expected]) : required('updates', 'recoverPortableUpdate')(action,expected)),
    launchPortable: async (expected:Parameters<ElectronAPI['launchPortableUpdate']>[0]) => (!bridge() && browserHost?.available() ? browserHost.update('launchPortable', [expected]) : required('updates', 'launchPortableUpdate')(expected)),
    inspectPortableLaunch: async (expected:Parameters<ElectronAPI['inspectPortableLaunch']>[0]) => (!bridge() && browserHost?.available() ? browserHost.update('inspectPortableLaunch', [expected]) : required('updates', 'inspectPortableLaunch')(expected)),
  },

  shared: {
    /** Public identity of this tab's same-origin cookie session; never a native/remote bearer authority. */
    browserIdentity():{origin:string;actor_id:string;revision:number}|null {
      if(bridge()||browserRemote.selection())return null;
      const connection=browserSession.connection();
      return connection&&connection.server_url===window.location.origin
        ?{origin:connection.server_url,actor_id:connection.user.id,revision:browserSession.revision()}:null;
    },
    requestOptions: (url: string, method: string) => browserRemote.selection()?browserRemote.requestOptions(url,method):browserSession.requestOptions(url, method),
    async connection(): Promise<SharedConnection | null> {
      const api = bridge();
      if (api?.getSharedConnection) return api.getSharedConnection();
      const connection = browserRemote.selection()??browserSession.connection();
      if (connection && browserHost?.available()) await browserHost?.initialize().catch(() => undefined);
      return connection;
    },
    async login(input: { server_url: string; username: string; password: string }): Promise<SharedConnection> {
      if (!bridge() && browserSession.available() && new URL(input.server_url).origin!==window.location.origin) return browserRemote.login(input);
      if (!bridge() && browserSession.available()) { await browserRemote.disconnect(); browserHost?.reset(); const connection = await browserSession.login(input); await browserHost?.initialize().catch(() => undefined); return connection; }
      return required('sharedSessions', 'loginSharedServer')(input);
    },
    async select(projectId: string): Promise<SharedConnection> {
      if (!bridge() && browserRemote.selection()) return browserRemote.select(projectId);
      if (!bridge() && browserSession.available()) { browserHost?.projectChanged(); return browserSession.select(projectId); }
      return required('sharedSessions', 'selectSharedProject')(projectId);
    },
    async disconnect(): Promise<void> {
      const api = bridge();
      if (api?.disconnectSharedServer) await api.disconnectSharedServer();
      else if (!api && browserRemote.selection()) { await browserRemote.disconnect(); }
      else if (!api) { await browserHost?.dispose().catch(() => undefined); await browserSession.disconnect(); }
    },
  },
};

/** The message to show for a failed host call: the host's own explanation, or the error's text. */
export function hostErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HostUnavailable) return error.message;
  return error instanceof Error && error.message ? error.message : fallback;
}

/** Reports carry their own bytes; a server filesystem path is never a browser download URL. */
export function downloadBrowserReport(report:{file_path:string;format:'html'|'json';content?:string|null}):void {
  if(typeof report.content!=='string')throw new Error('보고서 내용을 받지 못했습니다. 다시 내보내세요.');
  const blob=new Blob([report.content],{type:report.format==='html'?'text/html;charset=utf-8':'application/json;charset=utf-8'});
  const url=URL.createObjectURL(blob),anchor=document.createElement('a');
  anchor.href=url;
  anchor.download=(report.file_path.split(/[\\/]/).pop()||`report.${report.format}`).replace(/[\x00-\x1f<>:"|?*]/g,'_');
  document.body.appendChild(anchor);
  try{anchor.click();}finally{anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
}

/** Preview content remains an opaque-origin document with no scripts, network or parent access. */
export function reportPreviewDocument(content:string):string {
  return '<meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; img-src data:; style-src &apos;unsafe-inline&apos;; form-action &apos;none&apos;; base-uri &apos;none&apos;">'+content;
}
