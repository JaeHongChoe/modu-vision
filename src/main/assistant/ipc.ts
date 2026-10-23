/** IPC for the built-in assistant. Every handler is limited to the application's own main frame (the same check as
 * the other renderer capabilities); no handler returns a key. */
import path from 'path';
import {app, ipcMain, safeStorage, type IpcMainInvokeEvent} from 'electron';
import {AssistantKeyStore, electronEncryptor} from './keyStore';
import {AssistantService} from './service';
import {assistantRuns} from './runHeader';

export function registerAssistantHandlers(authorize:(event:IpcMainInvokeEvent)=>void):AssistantService {
  const service = new AssistantService({store: new AssistantKeyStore(path.join(app.getPath('userData'), 'assistant'), electronEncryptor(safeStorage))});
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
  return service;
}
