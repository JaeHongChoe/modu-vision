import type {ModelFamily} from '../../services/modelTrainingProgram';
export type TrainingBudget = {max_trials: number; max_total_epochs: number; max_seconds: number; max_memory_mb?: number};
export function validateTrainingBudget(budget: TrainingBudget, epochs: number): string | null {
  if (!Number.isInteger(budget.max_trials) || budget.max_trials < 1 || budget.max_trials > 32) return '최대 후보 수는 1~32 정수여야 합니다.';
  if (!Number.isInteger(budget.max_total_epochs) || budget.max_total_epochs < 1 || budget.max_total_epochs > 512) return '전체 Epoch 예산은 1~512 정수여야 합니다.';
  if (!Number.isFinite(budget.max_seconds) || budget.max_seconds <= 0 || budget.max_seconds > 86400) return '시간 예산은 0초 초과 86400초 이하여야 합니다.';
  if(budget.max_memory_mb!==undefined&&(!Number.isInteger(budget.max_memory_mb)||budget.max_memory_mb<1||budget.max_memory_mb>1048576))return '메모리 예산은 1~1048576 MB 정수여야 합니다.';
  if (!Number.isInteger(epochs) || epochs < 1 || epochs > 500 || epochs > budget.max_total_epochs) return '후보당 Epoch이 전체 Epoch 예산 안에 있어야 합니다.';
  return null;
}
export const familyPreparation: Record<ModelFamily, {label: string; truth: string; evaluation: string; route: string}> = {
  classification: {label:'이미지 분류',truth:'이미지별 클래스',evaluation:'독립 test 이미지의 혼동행렬',route:'라벨링 → 데이터 분할 → 아래 모델 학습'},
  segmentation: {label:'영역 분할',truth:'픽셀 마스크 또는 polygon',evaluation:'독립 test 이미지의 영역 IoU',route:'라벨링 → 데이터 분할 → 아래 모델 학습'},
  detection: {label:'객체 검출',truth:'객체 bbox',evaluation:'독립 test 이미지의 검출 지표',route:'라벨링 → 데이터 분할 → 아래 모델 학습'},
  anomaly: {label:'이상탐지',truth:'정상 학습 이미지; 영역 목적은 시험 마스크 추가',evaluation:'이미지 AUROC 또는 마스크 기반 영역 지표',route:'정상 데이터 확인 → 이미지/영역 목적 → 아래 모델 학습'},
  patch_classification: {label:'패치 분류',truth:'원본 이미지와 영역별 클래스',evaluation:'원본 단위로 분리한 시험 패치 지표',route:'라벨링 → 아래 패치 추출 → 학습'},
  ocr: {label:'문자 인식',truth:'한 줄 문자 이미지와 사람이 확인한 문자열',evaluation:'독립 test의 문자 오류율과 정확 일치율',route:'아래 프로젝트 이미지 선택 → 문자열 입력 → 정답 저장 → 학습'},
  rotated_detection: {label:'회전 객체 검출',truth:'회전 박스 또는 polygon; 방향 정답은 별도',evaluation:'독립 test의 회전 박스·방향 지표',route:'라벨링 → 아래 객체 정답 준비 → 학습'},
  rotation: {label:'정방향 보정',truth:'이미지별 사람이 확인한 반시계 보정각',evaluation:'독립 test의 원형 각도 오차',route:'아래 프로젝트 이미지 선택 → 보정각 입력 → 정답 저장 → 학습'},
  defect_gan: {label:'결함 이미지 생성',truth:'사람이 지정한 실제 결함 crop',evaluation:'시험 결함 비교와 생성 후보의 사람 검토',route:'라벨링 → 아래 결함 crop 준비 → 학습 → 후보 검토'},
  enhancement: {label:'이미지 개선',truth:'원본 정답·오염 입력 쌍; 합성 노이즈 목적 명시',evaluation:'독립 test의 입력/출력 PSNR',route:'아래 프로젝트 원본으로 쌍 준비 → 학습 → 시험 비교'},
};
