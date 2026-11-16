import React,{useRef,useState,useSyncExternalStore} from 'react';
import {getApiPersistenceIdentity,getProjectContext,getProjectContextGeneration,subscribeProjectContext} from '../../services/api';
import {readDatasetAuthority,useDatasetStore} from '../../stores/useDatasetStore';
import {projectSwitchRefusal,subscribeProjectSwitchRefusal,useProjectStore} from '../../stores/useProjectStore';
import {useTrainingStore} from '../../stores/useTrainingStore';
import {isSplitUnavailable} from '../../utils/datasetSplitCapability';
import type {VisionTask} from '../../types';

function scopeFor(task:string):string {
  const selected=useProjectStore.getState(),dataset=useDatasetStore.getState(),project=selected.project;
  return JSON.stringify([task,project?.id,selected.projectDir,project?.source_dataset_dir,project?.active_labelset_id,
    selected.task,getApiPersistenceIdentity(),getProjectContextGeneration(),readDatasetAuthority(),
    dataset.folderPath,dataset.datasetKey,dataset.sourceAuthority]);
}
function currentSource(task:string):boolean {
  const selected=useProjectStore.getState(),dataset=useDatasetStore.getState(),project=selected.project,context=getProjectContext();
  return Boolean(project&&selected.projectDir===project.project_dir&&project.task===task&&selected.task===task
    &&dataset.folderPath&&project.source_dataset_dir===dataset.folderPath
    &&dataset.datasetKey===`${dataset.folderPath}\0${task}`&&dataset.sourceAuthority
    &&dataset.sourceAuthority===readDatasetAuthority()
    &&(context?context.project_id===project.id:getApiPersistenceIdentity()==='local'));
}
function unavailable(task:string):boolean {
  const dataset=useDatasetStore.getState();
  return isSplitUnavailable(task as VisionTask,dataset.splitSupported);
}
function refusal(task:string,disabled:boolean):string|null {
  const project=useProjectStore.getState(),dataset=useDatasetStore.getState(),training=useTrainingStore.getState();
  if(disabled||project.isProjectBusy||project.sharedSessionChanging||training.isTraining||training.isRecoveringTraining)
    return '진행 중인 작업이 끝난 뒤 데이터를 나눌 수 있습니다.';
  if(dataset.isLoading||dataset.isSplitting||dataset.isGenerating)return '데이터 작업을 마친 뒤 나누기를 저장하세요.';
  if(dataset.importError||dataset.sourceSaveError)return dataset.importError||dataset.sourceSaveError;
  if(!currentSource(task))return '현재 프로젝트의 사진을 먼저 확인하세요.';
  if(unavailable(task))return dataset.splitUnavailableReason||'현재 데이터의 나누기 지원 여부를 사진 준비에서 확인하세요.';
  if(dataset.totalImages<1)return '나눌 수 있는 정답 사진을 먼저 준비하세요.';
  return projectSwitchRefusal();
}

