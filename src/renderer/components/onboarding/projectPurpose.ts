import type { VisionTask } from '../../types';

/** Results a person wants to see; only an explicit result choice selects a training task. */
export const PROJECT_PURPOSES = [
  { id: 'kind', task: 'classification', title: '사진의 종류를 구분하기',
    example: '예: 사진마다 정상 / 불량 또는 제품 종류 표시',
    preparation: '종류별 사진을 준비하고, 각 사진의 정답을 폴더 이름으로 정합니다.' },
  { id: 'location', task: 'detection', title: '위치와 개수 찾기',
    example: '예: 흠집이 어디에 몇 개 있는지 사각형으로 표시',
    preparation: '대상이 보이는 사진을 준비하고, 대상마다 사각형과 종류를 표시합니다.' },
  { id: 'shape', task: 'segmentation', title: '정확한 모양 표시하기',
    example: '예: 오염이나 균열의 경계를 따라 영역 표시',
    preparation: '경계를 구분할 수 있는 사진을 준비하고, 해당 영역의 모양을 표시합니다.' },
  { id: 'difference', task: 'anomaly', title: '정상과 다른 부분 찾기',
    example: '예: 정상 표면과 다른 부분을 이상 후보로 표시',
    preparation: '정상 사진을 중심으로 준비하고, 별도 시험 사진의 정상·이상 여부를 확인합니다.' },
] as const satisfies readonly { id: string; task: VisionTask; title: string; example: string; preparation: string }[];

export type ProjectPurpose = typeof PROJECT_PURPOSES[number]['id'] | 'unsure';

export function purposeToTask(purpose: string | null): VisionTask | null {
  return PROJECT_PURPOSES.find(choice => choice.id === purpose)?.task ?? null;
}
