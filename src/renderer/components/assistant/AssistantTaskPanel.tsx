import React,{useEffect} from 'react';
import {useAssistantTaskStore} from '../../stores/useAssistantTaskStore';
import {useProjectStore} from '../../stores/useProjectStore';
import type {WorkflowStep} from '../wizard/workflowReadiness';
import {AssistantTaskCard} from './AssistantTaskCard';

export function AssistantTaskPanel({ready,blocked,connectionKey,onNewRequestPrepared}:{ready:boolean;blocked:boolean;connectionKey:string;onNewRequestPrepared?():void}):React.ReactElement{
  const task=useAssistantTaskStore(state=>state.task),busy=useAssistantTaskStore(state=>state.busy);
  const loading=useAssistantTaskStore(state=>state.loading),error=useAssistantTaskStore(state=>state.error);
  const contextMatches=useAssistantTaskStore(state=>state.contextMatches),settingsRecoveryLabel=useAssistantTaskStore(state=>state.settingsRecoveryLabel);
  const restoreSettings=useAssistantTaskStore(state=>state.restoreSettings);
  const restore=useAssistantTaskStore(state=>state.restore),approve=useAssistantTaskStore(state=>state.approve);
  const pause=useAssistantTaskStore(state=>state.pause),resume=useAssistantTaskStore(state=>state.resume),refresh=useAssistantTaskStore(state=>state.refresh);
  const planningNew=useAssistantTaskStore(state=>state.planningNew),history=useAssistantTaskStore(state=>state.history),readPending=useAssistantTaskStore(state=>state.readPending);
  const prepareNewRequest=useAssistantTaskStore(state=>state.prepareNewRequest),stopRead=useAssistantTaskStore(state=>state.stopRead),loadHistory=useAssistantTaskStore(state=>state.loadHistory);
  useEffect(()=>{if(ready&&!task&&!busy&&!planningNew)void restore();},[ready,connectionKey]);
  const mayContinue=()=>!useAssistantTaskStore.getState().planningNew;
  return <AssistantTaskCard task={task} busy={busy} loading={loading} error={error} ready={ready} blocked={blocked} contextMatches={contextMatches} settingsRecoveryLabel={settingsRecoveryLabel} onRestoreSettings={(id,revision)=>{if(mayContinue())void restoreSettings(id,revision);}}
    planningNew={planningNew} history={history} readPending={readPending}
    onPrepareNewRequest={async(id,revision)=>{
      if(!ready||blocked)return;
      const prepared=await prepareNewRequest(id,revision),current=useAssistantTaskStore.getState();
      if(prepared&&current.planningNew&&current.task?.id===id)onNewRequestPrepared?.();
    }}
    onStopRead={()=>stopRead()} onLoadHistory={()=>void loadHistory()} onSelectHistory={id=>void restore(id)}
    onApprove={(id,revision)=>{if(mayContinue())void approve(id,revision);}} onPause={()=>{if(mayContinue())void pause();}} onResume={()=>{if(mayContinue())void resume();}}
    onRefresh={()=>{if(mayContinue())void refresh();}} onRestore={()=>void restore()}
    onNavigate={step=>{if(mayContinue()&&useAssistantTaskStore.getState().task?.id===task?.id&&useAssistantTaskStore.getState().contextMatches&&Number.isInteger(step)&&step>=1&&step<=6)void useProjectStore.getState().setStep(step as WorkflowStep);}}/>;
}