/** Configures the existing dataset split; no independent split or sampling engine. */
export function TrainingDataSplitBlock({task,disabled=false}:{task:string;disabled?:boolean}) {
  const dataset=useDatasetStore(),project=useProjectStore(),training=useTrainingStore();
  useSyncExternalStore(subscribeProjectContext,getProjectContextGeneration,getProjectContextGeneration);
  useSyncExternalStore(subscribeProjectSwitchRefusal,projectSwitchRefusal,projectSwitchRefusal);
  const scope=scopeFor(task),sourceReady=currentSource(task),unsupported=unavailable(task);
  const fixed=dataset.splitState==='saved'||dataset.splitState==='source_folders';
  const [draft,setDraft]=useState({scope,preset:'70',editing:!fixed});
  const selected=draft.scope===scope?draft:{scope,preset:'70',editing:!fixed};
  const [feedback,setFeedback]=useState<{scope:string;state:'saving'|'saved'|'error';message:string}|null>(null);
  const visible=feedback?.scope===scope?feedback:null;
  const mounted=useRef(true),owner=useRef(scope),parentDisabled=useRef(disabled);
  const pending=useRef<{scope:string}|null>(null);
  owner.current=scope;parentDisabled.current=disabled;
  React.useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const current=()=>mounted.current&&owner.current===scope&&scopeFor(task)===scope;
  const blocked=refusal(task,disabled);
  const saving=visible?.state==='saving';
  const edit=()=>{if(current()&&!refusal(task,parentDisabled.current))setDraft({...selected,editing:true});};
  const save=async()=>{
    if(!current()||pending.current?.scope===scope||!selected.editing||refusal(task,parentDisabled.current))return;
    const request={scope},before=useDatasetStore.getState().split,train=selected.preset==='80'?.8:.7,val=selected.preset==='80'?.1:.2;
    pending.current=request;setFeedback({scope,state:'saving',message:'데이터 나누기를 저장하고 있습니다.'});
    try {
      await useDatasetStore.getState().applySplit(train,val,.1);
      if(!current())return;
      const saved=useDatasetStore.getState();
      // applySplit deliberately resolves without a request when its source is
      // stale. A resolved Promise alone is not evidence that anything was saved.
      if(saved.split===before||saved.splitState!=='saved'||saved.trainRatio!==train||saved.splitError)
        throw new Error(saved.splitError||'나누기 저장을 확인하지 못했습니다. 현재 데이터 상태를 다시 확인하세요.');
      setDraft({scope,preset:selected.preset,editing:false});
      setFeedback({scope,state:'saved',message:'저장된 분할을 확인했습니다.'});
    } catch(error) {
      if(current())setFeedback({scope,state:'error',message:error instanceof Error&&error.message?error.message:'데이터 나누기를 저장하지 못했습니다.'});
    } finally {if(pending.current===request)pending.current=null;}
  };
  const openData=()=>{
    const selected=useProjectStore.getState(),running=useTrainingStore.getState();
    if(current()&&!parentDisabled.current&&!selected.isProjectBusy&&!running.isTraining&&!running.isRecoveringTraining)void selected.setStep(1);
  };
  return <section className="workspace-section space-y-3" aria-label="학습 데이터 나누기" aria-busy={saving}>
    <h2 className="text-sm font-semibold">학습·검증·시험 사진 나누기</h2>
    {sourceReady?<p className="text-sm tabular-nums">학습 {dataset.split.train.toLocaleString('ko-KR')}장 · 검증 {dataset.split.val.toLocaleString('ko-KR')}장 · 시험 {dataset.split.test.toLocaleString('ko-KR')}장</p>:<p className="text-sm">현재 프로젝트의 사진을 먼저 확인하세요.</p>}
    {sourceReady&&<p className="text-xs text-[var(--workspace-muted)]">{dataset.splitState==='source_folders'?'원본 폴더의 train / val / test 분할을 사용하고 있습니다.':dataset.splitState==='saved'?'현재 저장된 분할입니다.':dataset.splitState==='stale'?'사진이 바뀌었습니다. 나누기를 다시 저장하세요.':'현재 분할은 아직 저장되지 않았습니다.'}</p>}
    {unsupported||!sourceReady?<>
      <p className="text-sm text-[var(--workspace-muted)]">{dataset.splitUnavailableReason||blocked}</p>
      <button type="button" className="workspace-button" disabled={disabled||project.isProjectBusy||training.isTraining||training.isRecoveringTraining} onClick={openData}>사진 준비 열기</button>
    </>:selected.editing?<>
      <label className="workspace-field">사진을 나눌 비율
        <select aria-label="학습 데이터 나누기 비율" value={selected.preset} disabled={Boolean(blocked)||saving} onChange={event=>{
          if(current()&&!refusal(task,parentDisabled.current))setDraft({scope,preset:event.target.value==='80'?'80':'70',editing:true});
        }}><option value="70">학습 70% · 검증 20% · 시험 10%</option><option value="80">학습 80% · 검증 10% · 시험 10%</option></select>
      </label>
      {fixed&&<p className="text-xs text-[var(--workspace-muted)]">저장하면 기존 시험 사진도 다시 나뉩니다.</p>}
      <button type="button" className="workspace-button workspace-button--primary" disabled={Boolean(blocked)||saving} onClick={save}>데이터 나누기 저장</button>
    </>:<button type="button" className="workspace-button" disabled={Boolean(blocked)||saving} onClick={edit}>분할 비율 바꾸기</button>}
    {blocked&&!unsupported&&sourceReady&&<p role="status" className="text-xs text-[var(--workspace-muted)]">{blocked}</p>}
    {visible&&<p role={visible.state==='error'?'alert':'status'} className={visible.state==='error'?'text-sm text-[var(--workspace-danger)]':'text-xs text-[var(--workspace-muted)]'}>{visible.message}</p>}
  </section>;
}
