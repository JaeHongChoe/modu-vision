/**
 * src/renderer/data/jargonDictionary.ts
 * Manufacturing & Shop-Floor Plain-Korean Terminology Dictionary.
 * Every entry provides:
 * 1. Friendly Korean definition ("이게 무엇인가요?")
 * 2. Shop-floor manufacturing meaning ("제조 현장 실무 관점에서의 의미")
 * 3. Recommended values & benchmarks ("권장 기준")
 */

export interface JargonEntry {
  term: string;
  termKo: string;
  category: 'training' | 'evaluation' | 'flowchart' | 'inference' | 'dataset' | 'labeling';
  categoryLabelKo: string;
  definitionKo: string;
  shopFloorMeaningKo: string;
  recommendedValueKo: string;
  tag: string;
}

export const JARGON_DICTIONARY: Record<string, JargonEntry> = {
  focal_loss: {
    term: 'Focal Loss',
    termKo: '초점 손실 함수 (미세 결함 집중 학습)',
    category: 'training',
    categoryLabelKo: '학습 알고리즘',
    definitionKo: '정상 배경(쉬운 샘플)보다 찾기 힘든 미세 불량(어려운 샘플)에 모델이 훨씬 더 큰 가중치를 두어 집중 학습하도록 유도하는 특수 손실 함수입니다.',
    shopFloorMeaningKo: '웨이퍼나 디스플레이 표면처럼 99.9%가 정상이고 0.1%만 극소 스크래치인 경우, 일반 모델은 정상을 맞추는 데만 안주하여 불량을 놓칩니다. Focal Loss는 미세 결함에 학습 집중도를 강제로 부여해 미검(Escape)을 방지합니다.',
    recommendedValueKo: '감마(γ) = 2.0, 알파(α) = 0.25가 반도체/디스플레이 미세 스크래치 검출의 글로벌 표준값입니다.',
    tag: '미세결함집중',
  },
  auroc: {
    term: 'AUROC (ROC-AUC)',
    termKo: '이상 탐지 종합 변별력 (ROC 곡선하면적)',
    category: 'evaluation',
    categoryLabelKo: '품질 지표',
    definitionKo: '0.0부터 1.0 사이의 값으로, 양품과 불량을 판정하는 임계값을 어떻게 바꾸더라도 모델이 불량을 양품보다 일관되게 높은 불량 점수로 구분해내는 종합 변별력을 나타냅니다.',
    shopFloorMeaningKo: '정상(양품) 이미지만으로 학습한 비지도 이상 탐지(Anomaly Detection) 모델이 미지의 신규 불량을 얼마나 잘 솎아내는지 보여주는 "종합 성적표"입니다.',
    recommendedValueKo: '양산 라인 실투입 기준 0.95(95%) 이상 필수. 0.98 이상이면 최우수 A등급이며, 0.90 미만일 경우 추가 양품 이미지 수집 및 조명 균일화가 필요합니다.',
    tag: '이상탐지성적표',
  },
  map_50: {
    term: 'mAP@0.5',
    termKo: '결함 박스 평균 검출 정확도 (Mean Average Precision)',
    category: 'evaluation',
    categoryLabelKo: '품질 지표',
    definitionKo: '인공지능이 예측한 결함 박스가 실제 결함 위치와 50% 이상 겹쳤을 때(IoU ≥ 0.5) 맞춘 것으로 인정하여 계산한 모든 결함 유형의 평균 정확도 점수입니다.',
    shopFloorMeaningKo: '육안 검사원이 찾던 크랙, 이물, 기포 등의 부품 결함 위치를 AI가 빠짐없이 정확한 자리에 네모 박스로 찾아내는지를 나타냅니다.',
    recommendedValueKo: '일반 부품 외관 검사는 0.70(70%) 이상, 안전 관련 치명 불량(Critical Defect) 공정은 0.85(85%) 이상을 목표로 합니다.',
    tag: '객체검출정확도',
  },
  dice: {
    term: 'Dice Coefficient',
    termKo: '결함 형상 일치도 (다이스 계수)',
    category: 'evaluation',
    categoryLabelKo: '품질 지표',
    definitionKo: '실제 불량 영역(Ground Truth)과 AI가 칠한 불량 영역(Prediction)의 픽셀 일치도를 0~1(또는 0~100%)로 측정한 정밀 분할 지표입니다.',
    shopFloorMeaningKo: '스크래치 너비, 도포 불량 면적, 용접 비드 형상 등 불량의 실제 크기와 형상을 얼마나 정밀하게 본떠냈는지를 측정합니다. 면적 기준 불량 판정에 직결됩니다.',
    recommendedValueKo: '일반 영역 분할 0.80 이상 권장. 머리카락 굵기 이하의 극소 스크래치 공정은 0.70 이상도 양호한 수준입니다.',
    tag: '픽셀정밀형상',
  },
  iou: {
    term: 'IoU (Intersection over Union)',
    termKo: '영역 교차 비율 (포개어짐 일치도)',
    category: 'evaluation',
    categoryLabelKo: '품질 지표',
    definitionKo: '두 영역의 합집합 면적 대비 교집합 면적의 비율로, 실제 불량 위치와 모델이 예측한 위치가 얼마나 정확하게 포개어지는지를 뜻합니다.',
    shopFloorMeaningKo: 'AI가 결함의 얼추 근처만 찍었는지(IoU 낮음), 결함 외곽선에 딱 맞게 정밀 타격했는지(IoU 높음)를 판단하는 기초 측정 척도입니다.',
    recommendedValueKo: '박스 검출 기준 0.50 이상이면 유효 검출로 인정되며, 고정밀 정렬 및 치수 측정 공정은 0.75 이상을 권장합니다.',
    tag: '영역일치도',
  },
  p95_latency: {
    term: 'P95 Inference Latency',
    termKo: '95% 안정 구간 검사 지연시간 (P95)',
    category: 'inference',
    categoryLabelKo: '인라인 속도',
    definitionKo: '100장의 제품을 연속 검사했을 때, 가장 빠른 95장의 제품 중 가장 오래 걸린 장의 검사 시간(밀리초, ms)입니다. 최악 5%의 튐 현상을 제외한 안정 구간의 상한치입니다.',
    shopFloorMeaningKo: '평균 속도만 빠르고 가끔 100ms씩 렉(지터)이 걸리면 컨베이어 벨트에서 제품이 검사기를 지나쳐 버립니다. P95는 병목이나 라인 스톱 없이 공정이 안정적으로 유지되는지를 보증합니다.',
    recommendedValueKo: '컨베이어 라인 택트 타임(예: 50ms) 이하 필수. 일반 인라인 검사 기준 25ms 이하(40 FPS 이상)를 권장합니다.',
    tag: '택트타임보증',
  },
  early_stopping: {
    term: 'Early Stopping & Patience',
    termKo: '과적합 방지 자동 조기 종료 및 인내치',
    category: 'training',
    categoryLabelKo: '학습 제어',
    definitionKo: '학습을 계속 시켜도 검증 데이터의 성능이 N회(Patience) 연속 개선되지 않으면 과적합(Overfitting)을 막기 위해 학습을 스스로 멈추는 자동 안전장치입니다.',
    shopFloorMeaningKo: '불필요하게 밤새 GPU를 돌리며 전력을 낭비하거나, 학습 데이터에만 과도하게 익숙해져 새 제품이 들어왔을 때 오작동하는 현상을 방지합니다.',
    recommendedValueKo: '빠른 타진 시 Patience = 3, 고정밀 양산 모드 시 Patience = 5~10이 최적입니다.',
    tag: '자동종료안전장치',
  },
  batch_size: {
    term: 'Batch Size',
    termKo: '1회 동시 처리 묶음 크기 (배치 크기)',
    category: 'training',
    categoryLabelKo: '학습 파라미터',
    definitionKo: 'GPU가 한 번에 모아서 동시에 연산하고 모델 가중치를 갱신하는 이미지의 묶음 수량입니다.',
    shopFloorMeaningKo: '너무 크면 GPU 비디오 메모리(VRAM)가 꽉 차서 프로그램이 튕기고(CUDA OOM), 너무 작으면 학습 진동이 심해져 학습 시간이 오래 걸립니다.',
    recommendedValueKo: '본 시스템은 45MP 고해상도 이미지 및 하드웨어(Apple Silicon MPS / NVIDIA CUDA)에 맞춰 8~16개 단위로 자동 조율됩니다.',
    tag: '메모리안정성',
  },
  learning_rate: {
    term: 'Learning Rate',
    termKo: '인공지능 학습 보폭 (학습률)',
    category: 'training',
    categoryLabelKo: '학습 파라미터',
    definitionKo: 'AI 모델이 불량 패턴을 학습하면서 두뇌의 신경망 연결 가중치를 한 번에 얼마나 크게 바꿀지 결정하는 보폭 크기입니다.',
    shopFloorMeaningKo: '보폭이 너무 크면 최적의 결함 판정 기준을 훌쩍 지나쳐버리고, 너무 작으면 세월아 네월아 학습이 멈춥니다. 코사인 스케줄링은 처음엔 시원하게 학습하다가 끝으로 갈수록 미세 정밀 튜닝합니다.',
    recommendedValueKo: '고속 프로토타입 1e-3, 고정밀 양산 모드 5e-4(Cosine Annealing). 본 시스템의 AutoML이 자동 최적화합니다.',
    tag: '학습속도조율',
  },
  epochs: {
    term: 'Epochs',
    termKo: '전체 데이터 학습 완주 회수 (에폭)',
    category: 'training',
    categoryLabelKo: '학습 파라미터',
    definitionKo: '준비된 전체 데이터셋의 모든 검사 이미지를 AI 모델이 처음부터 끝까지 한 바퀴 통째로 공부한 회수(1회 완주 = 1 Epoch)입니다.',
    shopFloorMeaningKo: '같은 시험문제를 몇 번 복습했는지를 나타냅니다. 너무 적으면 덜 배워서 불량을 못 잡고, 너무 많으면 특정 샘플만 달달 외워버립니다.',
    recommendedValueKo: '빠른 프로토타입 프리셋: 5 Epochs (1~2분 소요), 고정밀 프로덕션 프리셋: 20 Epochs (조기 종료 적용).',
    tag: '학습반복회수',
  },
  underkill: {
    term: 'Underkill (Escape)',
    termKo: '🚨 미검 (불량 제품 유출 오류)',
    category: 'evaluation',
    categoryLabelKo: '공정 리스크',
    definitionKo: '실제로는 불량(NG) 제품인데 인공지능이 양품(OK)으로 잘못 판정하여 다음 공정이나 최종 고객사로 그대로 유출되는 최악의 판정 오류입니다.',
    shopFloorMeaningKo: '미검은 불량 유출 위험이므로 검증 데이터뿐 아니라 현장 조건에서 별도로 측정하고 관리해야 합니다.',
    recommendedValueKo: 'NG와 OK 검증 샘플을 모두 확보하고, 임계값 적용 후 독립 시험과 현장 검증을 진행하세요.',
    tag: '치명적유출',
  },
  overkill: {
    term: 'Overkill (Scrap)',
    termKo: '⚠️ 과검 (멀쩡한 제품 오경보/폐기)',
    category: 'evaluation',
    categoryLabelKo: '공정 리스크',
    definitionKo: '실제로는 정상(OK) 양품인데 인공지능이 의심스럽다고 불량(NG)으로 오경보를 울려 폐기 또는 재검사 대상으로 분류하는 오류입니다.',
    shopFloorMeaningKo: '미검을 막기 위해 기준을 너무 빡빡하게 잡으면 멀쩡한 제품을 다 버려 공장 수율(Yield)이 바닥나고 수작업 재검사 비용이 급증합니다.',
    recommendedValueKo: '현장 비용과 요구 품질에 맞는 허용치를 정하고, 독립 검증 데이터에서 과검률을 확인하세요.',
    tag: '수율손실',
  },
  optimal_threshold: {
    term: 'Optimal Threshold (τ*)',
    termKo: '검증 데이터 기준 권장 임계값 (τ*)',
    category: 'evaluation',
    categoryLabelKo: '판정 기준',
    definitionKo: 'NG와 OK 검증 샘플의 점수에서 계산한 후보 판정선입니다. 이 검증 집합에서 관측된 미검과 과검을 함께 확인해야 합니다.',
    shopFloorMeaningKo: '표본 밖의 새 제품이나 조명·설비 조건에서도 같은 성능이 나온다는 보장은 없습니다.',
    recommendedValueKo: '충분한 양쪽 클래스 검증 데이터가 있을 때만 계산하고, 독립 시험과 현장 검토 후 적용하세요.',
    tag: '검증필요',
  },
  roi_crop_padding: {
    term: 'ROI Crop Padding',
    termKo: '관심 영역 확장 여유 공간 (패딩 px)',
    category: 'flowchart',
    categoryLabelKo: '파이프라인',
    definitionKo: '1차 객체 검출 모델이 찾아낸 부품 박스 주변으로 상하좌우 N 픽셀만큼 여유 공간을 더 붙여서 잘라내는 설정값입니다.',
    shopFloorMeaningKo: '결함이 부품 경계선에 걸쳐있을 때 딱 맞게 자르면 결함 일부가 잘려나가 2차 검사 모델이 결함을 양품으로 오판할 수 있습니다. 여유 패딩을 주면 주변 정상 영역과의 대비가 살아납니다.',
    recommendedValueKo: '일반 부품 기준 8~16px, 극소 칩/패드 검사 시 10~20% 비율 확장을 권장합니다.',
    tag: '경계선보호',
  },
  tact_time: {
    term: 'Tact Time',
    termKo: '공정 생산 사이클 시간 (택트 타임)',
    category: 'inference',
    categoryLabelKo: '인라인 속도',
    definitionKo: '생산 라인에서 제품 1개가 완성되어 나오는 주기 시간입니다. 비전 검사 시간은 이 택트 타임보다 무조건 짧아야 라인이 멈추지 않습니다.',
    shopFloorMeaningKo: '1장당 검사 시간이 택트 타임을 초과하면 컨베이어 벨트에 제품이 밀려 라인이 정지(Line Stop)됩니다.',
    recommendedValueKo: '일반 전자부품 고속 라인: 50ms 미만 (20 FPS 이상), 배터리/디스플레이 라인: 100ms 미만 권장.',
    tag: '라인스톱방지',
  },
  confusion_matrix: {
    term: 'Confusion Matrix',
    termKo: '정오 판정 행렬 (혼동 행렬)',
    category: 'evaluation',
    categoryLabelKo: '품질 지표',
    definitionKo: '가로축(예측값)과 세로축(실제 정답)으로 구성된 2차원 표로, AI가 맞춘 것과 틀린 것의 분포를 한눈에 보여주는 표입니다.',
    shopFloorMeaningKo: '대각선(초록색)은 맞춘 정답이고, 대각선 밖(빨간색)은 오판입니다. 셀을 클릭하면 오판된 실제 사진을 즉시 띄워 원인을 분석할 수 있습니다.',
    recommendedValueKo: '대각선 셀의 비율이 95% 이상이고, 불량 행(Row)의 정상 예측 셀(미검)이 0이어야 합니다.',
    tag: '정오표',
  },
};

