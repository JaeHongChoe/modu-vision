import {useEffect,useRef} from 'react';
import {Save} from 'lucide-react';
import {getApiPersistenceIdentity} from '../../services/api';
import {useFlowchartStore} from '../../stores/useFlowchartStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useComputeStore} from '../../stores/useComputeStore';
import type {FlowchartPipeline} from '../../types';

export function flowDraftStatus(dirty:boolean,hash:string|null,activeVersion:boolean,hasSavedVersion:boolean):string {
  if(dirty)return '편집 초안 · 변경 사항 미저장';
  if(activeVersion)return '활성 저장본';
  if(hash)return '초안 저장됨 · 실행본 활성화 전';
  if(hasSavedVersion)return '저장본 보기 · 활성화 필요';
  return '편집 초안 · 저장 전';
}

export function FlowDraftControls({blocked=false,activeVersion=false,hasSavedVersion=false}:{blocked?:boolean;activeVersion?:boolean;hasSavedVersion?:boolean}) {
  const {pipeline,pipelineDirty,persistedDraftHash,isLoading,isSaving,isRunning,historyGroupStart}=useFlowchartStore();
  const {project,task}=useProjectStore();const {folderPath,datasetKey,isLoading:datasetLoading,importError}=useDatasetStore();
  const transport=useComputeStore(s=>s.transportRevision);const backend=getApiPersistenceIdentity();
  const blockedRef=useRef(blocked);blockedRef.current=blocked;
  const failedTarget=useRef<FlowchartPipeline|null>(null);
  const sourceReady=Boolean(project&&project.source_dataset_dir===folderPath&&datasetKey===`${folderPath}\0${task}`&&!datasetLoading&&!importError);
  const ownerKey=JSON.stringify([backend,transport,project?.id,project?.project_dir,project?.source_dataset_dir,project?.active_labelset_id,folderPath,datasetKey,task]);
  const owns=()=>{
    const owner=useProjectStore.getState();const data=useDatasetStore.getState();
    return getApiPersistenceIdentity()===backend&&useComputeStore.getState().transportRevision===transport
      &&owner.project?.id===project?.id&&owner.project?.project_dir===project?.project_dir
      &&owner.project?.source_dataset_dir===project?.source_dataset_dir&&owner.project?.active_labelset_id===project?.active_labelset_id
      &&owner.task===task&&data.folderPath===folderPath&&data.datasetKey===datasetKey&&!data.isLoading&&!data.importError;
  };
  const safe=()=>{const state=useFlowchartStore.getState();return sourceReady&&owns()&&!blockedRef.current&&!state.isLoading&&!state.isSaving&&!state.isRunning&&!state.historyGroupStart;};
  const persist=async()=>{
    const state=useFlowchartStore.getState();const target=state.pipeline;
    if(!target||!safe())return false;
    const saved=await state.saveDraft(owns);
    if(!saved&&owns()&&useFlowchartStore.getState().pipeline===target)failedTarget.current=target;
    return saved;
  };
  useEffect(()=>{
    if(!pipeline||!pipelineDirty||!safe()||failedTarget.current===pipeline)return;
    const timer=setTimeout(()=>{if(useFlowchartStore.getState().pipeline===pipeline)void persist();},650);
    return()=>clearTimeout(timer);
  },[pipeline,pipelineDirty,isLoading,isSaving,isRunning,historyGroupStart,blocked,ownerKey,sourceReady]);
  useEffect(()=>()=>{
    const state=useFlowchartStore.getState();
    if(state.pipelineDirty&&state.pipeline!==failedTarget.current&&safe())void persist();
  },[ownerKey]);
  return <div className="flex flex-wrap items-center gap-2">
    <span role="status" className="rounded bg-slate-900 px-2 py-1 text-xs text-slate-200">{isLoading?'플로우 불러오는 중':flowDraftStatus(pipelineDirty,persistedDraftHash,activeVersion,hasSavedVersion)} · 배포 상태는 실행 패키지에서 확인</span>
    <button type="button" aria-label="초안 저장" disabled={!pipeline||!sourceReady||isLoading||isSaving||isRunning||blocked||Boolean(historyGroupStart)}
      onClick={async()=>{failedTarget.current=null;return persist();}} title="모델 연결 전에도 편집 내용과 ROI를 저장합니다. 실행본 활성화와 배포는 별도로 저장하세요."
      className="flex items-center gap-1.5 rounded border border-sky-700 bg-sky-950 px-3 py-1.5 text-xs font-semibold text-sky-100 disabled:opacity-40">
      <Save className="h-3.5 w-3.5"/>{isSaving?'저장 중…':'초안 저장'}
    </button>
  </div>;
}
