import type {ModelFamily} from '../../services/modelTrainingProgram';
export type TaskStep=1|2|3|4|5|6;
import type {TaskRow} from './taskCenterModel';
export const modelFamilies:ModelFamily[]=['classification','segmentation','detection','anomaly','patch_classification','ocr','rotated_detection','rotation','defect_gan','enhancement'];
const basic=new Set(['classification','segmentation','detection','anomaly']);
export type HandoffScope={projectDir:string|null;transportRevision?:number;selectedProfileId?:string|null;apiTransportIdentity?:string;project?:{id?:string;source_dataset_dir?:string|null;active_labelset_id?:string}|null};
export interface TaskHandoff {scope:string;jobId:string;family:ModelFamily|null;kind:string;status:string;step:TaskStep;datasetPath:string;transport:string;taskKey:string;searchId?:string;selectionId?:string}
export const taskHandoffScope=(state:HandoffScope)=>JSON.stringify([state.projectDir,state.project?.id,state.project?.source_dataset_dir,state.project?.active_labelset_id||'default',state.apiTransportIdentity||'local',state.selectedProfileId||'local']);
export const taskHandoffContextScope=(state:HandoffScope)=>JSON.stringify([taskHandoffScope(state),state.transportRevision||0]);
let selectionSequence=0;
export function taskDestination(row:Pick<TaskRow,'task'|'kind'|'status'>):{step:TaskStep;family:ModelFamily|null}{
 const alias:Record<string,string>={'rotated-detection':'rotated_detection','defect-gan':'defect_gan',patch:'patch_classification'};const family=alias[row.task]||row.task;
 if(['inspection','optimization','export'].includes(row.kind))return {step:6,family:null};
 if(row.task==='labeling'||row.kind.startsWith('labeling-'))return {step:2,family:null};
 if(!modelFamilies.includes(family as ModelFamily))return {step:3,family:null};
 return {step:row.status==='completed'&&basic.has(family)?4:3,family:family as ModelFamily};
}
interface HandoffStorage {getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>void;removeItem:(key:string)=>void}
const key=(scope:string)=>`vision-task-handoff:${scope}`;
export function saveTaskHandoff(storage:HandoffStorage,state:HandoffScope,row:TaskRow):TaskHandoff{
 const scope=taskHandoffScope(state);const source=state.project?.source_dataset_dir||'';const labels=state.project?.active_labelset_id||'default';
 if(!state.projectDir||row.source!==source||(row.labelset!==labels&&row.raw.scope_kind!=='project'))throw new Error('작업 출처·라벨 세트가 현재 프로젝트와 다릅니다.');
 const target=taskDestination(row);const winner=row.kind==='automated'?row.raw.winner:undefined;
 // Search journals are not model jobs. Only an explicit winner checkpoint's
 // saved child ID can be selected as the completed candidate.
 const winnerPath=typeof winner?.checkpoint_path==='string'?winner.checkpoint_path.split(/[\\/]/):[];
 const child=winner?.job_id||winner?.trial_id||winnerPath.at(-2);
 if(row.kind==='automated'&&row.status==='completed'&&!child)throw new Error('완료 탐색의 후보 모델 ID를 찾지 못했습니다. 탐색 근거를 확인하세요.');
 const handoff:TaskHandoff={scope,selectionId:`${Date.now()}:${++selectionSequence}`,jobId:child||row.id,...target,kind:row.kind,status:row.status,datasetPath:row.raw.family_dataset_path||row.raw.dataset_path||'',transport:row.transport,taskKey:row.key,...(row.kind==='automated'?{searchId:row.id}:{})};
 storage.setItem(key(scope),JSON.stringify(handoff));if(typeof window!=='undefined')window.dispatchEvent(new Event('vision-task-handoff'));return handoff;
}
export function readTaskHandoff(storage:HandoffStorage,state:HandoffScope,family?:string):TaskHandoff|null{
 const scope=taskHandoffScope(state);const raw=storage.getItem(key(scope));if(!raw)return null;
 try{const row=JSON.parse(raw);if(row.scope===scope&&typeof row.jobId==='string'&&row.jobId&&(!family||row.family===family)&&[2,3,4,6].includes(row.step))return row;}catch{storage.removeItem(key(scope));}return null;
}
export function selectHandoffRecord<T extends {job_id:string}>(rows:T[],handoff:TaskHandoff|null):T|undefined{
 if(!handoff)return rows[0];const row=rows.find(item=>item.job_id===handoff.jobId);if(!row)throw new Error(`선택한 작업 ${handoff.jobId}을 현재 출처·라벨 세트에서 찾지 못했습니다. 작업 센터에서 다시 확인하세요.`);return row;
}
export function clearTaskHandoff(storage:HandoffStorage,state:HandoffScope){storage.removeItem(key(taskHandoffScope(state)));}
