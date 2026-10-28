import { ACTION_CATALOG, validateTaskPlan } from './catalog';
import type { AssistantTask, TaskActionResult, TaskControl, TaskEvent, TaskStep,TaskQualityAssessment } from './types';
import {EXECUTION_POLICY_VERSION} from './types';

export interface TaskRunnerDeps {
  read(id: string): Promise<AssistantTask>;
  event(id: string, event: TaskEvent): Promise<AssistantTask>;
  binding(): string;
  /** Run-local invalidation token; unlike the durable semantic binding it need not survive reload. */
  generation?(): unknown;
  execute(step: TaskStep, control: TaskControl): Promise<TaskActionResult>;
  reconcile?(step: TaskStep, control: TaskControl): Promise<TaskActionResult>;
  onChange(task: AssistantTask): void;
  /** Runs only after the evaluation receipt is durable, before the next planned side effect. */
  afterStep?(task:AssistantTask,step:TaskStep):Promise<TaskQualityAssessment|void>;
}

type EventFields = Omit<TaskEvent, 'expected_revision' | 'binding'>;
type EventInput = EventFields | ((task: AssistantTask) => EventFields | null);
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const stable = (value: unknown): string => JSON.stringify(value, (_key, item: unknown) => {
  if (item && typeof item === 'object' && !Array.isArray(item)) return Object.fromEntries(Object.entries(item).sort(([a],[b]) => a.localeCompare(b)));
  return item;
});
const planIdentity = (task: AssistantTask) => stable({id:task.id,goal:task.goal,hash:task.plan_sha256,steps:task.steps.map(({id,action,args,label})=>({id,action,args,label})),approval:task.approval,authorization:task.authorization??null});
const terminal = (task: AssistantTask) => task.status === 'completed' || task.status === 'failed';
const activeStep = (task: AssistantTask) => task.steps.find(step => step.id === task.current_step_id) ?? task.steps.find(step => ['running','uncertain','waiting_job','waiting_human'].includes(step.status));
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '실행 응답을 확인하지 못했습니다.';
const receiptCopy = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  try {
    const json = JSON.stringify(value);
    if (new TextEncoder().encode(json).byteLength > 16384) return null;
    return JSON.parse(json) as Record<string, unknown>;
  } catch { return null; }
};

/** A bounded supervisor. It never approves, resumes a human gate, polls jobs, or retries an unresolved mutation. */
export class AssistantTaskRunner {
  private running: Promise<AssistantTask> | null = null;
  private stopped = false;
  private latestTask: AssistantTask | null = null;
  private identity: string | null = null;
  private generation: unknown;
  private writes: Promise<unknown> = Promise.resolve();

  constructor(private readonly deps: TaskRunnerDeps) {}

  run(task: AssistantTask): Promise<AssistantTask> {
    if (this.running) return this.running;
    this.stopped = false;
    const input = clone(task);
    this.identity = planIdentity(input);
    this.generation = this.deps.generation?.();
    this.latestTask = input;
    const operation = this.supervise(input).finally(() => { this.running = null; });
    this.running = operation;
    return operation;
  }

  /** Stops dispatch immediately; an in-flight side effect can still finish and retain its verified receipt. */
  async pause(): Promise<AssistantTask | null> {
    this.stopped = true;
    if (!this.latestTask || terminal(this.latestTask) || this.latestTask.status === 'draft') return this.latestTask;
    const result = await this.post({type:'pause',message:'작업을 일시정지했습니다. 시작한 작업의 결과는 계속 확인합니다.'});
    return result.task;
  }

  private publish(task: AssistantTask): AssistantTask {
    this.latestTask = clone(task);
    this.deps.onChange(clone(task));
    return task;
  }

  private async read(): Promise<AssistantTask> {
    if (!this.latestTask) throw new Error('실행할 작업이 없습니다.');
    const task = await this.deps.read(this.latestTask.id);
    if (task.id !== this.latestTask.id) throw new Error('다른 작업의 응답을 받았습니다.');
    return this.publish(clone(task));
  }

  /** Serializes this supervisor's pause/completion CAS writes, and reads back ambiguous network responses. */
  private post(input: EventInput): Promise<{task:AssistantTask; recovered:boolean}> {
    const operation = this.writes.then(async () => {
      const before = await this.read();
      const fields = typeof input === 'function' ? input(before) : input;
      if (!fields) return {task:before,recovered:false};
      if (fields.type === 'pause' && (terminal(before) || before.status === 'paused')) return {task:before,recovered:false};
      try {
        const next = await this.deps.event(before.id,{...fields,expected_revision:before.revision,binding:before.binding});
        return {task:this.publish(next),recovered:false};
      } catch {
        // A response failure does not prove that the event failed. Its saved state is authoritative.
        return {task:await this.read(),recovered:true};
      }
    });
    this.writes = operation.catch(() => undefined);
    return operation;
  }

