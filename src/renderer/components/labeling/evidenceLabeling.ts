import type {ImageReviewMetadata} from '../../services/datasetWorkflow';
import type {TeamWorkspace} from '../../services/teamDataApi';

export interface EvidenceEditOrigin {
  comparison_id:string;project_id:string;source:string;task:string;labelset_id:string;
  image_id:string;file_path:string;image_sha256:string;product_filter:string;lot_filter:string;revision?:number;
}
interface Storage {getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>void;removeItem:(key:string)=>void}
const key=(scope:string)=>`modu-evidence-edit:${scope}`;
const changeEvent='modu-evidence-edit-change';
export const evidenceEditScope=(scope:string,actorId?:string)=>JSON.stringify([scope,actorId||'local']);
function notify(){if(typeof window!=='undefined')window.dispatchEvent(new Event(changeEvent));}
export function subscribeEvidenceEdit(onChange:()=>void){window.addEventListener(changeEvent,onChange);window.addEventListener('storage',onChange);return()=>{window.removeEventListener(changeEvent,onChange);window.removeEventListener('storage',onChange);};}
export function evidenceEditSnapshot(storage:Storage,scope:string){return storage.getItem(key(scope));}
function valid(value:unknown):value is EvidenceEditOrigin {
  if(!value||typeof value!=='object')return false;
  const row=value as Record<string,unknown>;
  return ['comparison_id','project_id','source','task','labelset_id','image_id','file_path'].every(field=>typeof row[field]==='string'&&!!row[field])
    &&typeof row.image_sha256==='string'&&/^[a-f0-9]{64}$/.test(row.image_sha256)
    &&typeof row.product_filter==='string'&&typeof row.lot_filter==='string';
}
export function rememberEvidenceEdit(storage:Storage,scope:string,origin:EvidenceEditOrigin){storage.setItem(key(scope),JSON.stringify(origin));notify();}
export function readEvidenceEdit(storage:Storage,scope:string):EvidenceEditOrigin|null {
  try{const raw=storage.getItem(key(scope));if(!raw)return null;const saved=JSON.parse(raw);return valid(saved)?saved:null;}catch{return null;}
}
export function clearEvidenceEdit(storage:Storage,scope:string){storage.removeItem(key(scope));notify();}

/** Read current authority and image metadata; never change the historical report or acquire a lease. */
export async function openEvidenceLabeling(origin:EvidenceEditOrigin,ports:{
  sameContext:()=>boolean;mode:string;actor:string;task:string;
  metadata:()=>Promise<ImageReviewMetadata>;workspace:()=>Promise<TeamWorkspace>;
  open:(imageId:string,path:string,expected:{imageSha256:string;revision:number})=>Promise<boolean>;
}):Promise<EvidenceEditOrigin> {
  if(!valid(origin))throw new Error('원본 해시·라벨 버전 기록이 없어 현재 라벨 편집에 연결할 수 없습니다.');
  if(origin.task!==ports.task)throw new Error('현재 프로젝트의 라벨 작업 종류가 비교 기록과 다릅니다.');
  if(!ports.sameContext())throw new Error('프로젝트·연결이 바뀌었습니다.');
  const [image,workspace]=await Promise.all([ports.metadata(),ports.workspace()]);
  if(!ports.sameContext())throw new Error('프로젝트·연결이 바뀌었습니다.');
  if(workspace.scope.project_id!==origin.project_id||workspace.scope.source!==origin.source||workspace.scope.labelset_id!==origin.labelset_id)
    throw new Error('현재 프로젝트·데이터·라벨 버전이 저장된 비교와 다릅니다.');
  if(image.file_path!==origin.file_path||image.content_hash!==origin.image_sha256||!Number.isInteger(image.revision)||image.revision<1)
    throw new Error('현재 원본 경로·해시·수정 버전이 비교 기록과 일치하지 않습니다.');
  if((ports.mode==='team'&&!workspace.actor)||workspace.actor&&!['owner','labeler','trainer','reviewer'].includes(workspace.actor.role))
    throw new Error('현재 프로젝트 역할로 라벨 편집에 진입할 수 없습니다.');
  const lease=image.team?.edit_lease as {owner?:string;expires_at?:number}|null|undefined;
  if(lease&&typeof lease.expires_at==='number'&&lease.expires_at>Date.now()/1000&&lease.owner!==(workspace.actor?.name||ports.actor))
    throw new Error('다른 사용자가 이 이미지를 편집 중입니다.');
  if(!await ports.open(origin.image_id,origin.file_path,{imageSha256:origin.image_sha256,revision:image.revision}))
    throw new Error('현재 라벨을 안전하게 열지 못했습니다. 원본·라벨 버전과 저장 상태를 확인하세요.');
  if(!ports.sameContext())throw new Error('프로젝트·연결이 바뀌었습니다.');
  return {...origin,revision:image.revision};
}
