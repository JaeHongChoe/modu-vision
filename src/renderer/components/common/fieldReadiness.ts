/** "현장 적용까지 남은 조건": every condition a field package needs, shown early instead of one step at a time
 *  (audit F-11 / A6). Pure, from the impact report and the step-1 split counts; it authorizes nothing. */
import type { RecordedInput, WorkflowImpact } from '../wizard/workflowReadiness';
import { APPROVAL_TEST_IMAGES_PER_ROLE, roleTestCounts } from '../dataset/importChecklist';

export type FieldConditionState = 'done' | 'todo' | 'unknown';
export type FieldCondition = { id: string; state: FieldConditionState; text: string; step: 1 | 3 | 4 | 5 | 6; where: string };

const stale = new Set(['changed', 'revalidation_required', 'different_source']);
const currentModel = (row: RecordedInput, task: string) => row.task === task && row.scope_matches === true
  && row.checkpoint_state === 'current' && row.data_state === 'current';

export function fieldConditions(input: { task: string; classSplitCounts: Record<string, { test: number }> | null; impact: WorkflowImpact | null }): FieldCondition[] {
  const per = APPROVAL_TEST_IMAGES_PER_ROLE;
  const conditions: FieldCondition[] = [];
  if (input.task === 'classification' && input.classSplitCounts) {
    const { normal, defect } = roleTestCounts(input.classSplitCounts);
    conditions.push({ id: 'test_images', state: normal >= per && defect >= per ? 'done' : 'todo', step: 1, where: '1단계 분할',
      text: `정상·불량 시험 이미지 각 ${per}장 이상 (지금 정상 ${normal} · 불량 ${defect})` });
  } else {
    conditions.push({ id: 'test_images', state: 'unknown', step: 4, where: '4단계 정답 확인',
      text: `정답을 확인한 정상·불량 시험 이미지 각 ${per}장 이상` });
  }
  const impact = input.impact;
  const models = impact ? impact.models.filter(row => currentModel(row, input.task)) : [];
  conditions.push({ id: 'two_models', state: !impact ? 'unknown' : models.length >= 2 ? 'done' : 'todo', step: 3, where: '3단계 학습',
    text: `지금 데이터로 학습한 모델 2개 (같은 시험 이미지로 비교한 뒤 승인, 지금 ${models.length}개)` });
  const approved = impact ? impact.approvals.filter(row => row.scope_matches === true && !stale.has(row.state || '')
    && models.some(model => model.job_id === row.job_id)) : [];
  conditions.push({ id: 'approved_model', state: !impact ? 'unknown' : approved.length ? 'done' : 'todo', step: 4, where: '4단계 비교·승인',
    text: '두 모델 비교 뒤 승인한 모델' });
  const flows = impact ? impact.flows.filter(row => row.scope_matches === true && row.state === 'current') : [];
  conditions.push({ id: 'flow_evaluated', state: !impact ? 'unknown' : flows.length ? 'done' : 'todo', step: 5, where: '5단계 흐름 평가',
    text: '저장한 검사 흐름의 전체 평가 (지금 정답과 일치)' });
  const packages = impact ? impact.packages.filter(row => row.scope_matches === true && !stale.has(row.state || '')) : [];
  conditions.push({ id: 'package', state: !impact ? 'unknown' : packages.length ? 'done' : 'todo', step: 6, where: '6단계 패키지',
    text: '승인 정보가 담긴 검사 흐름 패키지와 대상 장치 확인' });
  return conditions;
}

/** For the single-model export: what it does not prove, in one line, or null when the model is approved and current. */
export function singleModelExportNote(jobId: string | null, impact: WorkflowImpact | null): string | null {
  if (!jobId || !impact) return null;
  const model = impact.models.find(row => row.job_id === jobId);
  const notes: string[] = [];
  if (model?.data_state === 'changed') notes.push('학습 뒤 데이터가 바뀌었습니다');
  if (!impact.approvals.some(row => row.job_id === jobId && row.scope_matches === true && !stale.has(row.state || ''))) notes.push('아직 승인하지 않은 모델입니다');
  return notes.length ? `${notes.join(' · ')}. 단일 모델 파일은 만들 수 있지만, 현장 검사에 쓰기 전에 4단계 비교·승인과 검사 흐름 패키지를 거치세요.` : null;
}