  private valid(task: AssistantTask): boolean {
    try {
      const grant=task.authorization;
      const authorized=Boolean(grant&&!task.approval&&['user_request','oneclick'].includes(grant.source)
        &&grant.policy_version===EXECUTION_POLICY_VERSION&&grant.plan_sha256===task.plan_sha256
        &&grant.binding&&grant.authorized_at&&typeof grant.scope?.workspace_id==='string'&&grant.scope.workspace_id.trim()
        &&typeof grant.scope?.actor_id==='string'&&grant.scope.actor_id.trim()
        &&stable(grant.allowed_steps)===stable(task.steps.map(step=>step.id)));
      const approved=Boolean(task.approval&&task.approval.plan_sha256===task.plan_sha256&&task.approval.binding&&task.approval.confirmed_at);
      validateTaskPlan({goal:task.goal,binding:task.binding,steps:task.steps.map(({action,args,label})=>({action,args,label}))});
      if (task.schema_version !== 1 || !Number.isInteger(task.revision) || task.revision < 1 || !/^[a-f0-9]{64}$/.test(task.plan_sha256)
        || !['draft','ready','running','waiting_human','waiting_job','paused','needs_reconciliation','failed','completed'].includes(task.status)
        || (!authorized&&!approved)
        || new Set(task.steps.map(step=>step.id)).size !== task.steps.length || task.steps.some(step=>!step.id)) return false;
      let unfinished = false, active: TaskStep | undefined;
      for (const step of task.steps) {
        if (step.status === 'completed') {
          if (unfinished || !step.attempt_id || !step.receipt || typeof step.receipt !== 'object' || Array.isArray(step.receipt)) return false;
        } else if (step.status === 'pending') {
          unfinished = true;
          if (step.attempt_id !== null || step.receipt !== null) return false;
        } else {
          if (unfinished || active || !['running','uncertain','waiting_job','waiting_human'].includes(step.status) || !step.attempt_id) return false;
          unfinished = true; active = step;
        }
      }
      if ((active?.id ?? null) !== task.current_step_id) return false;
      if (task.status === 'ready' && active) return false;
      if (['running','waiting_human','waiting_job'].includes(task.status) && active?.status !== task.status) return false;
      if (task.status === 'needs_reconciliation' && (!active || !['uncertain','waiting_human','waiting_job'].includes(active.status))) return false;
      // Backend verifies the immutable original plan hash, including its original binding. The current binding may
      // legitimately advance after a receipt; the original approval and the goal/actions/args must stay unchanged.
      return planIdentity(task) === this.identity;
    } catch { return false; }
  }

  private current(task: AssistantTask): boolean {
    try { return this.valid(task) && this.deps.binding() === task.binding && Object.is(this.deps.generation?.(),this.generation); } catch { return false; }
  }

  private async stop(message: string): Promise<AssistantTask> {
    this.stopped = true;
    return (await this.post({type:'pause',message})).task;
  }

  private control(task: AssistantTask): TaskControl {
    const generation = this.generation, binding = task.binding;
    const activeIndex = task.steps.findIndex(step => step.id === task.current_step_id);
    let priorModelJobId: string | undefined;
    // Only the already-validated completed prefix belongs to this action. Current/future receipts and unrelated
    // actions cannot replace the model produced by the approved plan, including after reload or manual UI selection.
    for (let index = activeIndex - 1; index >= 0; index--) {
      const previous = task.steps[index];
      if (previous.status !== 'completed') break;
      if (previous.action !== 'start_training' && previous.action !== 'evaluate_model') continue;
      const jobId = previous.receipt?.job_id;
      if (typeof jobId === 'string' && jobId.trim()) {priorModelJobId = jobId; break;}
    }
    return {isCurrent:()=>{try {return this.deps.binding()===binding && Object.is(this.deps.generation?.(),generation);} catch {return false;}},
      isPaused:()=>this.stopped,...(priorModelJobId ? {priorModelJobId} : {})};
  }

  private async uncertain(step: TaskStep, message: string, receipt?: Record<string, unknown> | null): Promise<AssistantTask> {
    // Partial evidence identifies an accepted side effect; it never proves completion or grants another mutation.
    // A prior human prerequisite proved only the earlier preparation did not dispatch. Once a resumed attempt is
    // ambiguous, that old proof must not authorize another dispatch, including when the response lost every ID.
    const partial = step.receipt?.not_started === true ? {...receipt,not_started:false} : receipt;
    const evidence = partial && Object.keys(partial).length ? partial : null;
    return (await this.post({type:evidence?'record_evidence':'reconcile_step',step_id:step.id,attempt_id:step.attempt_id ?? undefined,
      message,...(evidence?{receipt:evidence}:{})})).task;
  }

