/** UI-owned task orchestration; the model can propose, never approve a plan. */
import {validateTaskPlan} from './catalog';
import {AssistantTaskRunner, type TaskRunnerDeps} from './runner';
import {taskFeedback} from './feedback';
import {acquireExecutionLease,releaseExecutionLease} from './executionLease';
import type {AssistantTaskClient} from './client';
import type {AssistantTask, TaskActionResult, TaskControl, TaskStep} from './types';

export interface TaskControllerState { task:AssistantTask|null; busy:boolean; loading:boolean; error:string|null;contextMatches:boolean;settingsRecoveryLabel:string|null }
export interface TaskControllerDeps {
  client:AssistantTaskClient;
  binding():string;
  /** Stable connected account/model identity; no credentials or transient UI object identity. */
  connection():string;
  connectionGeneration?():unknown;
  settingsRecovery?(savedBinding:string):{label:string;restore():boolean}|null;
  ready():boolean;
  mayStart():boolean;
  generation?():unknown;
  execute(step:TaskStep,control:TaskControl):Promise<TaskActionResult>;
  reconcile(step:TaskStep,control:TaskControl):Promise<TaskActionResult>;
  onChange(state:TaskControllerState):void;
  runnerFactory?:(deps:TaskRunnerDeps)=>Pick<AssistantTaskRunner,'run'|'pause'>;
}
const copy=<T>(value:T):T=>JSON.parse(JSON.stringify(value)) as T;
const message=(cause:unknown)=>cause instanceof Error?cause.message:'작업 상태를 확인하지 못했습니다.';
const unresolved=(task:AssistantTask|null)=>Boolean(task&&task.steps.some(step=>['running','uncertain','waiting_job'].includes(step.status)));

export class AssistantTaskController {
  state:TaskControllerState={task:null,busy:false,loading:false,error:null,contextMatches:true,settingsRecoveryLabel:null};
  private runner:Pick<AssistantTaskRunner,'run'|'pause'>|null=null;
  private version=0;
  constructor(private readonly deps:TaskControllerDeps){}
  private set(next:Partial<TaskControllerState>){
    this.state={...this.state,...next};
    this.state.contextMatches=!this.state.task||this.state.task.binding===this.binding();
    this.state.settingsRecoveryLabel=this.recovery(this.state.task)?.label??null;
    this.deps.onChange(copy(this.state));
  }
  private recovery(task:AssistantTask|null){
    if(!task?.approval||task.approval.plan_sha256!==task.plan_sha256||task.binding===this.binding())return null;
    try{
      const parts=JSON.parse(task.binding);
      return Array.isArray(parts)&&parts.length===2&&parts[1]===this.deps.connection()&&typeof parts[0]==='string'
        ?this.deps.settingsRecovery?.(parts[0])??null:null;
    }catch{return null;}
  }
  private binding(){return JSON.stringify([this.deps.binding(),this.deps.connection()]);}
  private requireConnection(){if(!this.deps.ready())throw new Error('ChatGPT 연결과 현재 로그인 상태를 확인하세요.');}
  ownsExecution(){return this.state.busy||unresolved(this.state.task);}
  blocksOtherWrites(){return this.ownsExecution()||['draft','ready','waiting_human'].includes(this.state.task?.status||'');}
  private authority(){
    const version=this.version,connection=this.deps.connection(),generation=this.deps.connectionGeneration?.();
    return ()=>version===this.version&&connection===this.deps.connection()&&this.deps.ready()
      &&Object.is(generation,this.deps.connectionGeneration?.());
  }


