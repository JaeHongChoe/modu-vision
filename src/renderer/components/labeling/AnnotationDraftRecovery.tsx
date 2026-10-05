import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {getApiPersistenceIdentity,getProjectContext,getProjectContextGeneration,subscribeProjectContext} from '../../services/api';
import {datasetWorkflow,workflowError,type ImageReviewMetadata} from '../../services/datasetWorkflow';
import type {AnnotationItem} from '../../types';
import {WorkspaceDialog} from '../common/WorkspaceDialog';
import {compareDraft,readDraft,removeDraft,saveDraft,type AnnotationDraft} from './annotationDraft';
function namespace(){
 const p=useProjectStore.getState(),c=getProjectContext(),a=useAnnotationStore.getState();
 if(!p.project?.source_dataset_dir||c&&c.project_id!==p.project.id||getApiPersistenceIdentity()!=='local'&&!c)return '';
 return JSON.stringify([getApiPersistenceIdentity(),c?.workspace_id,c?.actor_id,p.projectDir,p.project.id,p.project.source_dataset_dir,p.project.active_labelset_id||'default',p.project.task,c?.actor_id?'account':a.reviewerName.trim()||'operator']);
}
type Base={metadata:ImageReviewMetadata;annotations:AnnotationItem[]};
type Comparison={metadata:ImageReviewMetadata;annotations:AnnotationItem[];draft:AnnotationDraft};
const clone=<T,>(v:T):T=>JSON.parse(JSON.stringify(v));
const button='rounded border border-slate-600 px-3 py-1.5 disabled:opacity-40';
export function AnnotationDraftRecovery(){
 const a=useAnnotationStore(),project=useProjectStore(s=>s.project),projectDir=useProjectStore(s=>s.projectDir);
 const epoch=useSyncExternalStore(subscribeProjectContext,getProjectContextGeneration,getProjectContextGeneration);
 const scope=namespace(),imagePath=a.currentImage?.file_path||'',key=JSON.stringify([scope,imagePath,epoch]);
 const [saved,setSaved]=useState<{key:string;draft:AnnotationDraft}|null>(null),[error,setError]=useState(''),[open,setOpen]=useState(false),[comparison,setComparison]=useState<Comparison|null>(null),[accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false);
 const base=useRef<Base|null>(null),owned=useRef<AnnotationDraft|null>(null);const draft=saved?.key===key?saved.draft:null;
 const current=()=>namespace()===scope&&getProjectContextGeneration()===epoch&&useAnnotationStore.getState().currentImage?.file_path===imagePath;
 useEffect(()=>{
  base.current=null;owned.current=null;setSaved(null);setError('');setOpen(false);setComparison(null);setAccepted(false);setBusy(false);
  if(!scope||!imagePath)return;
  const sync=()=>{
   if(!current())return;const state=useAnnotationStore.getState(),meta=state.metadata;
   const loaded=JSON.stringify([getApiPersistenceIdentity(),epoch,useDatasetStore.getState().folderPath,state.task]);
   if(state.annotationLoadStatus!=='ready'||state.loadedAnnotationContext!==loaded||!meta||meta.file_path!==imagePath)return;
   try{
    const existing=readDraft(localStorage,scope,imagePath);
    if(!state.isDirty){
     if(owned.current&&meta.revision>owned.current.base_revision){removeDraft(localStorage,owned.current);owned.current=null;}
     base.current={metadata:clone(meta),annotations:clone(state.annotations)};
     const remaining=readDraft(localStorage,scope,imagePath);setSaved(remaining?{key,draft:remaining}:null);return;
    }
    if(existing&&existing.nonce!==owned.current?.nonce){setSaved({key,draft:existing});return;}
    if(!base.current)return;const starting=base.current;
    const next:AnnotationDraft={schema:1,scope,image_path:imagePath,image_uuid:starting.metadata.image_uuid,source_sha256:starting.metadata.content_hash,base_revision:starting.metadata.revision,base_annotations:starting.annotations,annotations:clone(state.annotations),width:starting.metadata.width,height:starting.metadata.height,saved_at:new Date().toISOString(),nonce:owned.current?.nonce||crypto.randomUUID()};
    saveDraft(localStorage,next);owned.current=next;setSaved({key,draft:next});setError('');
   }catch(cause){setError('이 컴퓨터의 초안을 저장·조회하지 못했습니다: '+workflowError(cause));}
  };
  sync();const stop=useAnnotationStore.subscribe(sync),storage=()=>sync();window.addEventListener('storage',storage);return()=>{stop();window.removeEventListener('storage',storage);};
 },[key]);
 const compare=async()=>{
  if(!draft||busy)return;setBusy(true);setError('');setAccepted(false);setComparison(null);setOpen(true);
  try{const fresh=await datasetWorkflow.annotations(a.currentImage!.image_id,imagePath);if(!current())return;if(!fresh.metadata)throw new Error('서버 라벨의 버전을 확인하지 못했습니다.');const persisted=readDraft(localStorage,scope,imagePath);if(!persisted||JSON.stringify(persisted)!==JSON.stringify(draft))throw new Error('초안이 다른 창에서 바뀌었습니다. 다시 선택하세요.');setComparison({metadata:fresh.metadata,annotations:fresh.annotations,draft});}catch(cause){if(current())setError(workflowError(cause));}finally{if(current())setBusy(false);}
 };
 const apply=async()=>{
  if(!comparison||!accepted||busy)return;setBusy(true);setError('');
  try{
   const state=useAnnotationStore.getState(),fresh=await datasetWorkflow.annotations(state.currentImage!.image_id,imagePath);if(!current())return;
   const currentState=useAnnotationStore.getState();
   if(!fresh.metadata||fresh.metadata.revision!==comparison.metadata.revision||JSON.stringify(fresh.annotations)!==JSON.stringify(comparison.annotations))throw new Error('서버 라벨이 비교 이후 바뀌었습니다. 최신 라벨과 다시 비교하세요.');
   if(!compareDraft(comparison.draft,fresh.metadata,fresh.annotations).can_apply)throw new Error('원본 이미지나 크기가 바뀌었습니다. 초안을 적용하지 않았습니다.');
   if(currentState.annotations!==state.annotations||currentState.isSaving||currentState.isDirty&&JSON.stringify(currentState.annotations)!==JSON.stringify(comparison.draft.annotations))throw new Error('현재 편집 내용이 초안과 다릅니다. 현재 내용을 먼저 저장하세요.');
   if(JSON.stringify(readDraft(localStorage,scope,imagePath))!==JSON.stringify(comparison.draft))throw new Error('다른 창의 새 초안이 있습니다. 다시 비교하세요.');
   const next={...comparison.draft,base_revision:fresh.metadata.revision,base_annotations:clone(fresh.annotations),nonce:crypto.randomUUID(),saved_at:new Date().toISOString()};
   saveDraft(localStorage,next);base.current={metadata:clone(fresh.metadata),annotations:clone(fresh.annotations)};owned.current=next;
   useAnnotationStore.setState({metadata:fresh.metadata,annotations:clone(next.annotations),isDirty:true,history:[...currentState.history,currentState.annotations].slice(-40),future:[],selectedAnnotationId:null});setSaved({key,draft:next});setOpen(false);setComparison(null);
  }catch(cause){if(current())setError(workflowError(cause));}finally{if(current())setBusy(false);}
 };
 const discard=()=>{if(!draft||!current())return;try{if(!removeDraft(localStorage,draft))throw new Error('다른 창에서 초안이 바뀌었습니다. 다시 비교하세요.');owned.current=null;setSaved(null);setOpen(false);setComparison(null);}catch(cause){setError(workflowError(cause));}};
 if(!project||!projectDir||!imagePath||!scope)return null;
 const details=comparison&&compareDraft(comparison.draft,comparison.metadata,comparison.annotations);
 return <section aria-label="라벨 연결 복구" className="shrink-0 border-b border-slate-700 px-4 py-1 text-xs text-slate-200">
  {draft&&<div className="flex items-center gap-3"><span>이 컴퓨터에 초안 보존됨 · 서버 저장 아님</span><button type="button" disabled={busy||a.isSaving} className={button} onClick={()=>void compare()}>저장 전 초안 비교·복구</button></div>}
  {error&&!open&&<p role="alert" className="text-rose-200">{error}</p>}
  {open&&<WorkspaceDialog title="서버 라벨과 로컬 초안 비교" onClose={()=>{setOpen(false);setComparison(null);setAccepted(false);}}><div className="space-y-3 text-sm text-slate-200">
   <p>현재 서버 라벨을 확인한 뒤 초안을 편집기에 적용합니다. 적용 후 편집 권한을 확보하고 별도로 저장해야 서버에 반영됩니다.</p>{busy&&<p role="status">현재 버전을 확인 중…</p>}{error&&<p role="alert" className="text-rose-200">{error}</p>}
   {comparison&&details&&<><p>초안 기준 r{comparison.draft.base_revision} · 현재 서버 r{details.current_revision} · {details.revision_changed?'충돌: 서버 변경 있음':'같은 기준 버전'}</p>{!details.can_apply&&<p role="alert" className="text-rose-200">원본 이미지 또는 크기가 달라 초안을 적용할 수 없습니다.</p>}
    <div className="grid gap-3 md:grid-cols-3">{[['초안 작성 당시',comparison.draft.base_annotations],['현재 서버 라벨',comparison.annotations],['보존된 로컬 초안',comparison.draft.annotations]].map(([label,rows])=><details open key={String(label)} className="rounded border border-slate-600 p-3"><summary>{String(label)}</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(rows,null,2)}</pre></details>)}</div>
    <label className="flex gap-2"><input type="checkbox" checked={accepted} onChange={e=>setAccepted(e.target.checked)}/>차이를 확인했으며 로컬 초안을 편집기에 적용합니다</label>
    <div className="flex gap-2"><button type="button" disabled={busy||!accepted||!details.can_apply||a.isSaving} className={button} onClick={()=>void apply()}>비교한 초안을 명시적으로 적용</button><button type="button" disabled={busy||a.isDirty||a.isSaving} className={button} onClick={discard}>보존 초안 버리고 서버 라벨 유지</button></div></>}
  </div></WorkspaceDialog>}
 </section>;
}