  private async recordResult(step: TaskStep, result: TaskActionResult, reconciliation: boolean): Promise<AssistantTask> {
    const task = await this.read();
    const saved = task.steps.find(row=>row.id===step.id);
    if (!saved || saved.attempt_id !== step.attempt_id) return this.stop('다른 실행이 작업 기록을 바꿨습니다. 결과를 다시 확인하세요.');
    if (saved.status === 'completed') return task;
    if (!this.valid(task)) return this.stop('확인한 계획이 바뀌어 다음 작업을 멈췄습니다.');
    if (!result || !['completed','waiting_human','waiting_job','needs_reconciliation'].includes(result.kind)) return this.uncertain(saved,'실행 결과 형식을 확인하지 못했습니다.');
    const receipt = receiptCopy(result.receipt);
    if (result.kind === 'needs_reconciliation' || !receipt)
      return this.uncertain(saved,result.message || '시작한 작업의 저장 결과를 다시 확인해야 합니다.',receipt);
    if(result.kind==='completed'&&task.authorization&&this.deps.afterStep&&['evaluate_model','evaluate_flow'].includes(step.action)&&typeof receipt.evaluation_id==='string')
      receipt.quality_assessment={status:'pending',evaluation_id:receipt.evaluation_id,decision_source:'policy'};
    const changesBinding = ACTION_CATALOG.find(row=>row.id===step.action)?.changesBinding === true;
    let bindingAfter: string | undefined;
    if ((result.kind === 'completed' || result.kind === 'waiting_job') && changesBinding
      && typeof result.bindingAfter === 'string' && result.bindingAfter.length <= 16384 && result.bindingAfter.trim()) {
      try {
        if (this.deps.binding() === result.bindingAfter) {
          bindingAfter=result.bindingAfter;
          this.generation=this.deps.generation?.();
        }
      } catch { /* keep the old authority */ }
    }
    const wait = result.kind === 'waiting_human' ? 'human' : result.kind === 'waiting_job' ? 'job' : undefined;
    const written = await this.post(before => {
      const active = before.steps.find(row=>row.id===step.id);
      if (!active || active.attempt_id !== step.attempt_id || active.status === 'completed' || !this.valid(before)) return null;
      // A queued pause can turn running into uncertain after recordResult's first read. Select the transition only
      // after this serialized read so the committed receipt is accepted without ever retrying the actual action.
      const type = reconciliation || active.status !== 'running' ? 'reconcile_step' : wait ? 'wait_step' : 'complete_step';
      return {type,step_id:step.id,attempt_id:step.attempt_id ?? undefined,receipt:{...receipt,...(result.reasonCode?{reason_code:result.reasonCode}:{})},
        ...(result.message?{message:result.message}:{}),...(wait?{wait_kind:wait}:{}),...(bindingAfter?{binding_after:bindingAfter}:{})};
    });
    const finished=written.task.steps.find(row=>row.id===step.id);
    if(finished?.status==='completed'&&!this.stopped&&written.task.status!=='paused')return this.qualify(written.task,finished);
    // If persistence is still unresolved, leave the intent in place for a later reconciliation; never execute again.
    if (written.recovered && !['completed','waiting_human','waiting_job'].includes(written.task.steps.find(row=>row.id===step.id)?.status || '')) return written.task;
    return written.task;
  }

  private async qualify(task:AssistantTask,step:TaskStep):Promise<AssistantTask>{
    if(!this.deps.afterStep)return task;
    const result=await this.deps.afterStep(clone(task),clone(step));
    if(!task.authorization||!['evaluate_model','evaluate_flow'].includes(step.action)||typeof step.receipt?.evaluation_id!=='string')return task;
    if(!result||!/^quality_[a-f0-9]{32}$/.test(result.assessment_id)||result.evaluation_id!==step.receipt.evaluation_id
      ||result.decision_source!=='policy'||!['qualified','not_qualified','input_needed'].includes(result.status))
      throw new Error('품질 판정의 저장된 결과를 확인하지 못했습니다. 평가를 반복하지 않고 판정 결과만 다시 확인합니다.');
    const written=await this.post({type:'quality_result',step_id:step.id,attempt_id:step.attempt_id??undefined,assessment_id:result.assessment_id});
    const stored=written.task.steps.find(row=>row.id===step.id)?.receipt?.quality_assessment as {assessment_id?:unknown;status?:unknown}|undefined;
    if(stored?.assessment_id!==result.assessment_id||stored.status==='pending')
      throw new Error('품질 판정 결과의 작업 기록 저장을 확인하지 못했습니다. 평가를 반복하지 않고 판정 저장만 다시 확인합니다.');
    return written.task;
  }

