/**
 * '클래스 이름 변경·병합' (v1): one class name becomes another inside the active label set's Studio overlay files only.
 * Preview first (open canvas edits are saved, then counts per store and conflicts), then an explicit confirm that names
 * the backup, then the result. Source label files (LabelMe/COCO/YOLO) are never changed; class ids and mask pixels
 * stay; machine drafts, flows, model receipts and truth declarations are only counted. Refusals come from the backend
 * in Korean ('이유. 다음 행동.'), including the ones that would refuse the apply right now (a running job, another
 * actor's edit lease). Built on the shared WorkspaceDialog: portalled, first control focused, Tab trapped, Escape
 * closes and focus returns to the opener; while a request runs, Escape and 닫기 are ignored.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { datasetWorkflow, workflowError } from '../../services/datasetWorkflow';
import type { ClassRenamePreview, ClassRenameResult } from '../../services/api';
import type { Category } from '../../types';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { saveOpenEdits } from '../../stores/useProjectStore';
import { bumpWorkflowEvidence } from '../../stores/workflowEvidence';
import { reportPanelError } from '../../stores/assistContext';
import { WorkspaceDialog } from '../common/WorkspaceDialog';

export const DIALOG_TITLE = '클래스 이름 변경·병합';
export const CONFIRM_LABEL = '백업 후 적용';
export const SOURCE_NOTE = '원본 라벨 파일(LabelMe·COCO·YOLO)은 바꾸지 않습니다. 활성 라벨 세트의 편집 라벨만 바꾸고, 클래스 ID와 마스크 픽셀은 그대로 둡니다.';
export const CONFIRM_TEXT = '적용 전에 데이터 버전(자동 백업)을 만듭니다. 이 백업을 복원하면 이름 변경 뒤에 고친 라벨도 함께 되돌아가고, 원본 이미지가 그대로일 때만 복원할 수 있음을 확인합니다.';
export const RELOADED = '라벨 세트를 다시 불러왔습니다.';
export const RELOAD_FAILED = '적용은 끝났지만 화면을 다시 불러오지 못했습니다. 2단계를 다시 여세요.';
export const NO_CLASSES_HINT = '편집 라벨에 클래스가 없습니다. 2단계에서 라벨을 저장한 뒤 다시 여세요.';
export const HINT_ID = 'class-rename-hint';

const SHAPE_NAMES: Record<string, string> = { bbox: '상자', rotated_bbox: '회전 상자', polygon: '다각형', brush_mask: '붓 마스크', tag: '태그' };

export function sameNameHint(from: string): string {
  return `"${from}"과(와) 같은 이름입니다. 다른 이름을 입력하거나 합칠 클래스를 고르세요.`;
}

export function sourceOnlyLine(count: number): string {
  return `원본 파일에만 있는 라벨 ${count}장 · 바꾸지 않음`;
}

/** The comparison the backend uses (NFKC, case-insensitive), as close as JavaScript gets. */
export function nameKey(name: string): string {
  return name.normalize('NFKC').toLowerCase();
}

/** Names the rename can act on: the dataset's classes and the labels of the open image, never the unused demo palette. */
export function renameCandidates(classes: Record<string, number> | null | undefined, annotations: { label?: unknown }[]): string[] {
  const labels = annotations.map((item) => item.label).filter((label): label is string => typeof label === 'string' && Boolean(label.trim()));
  return [...new Set([...Object.keys(classes || {}), ...labels])].sort((a, b) => a.localeCompare(b, 'ko'));
}

/** The palette after a rename: matching entries take the new name and keep their id; on a merge they fold into the
 * entry that already carries the target, so the palette shows neither the old name nor the target twice. */
