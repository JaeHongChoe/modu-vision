import type {TaskPlanInput} from './types';

/** Local intent only; execution still validates the current source, labels and split through existing APIs. */
export function oneclickPlan(input:{splitSaved:boolean;currentModel:boolean;trainRatio?:number}):Omit<TaskPlanInput,'binding'>{
  const steps:TaskPlanInput['steps']=[];
  if(!input.splitSaved){
    const train=Math.round((input.trainRatio??.8)*100)||70,val=Math.round((100-train)/2);
    steps.push({action:'save_split',args:{train_percent:train,val_percent:val,test_percent:100-train-val},label:'학습·검증·시험 분할 저장'});
  }
  if(!input.currentModel)steps.push({action:'start_training',args:{},label:'준비된 자료와 현재 설정으로 학습'});
  steps.push({action:'evaluate_model',args:{},label:'저장된 모델 평가 결과 확인'},
    {action:'save_flow',args:{},label:'현재 모델의 검사 흐름 저장'},
    {action:'evaluate_flow',args:{},label:'시험 자료로 검사 흐름 실행'},
    {action:'inspect_test',args:{},label:'시험 이미지 검사 결과 저장'});
  return{goal:'현재 프로젝트의 학습·평가·시험 검사까지 자동 진행',steps};
}
