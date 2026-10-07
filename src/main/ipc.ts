import { app, BrowserWindow, dialog, ipcMain, shell, type OpenDialogOptions,type IpcMainInvokeEvent } from 'electron';
import path from 'path';
import fs from 'fs';
import {fileURLToPath,pathToFileURL} from 'node:url';
import type { BackendSupervisor } from './supervisor';
import {getSharedConnection,loginSharedServer,selectSharedProject,disconnectSharedServer} from './sharedSession';
import {DistributionManager} from './distributionStatus';
import {PortableUpdateManager,validatedCanaryPins} from './portableUpdate';

// Documents, images and report folders the studio produces. Executables,
// scripts, shortcuts and application bundles are never opened from the renderer.
const OPENABLE_FILE_EXTENSIONS=new Set(['.html','.htm','.json','.csv','.txt','.md','.pdf','.png','.jpg','.jpeg',
  '.bmp','.webp','.tif','.tiff','.log','.xml','.yaml','.yml','.zip']);
const PACKAGE_DIRECTORY_EXTENSIONS=new Set(['.app','.appex','.bundle','.framework','.plugin','.prefpane','.workflow',
  '.action','.kext','.pkg','.mpkg','.xpc','.saver','.qlgenerator','.mdimporter','.osax','.component','.service','.scptd']);

export type OpenTarget={kind:'url';url:string}|{kind:'path';path:string}|{kind:'rejected';reason:string};

