export type TaskRow = {key:string; id:string; kind:string; task:string; status:string; phase:string; source:string; labelset:string;
  transport:string; epoch:number; totalEpochs:number; raw:Record<string,any>};
export type Reservation = {job_id:string; remote?:boolean; uncertain?:boolean; requires_reconciliation?:boolean; fence?:number|null};

/** S1-04: an operator may settle a reservation only when it is uncertain and its job is no longer active. */
export function releasableReservation(row:TaskRow,reservations:Reservation[]|null):Reservation|null {
  if(row.kind!=='training'||['queued','preparing','running','stopping','cancelling'].includes(row.status))return null;
  const lease=reservations?.find(item=>item.job_id===row.id);
  return lease&&lease.uncertain?lease:null;
}
export const terminalTask = (status:string) => ['completed','completed_with_errors','aborted','cancelled','stopped','failed'].includes(status);
export function normalizeTask(kind:string, raw:Record<string,any>):TaskRow {
  const id=String(raw.search_id || raw.job_id);const transport=String(raw.compute_profile_id || 'local');
  return {key:`${kind}:${transport}:${id}`,id,kind,task:raw.task || kind,status:raw.status,phase:raw.phase || raw.status,
    source:raw.source_dataset_path || raw.dataset_path || '',labelset:raw.scope_kind==='project'?'':raw.training_provenance?.labelset_id || 'default',transport,
    epoch:raw.current_epoch ?? raw.epoch ?? raw.epochs_completed ?? raw.epochs_consumed ?? 0,totalEpochs:raw.total_epochs ?? raw.epochs ?? raw.budget?.max_total_epochs ?? 0,raw};
}
export function tasksForScope(rows:TaskRow[],source:string,labelset:string):TaskRow[] {return rows.filter(row=>row.source===source && (row.labelset===labelset || row.raw?.scope_kind==='project'));}
export function taskSnapshotForScope<T>(snapshotScope:string,currentScope:string,rows:T[]):T[] {return snapshotScope===currentScope?rows:[];}
export function taskLifecycle(row:TaskRow,reservations:Reservation[]|null) {
  const lease=reservations?.find(item=>item.job_id===row.id);const terminal=terminalTask(row.status);
  const cancellation=['stopping','cancelling'].includes(row.status)?'requested':['aborted','cancelled','stopped'].includes(row.status)?'acknowledged':'none';
  const termination=terminal?'confirmed':row.status==='disconnected'||row.status==='interrupted'?'unconfirmed':'pending';
  const resource=lease?(lease.uncertain||lease.requires_reconciliation?'reserved_uncertain':'reserved'):terminal && reservations!==null?'released':'unconfirmed';
  return {cancellation,termination,resource};
}
export function taskSelection(storage:Pick<Storage,'getItem'|'setItem'>,scope:string,value?:string):string|null {
  const key=`vision-task-center:${scope}`;if(value!==undefined)storage.setItem(key,value);return storage.getItem(key);
}

/** S1-04: the cancel chain and next action the backend derived from evidence; null when the row has none.
 *  Each step is done only when its own evidence was recorded, never because a later stage was reached. */
export function observationSummary(row:TaskRow):{steps:Array<{label:string;done:boolean}>;facts:string[];nextAction:string|null;cause:string|null}|null {
  const observation=row.raw?.observation;if(!observation)return null;
  const cancel=observation.cancel||{};
  const steps=(cancel.stage||'none')==='none'?[]:[
    {label:'요청 저장',done:Boolean(cancel.requested_at)||cancel.stage!=='none'},
    {label:'작업자 확인',done:Boolean(cancel.acknowledged_at)},
    {label:'종료 신호',done:Array.isArray(cancel.signals)&&cancel.signals.length>0},
    {label:'종료 확인',done:cancel.exit_confirmed===true},
    {label:'예약 반환',done:cancel.reservation_released===true},
  ];
  // Without a cancel, the row still says whether its process exit and its reservation release were recorded.
  const facts:string[]=[];
  if(!steps.length){
    if(cancel.exit_confirmed===true)facts.push('실행 종료 확인');
    else if(terminalTask(row.status))facts.push(observation.worker_recorded===false?'작업자 실행 기록 없음':'실행 종료 미확인');
    if(cancel.reservation_released===true)facts.push('예약 반환');
    else if(cancel.reservation_released===false)facts.push('예약 보유 중');
  }
  return {steps,facts,nextAction:observation.next_action||null,cause:observation.cause||null};
}
