/** IPC for the built-in assistant. Every handler is limited to the application's own main frame (the same check as
 * the other renderer capabilities); no handler returns a key. */
import path from 'path';
import {app, ipcMain, safeStorage, shell, type IpcMainInvokeEvent} from 'electron';
import {AssistantKeyStore, electronEncryptor} from './keyStore';
import {AssistantService} from './service';
import {assistantRuns} from './runHeader';
import {SiwcStore} from './siwcStore';
import {SiwcConnection} from './siwcConnection';

let activeService:AssistantService|undefined;
export async function disposeAssistantHandlers():Promise<void> {await activeService?.dispose();}
export function registerAssistantHandlers(authorize:(event:IpcMainInvokeEvent)=>void):AssistantService {
  const directory = path.join(app.getPath('userData'), 'assistant'), encryptor = electronEncryptor(safeStorage);
  const chatgpt = new SiwcConnection({store: new SiwcStore(path.join(directory, 'chatgpt'), encryptor,{canRecover:()=>app.hasSingleInstanceLock()}),
    openExternal: url => shell.openExternal(url)});
  const service = new AssistantService({store: new AssistantKeyStore(directory, encryptor), chatgpt});
  const handle = (channel:string, run:(...args:any[])=>unknown) => ipcMain.handle(channel, (event, ...args) => {authorize(event);return run(...args);});
  handle('assistant:get-state', () => service.state());
  handle('assistant:save-provider', input => service.save(input));
  handle('assistant:remove-key', provider => service.removeKey(provider));
  handle('assistant:remove-provider', provider => service.removeProvider(provider));
  handle('assistant:set-active', provider => service.setActive(provider));
  handle('assistant:list-models', provider => service.listModels(provider));
  handle('assistant:send', input => service.send(input));
  handle('assistant:cancel', requestId => service.cancel(requestId));
  handle('assistant:image-intent', input => service.imageIntent(input));
  handle('assistant:run-begin', (runId, tool) => assistantRuns.arm(runId, tool));
  handle('assistant:run-end', runId => {assistantRuns.disarm(runId);});
  handle('assistant:chatgpt-begin', input => service.chatgptBegin(input));
  handle('assistant:chatgpt-acknowledge-plan', id => service.chatgptAcknowledgePlanUsage(id));
  handle('assistant:chatgpt-resume-usage', id => service.chatgptResumeUsage(id));
  handle('assistant:chatgpt-recover', input => service.chatgptRecover(input));
  handle('assistant:chatgpt-cancel', () => service.chatgptCancel());
  handle('assistant:chatgpt-account', id => service.chatgptSelectAccount(id));
  handle('assistant:chatgpt-model', (id, model) => service.chatgptSelectModel(id, model));
  handle('assistant:chatgpt-signout', id => service.chatgptSignOut(id));
  activeService=service;
  app.once('will-quit', () => {void service.dispose().catch(()=>undefined);});
  return service;
}