  private async supervise(_input: AssistantTask): Promise<AssistantTask> {
    let task: AssistantTask;
    // A failed read cannot establish whether the last action finished. Let the
    // controller expose the error and stop polling instead of reporting stale state.
    task=await this.read();
    if (task.status === 'draft' || task.status==='failed' || task.status === 'paused' || task.status === 'waiting_human') return task;
    if (!this.valid(task)) return task;
    // The completed evaluation plus this marker is durable pending postprocessing. A reload retries only
    // its idempotent qualification, never the already-completed inference or a later dependent action.
    if(task.authorization&&this.deps.afterStep){
      for(const step of task.steps){
        const quality=step.receipt?.quality_assessment as {status?:unknown}|undefined;
        if(step.status==='completed'&&['evaluate_model','evaluate_flow'].includes(step.action)&&typeof step.receipt?.evaluation_id==='string'
          &&(!quality||quality.status==='pending')){
          if(this.stopped||!this.current(task))return task;
          task=await this.qualify(task,step);
        }
      }
    }
    if(terminal(task))return task;
    // Each iteration consumes one of at most 24 immutable steps; waiting always returns to durable storage.
    for (let count=0; count<24; count++) {
      task=await this.read();
      if (!this.valid(task)) return this.stop('확인한 계획이나 실행 동의가 바뀌어 작업을 멈췄습니다.');
      if (terminal(task) || task.status === 'paused' || task.status === 'waiting_human') return task;
      if (this.stopped) return this.stop('작업을 일시정지했습니다.');
      if (!this.current(task)) return this.stop('프로젝트·자료·연결 또는 설정이 바뀌어 작업을 멈췄습니다.');
      const unresolved=activeStep(task);
      if (unresolved && unresolved.status !== 'pending' && unresolved.status !== 'completed') {
        if (!unresolved.attempt_id || !this.deps.reconcile) return this.uncertain(unresolved,'이전 실행의 저장 결과를 직접 확인해야 합니다. 다시 실행하지 않았습니다.');
        let result: TaskActionResult;
        try { result=await this.deps.reconcile(clone(unresolved),this.control(task)); }
        catch(error) { return this.uncertain(unresolved,errorMessage(error)); }
        task=await this.recordResult(unresolved,result,true);
        if (this.stopped || !this.current(task)) return terminal(task)?task:this.stop('결과를 기록하고 다음 작업을 일시정지했습니다.');
        if (task.status !== 'ready') return task;
        continue;
      }
      if (task.status === 'waiting_job' || task.status === 'needs_reconciliation' || task.status === 'running') return this.stop('진행 중인 작업의 기록을 확인해야 합니다.');
      const next=task.steps.find(step=>step.status==='pending');
      if (!next) return task;
      const attempt='attempt_'+globalThis.crypto.randomUUID().replaceAll('-','');
      const began=await this.post({type:'begin_step',step_id:next.id,attempt_id:attempt});
      task=began.task;
      const started=task.steps.find(step=>step.id===next.id);
      if (began.recovered) return started?.attempt_id===attempt && started.status==='running'
        ? this.uncertain(started,'실행 의도의 저장 응답이 끊겨 결과 확인을 기다립니다. 다시 실행하지 않았습니다.') : task;
      if (!started || started.status!=='running' || started.attempt_id!==attempt) return task;
      task=await this.read();
      if (this.stopped || !this.current(task)) return this.stop('작업 직전 문맥이 바뀌어 실행을 멈췄습니다.');
      const fresh=task.steps.find(step=>step.id===next.id);
      if (task.status!=='running' || fresh?.status!=='running' || fresh.attempt_id!==attempt) return task;
      let result: TaskActionResult;
      try {
        const executable=clone(fresh); Object.freeze(executable.args); Object.freeze(executable);
        result=await this.deps.execute(executable,this.control(task));
      } catch(error) { return this.uncertain(fresh,errorMessage(error)); }
      task=await this.recordResult(fresh,result,false);
      if (this.stopped || !this.current(task)) return terminal(task)?task:this.stop('시작한 작업의 결과를 기록하고 다음 작업을 일시정지했습니다.');
      if (task.status!=='ready') return task;
    }
    return this.latestTask ?? task;
  }
}