export function classifyOpenTarget(value:unknown):OpenTarget {
  const rejected=(reason:string):OpenTarget=>({kind:'rejected',reason});
  if(typeof value!=='string'||!value||value.length>4096)return rejected('invalid_target');
  let candidate=value;
  // A scheme, but not a Windows drive letter such as C:\
  if(/^[a-z][a-z0-9+.-]*:/i.test(value)&&!/^[a-z]:[\\/]/i.test(value)){
    let url:URL;
    try{url=new URL(value);}catch{return rejected('malformed_url');}
    if(url.protocol==='https:'||url.protocol==='http:')return {kind:'url',url:url.href};
    if(url.protocol!=='file:')return rejected('unsupported_scheme');
    if(url.host&&url.host!=='localhost')return rejected('network_path');
    try{candidate=fileURLToPath(url);}catch{return rejected('malformed_file_url');}
  }
  // UNC paths and the \\?\ / \\.\ prefixes (two leading separators) would make the
  // filesystem calls below contact a remote host, so they are refused before any access.
  if(/^[\\/]{2}/.test(candidate))return rejected('network_path');
  if(!path.isAbsolute(candidate))return rejected('relative_path');
  let real:string;
  try{real=fs.realpathSync(candidate);}catch{return rejected('missing');}
  const stat=fs.statSync(real);
  const extension=path.extname(real).toLowerCase();
  if(stat.isDirectory()){
    // Bundles execute when opened; detect them by layout, not only by name.
    if(PACKAGE_DIRECTORY_EXTENSIONS.has(extension)||fs.existsSync(path.join(real,'Contents','Info.plist')))return rejected('application_bundle');
    return {kind:'path',path:real};
  }
  if(stat.isFile()&&OPENABLE_FILE_EXTENSIONS.has(extension)){
    // A macOS alias is a bookmark file that can carry any name and resolves on open.
    const header=Buffer.alloc(16);
    const descriptor=fs.openSync(real,'r');
    try{fs.readSync(descriptor,header,0,16,0);}finally{fs.closeSync(descriptor);}
    if(header.subarray(0,4).toString('latin1')==='book'&&header.includes(Buffer.from('mark','latin1')))return rejected('alias_file');
    return {kind:'path',path:real};
  }
  return rejected('file_type_not_openable');
}

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
  ipcMain.handle('distribution:verify-offline-update',async event=>{
    authorizeShared(event);
    if(!app.isPackaged)throw new Error('Offline release handoff requires the packaged application and its provisioned publisher authority');
    const win=BrowserWindow.fromWebContents(event.sender),options:OpenDialogOptions={title:'서명된 오프라인 릴리스 목록 선택',properties:['openFile'],filters:[{name:'Release manifest',extensions:['json']}]};
    const result=win?await dialog.showOpenDialog(win,options):await dialog.showOpenDialog(options);
    if(result.canceled||!result.filePaths[0])return null;
    authorizeShared(event);return distribution.verifyOffline(result.filePaths[0]);
  });
  const portable=new PortableUpdateManager({packaged:app.isPackaged,platform:process.platform,arch:process.arch,
    resourcesPath:process.resourcesPath,userDataPath:app.getPath('userData'),appPath:distribution.options.appPath,
    signature:target=>distribution.signature(target)});
  const choosePortable=async(event:IpcMainInvokeEvent,options:OpenDialogOptions)=>{
    authorizeShared(event);const win=BrowserWindow.fromWebContents(event.sender);
    const result=win?await dialog.showOpenDialog(win,options):await dialog.showOpenDialog(options);
    authorizeShared(event);return result.canceled?null:result.filePaths[0]||null;
  };
  ipcMain.handle('distribution:select-portable-home',async event=>{
    authorizeShared(event);await portable.ensureSupported();
    const selected=await choosePortable(event,{title:'중지된 별도 portable 설치 폴더 선택',properties:['openDirectory']});
    return selected?portable.select(selected):null;
  });
  ipcMain.handle('distribution:inspect-portable',event=>{authorizeShared(event);return portable.inspect();});
  ipcMain.handle('distribution:preview-portable',async(event,channel,canary)=>{
    authorizeShared(event);const pins=validatedCanaryPins(canary);await portable.ensureSupported();
    if(!['stable','beta'].includes(channel))throw Error('Select a stable or beta channel');
    const selected=await choosePortable(event,{title:'portable 앱의 서명된 오프라인 릴리스 목록 선택',properties:['openFile'],filters:[{name:'Release manifest',extensions:['json']}]});
    return selected?portable.preview(selected,channel,pins):null;
  });
  ipcMain.handle('distribution:apply-portable',(event,id)=>{authorizeShared(event);return portable.apply(id);});
  ipcMain.handle('distribution:recover-portable',(event,action,expected)=>{authorizeShared(event);return portable.recover(action,expected);});
  ipcMain.handle('distribution:launch-portable',(event,expected)=>{authorizeShared(event);return portable.launch(expected);});
  ipcMain.handle('distribution:inspect-portable-launch',(event,expected)=>{authorizeShared(event);return portable.inspectLaunch(expected);});
  ipcMain.handle('shared:get',event=>{authorizeShared(event);return getSharedConnection();});
  ipcMain.handle('shared:login',(event,input)=>{authorizeShared(event);return loginSharedServer(input);});
  ipcMain.handle('shared:select',(event,project_id)=>{authorizeShared(event);return selectSharedProject(project_id);});
  ipcMain.handle('shared:disconnect',event=>{authorizeShared(event);return disconnectSharedServer();});
  // 1. Backend Port & Status Queries (support both hyphenated and namespaced names)
  // Every renderer capability is limited to the application's own main frame.
  const getPortHandler = async (event: IpcMainInvokeEvent) => { authorizeShared(event); return supervisor.getPort(); };
  const getStatusHandler = async (event: IpcMainInvokeEvent) => { authorizeShared(event); return supervisor.getStatus(); };

  ipcMain.handle('get-backend-port', getPortHandler);
  ipcMain.handle('backend:get-port', getPortHandler);

  ipcMain.handle('get-backend-status', getStatusHandler);
  ipcMain.handle('backend:get-status', getStatusHandler);

  // 2. Native Directory Selection Dialog
  ipcMain.handle(
    'dialog:select-folder',
    async (event, options?: { title?: string; defaultPath?: string }) => {
      authorizeShared(event);
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
      authorizeShared(event);
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

  // 4. External links, report files and output folders
  ipcMain.handle('shell:open-external', async (event, urlOrPath: unknown) => {
    authorizeShared(event);
    const target = classifyOpenTarget(urlOrPath);
    if (target.kind === 'rejected') {
      console.warn(`[IPC] shell:open-external refused: ${target.reason}`);
      return false;
    }
    try {
      if (target.kind === 'url') {
        await shell.openExternal(target.url);
        return true;
      }
      return (await shell.openPath(target.path)) === '';
    } catch (err) {
      console.error('[IPC] Failed to open external target:', err);
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
