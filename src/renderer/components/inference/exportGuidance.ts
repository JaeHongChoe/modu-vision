import type { VisionTask } from '../../types';

const GUIDANCE: Record<VisionTask, { ko: string; en: string }> = {
  classification: {
    ko: '분류 모델의 infer.py는 입력 이미지 한 장의 모델 점수를 계산합니다. 5단계의 모델 연결·ROI 크롭·최종 판정 룰은 포함되지 않습니다. 저장된 검사 플로우와 결과를 별도로 비교하세요.',
    en: 'The classification model infer.py scores one input image. It does not include Step 5 model connections, ROI crops, or final decision rules. Compare its output with the saved inspection flow separately.',
  },
  detection: {
    ko: '검출 모델의 infer.py는 입력 이미지에서 객체를 검출하고 패키지 config.json의 신뢰도 임계값으로 판정합니다. 5단계의 ROI 크롭·필터·최종 판정 노드는 포함되지 않습니다. 저장된 플로우의 신뢰도 임계값과 맞춰 비교하세요.',
    en: 'The detection model infer.py finds objects in one input image and uses the package config.json confidence threshold. It does not include Step 5 ROI crops, filters, or final decision nodes. Match the confidence threshold to the saved flow before comparing results.',
  },
  segmentation: {
    ko: '분할 모델의 infer.py는 원본 이미지를 겹치는 타일로 검사하고 결함 면적으로 판정합니다. 5단계 플로우차트의 탐지 ROI·크롭·필터·최종 판정 노드는 패키지에 포함되지 않습니다. 타일 해상도·임계값·최소 결함 면적을 5단계와 맞춰 비교하세요.',
    en: 'For segmentation, infer.py inspects the original image with overlapping tiles and decides from defect area. The package does not run the Step 5 detector ROI, crop, filter, or final decision nodes. Match tile resolution, threshold, and minimum defect area to Step 5 before comparing results.',
  },
  anomaly: {
    ko: '이상 탐지 모델의 단독 Python 패키지는 현재 제공되지 않습니다. 앱 안의 검사 플로우에서 모델을 확인하세요.',
    en: 'Standalone Python export for anomaly detection is not currently available. Inspect the model through the in-app inspection flow.',
  },
};

export function exportPackageGuidance(task: VisionTask, isKo: boolean): string {
  return GUIDANCE[task][isKo ? 'ko' : 'en'];
}
