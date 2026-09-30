export interface ScopedJob {task?:string;source_dataset_path?:string;dataset_path?:string;training_provenance?:{labelset_id?:string}}
export interface JobSnapshot<T> {scope:string;job:T}
/** Render/effect selection must discard old state before reset effects run. */
export function scopedTrainingJob<T extends ScopedJob>(snapshot:JobSnapshot<T>|null,scope:string,source:string,labelset:string,task?:string):T|null {
  if(!snapshot||snapshot.scope!==scope)return null;
  const job=snapshot.job;
  if((job.source_dataset_path||job.dataset_path)!==source||(job.training_provenance?.labelset_id||'default')!==labelset)return null;
  if(task&&job.task!==task)return null;
  return job;
}