export function renamePalette(categories: Category[], mapping: { from: string; to: string }[]): Category[] {
  let next = categories.map((row) => ({ ...row }));
  for (const row of mapping) {
    const renamed = next.filter((entry) => nameKey(entry.name) === nameKey(row.from));
    if (!renamed.length) continue;
    const target = next.find((entry) => entry.name === row.to && !renamed.includes(entry))
      ?? next.find((entry) => nameKey(entry.name) === nameKey(row.to) && !renamed.includes(entry));
    const keep = target ?? renamed[0];
    next = next.filter((entry) => entry === keep || !renamed.includes(entry)).map((entry) => (entry === keep ? { ...entry, name: row.to } : entry));
  }
  return next;
}

/**
 * The refresh bulk label writes use (as the external mask import does), plus the renderer state that holds class
 * names: the palette, the active class and a class filter on the old name. The label set stays active, so the team
 * settings, edit lease, label book palette and open image are kept. True only when the image list and the open
 * image's labels were read again.
 */
export async function refreshAfterRename(mapping: { from: string; to: string }[]): Promise<boolean> {
  let reloaded = false;
  try {
    const annotation = useAnnotationStore.getState();
    const categories = renamePalette(annotation.categories, mapping);
    const activeName = mapping.find((row) => nameKey(row.from) === nameKey(annotation.activeCategory.name))?.to ?? annotation.activeCategory.name;
    const active = categories.find((row) => row.name === activeName) ?? categories[0] ?? annotation.activeCategory;
    useAnnotationStore.setState({ categories, activeCategory: active, currentLabel: active.name });
    const filter = useDatasetStore.getState().activeClassFilter;
    if (filter && mapping.some((row) => nameKey(row.from) === nameKey(filter))) useDatasetStore.setState({ activeClassFilter: null, page: 1 });
    await useDatasetStore.getState().annotationsChanged();
    if (!useDatasetStore.getState().importError) {
      const current = useAnnotationStore.getState();
      reloaded = !current.currentImage || await current.loadAnnotationsForCurrent();
    }
  } catch {
    reloaded = false;
  }
  // The files changed either way: statistics and the footer's evidence built on the old names are stale.
  window.dispatchEvent(new Event('dataset-statistics-changed'));
  bumpWorkflowEvidence();
  return reloaded;
}

export interface PreviewRow { store: string; count: string; note: string }

/** One line per store the rename touches or only counts, in the order a person checks them. */
export function previewRows(preview: ClassRenamePreview): PreviewRow[] {
  const byType = Object.entries(preview.overlays.shapes_by_type).map(([kind, count]) => `${SHAPE_NAMES[kind] ?? kind} ${count}`).join(' · ');
  const book = preview.labelbook.version === null ? '없음'
    : preview.labelbook.names_old_class ? `v${preview.labelbook.version} · 이 클래스를 씀` : `v${preview.labelbook.version} · 영향 없음`;
  const review = preview.review;
  return [
    { store: '편집 라벨 파일', count: `${preview.overlays.files}장 · 도형 ${preview.overlays.shapes}개`, note: byType ? `바꿈 (${byType})` : '바꿈' },
    { store: '마스크 팔레트', count: `${preview.overlays.palette_entries}개`, note: `이름만 바꿈 · 마스크 ${preview.overlays.images_with_masks}장의 픽셀과 ID는 그대로` },
    ...(review ? [{ store: '검토 상태', count: `${preview.overlays.images}장 다시 검토 · 승인 ${review.approved}장 취소`, note: '바뀐 이미지는 모두 검토 대기로' }] : []),
    { store: '원본 라벨 파일', count: sourceOnlyLine(preview.source_only.images),
      note: preview.source_only.unreadable ? `읽지 못한 원본 라벨 ${preview.source_only.unreadable}장`
        : preview.source_only.images ? '2단계에서 열어 저장한 뒤 다시 미리보기' : '원본은 그대로' },
    { store: '라벨 기준서', count: book, note: preview.labelbook.names_old_class ? '기준서에 있는 클래스는 바꿀 수 없음' : '바꾸지 않음' },
    { store: '검토 대기 초안', count: `${preview.drafts.pending}건 · 후보 ${preview.drafts.candidates}개`, note: preview.drafts.pending ? '옛 이름 그대로 · 적용 뒤 다시 만들기' : '없음' },
    { store: '검사 흐름', count: `${preview.flows.versions}개 버전 · 참조 ${preview.flows.references}곳`, note: preview.flows.versions ? '바꾸지 않음 · 흐름에서 직접 고치기' : '없음' },
    { store: '모델', count: `${preview.models.count}개`, note: preview.models.count ? '학습 때 이름 유지 · 다시 학습하면 새 이름' : '없음' },
    { store: '정답 선언', count: `${preview.truth.images}장 · ${preview.truth.declarations}건`, note: preview.truth.images ? '적용 뒤 다시 선언 필요' : '없음' },
  ];
}

