import type {AssistantConfigState} from '../../../types/electron';

function selected(config:AssistantConfigState|null){
  const provider=config?.active==='chatgpt'?config.providers.find(row=>row.provider==='chatgpt'):undefined;
  const account=config?.chatgpt?.accounts.find(row=>row.recordId===config.chatgpt?.active);
  return {provider,account};
}
/** Durable account/model identity; live authority revisions are fenced separately. */
export function taskConnectionKey(config:AssistantConfigState|null):string {
  const {provider,account}=selected(config);
  return JSON.stringify([config?.active??null,account?.recordId??null,provider?.model??null]);
}
export function taskConnectionGeneration(config:AssistantConfigState|null):string {
  const {provider}=selected(config);
  return JSON.stringify([config?.chatgpt?.authority??null,provider?.connectionAuthority??null]);
}
export function taskConnectionReady(config:AssistantConfigState|null):boolean {
  const {provider,account}=selected(config);
  return Boolean(config?.active==='chatgpt'&&provider?.ready&&account?.signedIn&&account.planUsageEnabled
    &&!account.usagePaused&&account.model&&account.model===provider.model);
}
