/** Non-secret display preferences only. Credentials and conversation content never enter browser storage. */
import type {AssistantReasoningEffort} from '../../types/electron';
const EFFORTS=new Set(['none','minimal','low','medium','high','xhigh','max','ultra']);
const preferenceKey=(scope:string)=>`vision.assistant.effort.v1.${encodeURIComponent(scope)}`;
export function readAssistantEffort(scope:string):AssistantReasoningEffort|undefined {
  try{const value=globalThis.localStorage?.getItem(preferenceKey(scope));return value&&EFFORTS.has(value)?value as AssistantReasoningEffort:undefined;}catch{return undefined;}
}
export function saveAssistantEffort(scope:string,value:AssistantReasoningEffort|undefined):void {
  try{if(value&&EFFORTS.has(value))globalThis.localStorage?.setItem(preferenceKey(scope),value);else globalThis.localStorage?.removeItem(preferenceKey(scope));}catch{/* Preferences are optional; the current request remains usable. */}
}
