import type { QualityConflict, QualityCounts, QualityReport } from '../../services/teamDataApi';

/** E05: how a gold-sample label review reads. */

export const CONFLICT_NAMES: Record<QualityConflict['kind'], string> = {
  missing: '누락', extra: '추가', class: '클래스 불일치', geometry: '모양 불일치',
};

/** The object-level review compares detection and segmentation labels; other project tasks have nothing to compare. */
export function reviewTask(projectTask: string | undefined): 'detection' | 'segmentation' | null {
  return projectTask === 'detection' || projectTask === 'segmentation' ? projectTask : null;
}

const box = (values?: number[]) => values?.length === 4 ? `[${values.map(value => Math.round(value)).join(', ')}]` : '';

export function describeConflict(conflict: QualityConflict): string {
  const theirs = conflict.candidate_bbox && conflict.kind !== 'extra' ? ` · 작업 위치 ${box(conflict.candidate_bbox)}` : '';
  const where = (conflict.bbox?.length === 4 ? ` · 위치 ${box(conflict.bbox)}` : '') + theirs;
  switch (conflict.kind) {
    case 'missing': return `누락: 작업 라벨에 없는 기준 객체 ${conflict.reference_object} (${conflict.label})${where}`;
    case 'extra': return `추가: 기준에 없는 작업 객체 ${conflict.candidate_object} (${conflict.label})${where}`;
    case 'class': return `클래스 불일치: 기준 ${conflict.reference_label} → 작업 ${conflict.candidate_label} (겹침 ${conflict.overlap})${where}`;
    case 'geometry': return `모양 불일치: ${conflict.label} 겹침 ${conflict.overlap}${where}`;
    default: return JSON.stringify(conflict);
  }
}

export function countsLine(counts: QualityCounts | null): string {
  if (!counts) return '결과 파일이 바뀌어 읽지 않음';
  const line = (Object.keys(CONFLICT_NAMES) as QualityConflict['kind'][]).map(kind => `${CONFLICT_NAMES[kind]} ${counts[kind] ?? 0}`).join(' · ');
  return counts.not_comparable ? `${line} · 비교 불가 ${counts.not_comparable}` : line;
}

/** Why the review could not compare a labeler's labels of one image (a tag other than the normal mark, a normal mark
 *  beside objects, a mask of another size): the image stays in the report as not comparable. */
export function notComparableText(error: string): string {
  return `작업 라벨을 비교할 수 없습니다(정상 표시 외의 이미지 태그, 객체와 함께 둔 정상 표시, 크기가 다른 마스크 등): ${error}`;
}

export function staleReason(reason: string): string {
  if (reason === 'guideline_changed') return '라벨 기준서가 바뀌었습니다';
  if (reason === 'profile_changed') return '검수 기준이 바뀌었습니다';
  if (reason === 'profile_retired') return '검수 기준을 그만 쓰기로 했습니다';
  if (reason.startsWith('gold_label_changed:')) return `정답 라벨이 바뀌었습니다: ${reason.slice('gold_label_changed:'.length)}`;
  if (reason.startsWith('gold_unapproved:')) return `정답 이미지가 더 이상 승인 상태가 아닙니다: ${reason.slice('gold_unapproved:'.length)}`;
  return reason;
}

/** What the report means now: computed on the current gold set or not, and whether it can back an approval (current, the
 *  labeler's labels unchanged since, every gold image labeled and no conflict). */
export function eligibilityLine(report: Pick<QualityReport, 'current' | 'stale_reasons' | 'candidate_changes' | 'passes' | 'approval_eligible'>): string {
  if (!report.current) {
    return `이전 기준의 결과: 승인 근거로 쓸 수 없습니다 (${report.stale_reasons.map(staleReason).join(', ')}). 새 검수 기준을 만들어 다시 실행하세요.`;
  }
  if (report.candidate_changes.length) {
    return `현재 정답 기준으로 계산했지만 그 뒤 작업 라벨이 바뀌었습니다(${report.candidate_changes.length}장): 다시 실행해야 승인 근거가 됩니다.`;
  }
  if (!report.passes) return '현재 정답 기준으로 계산한 결과: 불일치, 라벨 없는 이미지 또는 비교할 수 없는 이미지가 있어 승인 근거로 쓸 수 없습니다.';
  return '현재 정답 기준과 모두 일치: 승인 근거로 쓸 수 있습니다.';
}

const LIMITATIONS: Record<string, string> = {
  'object-level comparison against approved gold samples only; it says nothing about images outside the gold set':
    '승인된 정답 이미지에서만 객체 단위로 비교합니다. 정답 밖의 이미지에 대해서는 말하지 않습니다.',
  'detection overlap (IoU) is exact for boxes, rotated boxes and convex polygons and rasterised at 1/8 px for a concave polygon':
    '검출 겹침(IoU)은 상자·회전 상자·볼록 다각형에서 정확히, 오목 다각형은 1/8 픽셀 격자로 계산합니다.',
  'segmentation compares 8-connected regions of each class: touching instances of one class are one region':
    '영역 분할은 클래스별로 맞닿은(8방향) 영역을 비교합니다. 맞닿은 같은 클래스 개체는 하나의 영역입니다.',
  'image tags (classification or anomaly labels) are refused, not compared':
    '이미지 태그(분류·이상 탐지 라벨)는 비교하지 않고 거절합니다.',
  "a normal mark as an image's only label is an image without objects; other image tags (classification or anomaly labels) are not compared, and a labeler image with one is reported as not comparable":
    '이미지의 유일한 라벨인 정상 표시는 객체가 없는 이미지로 비교합니다. 그 밖의 이미지 태그(분류·이상 탐지 라벨)는 비교하지 않으며, 작업 라벨에 있으면 그 이미지를 비교 불가로 표시합니다.',
  'only labels saved in the app are compared: labels that exist only in the source folder (imported COCO, YOLO or mask files) are not read':
    '앱에 저장된 라벨만 비교합니다. 원본 폴더에만 있는 라벨(가져온 COCO·YOLO·마스크 파일)은 읽지 않습니다.',
  'the report runs within the request, for up to 2000 gold images and 1000 objects per image and side':
    '검수는 요청 안에서 실행되며 정답 이미지 2000장, 이미지당 한쪽 객체 1000개까지 비교합니다.',
  'classes are compared by exact name; a renamed class is a class conflict':
    '클래스는 이름이 정확히 같아야 일치합니다. 이름을 바꾼 클래스는 클래스 불일치입니다.',
};

export function limitationText(limitation: string): string {
  return LIMITATIONS[limitation] ?? limitation;
}
