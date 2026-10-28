/** One app-owned task controller. No provider is called by job polling or recovery. */
import {create} from 'zustand';
import {useTrainingStore} from './useTrainingStore';
import {useProjectStore} from './useProjectStore';
import {useDatasetStore} from './useDatasetStore';
import {useComputeStore} from './useComputeStore';
import {trainingSettingsRecovery} from '../services/assistantTasks/settingsRecovery';
import type {AssistantConfigState} from '../../types/electron';
import {assistantTaskClient} from '../services/assistantTasks/client';
import {AssistantTaskController,type TaskControllerState} from '../services/assistantTasks/controller';
import {taskConnectionKey,taskConnectionGeneration,taskConnectionReady} from '../services/assistantTasks/connection';
import {taskRuntimeBinding,taskRuntimeGeneration,executeTaskAction,reconcileTaskAction,taskActionCatalog,inspectTaskScreen} from '../services/assistantTasks/runtime';
import {reducePaths} from '../services/assistantContext';
import {executionLeaseOwner} from '../services/assistantTasks/executionLease';

interface ConnectionState {config:AssistantConfigState|null;configuring:boolean;busy:boolean;writing:boolean;pending:unknown}
let connectionReader:()=>ConnectionState=()=>({config:null,configuring:false,busy:false,writing:false,pending:null});
const connection=()=>connectionReader();
let poll:ReturnType<typeof setTimeout>|null=null;
let pollCount=0;
let observedConnection='';
interface TaskState extends TaskControllerState {
  propose(input:unknown):Promise<Record<string,unknown>>;
  restore(id?:string):Promise<void>;
  prepareNewRequest(id:string,revision:number):Promise<boolean>;
  stopRead():void;
  loadHistory():Promise<void>;
  restoreSettings(id:string,revision:number):Promise<void>;
  approve(id:string,revision:number):Promise<void>;
  pause():Promise<void>;
  resume():Promise<void>;
  refresh():Promise<void>;
}
const failure=(cause:unknown)=>cause instanceof Error?cause.message:'작업 상태를 확인하지 못했습니다.';
async function operate(action:()=>Promise<unknown>):Promise<void>{
  const authority=connectionIdentity(),generation=taskRuntimeGeneration();
  try{await action();}catch(cause){if(authority===connectionIdentity()&&Object.is(generation,taskRuntimeGeneration())&&!['read_timeout','read_stopped'].includes(String((cause as {code?:unknown})?.code)))useAssistantTaskStore.setState({error:failure(cause)});}
}
function schedule(){
  if(poll!==null){clearTimeout(poll);poll=null;}
  const state=useAssistantTaskStore.getState();
  if(!controller.canPollWaiting()||state.task?.status!=='waiting_job'||state.busy||state.loading||state.error
      ||connection().configuring||!taskConnectionReady(connection().config))return;
  poll=setTimeout(()=>{poll=null;void operate(()=>controller.pollWaiting()).finally(schedule);},Math.min(15_000,1500+pollCount++*500));
}

export const useAssistantTaskStore=create<TaskState>(()=>({
  task:null,busy:false,loading:false,error:null,contextMatches:true,settingsRecoveryLabel:null,planningNew:false,history:[],readPending:false,
  propose:async input=>{await controller.propose(input);return controller.summary();},
  restore:id=>operate(()=>controller.restore(id)),
  prepareNewRequest:(id,revision)=>controller.prepareNewRequest(id,revision),
  stopRead:()=>controller.stopRead(),
  loadHistory:()=>operate(()=>controller.loadHistory()),
  restoreSettings:(id,revision)=>operate(()=>controller.restoreSettings(id,revision)),
  approve:(id,revision)=>operate(()=>controller.approve(id,revision)),
  pause:()=>operate(()=>controller.pause()),
  resume:()=>operate(()=>controller.resume()),
  refresh:()=>operate(async()=>{
    const current=controller.state.task;
    const authority=connectionIdentity(),generation=taskRuntimeGeneration(),planningNew=controller.state.planningNew;
    if(planningNew)return;
    const restored=await controller.restore(current?.id);
    if(!restored||authority!==connectionIdentity()||!Object.is(generation,taskRuntimeGeneration())||controller.state.planningNew||controller.state.task?.id!==restored.id)return;
    if(restored.status==='waiting_job'||restored.status==='needs_reconciliation')await controller.resume();
  }),
}));

const controller=new AssistantTaskController({
  client:assistantTaskClient,
  binding:taskRuntimeBinding,
  settingsRecovery:saved=>{
    const before=taskRuntimeBinding(),recovery=trainingSettingsRecovery(saved,before);
    return recovery?{label:recovery.label,restore:()=>{
      if(taskRuntimeBinding()!==before)return false;
      useTrainingStore.getState().setNextSettings({...(recovery.device!==null?{device:recovery.device}:{}),...(recovery.batch!==null?{batchSize:recovery.batch}:{})});
      return taskRuntimeBinding()===saved;
    }}:null;
  },
  connection:()=>taskConnectionKey(connection().config),
  connectionGeneration:()=>taskConnectionGeneration(connection().config),
  generation:()=>taskRuntimeGeneration(),
  ready:()=>!connection().configuring&&taskConnectionReady(connection().config),
  mayStart:()=>{const state=connection();return executionLeaseOwner()!=='automatic'&&!state.busy&&!state.writing&&!state.pending&&!state.configuring;},
  execute:executeTaskAction,reconcile:reconcileTaskAction,
  onChange:state=>{useAssistantTaskStore.setState(state);schedule();},
});

export function configureTaskConnection(reader:()=>ConnectionState):void{
  connectionReader=reader;observedConnection=connectionIdentity();
}
function connectionIdentity(){const state=connection();return JSON.stringify([taskConnectionKey(state.config),taskConnectionGeneration(state.config),state.configuring,taskConnectionReady(state.config)]);}
export function notifyTaskConnectionChange():void{
  const next=connectionIdentity();if(next===observedConnection)return;
  observedConnection=next;pollCount=0;
  if(poll!==null){clearTimeout(poll);poll=null;}
  controller.invalidateConnection();
}

for(const store of [useProjectStore,useDatasetStore,useTrainingStore,useComputeStore])store.subscribe?.(()=>controller.refreshContext());

/** These operations can be exposed to a model; approval/run are intentionally absent. */
export const assistantTaskTools={
  isExecuting:()=>controller.blocksOtherWrites(),
  features:(action?:string)=>{
    const rows=taskActionCatalog();
    if(action){const found=rows.find(row=>row.id===action);return found??{available:false,reason:'이 앱에 등록되지 않은 작업입니다.'};}
    return {features:rows.map(({id,title,description})=>({id,title,description})),
      workflow:'요청을 이해한 뒤 실행할 단계로 propose_task를 호출하세요. 계획은 사람이 확인하며, 현재 상태를 읽고 필요한 질문만 먼저 하세요.',
      human_gates:['라벨 검토·채택','정답 확정','모델·흐름 최종 승인','배포'],
      task_execution:'현재 macOS/Linux 로컬 실행을 검증하며, 지원하지 않는 실행 환경은 서버가 명시적으로 거절합니다.'};
  },
  screen:()=>inspectTaskScreen(),
  status:async()=>controller.observe(),
  cachedStatus:()=>controller.summary(),
  propose:async(input:unknown)=>{
    await controller.propose(input);
    const summary=controller.summary();
    return {...summary,message:reducePaths('실행 계획을 준비했습니다. 도우미 패널에서 계획을 확인하고 실행하세요.')};
  },
};
