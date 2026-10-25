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
  return new Set(drafts.filter((draft) => draft.status === 'pending' && draft.candidates.length > 0).map((draft) => draft.image_path)).size;
}

/** Drafts that found nothing: no work waits on them (their images still count as remaining), so they are not counted as
 *  waiting and are closed together in one press (each one recorded as rejected; labels unchanged). */
export function emptyPendingDrafts(drafts: LabelSuggestion[]): LabelSuggestion[] {
  return drafts.filter((draft) => draft.status === 'pending' && draft.candidates.length === 0);
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
// server; only their words change here. The server now answers in Korean; the English keys stay for drafts and batch
// records saved before that.
const DATA_CHANGED_TEXT = '초안을 만든 뒤 데이터가 바뀌어(직접 고쳐 저장한 라벨 등) 이 초안은 채택할 수 없습니다. 이 초안은 거절해 정리하고 남은 이미지의 초안을 새로 만드세요. 같은 모델로도 만들 수 있습니다(데이터가 바뀐 뒤 학습한 모델로 표시).';
const STALE_DRAFT_TEXT: Record<string, string> = {
  'Training dataset fingerprint changed after proposal generation. Generate again.': DATA_CHANGED_TEXT,
  // The server's Korean ends at the action; the panel adds that the same model can draft again.
  '초안을 만든 뒤 데이터가 바뀌어(직접 고쳐 저장한 라벨 등) 이 초안은 채택할 수 없습니다. 이 초안은 거절해 정리하고 남은 이미지의 초안을 새로 만드세요.': DATA_CHANGED_TEXT,
  'The project identity, task, source, or checkpoint, or the labels or split, changed during this batch.':
    '일괄 초안을 만드는 동안 프로젝트·작업 종류·데이터 폴더·모델 파일이나 라벨·분할이 바뀌어 멈췄습니다. 남은 이미지는 일괄 초안을 다시 시작해 만드세요.',
  'Image, labels, or checkpoint changed after proposal generation. Generate again.':
    '초안을 만든 뒤 이 이미지·라벨이나 모델 파일이 바뀌어 채택할 수 없습니다. 이 초안은 거절하고 초안을 새로 만드세요.',
  'Finish or stop active training before accepting labels.': '학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 채택하세요.',
  'Select one or more proposal candidates to accept.': '채택할 후보가 없습니다. 후보 없는 초안은 거절하세요.',
  // Making drafts from a phrase (the empty project's '문구로 초안 만들기') and the model folders it needs.
  'Choose an existing local model directory': '지정한 경로를 폴더로 찾지 못했습니다. 백엔드가 읽을 수 있는 모델 폴더 경로를 입력하세요.',
  'A labeling task is already running for this project': '이 프로젝트에서 다른 초안·학습 작업이 진행 중입니다. 끝나거나 멈춘 뒤 다시 시작하세요.',
  'Enter nonempty object phrases': '찾을 대상을 한 낱말 이상 적으세요(예: scratch, dent).',
  'Source dataset changed during labeling batch': '초안을 만드는 동안 데이터가 바뀌어 멈췄습니다. 남은 이미지는 다시 시작하면 이어서 만듭니다.',
  // External drafts (SAM2, Grounding DINO, template, VLM, remote) are refused by their own review path.
  'Source labels or dataset changed; generate candidates again': '초안을 만든 뒤 데이터가 바뀌어 이 초안은 채택할 수 없습니다. 이 초안은 거절하고 초안을 새로 만드세요.',
  'Finish or cancel the active labeling task before accepting labels': '라벨 작업이 진행 중입니다. 끝나거나 취소한 뒤 채택하세요.',
  'Select proposal candidates to adopt': '채택할 후보가 없습니다. 후보 없는 초안은 거절하세요.',
  'Another reviewer changed this image; generate again': '다른 검토자가 이 이미지를 바꿨습니다. 이 초안은 거절하고 초안을 새로 만드세요.',
  'Another reviewer changed this image; generate candidates again.': '다른 검토자가 이 이미지를 바꿨습니다. 이 초안은 거절하고 초안을 새로 만드세요.',
  'Project task changed': '작업 종류가 바뀌어 이 초안은 채택할 수 없습니다. 거절하세요.',
  'Source label set changed': '라벨 세트가 바뀌어 이 초안은 채택할 수 없습니다. 거절하세요.',
  'Finish training before adopting labels': '학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 채택하세요.',
  'Foundation model changed': '초안을 만든 모델 파일이 바뀌어 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'Semantic model changed': '초안을 만든 모델 파일이 바뀌어 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'Text grounding model changed': '초안을 만든 모델 파일이 바뀌어 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'DINOv3 feature weights changed': '초안을 만든 모델 파일이 바뀌어 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'Foundation provider setup changed': '모델 보조 설정이 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'Exemplar changed': '예시(템플릿) 이미지가 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'Image example changed': '예시 이미지가 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'VLM configuration changed': 'VLM 설정이 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'VLM image example changed': 'VLM 예시 이미지가 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
  'Trained feature model became unavailable or changed': '특징 모델이 바뀌거나 없어져 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.',
};

/** The mark the 2026-10-22 decision puts on a model trained before the data changed, in model lists and on its
 *  drafts: it may draft, but its numbers are never evaluation evidence. */
export const CHANGED_MODEL_MARK = '데이터가 바뀐 뒤 학습한 모델 · 평가 근거 아님';

/** Korean names for where a draft came from; older records without a backend are trained-model drafts. */
export const DRAFT_SOURCE_TEXT: Record<string, string> = {
  trained_model: '학습 모델',
  foundation: 'SAM2',
  grounding_dino: '텍스트 검출',
  template_match: '예시 템플릿',
  vlm: '이미지 VLM',
  remote: '원격 GPU',
};
export function draftSourceText(draft: Pick<LabelSuggestion, 'backend' | 'job_id'> & { model_data_state?: string; prompt?: string }): string {
  const backend = draft.backend || 'trained_model';
  // A phrase draft names its phrase: the text model found the region, SAM2 (when it ran) drew its outline.
  const phrase = (backend === 'foundation' || backend === 'grounding_dino') && draft.prompt?.trim() ? draft.prompt.trim() : '';
  const name = phrase ? `${backend === 'foundation' ? '문구 + SAM2' : '문구 초안'} · "${phrase.length > 40 ? `${phrase.slice(0, 40)}…` : phrase}"` : DRAFT_SOURCE_TEXT[backend] ?? backend;
  return draft.model_data_state === 'changed' ? `${name} · ${CHANGED_MODEL_MARK}` : name;
}

/** A provider's limits as one line. Every provider stores one sentence; a list (an older record) is joined. */
export function supportLimitsText(limits: unknown): string {
  if (Array.isArray(limits)) return limits.filter((item): item is string => typeof item === 'string' && item.length > 0).join(' · ');
  return typeof limits === 'string' ? limits : '';
}

/** Another panel asked for a draft the queue could not find, even after reading the list again. */
export const OPEN_REQUEST_MISSING_TEXT = '그 초안을 찾지 못했습니다(이미 채택·거절됐거나 다른 프로젝트의 초안입니다).';
/** The server changed the open image's labels while the person had unsaved edits on it: the canvas was not replaced, and
 *  a save with the old revision would be refused, so the edits stay (with their undo) and the image must be read again. */
export const CANVAS_NOT_RELOADED_TEXT = '그동안 열린 이미지를 고쳐서 캔버스에는 바뀐 라벨을 다시 불러오지 않았습니다(고친 내용과 되돌리기 기록은 그대로입니다). 서버의 라벨이 바뀌어 이대로는 저장되지 않으니, 고친 내용을 확인한 뒤 이미지를 다시 불러와 바뀐 라벨 위에서 고쳐 저장하세요.';

/** Another panel asked for a draft that is no longer waiting: say what became of it instead of opening nothing. */
export function closedDraftText(draft: Pick<LabelSuggestion, 'status' | 'image_path'>): string {
  const name = fileNameOf(draft.image_path);
  if (draft.status === 'accepted') return `${name}의 그 초안은 이미 채택했습니다. 아래 채택 기록에서 볼 수 있습니다.`;
  if (draft.status === 'reverted') return `${name}의 그 초안은 채택했다가 되돌렸습니다. 다시 쓰려면 초안을 새로 만드세요.`;
  return `${name}의 그 초안은 이미 거절했습니다. 다시 쓰려면 초안을 새로 만드세요.`;
}

/** The on-screen rule for mixed sources: each source's confidence means something different. */
export const MIXED_SOURCE_NOTE = '출처마다 신뢰도의 뜻이 다릅니다(학습 모델 점수, SAM2 겹침·유사도, 템플릿 일치도, VLM 자기 평가). 순서는 참고로만 보세요.';
export function sourcesMixed(drafts: Pick<LabelSuggestion, 'backend' | 'status'>[]): boolean {
  return new Set(drafts.filter((draft) => draft.status === 'pending').map((draft) => draft.backend || 'trained_model')).size > 1;
}

/** A draft with nothing to adopt: it can only be rejected, so it never joins a multi-adoption. */
/** Shown beside a candidate whose class is not in the project's class list now (a model trained before a rename). */
export const UNKNOWN_CLASS_MARK = '지금 클래스 목록에 없는 이름';

/** A candidate an older model drafted with a class the project no longer has. Only for drafts of a model trained
 *  before the data changed, and only when the project has classes at all (an empty project's first drafts name new
 *  classes on purpose). */
export function unknownClassCandidate(draft: Pick<LabelSuggestion, 'model_data_state'>, label: string, known: ReadonlySet<string>): boolean {
  return draft.model_data_state === 'changed' && known.size > 0 && !known.has(label);
}

/** The candidates chosen until the person changes them: all of them, except an older model's candidates whose class the
 *  project no longer has — those stay unchecked (the person can still check one; adding a class is allowed). */
export function defaultChoice(draft: LabelSuggestion, known: ReadonlySet<string>): Set<string> {
  return new Set(draft.candidates.filter((candidate) => !unknownClassCandidate(draft, candidate.annotation.label, known)).map((candidate) => candidate.id));
}

export function adoptable(draft: LabelSuggestion): boolean {
  return draft.status === 'pending' && draft.candidates.length > 0;
}

/** The server's refusal in words, plus the data version to restore when a take-back was refused. */
/** A refusal or batch reason the server gave as plain text, in the panel's words (unknown texts pass through). */
export function draftRefusalText(text: string): string {
  const reason = /^(실패한 이미지가 있습니다 · 첫 이유: )(.*)$/s.exec(text);
  return reason ? `${reason[1]}${STALE_DRAFT_TEXT[reason[2]] ?? reason[2]}` : STALE_DRAFT_TEXT[text] ?? text;
}

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

/** The server refuses the rest of a batch with this once its data changed; the loop stops there. The English pair is
 *  what the server said before its refusals moved to Korean; a changed image or model file does not stop the loop. */
export function staleBatchRefusal(error: unknown): boolean {
  return /fingerprint changed|labels or dataset changed|^초안을 만든 뒤 데이터가 바뀌어/i.test(workflowError(error));
}

export interface AdoptionResult { draft: LabelSuggestion; adopted: number; error?: string }

/** One sentence for what a multi-adoption did, with the first failure's reason. */
export function adoptionSummary(results: AdoptionResult[], stopped: boolean): string {
  const done = results.filter((row) => !row.error);
  const failed = results.filter((row) => row.error);
  const shapes = done.reduce((sum, row) => sum + row.adopted, 0);
  const parts = [`${done.length}장의 초안에서 ${shapes}개를 라벨로 채택했습니다(기존 라벨에 덧붙임, 채택마다 데이터 버전을 남김).`];
  if (failed.length) parts.push(`${failed.length}장은 채택하지 못했습니다: ${fileNameOf(failed[0].draft.image_path)} · ${failed[0].error}`);
  if (stopped) parts.push('남은 초안은 데이터가 바뀌어 멈췄습니다. 남은 이미지의 초안을 새로 만드세요.');
  return parts.join(' ');
}

const fileNameOf = (path: string) => path.split(/[\\/]/).pop() || path;

/** One line per model: how its drafts fared with people, and whether a confidence cut can be trusted. */
export interface ModelAdoptionStats { job_id: string; backend: string; drafts: number; pending: number; adopted: number; rejected: number; reverted: number;
  edited_after: number; edited_unknown: number; reviewed_candidates: number; adopted_candidates: number; rejected_candidates: number;
  mean_confidence_adopted: number | null; mean_confidence_rejected: number | null;
  advice: { reason: 'too_few' | 'already_high' | 'none_reaches' | 'task_not_applicable' | 'suggestion'; reviewed_candidates: number;
    adopted_rate?: number | null; threshold?: number | null; adopted_rate_above?: number | null; candidates_above?: number | null } }

export function modelStats(rows: ModelAdoptionStats[] | null | undefined, jobId: string): ModelAdoptionStats | null {
  return rows?.find((row) => row.job_id === jobId && (row.backend || 'trained_model') === 'trained_model') ?? null;
}

export function adoptionLine(row: ModelAdoptionStats | null): string {
  if (!row || row.drafts - row.pending === 0) return '아직 검토한 초안이 없습니다.';
  const parts = [`초안 ${row.drafts}장 · 채택 ${row.adopted} · 거절 ${row.rejected} · 채택 뒤 수정 ${row.edited_after}`];
  if (row.reverted) parts.push(`되돌림 ${row.reverted}`);
  if (row.edited_unknown) parts.push(`수정 여부 모름 ${row.edited_unknown}`);
  return parts.join(' · ');
}

export function adoptionTitle(row: ModelAdoptionStats | null): string | undefined {
  if (!row || row.mean_confidence_adopted === null && row.mean_confidence_rejected === null) return undefined;
  const pct = (value: number | null) => value === null ? '-' : `${Math.round(value * 100)}%`;
  return `채택 후보 평균 신뢰도 ${pct(row.mean_confidence_adopted)} · 거절 후보 ${pct(row.mean_confidence_rejected)}`;
}

export function thresholdAdvice(row: ModelAdoptionStats | null): string {
  if (!row) return '';
  const advice = row.advice, n = advice.reviewed_candidates;
  const rate = (value: number | null | undefined) => `${Math.round((value ?? 0) * 100)}%`;
  switch (advice.reason) {
    case 'task_not_applicable': return '분류·이상 탐지 초안은 후보가 한 개라 기준 신뢰도 추천을 하지 않습니다.';
    case 'too_few': return n === 0 ? '' : `검토한 후보 ${n}개 — 20개부터 기준 신뢰도를 추천합니다.`;
    case 'already_high': return `검토한 후보 ${n}개 중 ${rate(advice.adopted_rate)}를 채택했습니다. 지금 기준 신뢰도로 충분합니다.`;
    case 'none_reaches': return '기준 신뢰도를 올려도 채택률 90%에 닿지 않습니다. 라벨을 더 모아 3단계에서 다시 학습하세요.';
    case 'suggestion': return `후보 ${n}개 검토 결과, 신뢰도 ${(advice.threshold ?? 0).toFixed(2)} 이상은 ${rate(advice.adopted_rate_above)}가 채택됐습니다. 기준 신뢰도를 ${(advice.threshold ?? 0).toFixed(2)}로 올리면 거절할 초안이 줄어듭니다.`;
  }
}

/** Grid pages for the contact-sheet review: adoptable pending drafts grouped by batch (one page never mixes batches,
 *  one draft per image), in the chosen order, cut into pages of `pageSize`. Drafts that cannot join a page (no batch,
 *  a second draft of an image, nothing to adopt) are listed as singles. */
export function gridPages(pending: LabelSuggestion[], pageSize: number): { pages: LabelSuggestion[][]; singles: LabelSuggestion[] } {
  const pages: LabelSuggestion[][] = [], singles: LabelSuggestion[] = [];
  const byBatch = new Map<string, LabelSuggestion[]>();
  const seenImages = new Set<string>();
  for (const draft of pending) {
    if (!draft.batch_id || draft.candidates.length === 0 || seenImages.has(draft.image_path)) { singles.push(draft); continue; }
    seenImages.add(draft.image_path);
    const rows = byBatch.get(draft.batch_id) ?? [];
    rows.push(draft);
    byBatch.set(draft.batch_id, rows);
  }
  for (const rows of byBatch.values()) for (let start = 0; start < rows.length; start += pageSize) pages.push(rows.slice(start, start + pageSize));
  return { pages, singles };
}

/** Why a page cannot be adopted yet, or null: every tile not excluded must have shown its image. A tile whose image
 *  failed to load never will, so the refusal names it and the way out (leave it out, or open it and adopt it alone). */
export function pageAdoptionRefusal(page: LabelSuggestion[], excluded: Set<string>, loaded: Set<string>, failed: Set<string> = new Set()): string | null {
  const included = page.filter((draft) => !excluded.has(draft.id));
  if (!included.length) return '이 페이지에서 채택할 초안이 없습니다. 제외를 풀거나 다음 페이지로 가세요.';
  const broken = included.filter((draft) => failed.has(draft.id) && !loaded.has(draft.id));
  if (broken.length) {
    const names = broken.slice(0, 3).map((draft) => fileNameOf(draft.image_path)).join(', ') + (broken.length > 3 ? ' 외' : '');
    return `이 페이지에서 미리보기를 불러오지 못한 초안 ${broken.length}건(${names})이 있습니다. 그 타일을 눌러 이 페이지 채택에서 빼고 채택하세요. 뺀 초안은 '열기'로 캔버스에서 확인한 뒤 따로 채택할 수 있습니다.`;
  }
  const unseen = included.filter((draft) => !loaded.has(draft.id)).length;
  if (unseen) return `이 페이지에서 아직 보이지 않은 초안 ${unseen}건이 있습니다. 모두 보인 뒤 채택할 수 있습니다.`;
  return multiAdoptionRefusal(included);
}
