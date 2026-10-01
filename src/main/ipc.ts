import { app, BrowserWindow, dialog, ipcMain, shell, type OpenDialogOptions,type IpcMainInvokeEvent } from 'electron';
import path from 'path';
import fs from 'fs';
import {pathToFileURL} from 'node:url';
import type { BackendSupervisor } from './supervisor';
import {getSharedConnection,loginSharedServer,selectSharedProject,disconnectSharedServer} from './sharedSession';
import {DistributionManager} from './distributionStatus';

export function registerIpcHandlers(supervisor: BackendSupervisor): void {
  const authorizeShared=(event:IpcMainInvokeEvent)=>{
    const url=new URL(event.senderFrame?.url||'about:blank');
    const packaged=pathToFileURL(path.join(__dirname,'../../dist/index.html')).href;
    const ownMainFrame=event.senderFrame===event.sender.mainFrame;
    const trusted=ownMainFrame&&(url.href.split(/[?#]/,1)[0]===packaged||(!app.isPackaged&&url.protocol==='http:'&&['127.0.0.1','localhost'].includes(url.hostname)&&url.port==='5173'));
    if(!trusted)throw new Error('Shared server credentials require the application main frame');
  };
  const distribution=new DistributionManager({appVersion:app.getVersion(),packaged:app.isPackaged,
    appPath:process.platform==='darwin'?path.resolve(process.execPath,'../../..'):app.getAppPath(),
    executablePath:process.execPath,userDataPath:app.getPath('userData'),platform:process.platform,arch:process.arch});
  ipcMain.handle('distribution:get-status',event=>{authorizeShared(event);return distribution.status();});
  ipcMain.handle('distribution:configure-channel',(event,input)=>{authorizeShared(event);return distribution.configure(input);});
  ipcMain.handle('distribution:check-update',event=>{authorizeShared(event);return distribution.check();});
  ipcMain.handle('distribution:download-update',event=>{authorizeShared(event);return distribution.download();});
  ipcMain.handle('shared:get',event=>{authorizeShared(event);return getSharedConnection();});
  ipcMain.handle('shared:login',(event,input)=>{authorizeShared(event);return loginSharedServer(input);});
  ipcMain.handle('shared:select',(event,project_id)=>{authorizeShared(event);return selectSharedProject(project_id);});
  ipcMain.handle('shared:disconnect',event=>{authorizeShared(event);return disconnectSharedServer();});
  // 1. Backend Port & Status Queries (support both hyphenated and namespaced names)
  const getPortHandler = async () => supervisor.getPort();
  const getStatusHandler = async () => supervisor.getStatus();

  ipcMain.handle('get-backend-port', getPortHandler);
  ipcMain.handle('backend:get-port', getPortHandler);

  ipcMain.handle('get-backend-status', getStatusHandler);
  ipcMain.handle('backend:get-status', getStatusHandler);

  // 2. Native Directory Selection Dialog
  ipcMain.handle(
    'dialog:select-folder',
    async (event, options?: { title?: string; defaultPath?: string }) => {
      const win = BrowserWindow.fromWebContents(event.sender);
      const dialogOpts: OpenDialogOptions = {
        title: options?.title || 'Select Dataset Directory',
        defaultPath: options?.defaultPath,
        properties: ['openDirectory', 'createDirectory'],
      };
      const result = win
        ? await dialog.showOpenDialog(win, dialogOpts)
        : await dialog.showOpenDialog(dialogOpts);

      if (result.canceled || result.filePaths.length === 0) {
        return null;
      }
      return result.filePaths[0];
    }
  );

  // 3. Native File Selection Dialog
  ipcMain.handle(
    'dialog:select-file',
    async (
      event,
      options?: {
        title?: string;
        defaultPath?: string;
        filters?: Array<{ name: string; extensions: string[] }>;
      }
    ) => {
      const win = BrowserWindow.fromWebContents(event.sender);
      const dialogOpts: OpenDialogOptions = {
        title: options?.title || 'Select File',
        defaultPath: options?.defaultPath,
        filters: options?.filters || [
          { name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'bmp', 'webp'] },
          { name: 'All Files', extensions: ['*'] },
        ],
        properties: ['openFile'],
      };
      const result = win
        ? await dialog.showOpenDialog(win, dialogOpts)
        : await dialog.showOpenDialog(dialogOpts);

      if (result.canceled || result.filePaths.length === 0) {
        return null;
      }
      return result.filePaths[0];
    }
  );

  // 4. External URL and Local Report File Open Handler
  ipcMain.handle('shell:open-external', async (_event, urlOrPath: string) => {
    if (!urlOrPath || typeof urlOrPath !== 'string') {
      return false;
    }

    try {
      if (
        urlOrPath.startsWith('http://') ||
        urlOrPath.startsWith('https://') ||
        urlOrPath.startsWith('file://')
      ) {
        await shell.openExternal(urlOrPath);
        return true;
      }

      // Local filesystem path (e.g. exported HTML/JSON report)
      const resolvedPath = path.isAbsolute(urlOrPath)
        ? urlOrPath
        : path.resolve(process.cwd(), urlOrPath);

      if (fs.existsSync(resolvedPath)) {
        const error = await shell.openPath(resolvedPath);
        return error === '';
      } else {
        console.warn(`[IPC] shell:open-external target not found on disk: ${resolvedPath}`);
        return false;
      }
    } catch (err) {
      console.error('[IPC] Failed to open external target:', urlOrPath, err);
      return false;
    }
  });

  // 5. Broadcast Supervisor Events to All Renderer Windows
  supervisor.on('status-change', (statusInfo) => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) {
        win.webContents.send('backend:status-changed', supervisor.getStatus());
      }
    }
  });

  supervisor.on('crashed', (crashData) => {
    for (const win of BrowserWindow.getAllWindows()) {
      if (!win.isDestroyed()) {
        win.webContents.send('backend:crashed', crashData);
      }
    }
  });
}