export interface StepGuidance {
  stepNum: number;
  titleKo: string;
  titleEn: string;
  purposeKo: string;
  conditionKo: string;
  tipKo: string;
}

export const STEP_GUIDANCE_DATA: Record<number, StepGuidance> = {
  1: {
    stepNum: 1,
    titleKo: '데이터 관리',
    titleEn: 'Dataset Studio',
    purposeKo: '검사 이미지 폴더를 불러오고, 라벨이 있는 이미지를 학습·검증·시험 세트로 분할합니다.',
    conditionKo: '최소 1개 이상의 이미지 폴더를 열고 학습 세트 분할을 완료해야 다음 라벨링 단계로 진행할 수 있습니다. (데이터가 없을 시 상단 "합성 데이터 생성기" 활용 가능)',
    tipKo: '라벨이 없는 이미지는 학습 분할에서 제외됩니다. OK·NG 중 한쪽만 있으면 과검과 미검을 함께 평가할 수 없습니다.',
  },
  2: {
    stepNum: 2,
    titleKo: '라벨링 & AI 오토라벨러',
    titleEn: 'Labeling Studio',
    purposeKo: '이미지 속 실제 결함 위치(스크래치, 이물, 찍힘 등)를 박스나 마스크로 지정하여 AI에게 정답을 가르쳐주는 단계입니다.',
    conditionKo: '최소 1장 이상의 결함 이미지에 라벨링을 하거나, 이상 탐지(Anomaly) 태스크의 경우 양품(Normal) 태깅을 완료한 뒤 상단 "저장" 버튼을 누르세요.',
    tipKo: '일일이 손으로 외곽선을 따지 마세요! 상단 "오토 셀렉터" 지팡이 아이콘을 클릭하고 결함 위를 1번만 클릭하면 AI가 결함 외곽선을 자동으로 추출합니다.',
  },
  3: {
    stepNum: 3,
    titleKo: '오토딥러닝 학습',
    titleEn: 'AutoML Trainer',
    purposeKo: '복잡한 딥러닝 코딩이나 하이퍼파라미터 튜닝 없이, 원클릭으로 내 공정에 최적화된 비전 인공지능 모델을 자동 학습시킵니다.',
    conditionKo: '"AutoML 원클릭 학습 시작" 버튼을 누르고, 모든 Epoch 학습이 끝나 "completed" 상태가 될 때까지 기다립니다.',
    tipKo: '빠른 프로토타입은 연결 상태를 확인하는 데 사용하세요. 학습 완료만으로 모델 품질이나 양산 적합성이 확인되지는 않습니다.',
  },
  4: {
    stepNum: 4,
    titleKo: '품질 평가 & 과검/미검 분석',
    titleEn: 'Evaluation & Overkill',
    purposeKo: '학습된 모델의 검증 예측을 확인하고, NG와 OK 샘플이 모두 있을 때 과검·미검을 분석합니다.',
    conditionKo: '완료된 모델로 평가를 실행하고 오분류 샘플을 검토하세요. 임계값 후보는 양쪽 클래스가 있을 때만 계산할 수 있습니다.',
    tipKo: '검증 집합에서 미검이 0건이어도 현장 성능이 보장되지는 않습니다. 별도 시험과 공정 검토를 진행하세요.',
  },
  5: {
    stepNum: 5,
    titleKo: '플로우차트 다중 모델 스튜디오',
    titleEn: 'Flowchart Studio',
    purposeKo: '"1차 부품 위치 검출 ➔ 영역 크롭 ➔ 2차 표면 미세 결함 검사 ➔ 최종 OK/NG 판정"으로 이어지는 공정 파이프라인을 시각적으로 연결합니다.',
    conditionKo: '학습 완료된 호환 모델을 각 검사 노드에 지정하고 샘플 이미지로 파이프라인을 실행하세요.',
    tipKo: '결과를 확인한 뒤 실제 설비 연동과 판정 적용은 별도로 검증하세요.',
  },
  6: {
    stepNum: 6,
    titleKo: '인퍼런스 센터 & 모델 내보내기',
    titleEn: 'Inference & Export',
    purposeKo: '선택한 모델의 로컬 추론 속도를 측정하고 Python 사용 예시가 포함된 독립 실행 패키지를 내보냅니다.',
    conditionKo: '학습 완료된 모델을 선택해 추론 속도와 내보낸 패키지의 단독 실행 결과를 확인하세요.',
    tipKo: '로컬 속도 측정은 설비 PC나 PLC의 응답 시간을 대변하지 않습니다. 현장 통합 시험이 필요합니다.',
  },
};