export function canApply(preview: ClassRenamePreview | null, confirmed: boolean, busy: boolean): boolean {
  return Boolean(preview) && (preview as ClassRenamePreview).conflicts.length === 0 && confirmed && !busy;
}

export function backupNotice(result: ClassRenameResult): string {
  return `데이터 버전 ${result.backup_version_id}에 백업했습니다 · 편집 라벨 ${result.overlays.files}장 · 도형 ${result.overlays.shapes}개 바꿈`;
}

export function mappingLine(preview: ClassRenamePreview): string {
  return preview.mapping.map((row) => {
    const variants = row.source_spellings && row.source_spellings.length > 1 ? ` (함께 바뀌는 이름: ${row.source_spellings.join(', ')})` : '';
    return `${row.from} → ${row.to}${variants}${row.merge ? ` (병합: ${row.existing_spellings.join(', ') || row.to}에 합침)` : ''}`;
  }).join(' · ');
}

export interface ClassRenameDialogViewProps {
  names: string[];
  from: string;
  to: string;
  preview: ClassRenamePreview | null;
  result: ClassRenameResult | null;
  /** After an apply: whether the image list and the open image were read again (null before the refresh ends). */
  reloaded: boolean | null;
  error: string;
  busy: 'preview' | 'apply' | null;
  confirmed: boolean;
  onFromChange: (value: string) => void;
  onToChange: (value: string) => void;
  onConfirmedChange: (value: boolean) => void;
  onPreview: () => void;
  onApply: () => void;
  onClose: () => void;
}

const field = 'rounded-md border border-[#3D5368] bg-[#0C1724] px-2.5 py-1.5 text-sm text-white outline-none focus:border-cyan-400 disabled:opacity-50';
const primary = 'workspace-button workspace-button--primary';

/** The dialog without state, so a test can render every stage. Exactly one primary button at a time: 미리보기 until a
 * clean preview, then 백업 후 적용 (미리보기 becomes the outline '다시 미리보기'), then 닫기 on the result. */
