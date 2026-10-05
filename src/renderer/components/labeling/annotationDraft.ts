import type {AnnotationItem} from '../../types';
export interface AnnotationDraft {
 schema:1;scope:string;image_path:string;image_uuid:string;source_sha256:string;base_revision:number;
 base_annotations:AnnotationItem[];annotations:AnnotationItem[];width:number;height:number;saved_at:string;nonce:string;
}
type Storage=Pick<globalThis.Storage,'getItem'|'setItem'|'removeItem'>;
const MAX_UNITS=1024*1024; // A bounded two MiB UTF-16 value; quota refusal remains visible.
export function draftKey(scope:string,imagePath:string){return 'modu-annotation-draft:v1:'+JSON.stringify([scope,imagePath]);}
function checked(value:unknown,scope:string,imagePath:string):AnnotationDraft {
 if(!value||typeof value!=='object')throw new Error('초안 형식을 확인하세요.');
 const d=value as AnnotationDraft;
 if(d.schema!==1||d.scope!==scope||d.image_path!==imagePath)throw new Error('초안 문맥 scope가 현재 이미지와 다릅니다.');
 if(!d.image_uuid||!d.nonce||typeof d.nonce!=='string'||typeof d.saved_at!=='string'||!Number.isSafeInteger(d.base_revision)||d.base_revision<1||!/^[a-f0-9]{64}$/.test(d.source_sha256)||!Number.isInteger(d.width)||!Number.isInteger(d.height)||d.width<1||d.height<1)throw new Error('초안의 원본·버전·크기 정보를 확인하세요.');
 for(const rows of [d.base_annotations,d.annotations])if(!Array.isArray(rows)||rows.length>10000||rows.some(r=>!r||typeof r!=='object'||!['tag','bbox','polygon','rotated_bbox','brush_mask'].includes(r.type)))throw new Error('초안 라벨 형식을 확인하세요.');
 return d;
}
export function readDraft(storage:Storage,scope:string,imagePath:string):AnnotationDraft|null {
 const raw=storage.getItem(draftKey(scope,imagePath));if(raw===null)return null;
 if(raw.length>MAX_UNITS)throw new Error('초안 크기 size가 허용 범위를 넘었습니다.');
 return checked(JSON.parse(raw),scope,imagePath);
}
export function saveDraft(storage:Storage,draft:AnnotationDraft):void {
 checked(draft,draft.scope,draft.image_path);const raw=JSON.stringify(draft);
 if(raw.length>MAX_UNITS)throw new Error('초안 크기 size가 2 MiB 한도를 넘었습니다. 현재 수정 내용을 유지하고 서버에 저장하세요.');
 const key=draftKey(draft.scope,draft.image_path);storage.setItem(key,raw);
 if(storage.getItem(key)!==raw)throw new Error('초안 저장 결과를 확인하지 못했습니다. 현재 수정 내용을 유지하세요.');
}
export function removeDraft(storage:Storage,draft:AnnotationDraft):boolean {
 const key=draftKey(draft.scope,draft.image_path);
 if(storage.getItem(key)!==JSON.stringify(draft))return false;
 storage.removeItem(key);return storage.getItem(key)===null;
}
export function compareDraft(draft:AnnotationDraft,current:{image_uuid:string;content_hash:string;revision:number;width:number;height:number},annotations:AnnotationItem[]){
 return {revision_changed:draft.base_revision!==current.revision,can_apply:draft.image_uuid===current.image_uuid&&draft.source_sha256===current.content_hash&&draft.width===current.width&&draft.height===current.height,current_revision:current.revision,current_annotations:annotations};
}
