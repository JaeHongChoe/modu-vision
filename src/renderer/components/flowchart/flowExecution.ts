export type FlowExecutionChoice = 'selected_compute' | 'local_cpu' | 'local_mps' | 'local_cuda' | 'model_compute';

export function flowExecutionOptions(choice: FlowExecutionChoice, profileId: string | null): {
  executionTarget: 'local' | 'selected_compute' | 'model_compute';
  device: 'cpu' | 'mps' | 'cuda';
  computeProfileId?: string;
} {
  if (choice === 'selected_compute') {
    if (!profileId) throw new Error('플로우를 실행할 서버 설정을 먼저 선택하세요.');
    return { executionTarget: 'selected_compute', device: 'cuda', computeProfileId: profileId };
  }
  return { executionTarget: choice === 'model_compute' ? 'model_compute' : 'local',
    device: choice === 'local_mps' ? 'mps' : choice === 'local_cuda' ? 'cuda' : 'cpu' };
}
