/** Evidence-only assistant views. No requests, store changes, threshold application or model execution live here. */
import {fileName, reducePaths} from './assistantContext';

const record = (value: unknown): Record<string, any> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {};
const count = (value: unknown): number | null => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
const number = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const words = (value: unknown, limit = 300): string => typeof value === 'string' ? reducePaths(value).slice(0, limit) : '';

/** Backend readiness means prerequisites were inspected, never that training/inference or quality were verified. */
export function preparationEvidence(value: unknown) {
  const raw = record(value), weights = record(raw.weights), runtime = record(raw.runtime), dependencies = record(raw.dependencies);
  const split = record(record(raw.input).split_counts);
  const counts = {train: count(split.train), val: count(split.val), test: count(split.test)};
  const weightsAvailable = ['file_available', 'not_required', 'transfer_ready', 'server_file_available', 'parent_verified'].includes(weights.state);
  // Some existing train/val loaders omit test entirely; training requires measured train/val, and an absent test stays unknown.
  const measuredInput = counts.train !== null && counts.train > 0 && counts.val !== null && counts.val > 0;
  const missing = Array.isArray(dependencies.missing) ? dependencies.missing.map((item: unknown) => words(item, 80)).filter(Boolean) : null;
  return {
    ready: raw.ready === true && measuredInput && runtime.available === true && weightsAvailable && missing?.length === 0,
    backend_ready: typeof raw.ready === 'boolean' ? raw.ready : null,
    model: words(raw.model, 100) || null,
    input: {split_counts: counts, measured: measuredInput},
    runtime: {available: runtime.available === true, device: words(runtime.device, 60) || null, reason: words(runtime.reason), auto: runtime.auto === true},
    dependencies: {missing},
    weights: {state: words(weights.state, 80) || 'unknown', filename: fileName(weights.filename) || null,
      sha256: typeof weights.sha256 === 'string' && /^[a-f0-9]{64}$/.test(weights.sha256) ? weights.sha256 : null,
      content_verified: weights.content_verified === true, problem: words(weights.problem) || null},
    next_actions: Array.isArray(raw.next_actions) ? raw.next_actions.slice(0, 10).map((item: unknown) => words(item)).filter(Boolean) : [],
    execution_verified: raw.execution_verified === true,
    training_started: false, quality_approved: false,
    limits: '준비 항목 점검입니다. 파일 해시는 모델 구조·제공처 진위·현장 품질 승인이 아닙니다. 학습 시작과 가중치 다운로드는 하지 않았습니다.',
  };
}

export interface GuidanceEvaluationInput {
  task: string; jobId: string | null; threshold: number; samples: number;
  knownNormals: number; knownDefects: number; unresolved: number;
  summary: {samples: number; escapes: number; overkills: number; accuracy: number | null; threshold: number};
  metrics: Record<string, unknown>; analysis: unknown; sourceVerified: boolean;
  cohort?: unknown; evaluatedAt?: string;
}

/** Use the UI's already-loaded tradeoff receipt, and refuse to infer a recommendation from unrelated/unknown truth. */
export function evaluationGuidance(input: GuidanceEvaluationInput) {
  const metrics = record(input.metrics), spec = record(metrics.score_spec), analysis = record(input.analysis), optimal = record(analysis.optimal_stats);
  const cohort = record(input.cohort), fixedCohort = Boolean(cohort.dataset_version_id);
  const legacyProbability = input.task !== 'anomaly' && !spec.domain;
  const domain = spec.domain === 'probability' || spec.domain === 'distance' ? spec.domain : legacyProbability ? 'probability' : null;
  const threshold = number(analysis.optimal_threshold);
  let reason: string | null = null;
  if (!input.sourceVerified) reason = '현재 프로젝트·원본·라벨 세트에 묶인 평가가 아닙니다. 4단계에서 현재 평가를 다시 여세요.';
  else if (fixedCohort) reason = '고정 코호트의 추가 기준 점수 보정은 지원하지 않습니다. 반환된 고정 평가 결과만 읽습니다.';
  else if (domain === 'distance') reason = '거리 점수는 확률이 아닙니다. 확률 기준값 권고로 바꾸지 않습니다.';
  else if (domain !== 'probability' || (spec.direction && spec.direction !== 'higher_is_defect')) reason = '점수 의미가 확인되지 않아 기준 점수를 권고하지 않습니다.';
  else if (input.task === 'anomaly' && metrics.evaluated_split === 'test') reason = '이상탐지 test 정답으로 기준 점수를 고르지 않습니다. 별도 보정 자료를 확인하세요.';
  else if (!input.knownNormals || !input.knownDefects || input.unresolved) reason = '확정된 정상·불량이 모두 필요하고 미확정 정답은 없어야 합니다.';
  else if (count(analysis.sample_count) !== input.samples || count(analysis.total_normals) !== input.knownNormals || count(analysis.total_defects) !== input.knownDefects)
    reason = '현재 평가 표본과 일치하는 기준 점수 분석이 없습니다. 4단계의 분석 근거를 확인하세요.';
  else if (threshold === null || threshold < 0 || threshold > 1 || number(optimal.threshold) !== threshold
    || count(optimal.underkill_count) === null || optimal.underkill_count > input.knownDefects
    || count(optimal.overkill_count) === null || optimal.overkill_count > input.knownNormals)
    reason = '유효한 기준 점수와 놓친 불량·잘못 잡은 정상 수가 함께 기록되지 않았습니다.';
  const recommendation = reason ? null : {threshold, escapes: optimal.underkill_count as number, overkills: optimal.overkill_count as number,
    target_max_underkill: number(analysis.target_max_underkill), basis: 'loaded_tradeoff_analysis'};
  return {
    loaded: input.sourceVerified && input.samples > 0, job_id: input.jobId, task: input.task,
    current: {...input.summary, known_normals: input.knownNormals, known_defects: input.knownDefects, unresolved: input.unresolved},
    score: {domain, unit: words(spec.unit, 80) || (legacyProbability ? 'probability' : null),
      direction: words(spec.direction, 80) || (legacyProbability ? 'higher_is_defect' : null),
      basis: legacyProbability ? 'image_verdict_confidence_contract' : 'recorded_score_spec',
      meaning: domain === 'distance' ? '값이 클수록 이상인 거리입니다. 0~1 확률·백분율이 아닙니다.' : domain === 'probability' ? '불량 쪽 점수가 기준 이상이면 불량으로 판단합니다. 모델 confidence는 정답 대비 품질 점수가 아닙니다.' : '기록된 점수 의미가 없습니다.'},
    conditions: {source_current: input.sourceVerified, evaluated_split: words(metrics.evaluated_split, 40) || words(cohort.split, 40) || null,
      selection_overlap: typeof metrics.selection_overlap === 'boolean' ? metrics.selection_overlap : null,
      evaluated_at: words(input.evaluatedAt, 80) || null,
      cohort: fixedCohort ? {dataset_version_id: words(cohort.dataset_version_id, 100), image_count: count(cohort.image_count),
        cohort_sha256: typeof cohort.cohort_sha256 === 'string' && /^[a-f0-9]{64}$/.test(cohort.cohort_sha256) ? cohort.cohort_sha256 : null} : null},
    recommendation, recommendation_reason: reason, quality_approved: false,
    limits: '현재 평가 표본 안의 비교입니다. 기준값을 자동 적용하거나 모델을 승인하지 않습니다. 놓친 불량과 정상 손실을 함께 검토한 뒤 사람이 4단계에서 직접 결정하세요.',
  };
}
