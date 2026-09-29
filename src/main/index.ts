import { app, BrowserWindow, nativeTheme, shell } from 'electron';
import path from 'path';
import { BackendSupervisor } from './supervisor';
import { registerIpcHandlers } from './ipc';

// Determine execution mode
const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

let mainWindow: BrowserWindow | null = null;
const supervisor = new BackendSupervisor();

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

  // Load renderer
  if (isDev) {
    const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
    try {
      await win.loadURL(devServerUrl);
    } catch (err) {
      console.warn(`[Main] Failed to load dev server at ${devServerUrl}, falling back to static build if present:`, err);
      const prodHtmlPath = path.join(__dirname, '../../dist/index.html');
      await win.loadFile(prodHtmlPath);
    }
  } else {
    // In production, load dist/index.html
    const prodHtmlPath = path.join(__dirname, '../../dist/index.html');
    await win.loadFile(prodHtmlPath);
  }

  win.on('closed', () => {
    mainWindow = null;
  });

  return win;
}

// Single instance lock
const gotSingleInstanceLock = app.requestSingleInstanceLock();
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
