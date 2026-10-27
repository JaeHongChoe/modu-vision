import React,{useEffect} from 'react';
import {useAssistantTaskStore} from '../../stores/useAssistantTaskStore';
import {useProjectStore} from '../../stores/useProjectStore';
import type {WorkflowStep} from '../wizard/workflowReadiness';
import {AssistantTaskCard} from './AssistantTaskCard';

export function AssistantTaskPanel({ready,blocked,connectionKey}:{ready:boolean;blocked:boolean;connectionKey:string}):React.ReactElement{
  const task=useAssistantTaskStore(state=>state.task),busy=useAssistantTaskStore(state=>state.busy);
  const loading=useAssistantTaskStore(state=>state.loading),error=useAssistantTaskStore(state=>state.error);
  const contextMatches=useAssistantTaskStore(state=>state.contextMatches),settingsRecoveryLabel=useAssistantTaskStore(state=>state.settingsRecoveryLabel);
  const restoreSettings=useAssistantTaskStore(state=>state.restoreSettings);
  const restore=useAssistantTaskStore(state=>state.restore),approve=useAssistantTaskStore(state=>state.approve);
  const pause=useAssistantTaskStore(state=>state.pause),resume=useAssistantTaskStore(state=>state.resume),refresh=useAssistantTaskStore(state=>state.refresh);
  useEffect(()=>{if(ready&&!task&&!busy)void restore();},[ready,connectionKey]);
  return <AssistantTaskCard task={task} busy={busy} loading={loading} error={error} ready={ready} blocked={blocked} contextMatches={contextMatches} settingsRecoveryLabel={settingsRecoveryLabel} onRestoreSettings={(id,revision)=>void restoreSettings(id,revision)}
    onApprove={(id,revision)=>void approve(id,revision)} onPause={()=>void pause()} onResume={()=>void resume()}
    onRefresh={()=>void refresh()} onRestore={()=>void restore()}
    onNavigate={step=>{if(useAssistantTaskStore.getState().task?.id===task?.id&&useAssistantTaskStore.getState().contextMatches&&Number.isInteger(step)&&step>=1&&step<=6)void useProjectStore.getState().setStep(step as WorkflowStep);}}/>;
}
