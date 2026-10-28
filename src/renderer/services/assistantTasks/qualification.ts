import {request} from '../api';
import type {AssistantTask,TaskStep,TaskQualityAssessment} from './types';

/** Policy qualification is a separate durable result; it never creates a human review record. */
export async function qualifyCompletedTaskStep(task:AssistantTask,step:TaskStep):Promise<TaskQualityAssessment|void>{
  if(!task.authorization||step.status!=='completed'||typeof step.receipt?.evaluation_id!=='string')return;
  const path=step.action==='evaluate_model'?'/api/model-operations/qualify-model'
    :step.action==='evaluate_flow'?'/api/flow-evaluations/qualifications':null;
  if(!path)return;
  const result=await request<{assessment?:TaskQualityAssessment}>(path,{method:'POST',body:JSON.stringify({task_id:task.id,step_id:step.id,evaluation_id:step.receipt.evaluation_id})});
  if(!result.assessment)throw new Error('저장된 품질 판정 결과가 없습니다. 평가를 다시 실행하지 않고 판정 기록을 확인하세요.');
  return result.assessment;
}