export const ClassRenameDialogView: React.FC<ClassRenameDialogViewProps> = (props) => {
  const { names, from, to, preview, result, reloaded, error, busy, confirmed } = props;
  const ready = canApply(preview, confirmed, busy !== null);
  const clean = preview !== null && preview.conflicts.length === 0;
  const sameName = Boolean(to.trim()) && to.trim() === from;
  const hint = names.length === 0 ? NO_CLASSES_HINT : sameName ? sameNameHint(from) : '';
  return (
    <WorkspaceDialog title={DIALOG_TITLE} description={SOURCE_NOTE} onClose={() => { if (busy === null) props.onClose(); }}>
      <div data-class-rename-body aria-busy={busy !== null} className="space-y-4 text-sm text-slate-200">
        {result ? (
          <section aria-label="적용 결과" className="space-y-2">
            <p role="status" className="rounded border border-emerald-700 bg-emerald-950/40 p-2 text-emerald-100">{backupNotice(result)}</p>
            <p className="text-slate-300">{mappingLine(result)}</p>
            {reloaded === false && <p role="alert" className="text-amber-200">{RELOAD_FAILED}</p>}
            <p className="text-xs text-slate-400">
              {reloaded ? `${RELOADED} ` : ''}되돌리려면 데이터 버전 목록에서 위 백업을 복원하세요.
              {result.drafts.pending ? ` 검토 대기 초안 ${result.drafts.pending}건은 옛 이름을 가지고 있으니 다시 만드세요.` : ''}
              {result.truth.images ? ` 정답 선언 ${result.truth.images}장은 다시 선언해야 합니다.` : ''}
            </p>
          </section>
        ) : (
          <>
            <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); props.onPreview(); }}>
              <label className="flex flex-col gap-1 text-xs text-slate-300">
                바꿀 클래스
                <select aria-label="바꿀 클래스" value={from} disabled={busy !== null || names.length === 0} onChange={(event) => props.onFromChange(event.target.value)} className={`${field} min-w-[180px]`}>
                  {names.length === 0 && <option value="">클래스 없음</option>}
                  {names.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-slate-300">
                새 이름
                <input aria-label="새 이름" list="class-rename-names" value={to} maxLength={120} disabled={busy !== null}
                  onChange={(event) => props.onToChange(event.target.value)} placeholder="새 이름 또는 합칠 클래스" className={`${field} min-w-[200px]`} />
                <datalist id="class-rename-names">{names.filter((name) => name !== from).map((name) => <option key={name} value={name} />)}</datalist>
              </label>
              <button type="submit" disabled={busy !== null || !from || !to.trim() || sameName} aria-describedby={hint ? HINT_ID : undefined}
                className={clean ? 'workspace-button' : primary}>{busy === 'preview' ? '미리보기 중…' : preview ? '다시 미리보기' : '미리보기'}</button>
            </form>
            {hint && <p id={HINT_ID} className="text-xs text-amber-200">{hint}</p>}
            {preview && (
              <section aria-label="변경 미리보기" className="space-y-2">
                <p className="font-semibold text-slate-100">{mappingLine(preview)}</p>
                <table className="w-full text-left text-xs">
                  <thead><tr className="text-slate-400"><th className="py-1 pr-3 font-medium">저장소</th><th className="py-1 pr-3 font-medium">수</th><th className="py-1 font-medium">처리</th></tr></thead>
                  <tbody>
                    {previewRows(preview).map((row) => (
                      <tr key={row.store} className="border-t border-[#243447]"><td className="py-1 pr-3 text-slate-200">{row.store}</td><td className="py-1 pr-3 text-slate-100">{row.count}</td><td className="py-1 text-slate-400">{row.note}</td></tr>
                    ))}
                  </tbody>
                </table>
                {preview.conflicts.length > 0 ? (
                  <div role="alert" aria-label="적용할 수 없는 이유" className="rounded border border-amber-600 bg-amber-950/40 p-2 text-amber-100">
                    <ul className="list-disc space-y-1 pl-4">
                      {preview.conflicts.map((conflict, index) => <li key={`${conflict.code}-${index}`}>{conflict.message}</li>)}
                    </ul>
                  </div>
                ) : (
                  <label className="flex items-start gap-2 rounded border border-[#33465C] p-2 text-slate-200">
                    <input type="checkbox" aria-label="백업 확인" checked={confirmed} disabled={busy !== null} onChange={(event) => props.onConfirmedChange(event.target.checked)} className="mt-0.5" />
                    <span>{CONFIRM_TEXT}</span>
                  </label>
                )}
              </section>
            )}
          </>
        )}
        {error && <p role="alert" className="text-red-300">{error}</p>}
        {(result || clean) && (
          <div className="flex justify-end gap-2 border-t border-[#33465C] pt-3">
            {result
              ? <button type="button" onClick={props.onClose} disabled={busy !== null} className={primary}>닫기</button>
              : <button type="button" onClick={props.onApply} disabled={!ready} className={primary}>{busy === 'apply' ? '적용 중…' : CONFIRM_LABEL}</button>}
          </div>
        )}
      </div>
    </WorkspaceDialog>
  );
};

