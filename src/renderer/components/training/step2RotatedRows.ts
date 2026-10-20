/** Step-2 rotated boxes into the rotated detection workbench's answer table (audit F-16 / B3). Pure helpers. */
import type { MultiRotatedSample } from '../../services/specializedApi';
import { formatOBBRows, parseOBBRows } from './modelAdapterRecipes';

export type Step2Rotated = { samples: MultiRotatedSample[]; skipped: Array<{ image: string; reason: string }>; unlabeled_images: number;
  directions_dropped: number; split_source: 'saved' | 'folders' | 'proposed' | 'mixed' | 'none'; counts: { images: number; objects: number } };

/** Rows already typed stay as they are; images not in the table yet are added. An unreadable table is never rewritten. */
export function mergeRotatedRows(current: string, imported: MultiRotatedSample[]): string {
  if (!current.trim()) return formatOBBRows(imported);
  let existing: MultiRotatedSample[];
  try { existing = parseOBBRows(current); }
  catch { throw new Error('지금 정답 표에 고칠 곳이 있어 가져온 회전 박스를 합칠 수 없습니다. 표를 고치거나 비운 뒤 다시 가져오세요.'); }
  const present = new Set(existing.map(row => row.image));
  return formatOBBRows([...existing, ...imported.filter(row => !present.has(row.image))]);
}

const SPLIT_SOURCE: Record<Step2Rotated['split_source'], string> = { saved: '1단계에서 저장한 분할', folders: '폴더 이름(train/val/test)',
  proposed: '제안한 분할(70/15/15, 저장 전에 고칠 수 있음)', mixed: '저장한 분할·폴더·제안 혼합', none: '없음' };

export function step2ImportNotice(result: Step2Rotated): string {
  if (!result.samples.length) return `2단계에서 그린 회전 박스가 없습니다 (회전 박스가 없는 이미지 ${result.unlabeled_images}장). 2단계에서 회전 박스 도구로 그리세요.`;
  const parts = [`2단계 회전 박스 ${result.counts.objects}개(이미지 ${result.counts.images}장)를 표에 넣었습니다`, `분할: ${SPLIT_SOURCE[result.split_source]}`];
  if (result.unlabeled_images) parts.push(`회전 박스가 없는 이미지 ${result.unlabeled_images}장은 넣지 않았습니다`);
  if (result.skipped.length) parts.push(`읽지 못한 박스 ${result.skipped.length}개는 뺐습니다`);
  if (result.directions_dropped) parts.push(`방향이 일부 박스에만 있어 방향 ${result.directions_dropped}개는 쓰지 않았습니다`);
  return `${parts.join(' · ')}. 아직 저장 전입니다. '정답·이미지 해시 저장'을 누르세요.`;
}
