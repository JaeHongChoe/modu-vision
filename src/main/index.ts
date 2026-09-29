import { app, BrowserWindow, nativeTheme, shell } from 'electron';
import path from 'path';
import { pathToFileURL } from 'url';
import { BackendSupervisor } from './supervisor';
import { registerIpcHandlers } from './ipc';
import { acquireAppInstanceLock } from './instanceLock';

// Determine execution mode
const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

let mainWindow: BrowserWindow | null = null;
const supervisor = new BackendSupervisor();

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
      sandbox: false,
      webSecurity: true,
    },
  });
  const prodHtmlPath = path.join(__dirname, '../../dist/index.html');
  const packagedUrl = pathToFileURL(prodHtmlPath).href;

  // Keep the capability in the main process. This covers fetch, <img>, canvas
  // image loads, and WebSocket handshakes without exposing it to page scripts.
  win.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
    const port = supervisor.getPort();
    const token = supervisor.getApiToken();
    const frameUrl = details.frame?.url;
    if (!port || !token || details.method === 'OPTIONS'
      || details.webContentsId !== win.webContents.id
      || !frameUrl || !trustedRendererUrl(frameUrl, packagedUrl)
      || !trustedRendererUrl(win.webContents.getURL(), packagedUrl)) {
      callback({ requestHeaders: details.requestHeaders });
      return;
    }
    try {
      const target = new URL(details.url);
      if (['http:', 'ws:'].includes(target.protocol)
        && target.hostname === '127.0.0.1' && Number(target.port) === port) {
        callback({ requestHeaders: { ...details.requestHeaders, 'X-Vision-Token': token } });
        return;
      }
    } catch {
      // Non-URL requests are sent without the capability.
    }
    callback({ requestHeaders: details.requestHeaders });
  });

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

  // Load renderer
  if (isDev) {
    const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
    try {
      await win.loadURL(devServerUrl);
    } catch (err) {
      console.warn(`[Main] Failed to load dev server at ${devServerUrl}, falling back to static build if present:`, err);
      await win.loadFile(prodHtmlPath);
    }
  } else {
    // In production, load dist/index.html
    await win.loadFile(prodHtmlPath);
  }

  win.on('closed', () => {
    mainWindow = null;
  });

  return win;
}

// Single instance lock
const gotSingleInstanceLock = acquireAppInstanceLock(app, process.env.VISION_AI_STUDIO_USER_DATA_DIR);
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    // Register IPC handlers
    registerIpcHandlers(supervisor);

    // Launch Python Backend Supervisor in background
    supervisor.startBackend().catch((err) => {
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

// Graceful application shutdown and supervisor cleanup
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

let isQuitting = false;
app.on('before-quit', async (event) => {
  if (!isQuitting) {
    isQuitting = true;
    event.preventDefault();
    console.log('[Main] Application quit initiated. Stopping backend daemon...');
    try {
      await supervisor.stopBackend();
    } catch (err) {
      console.error('[Main] Error during supervisor shutdown:', err);
    } finally {
      app.quit();
    }
  }
});
