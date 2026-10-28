/** Local attachment preparation only. No upload, filesystem path or credential access. */
import {normalizeSelectedImage} from './assistantImagePreparation';
import type {AssistantReasoningEffort} from '../../types/electron';
export interface AssistantAttachment {binding?:string;fileName:string;mediaType:'image/png'|'image/jpeg'|'image/webp';base64:string;imageIdentity:string;previewUrl:string}
export interface AssistantQuestionOptions {attachment?:AssistantAttachment;reasoningEffort?:AssistantReasoningEffort;explainOnly?:boolean}
const TYPES=new Set(['image/png','image/jpeg','image/webp']);
export function isAssistantExecutionRequest(text:string):boolean {
  const value=text.trim().replace(/"[^"\n]*"|'[^'\n]*'|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|`[^`]*`/g,' ');
  if(/(?:해줘|해주세요|해죠)(?:라는|라고)\s*(?:요청|명령|문구|말|표현)|(?:하지(?:는)?\s*(?:마|말)|안\s*(?:실행|학습|진행|저장|시작))/.test(value)||/\b(?:run|execute|train)\s+(?:nothing|no\b)|\b(?:don't|do not)\s+(?:run|execute|train)\b/i.test(value))return false;
  if(/(?:설명|안내|조회|확인)\s?만/.test(value)||/\b(?:just|only)\s+explain\b|\bdo\s+not\s+(?:run|execute|train)\b/i.test(value))return false;
  if(/(?:뜻|의미|문구|표현|예문|문장).*(?:뭐|무슨|왜|인지|[?？])|(?:뭐|무슨|왜).*(?:뜻|의미|문구|표현|예문|문장)/.test(value))return false;
  // Advice/inspection questions do not authorize an action just because they mention a model or a workflow.
  if(!/(?:시작|진행|만들|생성|저장|적용|가져|분할|학습|평가|검사|수정|고쳐|라벨|실행|준비|연결)/.test(value)&&!/(?:train|evaluate|inspect|create|save|import|split|run|fix|label|prepare|build)/i.test(value))return false;
  if(/(?:설명|알려|무엇|뭐야|왜|어떻게|해야\s?하|해야\s?되|되나요|인가요)/.test(value)&&!/(?:수정해|고쳐|진행해|시작해|만들어|실행해|저장해)/.test(value))return false;
  return /(?:해\s?(?:줘|주세요|주렴|줄래)|해죠|시작하자|진행하자|만들어\s?(?:줘|주세요)|고쳐\s?(?:줘|주세요)|학습시켜|부탁)/.test(value)
    || /^(?:please\s+|(?:can|could|would)\s+you\s+)?(?:train|evaluate|inspect|create|save|import|split|run|fix|label|prepare|build)\b/i.test(value);
}
export async function prepareAssistantAttachment(file:Blob & {name?:string}):Promise<AssistantAttachment> {
  if(!TYPES.has(file.type))throw new Error('PNG·JPEG·WebP 이미지를 선택하세요.');
  if(!file.size)throw new Error('이미지 파일이 비어 있습니다.');
  if(file.size>5*1024*1024)throw new Error('이미지는 5MB 이하로 선택하세요.');
  const prepared=await normalizeSelectedImage(file);
  const bytes=new Uint8Array(await prepared.arrayBuffer());
  let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  const base64=btoa(binary),digest=await crypto.subtle.digest('SHA-256',bytes);
  const imageIdentity=Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('');
  const fileName=(file.name||'첨부 이미지').split(/[\\/]/).at(-1)!.replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,160);
  const mediaType=prepared.type as AssistantAttachment['mediaType'];
  return {fileName,mediaType,base64,imageIdentity,previewUrl:`data:${mediaType};base64,${base64}`};
}
export async function attachmentFromPixels(image:{mediaType:AssistantAttachment['mediaType'];base64:string},fileName='선택 이미지'):Promise<AssistantAttachment>{
  const binary=atob(image.base64),bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
  return prepareAssistantAttachment(Object.assign(new Blob([bytes],{type:image.mediaType}),{name:fileName}));
}
