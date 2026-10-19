import { app, BrowserWindow, Menu, nativeTheme, shell } from 'electron';
import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';
import { BackendSupervisor } from './supervisor';
import { registerIpcHandlers } from './ipc';
import { acquireAppInstanceLock } from './instanceLock';
import {isSharedServerUrl,sharedHeaders} from './sharedSession';
import {authenticateMainLaunch} from './applicationLaunch';

// Determine execution mode
const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

let mainWindow: BrowserWindow | null = null;
let supervisor:BackendSupervisor|undefined;

function trustedRendererUrl(url: string, packagedUrl: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'file:') {
      return parsed.href.split(/[?#]/, 1)[0] === packagedUrl;
    }
    return isDev && parsed.protocol === 'http:'
      && ['127.0.0.1', 'localhost'].includes(parsed.hostname)
      && parsed.port === '5173';
  } catch {
    return false;
  }
}

// The user's zoom (Ctrl/Cmd +, -, 0) is a device preference kept next to the other UI settings: a first-time user
// with a 4K monitor or weaker eyesight can make the whole app larger once and keep it.
const ZOOM_FILE = 'ui-zoom.json';
const ZOOM_MIN = 0.8, ZOOM_MAX = 2.0;
function readSavedZoom(): number {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(app.getPath('userData'), ZOOM_FILE), 'utf8')) as { zoom?: unknown };
    const zoom = Number(raw.zoom);
    return Number.isFinite(zoom) && zoom >= ZOOM_MIN && zoom <= ZOOM_MAX ? zoom : 1;
  } catch { return 1; }
}
function rememberZoom(zoom: number): void {
  try { fs.writeFileSync(path.join(app.getPath('userData'), ZOOM_FILE), JSON.stringify({ zoom })); }
  catch { /* The zoom still applies for this session. */ }
}
function installZoomMenu(win: BrowserWindow): void {
  const step = (delta: number) => {
    const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((win.webContents.getZoomFactor() + delta) * 10) / 10));
    win.webContents.setZoomFactor(next);
    rememberZoom(next);
  };
  const reset = () => { win.webContents.setZoomFactor(1); rememberZoom(1); };
  const isMac = process.platform === 'darwin';
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac ? [{ role: 'appMenu' as const }] : []),
    { role: 'editMenu' },
    { label: '보기', submenu: [
      { label: '크게 보기', accelerator: 'CommandOrControl+=', click: () => step(0.1) },
      { label: '크게 보기', accelerator: 'CommandOrControl+Plus', visible: false, click: () => step(0.1) },
      { label: '작게 보기', accelerator: 'CommandOrControl+-', click: () => step(-0.1) },
      { label: '기본 크기', accelerator: 'CommandOrControl+0', click: reset },
      { type: 'separator' },
      { role: 'togglefullscreen', label: '전체 화면' },
      ...(isDev ? [{ type: 'separator' as const }, { role: 'toggleDevTools' as const }] : []),
    ] },
    { role: 'windowMenu', label: '창' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  win.webContents.on('did-finish-load', () => {
    const saved = readSavedZoom();
    if (saved !== 1) win.webContents.setZoomFactor(saved);
  });
}

