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
import type {AssistantTask,TaskExecutionRequest} from '../services/assistantTasks/types';
import {hasPendingTaskQuality} from '../services/assistantTasks/types';
import {oneclickPlan} from '../services/assistantTasks/oneclickPlan';
import {getProjectContext,getApiPersistenceIdentity} from '../services/api';
import {qualifyCompletedTaskStep} from '../services/assistantTasks/qualification';
import {subscribeWorkflowEvidence} from './workflowEvidence';

interface ConnectionState {config:AssistantConfigState|null;configuring:boolean;busy:boolean;writing:boolean;pending:unknown}
let connectionReader:()=>ConnectionState=()=>({config:null,configuring:false,busy:false,writing:false,pending:null});
const connection=()=>connectionReader();
let poll:ReturnType<typeof setTimeout>|null=null;
let pollCount=0;
let observedConnection='';
interface TaskState extends TaskControllerState {
  propose(input:unknown,executionRequest?:TaskExecutionRequest):Promise<Record<string,unknown>>;
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
  if(!controller.canPollWaiting()||(state.task?.status!=='waiting_job'&&!hasPendingTaskQuality(state.task))||state.task?.status==='paused'||state.busy||state.loading||state.error
      ||connection().configuring||!taskConnectionReady(connection().config))return;
  poll=setTimeout(()=>{poll=null;void operate(()=>controller.pollWaiting()).finally(schedule);},Math.min(15_000,1500+pollCount++*500));
}

export const useAssistantTaskStore=create<TaskState>(()=>({
  task:null,busy:false,loading:false,error:null,contextMatches:true,settingsRecoveryLabel:null,planningNew:false,history:[],readPending:false,
  propose:async(input,executionRequest)=>{await controller.propose(input,executionRequest);return controller.summary();},
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
    if(restored.status==='waiting_job'||restored.status==='needs_reconciliation'||hasPendingTaskQuality(restored))await controller.resume();
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
  afterStep:qualifyCompletedTaskStep,
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
      workflow:'명시적인 실행 요청은 현재 상태를 확인하고 propose_task로 맡긴 단계를 진행합니다. 설명·상태 질문은 읽기만 합니다. 필요한 입력이 없을 때만 구체적으로 안내하세요.',
      human_gates:['라벨 검토·채택','정답 확정','모델·흐름 최종 승인','배포'],
      task_execution:'현재 macOS/Linux 로컬 실행을 검증하며, 지원하지 않는 실행 환경은 서버가 명시적으로 거절합니다.'};
  },
  screen:()=>inspectTaskScreen(),
  status:async()=>controller.observe(),
  cachedStatus:()=>controller.summary(),
  propose:async(input:unknown,executionRequest?:TaskExecutionRequest)=>{
    await controller.propose(input,executionRequest);
    const summary=controller.summary();
    return {...summary,message:reducePaths(executionRequest?'요청한 계획의 실행 권한과 결과를 저장했습니다. 진행 상태와 필요한 입력을 확인하세요.':'실행 계획을 준비했습니다. 실행을 요청하면 이어서 진행합니다.')};
  },
};

let oneclickPoll:ReturnType<typeof setTimeout>|null=null;
let oneclickObserver:((task:AssistantTask|null,error:string|null)=>void)|null=null;
let oneclickCancelled=false;
let oneclickRequestGeneration=0;
const localIdentity=()=>{const context=getProjectContext();return JSON.stringify(['oneclick',context?.workspace_id??null,context?.actor_id??null,context?.mode??'local']);};
const oneclickController=new AssistantTaskController({
  client:assistantTaskClient,binding:taskRuntimeBinding,connection:localIdentity,generation:taskRuntimeGeneration,
  connectionGeneration:localIdentity,ready:()=>Boolean(useProjectStore.getState().projectDir&&!useProjectStore.getState().isProjectBusy),
  unavailableMessage:'프로젝트를 먼저 만들거나 열어 자동 진행할 자료를 선택하세요.',
  mayStart:()=>executionLeaseOwner()!=='assistant',leaseOwner:'automatic',execute:executeTaskAction,reconcile:reconcileTaskAction,
  afterStep:qualifyCompletedTaskStep,
  authorizationCurrent:()=>!oneclickCancelled,
  onChange:state=>{
    oneclickObserver?.(state.task,state.error);
    if(oneclickPoll!==null){clearTimeout(oneclickPoll);oneclickPoll=null;}
    if(!oneclickCancelled&&(state.task?.status==='waiting_job'||hasPendingTaskQuality(state.task))&&state.task?.status!=='paused'&&!state.busy&&!state.loading&&!state.error){
      const generation=oneclickRequestGeneration;
      oneclickPoll=setTimeout(()=>{oneclickPoll=null;if(oneclickCancelled||generation!==oneclickRequestGeneration)return;
        void oneclickController.pollWaiting().catch(cause=>oneclickObserver?.(oneclickController.state.task,failure(cause)));},1500);
    }
  },
});
const oneclickPhysicalContext=()=>JSON.stringify([getApiPersistenceIdentity(),getProjectContext(),useProjectStore.getState().projectDir,
  useProjectStore.getState().project?.id,useProjectStore.getState().project?.source_dataset_dir,useProjectStore.getState().project?.active_labelset_id,useProjectStore.getState().task]);
let observedOneclickContext=oneclickPhysicalContext();
function refreshOneclickContext(){
  const next=oneclickPhysicalContext();
  if(next===observedOneclickContext)return;
  observedOneclickContext=next;
  if(!oneclickController.ownsExecution()){
    oneclickRequestGeneration++;
    oneclickController.invalidateConnection();
  }
}
/** Offline oneclick uses the same ledger/runner and project APIs; it has no provider dependency. */
export async function startOneclickTask(onChange:(task:AssistantTask|null,error:string|null)=>void):Promise<AssistantTask|null>{
  oneclickObserver=onChange;
  refreshOneclickContext();
  const generation=++oneclickRequestGeneration,context=oneclickPhysicalContext();
  oneclickCancelled=false;
  if(oneclickController.state.busy)return oneclickController.state.task;
  if(!oneclickController.state.task)await oneclickController.restore();
  if(oneclickCancelled||generation!==oneclickRequestGeneration||context!==oneclickPhysicalContext())return oneclickController.state.task;
  const current=oneclickController.state.task;
  if(current?.status==='paused'||current?.status==='waiting_human'||current?.status==='needs_reconciliation')return oneclickController.resume();
  if(current?.status==='waiting_job'||hasPendingTaskQuality(current))return oneclickController.pollWaiting();
  if(current?.status==='running')return oneclickController.resume();
  const data=useDatasetStore.getState(),training=useTrainingStore.getState();
  return oneclickController.propose(oneclickPlan({splitSaved:data.splitState==='saved',
    currentModel:training.isCurrentData&&training.status==='completed'&&Boolean(training.jobId),trainRatio:data.trainRatio}),{source:'oneclick'});
}
export async function pauseOneclickTask():Promise<void>{
  oneclickCancelled=true;
  oneclickRequestGeneration++;
  if(oneclickPoll!==null){clearTimeout(oneclickPoll);oneclickPoll=null;}
  oneclickController.stopRead();
  await oneclickController.pause();
}
subscribeWorkflowEvidence(()=>{void controller.recheckInputs();void oneclickController.recheckInputs();});
for(const store of [useProjectStore,useDatasetStore,useTrainingStore,useComputeStore])
  store.subscribe?.(()=>{refreshOneclickContext();void controller.recheckInputs();void oneclickController.recheckInputs();});
