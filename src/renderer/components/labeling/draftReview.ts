import type { LabelSuggestion } from '../../services/api';
import { workflowError } from '../../services/datasetWorkflow';

/** Which drafts a person sees first. */
export type DraftOrder = 'uncertain' | 'confident';

export const DRAFT_ORDER_TEXT: Record<DraftOrder, string> = {
  uncertain: '모델이 덜 확신하는 것부터 (후보의 가장 높은 신뢰도가 낮은 순)',
  confident: '확실한 것부터 (후보의 가장 높은 신뢰도가 높은 순, 여러 장을 빨리 채택할 때)',
};

/** The model's strongest candidate on the image; a draft with no candidate counts as 0. */
export function topConfidence(draft: LabelSuggestion): number {
  return draft.candidates.reduce((best, candidate) => Math.max(best, candidate.confidence), 0);
}

/** Pending drafts in the chosen order; equal confidences keep the older draft first. */
export function orderDrafts(drafts: LabelSuggestion[], order: DraftOrder): LabelSuggestion[] {
  const sign = order === 'uncertain' ? 1 : -1;
  return drafts.filter((draft) => draft.status === 'pending').sort((a, b) =>
    sign * (topConfidence(a) - topConfidence(b)) || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
}

/** Images that still have a draft waiting for a person (several drafts on one image count once). */
export function pendingImageCount(drafts: LabelSuggestion[]): number {
  return new Set(drafts.filter((draft) => draft.status === 'pending').map((draft) => draft.image_path)).size;
}

/** An adoption that recorded its shapes, so it can be taken back on its own. */
export function canTakeBack(draft: LabelSuggestion): boolean {
  return draft.status === 'accepted' && Boolean(draft.adopted_annotations?.length);
}

/** Adoptions newest first, with the ones already taken back after them. */
export function adoptionHistory(drafts: LabelSuggestion[]): LabelSuggestion[] {
  return drafts.filter((draft) => draft.status === 'accepted' || draft.status === 'reverted')
    .sort((a, b) => (b.reverted_at || b.reviewed_at || '').localeCompare(a.reverted_at || a.reviewed_at || ''));
}

// The server's refusals of a draft that no longer matches the data, said with what to do next. The checks stay on the
// server; only their words change here.
const STALE_DRAFT_TEXT: Record<string, string> = {
  'Training dataset fingerprint changed after proposal generation. Generate again.':
    '초안을 만든 뒤 데이터가 바뀌어(직접 고쳐 저장한 라벨 등) 이 초안은 채택할 수 없습니다. 이 초안은 거절해 정리하고, 3단계에서 다시 학습한 뒤 남은 이미지의 초안을 새로 만드세요.',
  'Image, labels, or checkpoint changed after proposal generation. Generate again.':
    '초안을 만든 뒤 이 이미지·라벨이나 모델 파일이 바뀌어 채택할 수 없습니다. 이 초안은 거절하고 초안을 새로 만드세요.',
  'Finish or stop active training before accepting labels.': '학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 채택하세요.',
  'Select one or more proposal candidates to accept.': '채택할 후보가 없습니다. 후보 없는 초안은 거절하세요.',
};

/** A draft with nothing to adopt: it can only be rejected, so it never joins a multi-adoption. */
export function adoptable(draft: LabelSuggestion): boolean {
  return draft.status === 'pending' && draft.candidates.length > 0;
}

/** The server's refusal in words, plus the data version to restore when a take-back was refused. */
export function draftError(error: unknown): string {
  // The API client throws a structured refusal's detail itself (with its status); a wrapped `detail` is read too.
  const record = error && typeof error === 'object' ? error as { detail?: unknown; backup_version_id?: unknown } : undefined;
  const wrapped = record?.detail && typeof record.detail === 'object' ? record.detail as { backup_version_id?: unknown } : undefined;
  const backup = record?.backup_version_id ?? wrapped?.backup_version_id;
  const raw = workflowError(error);
  const text = STALE_DRAFT_TEXT[raw] ?? raw;
  return typeof backup === 'string' && backup ? `${text} (채택 전 데이터 버전: ${backup} · 1단계 데이터 버전에서 복원)` : text;
}

/** Why several drafts cannot be adopted in one go, or null. Drafts from one batch stay adoptable after each other
 *  (the batch's review baseline advances with every verified adoption); a draft from another batch, a single draft
 *  with no batch, or a second draft on the same image would be refused by the server after the first adoption. */
export function multiAdoptionRefusal(selected: LabelSuggestion[]): string | null {
  if (selected.length < 2) return null;
  const batches = new Set(selected.map((draft) => draft.batch_id || ''));
  if (batches.has('')) return '한 번에 채택하려면 같은 묶음으로 만든 초안만 고르세요. 따로 만든 초안은 하나씩 채택하세요.';
  if (batches.size > 1) return '한 번에 채택하려면 같은 묶음으로 만든 초안만 고르세요 (지금 묶음 ' + batches.size + '개).';
  const images = new Set<string>();
  for (const draft of selected) {
    if (images.has(draft.image_path)) return '같은 이미지의 초안이 둘 이상 있습니다. 이미지마다 하나만 고르세요.';
    images.add(draft.image_path);
  }
  return null;
}

/** The server refuses the rest of a batch with this once its data changed; the loop stops there. */
export function staleBatchRefusal(error: unknown): boolean {
  return /fingerprint changed/i.test(workflowError(error));
}

export interface AdoptionResult { draft: LabelSuggestion; adopted: number; error?: string }

/** One sentence for what a multi-adoption did, with the first failure's reason. */
export function adoptionSummary(results: AdoptionResult[], stopped: boolean): string {
  const done = results.filter((row) => !row.error);
  const failed = results.filter((row) => row.error);
  const shapes = done.reduce((sum, row) => sum + row.adopted, 0);
  const parts = [`${done.length}장의 초안에서 ${shapes}개를 라벨로 채택했습니다(기존 라벨에 덧붙임, 채택마다 데이터 버전을 남김).`];
  if (failed.length) parts.push(`${failed.length}장은 채택하지 못했습니다: ${fileNameOf(failed[0].draft.image_path)} · ${failed[0].error}`);
  if (stopped) parts.push('남은 초안은 데이터가 바뀌어 멈췄습니다. 3단계에서 다시 학습한 뒤 초안을 새로 만드세요.');
  return parts.join(' ');
}

const fileNameOf = (path: string) => path.split(/[\\/]/).pop() || path;