  async propose(input:unknown):Promise<AssistantTask>{
    this.requireConnection();
    if(this.state.loading||this.ownsExecution()||['ready','waiting_human'].includes(this.state.task?.status||''))
      throw new Error('진행 중인 작업을 일시정지하고 결과를 확인한 뒤 새 계획을 요청하세요.');
    if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(key=>!['goal','steps'].includes(key)))
      throw new Error('작업 계획에는 목표와 실행 단계만 넣으세요.');
    const value=input as {goal:unknown;steps:unknown};
    const plan=validateTaskPlan({goal:value.goal,steps:value.steps,binding:this.binding()});
    const version=++this.version,authority=this.authority(),generation=this.deps.generation?.();
    this.set({loading:true,error:null});
    try {
      const task=await this.deps.client.create(plan);
      if(!authority()||!Object.is(generation,this.deps.generation?.()))throw new Error('연결이 바뀌어 이전 계획을 화면에 적용하지 않았습니다.');
      this.set({task});return task;
    }catch(cause){if(version===this.version)this.set({error:message(cause)});throw cause;}
    finally{if(version===this.version)this.set({loading:false});}
  }

  async restore(id?:string):Promise<AssistantTask|null>{
    this.requireConnection();if(this.state.busy||this.state.loading)throw new Error('실행 중인 작업을 먼저 일시정지하세요.');
    const connection=this.deps.connection(),version=++this.version,authority=this.authority();
    this.set({loading:true,error:null});
    try{
      let task:AssistantTask|null;
      if(id)task=await this.deps.client.get(id);
      else{
        const rows=(await this.deps.client.list()).filter(row=>{
          try{return JSON.parse(row.binding)?.[1]===connection;}catch{return false;}
        }).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
        const unfinished=(row:AssistantTask)=>!['completed','failed'].includes(row.status);
        task=rows.find(row=>row.binding===this.binding()&&unfinished(row))
          ??rows.find(unfinished)??rows[0]??null;
      }
      if(!authority())return null;
      this.set({task,error:task&&task.binding!==this.binding()
        &&!this.recovery(task)?'저장된 작업의 프로젝트·자료가 현재 화면과 다릅니다. 기록만 표시하며, 원래 작업을 연 뒤 다시 확인하세요.':null});return task;
    }catch(cause){if(version===this.version)this.set({error:message(cause)});throw cause;}
    finally{if(version===this.version)this.set({loading:false});}
  }

  /** Called with the card's captured identity/revision, never by a model tool. */
  async approve(id:string,revision:number):Promise<AssistantTask|null>{
    this.requireConnection();
    const task=this.state.task;
    if(!task||task.id!==id||task.revision!==revision||task.status!=='draft')throw new Error('확인할 작업 계획이 바뀌었습니다. 현재 계획을 다시 읽으세요.');
    if(this.state.loading||this.state.busy||!this.deps.mayStart())throw new Error('다른 도우미 작업이 끝난 뒤 이 계획을 확인하세요.');
    if(task.binding!==this.binding())throw new Error('프로젝트·자료·연결이 바뀌었습니다. 현재 상태로 계획을 다시 요청하세요.');
    const connection=this.deps.connection(),authority=this.authority(),generation=this.deps.generation?.();this.set({loading:true,error:null});
    try{
      const current=await this.deps.client.get(id);
      if(!authority()||!Object.is(generation,this.deps.generation?.())||current.revision!==revision||current.plan_sha256!==task.plan_sha256||current.binding!==this.binding())
        throw new Error('확인할 계획 또는 연결이 바뀌었습니다. 다시 확인하세요.');
      const approved=await this.deps.client.event(id,{type:'approve',confirmed:true,expected_revision:revision,binding:task.binding});
      if(!authority()||!Object.is(generation,this.deps.generation?.()))throw new Error('연결이 바뀌어 실행하지 않았습니다.');
      this.set({task:approved,loading:false});return await this.drive(approved,connection);
    }catch(cause){if(connection===this.deps.connection())this.set({error:message(cause)});throw cause;}
    finally{this.set({loading:false});}
  }

  async resume():Promise<AssistantTask|null>{
    this.requireConnection();
    const task=this.state.task;
    if(!task||this.state.loading||this.state.busy||!this.deps.mayStart())return task;
    if(task.binding!==this.binding())throw new Error('프로젝트·자료·연결이 달라졌습니다. 새 상태에 맞는 계획을 요청하세요.');
    const connection=this.deps.connection(),authority=this.authority(),generation=this.deps.generation?.();
    this.set({loading:true,error:null});
    try{
      const current=await this.deps.client.get(task.id);
      if(!authority()||!Object.is(generation,this.deps.generation?.()))throw new Error('연결이 바뀌었습니다.');
      if(current.revision!==task.revision){this.set({task:current});return current;}
      if(current.binding!==this.binding())throw new Error('프로젝트·자료가 달라졌습니다. 현재 상태를 다시 확인하세요.');
      const next=current.status==='paused'||current.status==='waiting_human'
        ?await this.deps.client.event(task.id,{type:'resume',confirmed:true,expected_revision:current.revision,binding:current.binding}):current;
      if(!authority()||!Object.is(generation,this.deps.generation?.()))throw new Error('연결이 바뀌어 실행하지 않았습니다.');
      this.set({task:next,loading:false});return await this.drive(next,connection);
    }catch(cause){if(authority())this.set({error:message(cause)});throw cause;}
    finally{if(authority())this.set({loading:false});}

  }

  /** A separate person action restores only approved ephemeral inputs, never starts a model or task. */
  async restoreSettings(id?:string,revision?:number):Promise<void>{
    this.requireConnection();const task=this.state.task;
    if(!task||this.state.busy||this.state.loading||!this.deps.mayStart())return;
    if(id!==undefined&&(task.id!==id||task.revision!==revision))throw new Error('복원할 작업 계획이 바뀌었습니다. 다시 확인하세요.');
    const authority=this.authority(),generation=this.deps.generation?.();this.set({loading:true,error:null});
    try{
      const fresh=await this.deps.client.get(task.id);
      if(!authority()||!Object.is(generation,this.deps.generation?.())||fresh.revision!==task.revision||fresh.plan_sha256!==task.plan_sha256)throw new Error('저장된 작업이나 연결이 바뀌었습니다. 다시 확인하세요.');
      const recovery=this.recovery(fresh);
      if(!recovery||!recovery.restore()||fresh.binding!==this.binding())throw new Error('현재 프로젝트·자료 또는 직접 바꾼 설정이 달라 복원하지 않았습니다.');
      this.set({task:fresh});
    }catch(cause){if(authority())this.set({error:message(cause)});throw cause;}
    finally{if(authority())this.set({loading:false});}
  }

  refreshContext():void{
    const matches=!this.state.task||this.state.task.binding===this.binding(),label=this.recovery(this.state.task)?.label??null;
    if(matches!==this.state.contextMatches||label!==this.state.settingsRecoveryLabel)this.set({});
  }

  async pollWaiting():Promise<AssistantTask|null>{
    const task=this.state.task;
    if(!task||task.status!=='waiting_job'||this.state.busy||!this.deps.ready()||!this.deps.mayStart())return task;
    if(task.binding!==this.binding())return this.pause();
    return this.drive(task,this.deps.connection());
  }

  private async drive(task:AssistantTask,connection:string):Promise<AssistantTask|null>{
    if(this.state.busy)return this.state.task;
    this.requireConnection();
    const lease=acquireExecutionLease('assistant');
    if(!lease){this.set({error:'다른 자동 진행이 실행 중입니다. 해당 진행이 멈춘 뒤 저장 결과를 확인하고 이어 가세요.'});return null;}
    this.set({busy:true,error:null});
    const version=this.version,connectionGeneration=this.deps.connectionGeneration?.();
    const connectionCurrent=()=>version===this.version&&connection===this.deps.connection()&&this.deps.ready()
      &&Object.is(connectionGeneration,this.deps.connectionGeneration?.());
    const guard=()=>{if(!connectionCurrent())throw new Error('연결이 바뀌어 다음 작업을 실행하지 않았습니다.');};
    const wrap=(run:TaskControllerDeps['execute'])=>async(step:TaskStep,control:TaskControl)=>{
      const checkpoint=async(receipt:Record<string,unknown>)=>{
        const claim=receipt.unstarted_claim as {nonce?:unknown;phase?:unknown}|undefined;
        const claiming=claim!==undefined&&Object.keys(receipt).length===2&&Object.hasOwn(receipt,'not_started');
        if(claiming&&(typeof claim?.nonce!=='string'||claim.phase!=='claimed'||receipt.not_started!==false))
          throw new Error('재개 실행 소유권 기록이 올바르지 않습니다.');
        for(let attempt=0;attempt<(claiming?1:2);attempt++){
          guard();if(control.isPaused())throw new Error('작업을 일시정지했습니다.');
          const current=await this.deps.client.get(task.id);guard();
          const active=current.steps.find(row=>row.id===step.id);
          if(active?.attempt_id!==step.attempt_id||active.status==='completed')throw new Error('실행 중인 작업 기록이 바뀌었습니다.');
          if(claiming&&active.receipt?.not_started!==true)throw new Error('이전 실행의 시작 전 확인은 이미 소비됐습니다. 저장 결과를 확인하세요.');
          try{
            const saved=await this.deps.client.event(task.id,{type:'record_evidence',step_id:step.id,
              attempt_id:step.attempt_id??undefined,expected_revision:current.revision,binding:current.binding,receipt});
            guard();
            if(claiming){
              const savedStep=saved.steps.find(row=>row.id===step.id),savedClaim=savedStep?.receipt?.unstarted_claim as {nonce?:unknown;phase?:unknown}|undefined;
              if(savedStep?.attempt_id!==step.attempt_id||savedStep.receipt?.not_started!==false||savedClaim?.nonce!==claim?.nonce||savedClaim?.phase!=='claimed')
                throw new Error('재개 실행 소유권을 확인하지 못했습니다. 다시 실행하지 않았습니다.');
            }
            this.set({task:saved});return;
          }catch(cause){if(claiming||attempt===1)throw cause;}
        }
      };
      guard();const result=await run(step,{isPaused:control.isPaused,isCurrent:()=>connectionCurrent()&&control.isCurrent(),isAuthorityCurrent:connectionCurrent,priorModelJobId:control.priorModelJobId,checkpoint});
      guard();
      // Only a verified app-owned transition may update the runtime half; it cannot replace the account/model half.
      return result.bindingAfter?{...result,bindingAfter:JSON.stringify([result.bindingAfter,connection])}:result;
    };
    const deps:TaskRunnerDeps={
      read:async id=>{guard();const record=await this.deps.client.get(id);guard();return record;},
      event:async(id,event)=>{guard();return this.deps.client.event(id,event);},
      binding:()=>this.binding(),generation:this.deps.generation,
      execute:wrap(this.deps.execute),reconcile:wrap(this.deps.reconcile),
      onChange:record=>{if(connectionCurrent())this.set({task:record});},
    };
    const runner=this.deps.runnerFactory?.(deps)??new AssistantTaskRunner(deps);this.runner=runner;
    try{const result=await runner.run(task);if(connectionCurrent()){this.set({task:result});return result;}return null;}
    catch(cause){if(connectionCurrent())this.set({error:message(cause)});return null;}
    finally{if(this.runner===runner)this.runner=null;releaseExecutionLease(lease);this.set({busy:false});}
  }

  async pause():Promise<AssistantTask|null>{
    const task=this.state.task;if(!task)return null;
    if(this.runner)return this.runner.pause();
    if(['draft','completed','failed','paused'].includes(task.status))return task;
    const result=await this.deps.client.event(task.id,{type:'pause',expected_revision:task.revision,binding:task.binding,message:'작업을 일시정지했습니다. 진행 중인 학습은 별도로 계속됩니다.'});
    this.set({task:result});return result;
  }

  /** Account/model changes hide old results immediately; never transfer a running task to a new connection. */
  invalidateConnection(){this.version++;void this.runner?.pause().catch(()=>undefined);this.set({task:null,loading:false,error:null});}

  summary():Record<string,unknown>{
    return taskFeedback(this.state.task,{contextMatches:!this.state.task||this.state.task.binding===this.binding(),controllerError:this.state.error});
  }

  /** A model observation is GET-only, even when a saved step could be resumed. Never changes execution state. */
  async observe():Promise<Record<string,unknown>>{
    const task=this.state.task,authority=this.authority(),binding=this.binding(),generation=this.deps.generation?.();
    const current=()=>authority()&&binding===this.binding()&&Object.is(generation,this.deps.generation?.())&&this.state.task?.id===task?.id;
    const changed=()=>taskFeedback(null,{contextMatches:false});
    if(!authority())return changed();
    if(!task)return this.summary();
    try{
      const saved=await this.deps.client.get(task.id);
      if(!current())return changed();
      if(!saved||saved.schema_version!==1||saved.id!==task.id||!Number.isInteger(saved.revision)||saved.revision<1
        ||!['draft','ready','running','waiting_human','waiting_job','paused','needs_reconciliation','failed','completed'].includes(saved.status)
        ||!Array.isArray(saved.steps)||saved.steps.some(step=>!step||typeof step.id!=='string'||!step.id
          ||!['pending','running','waiting_human','waiting_job','completed','failed','uncertain'].includes(step.status)
          ||(step.receipt!==null&&(typeof step.receipt!=='object'||Array.isArray(step.receipt)))))
        throw new Error('작업의 저장 응답 형식이나 식별자를 확인하지 못했습니다.');
      validateTaskPlan({goal:saved.goal,binding:saved.binding,steps:saved.steps.map(({action,args,label})=>({action,args,label}))});
      if(saved.revision<(this.state.task?.revision??task.revision))throw new Error('조회 중 작업 기록이 갱신됐습니다. 현재 캐시를 표시하며 다시 읽어 확인하세요.');
      return taskFeedback(saved,{contextMatches:saved.binding===this.binding(),controllerError:this.state.error,fresh:true,source:'ledger'});
    }catch(cause){
      if(!current())return changed();
      const cached=this.state.task;
      return taskFeedback(cached,{contextMatches:cached?.binding===this.binding(),controllerError:this.state.error,readError:cause});
    }
  }
}
