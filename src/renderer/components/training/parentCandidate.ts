import type {ParentCandidate} from '../../types/parentCandidate';
export type {ParentCandidate} from '../../types/parentCandidate';

export const parentCandidateNotice = '지표는 저장된 학습 기록입니다. 현재 정답 버전의 평가와 다를 수 있으므로 새 후보는 다시 평가하세요.';
const names: Record<string, string> = {best_metric: '저장 모델 지표', saved_val_loss: '저장 모델 검증 손실', saved_angular_mae_deg: '저장 모델 각도 MAE', saved_mean_oriented_iou: '저장 모델 IoU', saved_mean_angle_error_deg: '저장 모델 각도 오차', saved_mean_direction_error_deg: '저장 모델 방향 오차', saved_mAP_50: '저장 모델 mAP50', saved_mAP_50_95: '저장 모델 mAP50–95', saved_psnr: '저장 모델 PSNR', saved_output_mse: '저장 모델 MSE', last_generator_loss: '최종 생성자 손실', last_discriminator_loss: '최종 판별자 손실', val_loss: '검증 손실', val_accuracy: '검증 정확도', accuracy: 'accuracy', angular_mae_deg: '각도 MAE', val_cer: '검증 CER', cer: 'CER', map50: 'mAP50', mAP: 'mAP', psnr: 'PSNR'};
function date(value: unknown): string | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return null;
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().replace('.000Z', 'Z') !== value) return null;
  return `${value.slice(0,16).replace('T',' ')} UTC`;
}
export function parentCandidateLabel(parent: ParentCandidate, index?: number): string {
  const summary = parent.summary;
  const metrics = Object.entries(names).flatMap(([key, label]) => {
    const value = summary?.training_metrics?.[key];
    return typeof value === 'number' && Number.isFinite(value) ? [`${label}=${value.toPrecision(4)}`] : [];
  }).slice(0,3);
  const saved = date(summary?.model_recorded_at), completed = date(summary?.completed_at);
  return [index === undefined ? parent.job_id : `부모 ${index+1} (${parent.job_id.slice(-8)})`,
    metrics.length ? metrics.join(', ') : '학습 지표 미확인', saved ? `모델 기록 ${saved}` : '모델 기록일 미확인',
    completed ? `완료 기록 ${completed}` : '완료일 미확인', `SHA ${parent.checkpoint_sha256.slice(0,12)}`].join(' · ');
}
