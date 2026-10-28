import type {AssistantTask, TaskStep} from './types';

/** Describe the prerequisite already recorded by the runtime; this never authorizes an action. */
export function taskWaitingGuidance(task: AssistantTask, step?: TaskStep) {
  const receipt = step?.receipt;
  if (step?.action === 'import_dataset' && receipt?.not_started === true && /폴더/.test(task.message || '')) {
    return {status: '폴더 선택 필요', label: '폴더 선택하고 이어하기', action: 'resume' as const,
      description: '버튼을 눌러 사용할 자료 폴더를 다시 선택하세요. 폴더를 고르면 확인한 계획을 이어 갑니다.', secondary: null};
  }
  if(receipt?.reason_code==='waiting_resource')return{status:'실행 자원 대기',label:'작업 상태 확인',action:'navigate' as const,
    description:'현재 실행 중인 작업이나 장치 조건을 기다립니다. 새 상태가 확인되면 남은 단계를 다시 확인합니다.',secondary:'현재 상태 다시 확인'};
  if(receipt?.reason_code==='waiting_input')return{status:'입력 필요',label:'필요한 입력 화면 열기',action:'navigate' as const,
    description:'위 안내의 데이터·라벨·클래스·분할 조건을 준비하세요. 준비 상태가 바뀌면 실행하지 않은 단계부터 다시 확인합니다.',secondary:'준비 상태 다시 확인'};
  if(receipt?.reason_code==='failed')return{status:'작업 오류',label:'오류 기록 열기',action:'navigate' as const,
    description:'저장된 작업의 오류와 원인을 확인하세요. 이미 시작한 작업을 자동으로 다시 실행하지 않습니다.',secondary:null};
  const unsuccessful = ['failed', 'stopped', 'aborted', 'cancelled', 'canceled', 'interrupted', 'disconnected'].includes(String(receipt?.status));
  if (receipt?.not_started === false && unsuccessful && ['generate_label_drafts', 'start_training'].includes(step?.action || '')) {
    const drafts = step?.action === 'generate_label_drafts';
    return {status: drafts ? '초안 작업 결과 확인' : '학습 결과 확인', label: drafts ? '초안 작업 기록 열기' : '학습 작업 기록 열기', action: 'navigate' as const,
      description: `저장된 ${drafts ? '초안' : '학습'} 작업이 완료되지 않았습니다. 위 오류 안내와 ${drafts ? '2' : '3'}단계 작업 기록에서 멈춘 이유를 확인하세요.`, secondary: null};
  }

  if (receipt?.truth_review_required === true || (['evaluate_model', 'evaluate_flow', 'inspect_test'].includes(step?.action || '') && /정답/.test(task.message || ''))) {
    return {status: '정답 확인 필요', label: '정답 확인 화면 열기', action: 'navigate' as const,
      description: '현재 평가의 정답과 미확정 자료를 직접 확인하세요. 확인한 뒤 계획을 이어갈 수 있습니다.', secondary: '검토 결과 확인하고 이어하기'};
  }
  if (step?.action === 'generate_label_drafts' && receipt?.not_started !== true) {
    return {status: '초안 검토 대기', label: '검토 화면 열기', action: 'navigate' as const,
      description: '라벨 초안 채택·정답 확정·최종 승인은 직접 합니다. 초안을 검토한 뒤 결과를 확인하고 이어가세요.', secondary: '검토 결과 확인하고 이어하기'};
  }
  if (receipt?.not_started === true || ['create_project', 'import_dataset', 'save_split', 'configure_training', 'start_training'].includes(step?.action || '')) {
    return {status: '설정 확인 필요', label: '설정 화면 열기', action: 'navigate' as const,
      description: '위 안내에 따라 현재 단계의 자료와 설정을 확인하세요. 설정을 마친 뒤 확인한 계획을 이어 갑니다.', secondary: '설정 확인하고 이어하기'};
  }
  return {status: '직접 확인 필요', label: '확인 화면 열기', action: 'navigate' as const,
    description: '위 안내에서 필요한 항목을 직접 확인한 뒤 계획을 이어가세요.', secondary: '확인하고 이어하기'};
}

export function newRequestPendingReason(task: AssistantTask): string | null {
  const step = task.steps.find(item => ['running', 'uncertain', 'waiting_job'].includes(item.status));
  if (step) return step.status === 'waiting_job'
    ? '진행 중인 작업의 완료 기록을 먼저 확인하세요. 지금도 다른 질문을 입력할 수 있습니다.'
    : `${step.label || '현재 단계'}의 실행 결과를 먼저 확인해야 합니다. 지금도 다른 질문을 입력할 수 있습니다.`;
  if (['running', 'waiting_job', 'needs_reconciliation'].includes(task.status))
    return '현재 계획의 실행 결과를 먼저 확인해야 합니다. 지금도 다른 질문을 입력할 수 있습니다.';
  return null;
}
