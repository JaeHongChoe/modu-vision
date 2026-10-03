import type {LibraryImage, LibraryResolution, LibrarySelection} from '../../services/api';
import {MAX_PARITY_IMAGES} from './flowPackageRelease';

export interface ParityPick extends LibrarySelection {file_name:string; file_path:string}
export interface UnresolvedParityPick extends ParityPick {status:Exclude<LibraryResolution['status'],'found'>}
export interface ParityCohortScope {backend:string; workspace:string; actor:string; project:string; projectDir:string; source:string; task:string; labelset:string}
export function parityCohortStorageKey(scope:ParityCohortScope):string {
  return `modu:package-parity:v1:${JSON.stringify([scope.backend,scope.workspace,scope.actor,scope.project,scope.projectDir,scope.source,scope.task,scope.labelset])}`;
}
export const parityPickFrom=(image:LibraryImage):ParityPick=>({image_uuid:image.image_uuid,sha256:image.sha256,relative_path:image.relative_path,file_name:image.file_name,file_path:image.file_path});
export function readParityCohort(raw:string|null,strict=false):ParityPick[] {
  const invalid=()=>{if(strict)throw new Error('저장된 선택 형식이 손상되었습니다. 선택 해제로 목록을 초기화할 수 있습니다.');return [];};
  let value:unknown;try{value=JSON.parse(raw??'[]');}catch{return invalid();}
  if(!Array.isArray(value))return invalid();
  const rows:ParityPick[]=[];
  for(const item of value){
    if(!item||typeof item!=='object')continue;
    const row=item as ParityPick;
    if(![row.image_uuid,row.relative_path,row.file_name,row.file_path].every(v=>typeof v==='string'&&v.length>0)
      || !(row.sha256===null||typeof row.sha256==='string'&&/^[a-f0-9]{64}$/.test(row.sha256))
      || rows.some(other=>other.image_uuid===row.image_uuid))continue;
    rows.push({image_uuid:row.image_uuid,sha256:row.sha256,relative_path:row.relative_path,file_name:row.file_name,file_path:row.file_path});
    if(rows.length===MAX_PARITY_IMAGES)break;
  }
  if(strict&&rows.length!==value.length)return invalid();
  return rows;
}
export function readParityPaths(raw:string|null,strict=false):string[] {
  const invalid=()=>{if(strict)throw new Error('저장된 경로 선택 형식이 손상되었습니다. 선택 해제로 목록을 초기화할 수 있습니다.');return [];};
  let rows:unknown;try{rows=JSON.parse(raw??'[]');}catch{return invalid();}
  if(!Array.isArray(rows))return invalid();
  const paths=[...new Set(rows.filter((v):v is string=>typeof v==='string'&&v.length>0))].slice(0,MAX_PARITY_IMAGES);
  return strict&&paths.length!==rows.length?invalid():paths;
}
export function resolveParityCohort(saved:ParityPick[],results:LibraryResolution[]):{picks:ParityPick[];unresolved:UnresolvedParityPick[]} {
  const picks:ParityPick[]=[],unresolved:UnresolvedParityPick[]=[];
  for(const row of saved){
    const matches=results.filter(result=>result.image_uuid===row.image_uuid);
    const result=matches.length===1?matches[0]:undefined;
    const current=result?.current;
    if(result?.status==='found'&&current?.valid&&current.image_uuid===row.image_uuid&&current.sha256===row.sha256&&row.sha256){
      picks.push({...row,file_path:current.file_path,relative_path:current.relative_path,file_name:current.relative_path.split(/[\\/]/).pop()||row.file_name});
    }else{
      const status=result?.status&&result.status!=='found'?result.status:!result?'missing':current&&!current.valid?'unreadable':'changed';
      unresolved.push({...row,status});
    }
  }
  return {picks,unresolved};
}
export function pickParityImage(picks:ParityPick[],unresolved:UnresolvedParityPick[],image:LibraryImage):{picks:ParityPick[];unresolved:UnresolvedParityPick[]} {
  if(picks.some(row=>row.image_uuid===image.image_uuid))return {picks:picks.filter(row=>row.image_uuid!==image.image_uuid),unresolved};
  if(!image.valid)return {picks,unresolved};
  const pending=unresolved.filter(row=>row.image_uuid!==image.image_uuid);
  if(picks.length+pending.length>=MAX_PARITY_IMAGES)return {picks,unresolved};
  return {picks:[...picks,parityPickFrom(image)],unresolved:pending};
}
export const parityUnresolvedReason=(status:UnresolvedParityPick['status']):string=>({moved:'옮겨짐',changed:'내용 바뀜',unreadable:'읽지 못함',missing:'찾을 수 없음'}[status]);
