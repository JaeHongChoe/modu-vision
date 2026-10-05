import type {Category} from '../../types';
import type {BookCategory,EditLease,TeamReadiness} from '../../services/teamDataApi';
type Scope={projectDir?:string|null;project?:{id?:string;source_dataset_dir?:string|null;active_labelset_id?:string}|null;apiTransportIdentity?:string;transportRevision?:number;selectedProfileId?:string|null;apiNamespaceEpoch?:number};
export function teamDataScope(state:Scope):string{return JSON.stringify([state.projectDir,state.project?.id,state.project?.source_dataset_dir,state.project?.active_labelset_id||'default',state.apiTransportIdentity||'local',state.transportRevision||0,state.selectedProfileId||'local',state.apiNamespaceEpoch||0]);}
export function imageLeaseToken(lease:EditLease|null,imageUuid:string|undefined,now=Date.now()/1000):string|undefined{return lease&&lease.image_uuid===imageUuid&&lease.expires_at>now?lease.token:undefined;}
export function validatedBookCategories(rows:BookCategory[]):BookCategory[]{
 if(!rows.length)throw new Error('클래스를 하나 이상 추가하세요.');
 const ids=new Set<number>(),names=new Set<string>();
 return rows.map(row=>{const name=row.name.trim();if(!name)throw new Error('클래스 이름을 입력하세요.');if(!Number.isInteger(row.id)||row.id<0||ids.has(row.id))throw new Error('클래스 ID가 중복되거나 올바르지 않습니다.');if(names.has(name))throw new Error('클래스 이름이 중복됩니다.');if(!/^#[0-9a-fA-F]{6}$/.test(row.color))throw new Error('클래스 색상을 확인하세요.');ids.add(row.id);names.add(name);return{...row,name,definition:row.definition.trim(),inclusion:row.inclusion.trim(),exclusion:row.exclusion.trim(),annotation_guidance:row.annotation_guidance.trim()};});
}
export function bookPalette(rows:Pick<BookCategory,'id'|'name'|'color'>[],existing:Category[]):Category[]{
 const next:Category[]=rows.map(({id,name,color})=>({id,name,color}));const names=new Set(next.map(row=>row.name));const ids=new Set(next.map(row=>row.id));
 for(const row of existing){if(names.has(row.name))continue;let id=row.id;if(ids.has(id))id=Math.max(-1,...ids)+1;next.push({...row,id});names.add(row.name);ids.add(id);}return next;
}
export function reviewTrainingMessage(readiness:Pick<TeamReadiness,'ready'|'blockers'> & {counts:Partial<TeamReadiness['counts']>}):string{
 if(!readiness.ready)return readiness.blockers.join(' · ')||'학습할 승인 데이터를 준비하세요.';
 return `학습 대상 ${readiness.counts.eligible||0}장 · 승인 ${readiness.counts.approved||0}장 · 검수 대기 ${readiness.counts.pending||0}장. 기존 시험 분할을 유지합니다.`;
}
