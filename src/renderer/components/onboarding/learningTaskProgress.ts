import type {AssistantTask} from '../../services/assistantTasks/types';
import {taskWaitingGuidance} from '../../services/assistantTasks/taskRecoveryPresentation';

type TaskSnapshot = {task: AssistantTask | null; contextMatches: boolean; error: string | null; planningNew?: boolean};

/** Presentation only. Execution, resume and recovery remain owned by the existing controllers. */
export function selectLearningTask(oneclick: TaskSnapshot, assistant: TaskSnapshot) {
  const rows = ([{owner: 'oneclick', state: oneclick}, {owner: 'assistant', state: assistant}] as const)
    .filter(row => row.state.task && row.state.contextMatches && !row.state.planningNew);
  const priority = (task: AssistantTask) => ['running', 'waiting_job', 'needs_reconciliation'].includes(task.status) ? 2 : task.status === 'completed' ? 0 : 1;
  return rows.sort((a, b) => priority(b.state.task!) - priority(a.state.task!)
    || Date.parse(b.state.task!.updated_at) - Date.parse(a.state.task!.updated_at))[0] ?? null;
}

export function learningTaskProgress(task: AssistantTask, error: string | null = null) {
  const step = task.steps.find(row => row.id === task.current_step_id);
  let label = '실행할 순서를 확인해 주세요';
  let detail = '진행 내역에서 사용할 자료와 설정을 확인할 수 있습니다.';
  if (error) {
    label = '작업 상태를 확인해 주세요';
    detail = '진행 내역에서 연결 상태와 저장된 결과를 확인하세요. 실행을 자동으로 반복하지 않습니다.';
  } else if (task.status === 'running' || task.status === 'waiting_job') {
    label = '진행 중이에요';
    detail = '이 화면을 보면서 기다리면 됩니다. 완료 기록을 확인한 뒤 다음 작업으로 이어집니다.';
  } else if (task.status === 'waiting_human') {
    const guidance = taskWaitingGuidance(task, step);
    label = guidance.status === '실행 자원 대기' ? '사용 가능한 장치를 기다려요'
      : guidance.status === '입력 필요' ? '입력이 필요해요' : guidance.status;
    detail = guidance.description;
  } else if (task.status === 'paused') {
    label = '자동 진행을 쉬고 있어요';
    detail = '진행 내역에서 이어갈 수 있습니다. 이미 시작한 학습은 별도로 계속될 수 있습니다.';
  } else if (task.status === 'needs_reconciliation') {
    label = '실행 결과 확인이 필요해요';
    detail = '응답이 끊긴 작업은 다시 실행하지 않고 저장된 결과부터 확인해야 합니다. 진행 내역을 열어 주세요.';
  } else if (task.status === 'failed') {
    label = '작업 상태를 확인해 주세요';
    detail = '진행 내역에 기록된 오류를 확인하세요. 원래 사진과 저장된 결과는 그대로 있습니다.';
  } else if (task.status === 'completed') {
    label = '요청한 실행이 끝났어요';
    detail = '결과를 살펴보세요. 현장에 사용할 품질 확인과 승인은 별도입니다.';
  }
  return {label, detail, message: error || task.message || step?.label || task.goal,
    completed: task.steps.filter(row => row.status === 'completed').length, total: task.steps.length};
}
