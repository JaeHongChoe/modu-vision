import {request} from './api';
import type {FlowEvaluation,FlowEvaluationHistory,FlowEvaluationCohort,SavedEvaluationFlow,ImageTruth,TruthScope,TruthVerdict} from './flowEvaluationTypes';
export type {FlowEvaluation,FlowImageEvidence,ImageTruth,TruthScope} from './flowEvaluationTypes';

const json = (method:string,body:unknown):RequestInit => ({method,body:JSON.stringify(body)});
function explicitRoles(scope:TruthScope) {
  return Object.values(scope.class_semantics.basis).some(basis=>basis==='explicit') ? scope.class_semantics.roles : null;
}
export function formatFlowMetric(value:number|null|undefined):string {
  return value===null||value===undefined||!Number.isFinite(value) ? '계산 불가' : `${(value*100).toFixed(1)}%`;
}
export function isCompatibleFlowCohort(cohort:Pick<FlowEvaluationCohort,'scope'|'participating_tasks'>,scope:TruthScope):boolean {
  const saved=cohort.scope;
  if(saved.project_id!==scope.project_id||saved.source_dataset_path!==scope.source_dataset_path||saved.labelset_id!==scope.labelset_id||saved.task!==scope.task)return false;
  if(saved.classes.length!==scope.classes.length||saved.classes.some((name,index)=>name!==scope.classes[index]))return false;
  if(saved.class_semantics.version!==scope.class_semantics.version||scope.classes.some(name=>saved.class_semantics.roles[name]!==scope.class_semantics.roles[name]))return false;
  if(scope.task==='mixed') {
    const frozen=[...(cohort.participating_tasks||[])].sort(),current=[...(scope.participating_tasks||[])].sort();
    return frozen.length>=2&&JSON.stringify(frozen)===JSON.stringify(current);
  }
  return true;
}
export const flowEvaluation = {
  versions:(source:string)=>request<{pipelines:SavedEvaluationFlow[];total:number}>(`/api/flowchart/pipelines?source_dataset_path=${encodeURIComponent(source)}`),
  scope:(versionId:string)=>request<TruthScope>(`/api/flow-evaluations/scope/${encodeURIComponent(versionId)}`),
  cohorts:()=>request<{cohorts:FlowEvaluationCohort[];total:number}>('/api/flow-evaluations/cohorts'),
  freeze:(version_id:string,name:string)=>request<FlowEvaluationCohort>('/api/flow-evaluations/cohorts',json('POST',{version_id,name})),
  run:(version_id:string,cohort_id:string)=>request<FlowEvaluation>('/api/flow-evaluations',json('POST',{version_id,cohort_id})),
  history:()=>request<{evaluations:FlowEvaluationHistory[];total:number}>('/api/flow-evaluations'),
  read:(id:string)=>request<FlowEvaluation>(`/api/flow-evaluations/${encodeURIComponent(id)}`),
  reviewQueue:(id:string)=>request<{id:string;items:unknown[]}>(`/api/flow-evaluations/${encodeURIComponent(id)}/review-queue`,json('POST',{})),
  truth:(imagePath:string,scope:TruthScope)=>{
    const params=new URLSearchParams({image_path:imagePath,task:scope.task});
    scope.classes.forEach(name=>params.append('classes',name));
    scope.participating_tasks?.forEach(task=>params.append('participating_tasks',task));
    const roles=explicitRoles(scope);if(roles)params.set('class_roles',JSON.stringify(roles));
    return request<ImageTruth>(`/api/image-truth?${params}`);
  },
  declareTruth:(current:ImageTruth,verdict:TruthVerdict,defect_classes:string[],reviewer:string,note:string)=>
    request<ImageTruth>('/api/image-truth',json('PUT',{image_path:current.image_path,task:current.scope.task,
      classes:current.scope.classes,class_roles:explicitRoles(current.scope),verdict,defect_classes,reviewer,note,
      ...((current.participating_tasks||current.scope.participating_tasks)?{participating_tasks:current.participating_tasks||current.scope.participating_tasks}:{}),
      expected_revision:current.truth_revision,expected_image_revision:current.image_revision})),
};
