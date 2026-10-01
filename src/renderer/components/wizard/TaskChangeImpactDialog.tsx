import { useEffect, useRef, useState } from 'react';
import { getApiPersistenceIdentity, getProjectContext, subscribeProjectContext } from '../../services/api';
import { taskChangeScope, useProjectStore, type TaskChangeOutcome } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { WorkspaceDialog } from '../common/WorkspaceDialog';
import { AsyncAction } from '../common/AsyncAction';
import type { VisionTask } from '../../types';

export function taskPreviewScope() {
  return JSON.stringify([taskChangeScope(useProjectStore.getState()),getProjectContext(),getApiPersistenceIdentity(),useProjectStore.getState().project?.source_dataset_dir,useProjectStore.getState().project?.active_labelset_id]);
}
export function taskPreviewBlocker(scope:string): string | null {
  if(scope!==taskPreviewScope())return '프로젝트·연결이 바뀌었습니다. 현재 작업에서 다시 선택하세요.';
  const project=useProjectStore.getState(),data=useDatasetStore.getState();
  if(project.isProjectBusy || data.isLoading || data.isSplitting)return '프로젝트·데이터 작업이 진행 중입니다. 완료 후 다시 확인하세요.';
  if(useAnnotationStore.getState().isDirty || useFlowchartStore.getState().pipelineDirty)return '저장하지 않은 라벨·플로우가 있습니다. 먼저 저장하거나 변경을 취소하세요.';
  return null;
}

export function TaskChangeImpactDialog({nextTask,scope,onClose,onResult}:{nextTask:VisionTask;scope:string;onClose:()=>void;onResult:(outcome:TaskChangeOutcome)=>void}) {
  const project=useProjectStore(),data=useDatasetStore();
  useAnnotationStore(s=>s.isDirty);useFlowchartStore(s=>s.pipelineDirty);
  const [context,setContext]=useState(getProjectContext);
  useEffect(()=>subscribeProjectContext(setContext),[]);
  const [pending,setPending]=useState(false),[error,setError]=useState('');
  const inFlight=useRef(false);
  const currentScope=taskPreviewScope();
  useEffect(()=>{if(scope!==currentScope)onClose();},[scope,currentScope,context,onClose]);
  const blocker=taskPreviewBlocker(scope);
  const confirm=async()=>{
    const blocked=taskPreviewBlocker(scope);
    if(inFlight.current)return;
    if(blocked){setError(blocked);return;}
    inFlight.current=true;setPending(true);setError('');
    try {
      const outcome=await useProjectStore.getState().updateTask(nextTask);
      if(scope===taskPreviewScope())onResult(outcome);
    } catch(cause){if(scope===taskPreviewScope())setError(String((cause as Error).message||cause));}
    finally{inFlight.current=false;setPending(false);}
  };
  return <WorkspaceDialog title="검사 작업 변경 영향" onClose={()=>{if(!inFlight.current)onClose();}} description="변경 내용을 확인한 뒤 현재 프로젝트에 적용합니다.">
    <div className="workspace-stack">
      <p><strong>{project.task} → {nextTask}</strong></p>
      <dl className="workspace-record"><dt>데이터 원본</dt><dd className="break-all">{project.project?.source_dataset_dir||'선택 없음'}</dd><dt>라벨셋</dt><dd>{project.project?.active_labelset_id||'default'}</dd><dt>현재 가져온 이미지·분할</dt><dd>{data.totalImages}장 · Train {data.split.train} / Val {data.split.val} / Test {data.split.test}</dd></dl>
      <ul className="list-disc space-y-2 pl-5 text-sm"><li>작업별 라벨과 저장된 분할을 다시 가져옵니다. 새 작업에서 사용할 수 있는 라벨·검수 상태는 가져오기 후 확인합니다.</li><li>이전 결과와 저장 모델·평가·플로우 기록은 보존됩니다. 새 작업의 현재 결과로 승인되지는 않습니다.</li><li>이전 모델·라벨 호환성은 이 미리보기로 확정하지 않습니다. 새 입력에서 평가·승인 근거를 다시 확인하세요.</li></ul>
      {(error||blocker)&&<p role="alert" className="workspace-description text-amber-200">{error||blocker}</p>}
      <div className="workspace-toolbar"><AsyncAction disabled={pending} onClick={onClose}>취소</AsyncAction><AsyncAction pending={pending} pendingLabel="작업 변경 확인 중…" disabledReason={blocker||undefined} onClick={()=>void confirm()}>영향 확인 후 변경</AsyncAction></div>
    </div>
  </WorkspaceDialog>;
}
