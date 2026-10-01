export type TaskRow = {key:string; id:string; kind:string; task:string; status:string; phase:string; source:string; labelset:string;
  transport:string; epoch:number; totalEpochs:number; raw:Record<string,any>};
export type Reservation = {job_id:string; remote?:boolean; uncertain?:boolean; requires_reconciliation?:boolean};
export const terminalTask = (status:string) => ['completed','aborted','cancelled','stopped','failed'].includes(status);
export function normalizeTask(kind:string, raw:Record<string,any>):TaskRow {
  const id=String(raw.search_id || raw.job_id);const transport=String(raw.compute_profile_id || 'local');
  return {key:`${kind}:${transport}:${id}`,id,kind,task:raw.task || kind,status:raw.status,phase:raw.phase || raw.status,
    source:raw.source_dataset_path || raw.dataset_path || '',labelset:raw.scope_kind==='project'?'':raw.training_provenance?.labelset_id || 'default',transport,
    epoch:raw.current_epoch ?? raw.epoch ?? raw.epochs_consumed ?? 0,totalEpochs:raw.total_epochs ?? raw.epochs ?? raw.budget?.max_total_epochs ?? 0,raw};
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
