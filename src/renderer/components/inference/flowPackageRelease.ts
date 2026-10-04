import type { FlowApprovalPrerequisites, FlowExportBody, FlowParityReport } from '../../services/flowPackageExport';

export const MAX_PARITY_IMAGES = 64;
export const DEFAULT_COHORT_SIZE = 8;
export type ParityMode = 'cohort' | 'single' | 'none';
export interface ParityImageChoice { file_path: string; image_id?: string }

/** First images of the listed held-out set, or nothing when fewer than two are available. */
export function defaultCohort(paths: readonly string[], size = DEFAULT_COHORT_SIZE): string[] {
  return paths.length >= 2 ? paths.slice(0, Math.min(Math.max(2, size), MAX_PARITY_IMAGES)) : [];
}

export function toggleCohort(selected: readonly string[], path: string): string[] {
  if (selected.includes(path)) return selected.filter((item) => item !== path);
  return selected.length >= MAX_PARITY_IMAGES ? [...selected] : [...selected, path];
}

/** Every model needs one of its verified candidates; never invents or reuses another model's approval. */
export function releaseApprovalIds(prerequisites: FlowApprovalPrerequisites | null,
  selection: Readonly<Record<string, string>>): Record<string, string> | null {
  if (!prerequisites || prerequisites.models.length === 0) return null;
  const chosen: Record<string, string> = {};
  for (const model of prerequisites.models) {
    const revision = selection[model.job_id];
    if (!revision || !model.candidates.some((candidate) => candidate.revision_id === revision)) return null;
    chosen[model.job_id] = revision;
  }
  return chosen;
}

type ParityFields = Pick<FlowExportBody, 'parity_images' | 'parity_device' | 'verification_image_path' | 'verification_image_id' | 'compute_profile_id'>;

export function parityFields(mode: ParityMode, cohort: readonly ParityImageChoice[], single: ParityImageChoice | undefined,
  device: string, computeProfileId?: string | null): { fields: ParityFields } | { error: string } {
  if (mode === 'none') return { fields: {} };
  if (computeProfileId && mode !== 'cohort') return { error: '원격 패키지 비교에는 고정 이미지 여러 장을 선택하세요.' };
  if (mode === 'single') {
    return single ? { fields: { verification_image_path: single.file_path, verification_image_id: single.image_id } }
      : { error: '한 장 확인에 사용할 이미지를 선택하세요.' };
  }
  if (cohort.length < 2 || cohort.length > MAX_PARITY_IMAGES) {
    return { error: `동일성 검증 이미지는 2장 이상 ${MAX_PARITY_IMAGES}장 이하로 선택하세요.` };
  }
  return { fields: { parity_images: cohort.map((item) => ({ path: item.file_path, ...(item.image_id ? { image_id: item.image_id } : {}) })),
    parity_device: device, ...(computeProfileId ? { compute_profile_id: computeProfileId } : {}) } };
}

export function parityTargetLabel(report: FlowParityReport): string {
  if (report.execution_target === 'selected_compute') {
    const gpu = report.compute_gpu_selector ? ` · GPU 선택 ${report.compute_gpu_selector}` : '';
    const uuid = report.reference_runtime?.runtime_device_identity?.gpu_uuid;
    return `${report.compute_profile_name || report.compute_profile_id}${gpu} · ${report.device || '장치 확인 필요'}${uuid ? ` · ${uuid}` : ''}`;
  }
  return report.status === 'not_run' ? '검증 실행 전' : `이 컴퓨터 · ${report.device || '장치 확인 필요'}`;
}

export function parityHeadline(report: FlowParityReport): { tone: 'ok' | 'limited' | 'warn' | 'fail'; text: string } {
  if (report.status === 'passed' && report.scope === 'cohort') {
    return { tone: 'ok', text: `고정 이미지 ${report.image_count}장 · ${report.device} 결과 일치` };
  }
  if (report.status === 'passed') return { tone: 'limited', text: '이미지 1장 CPU 결과 일치 (제한된 확인)' };
  if (report.status === 'mismatch') return { tone: 'fail', text: '앱 엔진과 패키지 결과 불일치' };
  if (report.status === 'failed') return { tone: 'fail', text: '동일성 검증 실패' };
  return { tone: 'warn', text: '동일성 미검증' };
}