async function createWindow(): Promise<BrowserWindow> {
  // Enforce native dark theme styling
  nativeTheme.themeSource = 'dark';

  const win = new BrowserWindow({
    title: 'Vision AI Studio',
    width: 1440,
    height: 900,
    minWidth: 1280,
    minHeight: 800,
    backgroundColor: '#0f172a', // Tailwind slate-900 prevents white flash
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 16 },
    show: false, // Show once ready-to-show
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // The preload only uses contextBridge and ipcRenderer, which sandboxed
      // preloads provide; the renderer gets no Node.js access.
      sandbox: true,
      webSecurity: true,
    },
  });
  const prodHtmlPath = path.join(__dirname, '../../dist/index.html');
  const packagedUrl = pathToFileURL(prodHtmlPath).href;

  // The CSP admits HTTPS/WSS so a shared project server can be used; only the
  // connected server origin is actually reachable from the renderer.
  win.webContents.session.webRequest.onBeforeRequest({ urls: ['https://*/*', 'wss://*/*'] }, (details, callback) => {
    callback({ cancel: !isSharedServerUrl(details.url) });
  });

  // Keep the capability in the main process. This covers fetch, <img>, canvas
  // image loads, and WebSocket handshakes without exposing it to page scripts.
  win.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
    if (win.isDestroyed() || win.webContents.isDestroyed()) {
      callback({ requestHeaders: details.requestHeaders });
      return;
    }
    const port = supervisor?.getPort();
    const token = supervisor?.getApiToken();
    const frameUrl = details.frame?.url;
    if (details.method === 'OPTIONS'
      || details.webContentsId !== win.webContents.id
      || !frameUrl || !trustedRendererUrl(frameUrl, packagedUrl)
      || !trustedRendererUrl(win.webContents.getURL(), packagedUrl)) {
      callback({ requestHeaders: details.requestHeaders });
      return;
    }
    try {
      const target = new URL(details.url);
      const remoteHeaders=sharedHeaders(details.url);
      if(Object.keys(remoteHeaders).length){
        callback({requestHeaders:{...details.requestHeaders,...remoteHeaders}});return;
      }
      if (port && token && ['http:', 'ws:'].includes(target.protocol)
        && target.hostname === '127.0.0.1' && Number(target.port) === port) {
        callback({ requestHeaders: { ...details.requestHeaders, 'X-Vision-Token': token } });
        return;
      }
    } catch {
      // Non-URL requests are sent without the capability.
    }
    callback({ requestHeaders: details.requestHeaders });
  });

  installZoomMenu(win);

  // Smooth window display without flicker
  win.once('ready-to-show', () => {
    win.show();
  });

  // Handle external navigation securely
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https:') || url.startsWith('http:')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  win.webContents.on('will-navigate', (event, url) => {
    if (trustedRendererUrl(url, packagedUrl)) return;
    event.preventDefault();
    if (url.startsWith('https:') || url.startsWith('http:')) {
      shell.openExternal(url);
    }
  });

  // Load renderer. MV_VIEW_MODE=full (the test launches) keeps every control on screen through the page URL; a user's
  // first launch shows the simple view and a stored choice wins either way.
  const viewMode = process.env.MV_VIEW_MODE === 'full' || process.env.MV_VIEW_MODE === 'simple' ? process.env.MV_VIEW_MODE : null;
  const loadProd = () => win.loadFile(prodHtmlPath, viewMode ? { query: { view: viewMode } } : undefined);
  if (isDev) {
    const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
    try {
      await win.loadURL(viewMode ? `${devServerUrl}${devServerUrl.includes('?') ? '&' : '?'}view=${viewMode}` : devServerUrl);
    } catch (err) {
      console.warn(`[Main] Failed to load dev server at ${devServerUrl}, falling back to static build if present:`, err);
      await loadProd();
    }
  } else {
    // In production, load dist/index.html
    await loadProd();
  }

  win.on('closed', () => {
    mainWindow = null;
  });

  return win;
}

// Authenticate before instance-lock/userData mkdir, supervisor dependency/project
// bootstrap, IPC or a renderer can observe mutable application scopes.
async function startApplication():Promise<void>{
 const owned=await authenticateMainLaunch();
 if(owned)for(const key of Object.keys(process.env))if(key.startsWith('VISION_APPLICATION_'))delete process.env[key];
 const gotSingleInstanceLock=acquireAppInstanceLock(app,owned?.root||process.env.VISION_AI_STUDIO_USER_DATA_DIR);
 if(!gotSingleInstanceLock){owned?.refuse();app.quit();return;}
 supervisor=new BackendSupervisor({ownedApplicationLaunch:owned||undefined});
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    // Register IPC handlers
    registerIpcHandlers(supervisor!);

    // Launch Python Backend Supervisor in background
    supervisor!.startBackend().catch((err) => {
      console.error('[Main] Python backend failed to launch:', err);
    });

    // Create primary application window
    mainWindow = await createWindow();

    app.on('activate', async () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = await createWindow();
      }
    });
  });
}
startApplication().catch(()=>{app.exit(2);});

// Graceful application shutdown and supervisor cleanup
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

let isQuitting = false;
let quitDecisionSettled = false;
app.on('before-quit', async (event) => {
  if (quitDecisionSettled) return;
  event.preventDefault();
  if (!isQuitting) {
    isQuitting = true;
    console.log('[Main] Application quit initiated. Stopping backend daemon...');
    try {
      await supervisor?.stopBackend();
    } catch (err) {
      console.error('[Main] Error during supervisor shutdown:', err);
    } finally {
      quitDecisionSettled = true;
      app.quit();
    }
  }
});
