/** Korean words for the codes a result carries (audit F-15): step statuses, crop labels, split names and score units.
 *  The codes stay in the data and in the technical details; the screen reads the words. */
const STEP_STATUS: Record<string, string> = { passed: '통과', flagged_ng: 'NG 표시', skipped: '건너뜀', failed: '실패', error: '오류',
  pending: '대기', running: '실행 중', review_required: '검토 필요', warning_untrained: '학습 안 된 모델' };
const SPLIT: Record<string, string> = { all: '전체', train: '학습', val: '검증', test: '시험', not_used: '사용 안 함', not_split: '미분할' };
const SCORE_UNIT: Record<string, string> = { probability: '확률(0~1)', mahalanobis_distance: '마할라노비스 거리', euclidean_distance: '유클리드 거리' };

export const stepStatusLabel = (status: string) => STEP_STATUS[status] || status;
export const splitLabel = (split: string | null | undefined) => (split ? SPLIT[split] || split : '미분할');
export const cropLabel = (label: string) => (label === 'Full image' ? '전체 이미지' : label);
export const scoreUnitLabel = (unit: string | null | undefined) => SCORE_UNIT[unit || 'probability'] || unit || SCORE_UNIT.probability;
