import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {useProjectStore} from '../../stores/useProjectStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {getApiPersistenceIdentity,getProjectContext,getProjectContextGeneration} from '../../services/api';
import {datasetWorkflow} from '../../services/datasetWorkflow';
import {teamDataApi} from '../../services/teamDataApi';
import {evaluationOriginScope,rememberReviewOrigin} from './productDataWorkflow';
import {clearEvidenceEdit,readEvidenceEdit,subscribeEvidenceEdit,evidenceEditSnapshot,openEvidenceLabeling,rememberEvidenceEdit,evidenceEditScope} from './evidenceLabeling';

export function EvidenceEditReturn(){
  const project=useProjectStore(),compute=useComputeStore();
  const {isDirty,isSaving,currentImage}=useAnnotationStore();
  const scope=evaluationOriginScope(project.project?.id,project.project?.source_dataset_dir||'',project.task,
    project.project?.active_labelset_id||'default',{...compute,apiTransportIdentity:getApiPersistenceIdentity()});
  const editScope=evidenceEditScope(scope,getProjectContext()?.actor_id);
  const snapshot=useSyncExternalStore(subscribeEvidenceEdit,()=>evidenceEditSnapshot(localStorage,editScope));
  const authority=getProjectContextGeneration(),editorMounted=useRef(true);
  useEffect(()=>{editorMounted.current=true;return()=>{editorMounted.current=false;};},[]);
  const origin=readEvidenceEdit(localStorage,editScope),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{setBusy(false);setError('');},[editScope,authority,compute.transportRevision,snapshot]);
  const same=()=>{
    const state=useProjectStore.getState(),currentCompute=useComputeStore.getState();
    const currentScope=evaluationOriginScope(state.project?.id,state.project?.source_dataset_dir||'',state.task,
      state.project?.active_labelset_id||'default',{...currentCompute,apiTransportIdentity:getApiPersistenceIdentity()});
    return authority===getProjectContextGeneration()&&compute.transportRevision===currentCompute.transportRevision
      &&editScope===evidenceEditScope(currentScope,getProjectContext()?.actor_id)
      &&snapshot===evidenceEditSnapshot(localStorage,editScope);
  };
  if(!origin)return null;
  const reopen=async()=>{
    const sameEditor=()=>editorMounted.current&&useProjectStore.getState().activeStep===2&&same();
    if(isDirty||isSaving||busy||!sameEditor())return;
    setBusy(true);setError('');
    try{
      const updated=await openEvidenceLabeling(origin,{sameContext:sameEditor,mode:getProjectContext()?.mode||'local',actor:useAnnotationStore.getState().reviewerName,task:useProjectStore.getState().task,
        metadata:()=>datasetWorkflow.image(origin.file_path),workspace:()=>teamDataApi.workspace(),
        open:(id,path,expected)=>useProjectStore.getState().openImageForLabeling(id,path,{...expected,isCurrent:sameEditor})});
      if(sameEditor())rememberEvidenceEdit(localStorage,editScope,updated);
    }catch(cause){if(sameEditor())setError(cause instanceof Error?cause.message:String(cause));}finally{if(sameEditor())setBusy(false);}
  };
  const returnToComparison=async()=>{
    if(isDirty||isSaving||busy||!same())return;
    setBusy(true);setError('');
    try{
      const state=useProjectStore.getState();
      rememberReviewOrigin(localStorage,scope,origin.comparison_id,origin.image_id,origin.file_path,'comparison',
        {product_filter:origin.product_filter,lot_filter:origin.lot_filter});
      await state.setStep(4);
      if(same()&&useProjectStore.getState().activeStep===4)clearEvidenceEdit(localStorage,editScope);
    }catch(cause){if(same())setError(cause instanceof Error?cause.message:String(cause));}finally{if(same())setBusy(false);}
  };
  return <section aria-label="판정 근거에서 시작한 라벨 편집" className="shrink-0 border-b border-cyan-800 bg-cyan-950/30 px-4 py-2 text-xs">
    <p>현재 라벨 편집 · {origin.labelset_id} · 확인한 수정 버전 {origin.revision} · {origin.image_id}</p>
    <p className="text-slate-400">원판정 기록은 읽기 전용입니다. 라벨 저장은 현재 수정 버전·역할·편집 잠금 검사를 따릅니다.{currentImage?.file_path!==origin.file_path&&' 다른 이미지를 보고 있습니다. 복귀하면 원래 근거 이미지를 선택합니다.'}</p>
    <button type="button" disabled={busy||isDirty||isSaving} onClick={()=>void returnToComparison()} className="mt-1 rounded border border-cyan-600 px-3 py-1 disabled:opacity-40">원래 판정 근거로 돌아가기</button>
    <button type="button" disabled={busy||isDirty||isSaving} onClick={()=>void reopen()} className="ml-2 mt-1 rounded border border-slate-600 px-3 py-1 disabled:opacity-40">근거 이미지의 현재 라벨 다시 열기</button>
    {(isDirty||isSaving)&&<span className="ml-2 text-amber-200">현재 라벨을 먼저 저장하세요.</span>}{error&&<p role="alert">{error}</p>}
  </section>;
}