export const ClassRenameDialog: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const annotations = useAnnotationStore((state) => state.annotations);
  const reviewerName = useAnnotationStore((state) => state.reviewerName);
  const datasetClasses = useDatasetStore((state) => state.classes);
  const names = useMemo(() => renameCandidates(datasetClasses, annotations), [datasetClasses, annotations]);
  const [from, setFrom] = useState(names[0] ?? '');
  const [to, setTo] = useState('');
  const [preview, setPreview] = useState<ClassRenamePreview | null>(null);
  const [result, setResult] = useState<ClassRenameResult | null>(null);
  const [reloaded, setReloaded] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null);
  const actor = (reviewerName || '').trim() || 'operator';
  useEffect(() => { reportPanelError(DIALOG_TITLE, error); return () => reportPanelError(DIALOG_TITLE, null); }, [error]);
  const mapping = [{ from: from.trim(), to: to.trim() }];

  // Any change to the mapping drops the preview and its confirmation: the apply must match what was previewed.
  const changeFrom = (value: string) => { setFrom(value); setPreview(null); setConfirmed(false); };
  const changeTo = (value: string) => { setTo(value); setPreview(null); setConfirmed(false); };
  // The class list can arrive after the dialog opened, or lose the chosen name: keep the choice one the list offers.
  useEffect(() => { if (!from || !names.includes(from)) changeFrom(names[0] ?? ''); }, [names]);

  // Every control turns off while a preview or an apply runs, so the focus drops to the page, where the dialog's
  // Escape and Tab no longer reach. When the work ends, put the focus back on the dialog's next action.
  useEffect(() => {
    if (busy !== null || typeof document === 'undefined') return;
    const body = document.querySelector<HTMLElement>('[data-class-rename-body]');
    const active = document.activeElement;
    if (!body || (active && active !== document.body && active !== document.documentElement)) return;
    (body.querySelector<HTMLElement>('button.workspace-button--primary:not(:disabled)')
      ?? body.querySelector<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)'))?.focus();
  }, [busy]);

  const runPreview = async () => {
    if (busy || !mapping[0].from || !mapping[0].to) return;
    setBusy('preview'); setError('');
    try {
      // Open canvas edits land first, so the preview counts what is on disk and the apply's file check still matches.
      await saveOpenEdits();
      setPreview(await datasetWorkflow.classRenamePreview(mapping, actor));
      setConfirmed(false);
    } catch (cause) {
      setPreview(null);
      setError(workflowError(cause));
    } finally {
      setBusy(null);
    }
  };

  const runApply = async () => {
    if (!preview || !canApply(preview, confirmed, busy !== null)) return;
    setBusy('apply'); setError('');
    let sent = false;
    try {
      // A guard only: the preview saved already, so this writes nothing unless the canvas changed since.
      await saveOpenEdits();
      sent = true;
      const applied = await datasetWorkflow.classRenameApply(mapping, actor, preview.preview_sha256);
      setResult(applied);
      setReloaded(await refreshAfterRename(applied.mapping));
    } catch (cause) {
      // A refusal before the request leaves the files as previewed, so the preview stays; after it, preview again.
      if (sent) { setPreview(null); setConfirmed(false); }
      setError(workflowError(cause));
    } finally {
      setBusy(null);
    }
  };

  return (
    <ClassRenameDialogView names={names} from={from} to={to} preview={preview} result={result} reloaded={reloaded} error={error} busy={busy} confirmed={confirmed}
      onFromChange={changeFrom} onToChange={changeTo} onConfirmedChange={setConfirmed}
      onPreview={() => void runPreview()} onApply={() => void runApply()} onClose={() => { if (!busy) onClose(); }} />
  );
};
