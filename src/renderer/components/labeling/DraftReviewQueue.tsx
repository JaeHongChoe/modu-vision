import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ClipboardCheck, Loader2, RotateCcw, X } from 'lucide-react';
import { api, type LabelSuggestion, type LabelSuggestionBatchEntry, type LabelSuggestionModel } from '../../services/api';
import { datasetWorkflow } from '../../services/datasetWorkflow';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useModelAssistRunStore } from '../../stores/useModelAssistRunStore';
import { draftReviewScope, useDraftReviewStore } from '../../stores/useDraftReviewStore';
import { bumpWorkflowEvidence } from '../../stores/workflowEvidence';
import { BulkLabelAssist } from './BulkLabelAssist';
import type { LabelAssistExecution } from './LabelAssistDeviceSizes';
import { SuggestionPreview } from './SuggestionPreview';
import { DRAFT_ORDER_TEXT, adoptionHistory, canTakeBack, draftError, orderDrafts, pendingImageCount, topConfidence, type DraftOrder } from './draftReview';

const EXECUTION: LabelAssistExecution = { device: 'cpu', min_area: 0, min_width: 0, min_height: 0 };
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;
const stem = (path: string) => fileName(path).replace(/\.[^.]+$/, '');

/** Step 2's draft review: machine label drafts for the whole project, reviewed one image at a time.
 *  A draft is never a label until a person adopts it; adoption adds (never replaces) shapes, keeps a backup and the
 *  shapes' origin, and one adoption can be taken back while its shapes are unchanged. It stays visible in focus mode. */
export const DraftReviewQueue: React.FC = () => {
  const projectDir = useProjectStore((state) => state.projectDir);
  const project = useProjectStore((state) => state.project);
  const task = useProjectStore((state) => state.task);
  const image = useAnnotationStore((state) => state.currentImage);
  const isDirty = useAnnotationStore((state) => state.isDirty);
  const reviewerName = useAnnotationStore((state) => state.reviewerName);
  const setReviewerName = useAnnotationStore((state) => state.setReviewerName);
  const publish = useDraftReviewStore((state) => state.publish);
  const beginRun = useModelAssistRunStore((state) => state.begin);
  const endRun = useModelAssistRunStore((state) => state.end);
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<LabelSuggestion[]>([]);
  const [models, setModels] = useState<LabelSuggestionModel[]>([]);
  const [modelId, setModelId] = useState('');
  const [threshold, setThreshold] = useState(0.5);
  const [order, setOrder] = useState<DraftOrder>('uncertain');
  const [selectedId, setSelectedId] = useState('');
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<'loading' | 'accept' | 'reject' | 'undo' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [reloads, setReloads] = useState(0);
  const [room, setRoom] = useState<number>();
  const rowRef = useRef<HTMLDivElement>(null);
  const scope = draftReviewScope({ projectDir, project, task });
  const live = useRef(scope);
  live.current = scope;

  useEffect(() => {
    let current = true;
    setError(null);
    if (!projectDir || !project?.source_dataset_dir) { setDrafts([]); setModels([]); publish(scope, 0); return; }
    setBusy((value) => value ?? 'loading');
    Promise.all([api.labelSuggestions.list(), api.labelSuggestions.models()]).then(([listed, catalog]) => {
      if (!current || live.current !== scope) return;
      setDrafts(listed.suggestions);
      setModels(catalog.models);
      setModelId((previous) => catalog.models.some((model) => model.job_id === previous) ? previous : catalog.models[0]?.job_id || '');
      publish(scope, pendingImageCount(listed.suggestions));
    }).catch((cause) => { if (current && live.current === scope) setError(draftError(cause)); })
      .finally(() => { if (current && live.current === scope) setBusy((value) => value === 'loading' ? null : value); });
    return () => { current = false; };
  }, [scope, reloads, projectDir, project?.source_dataset_dir, publish]);

  // A batch that stopped leaves new drafts; read them once. The batch panel's callback must stay stable, since it
  // reports `false` on every cleanup.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !batchRunning) setReloads((value) => value + 1);
    wasRunning.current = batchRunning;
  }, [batchRunning]);

  const pending = useMemo(() => orderDrafts(drafts, order), [drafts, order]);
  const history = useMemo(() => adoptionHistory(drafts).slice(0, 20), [drafts]);
  const selected = drafts.find((draft) => draft.id === selectedId);
  const waiting = pendingImageCount(drafts);

  const choose = (draft: LabelSuggestion) => {
    setSelectedId(draft.id);
    setChosen(new Set(draft.candidates.map((candidate) => candidate.id)));
    setError(null);
    setNotice(null);
  };

  // Show the draft's image on the canvas too (current edits are saved first, as with any image change).
  const openDraft = async (draft: LabelSuggestion) => {
    choose(draft);
    if (useAnnotationStore.getState().currentImage?.file_path === draft.image_path) return;
    const opened = await useProjectStore.getState().openImageForLabeling(stem(draft.image_path), draft.image_path);
    if (!opened && live.current === scope) setError('현재 이미지의 수정 내용을 저장하지 못해 초안 이미지로 이동하지 않았습니다.');
  };

  const openBatchEntry = async (entry: LabelSuggestionBatchEntry) => {
    if (!entry.proposal_id) return;
    const known = drafts.find((draft) => draft.id === entry.proposal_id)
      || await api.labelSuggestions.list(entry.image_path).then((result) => result.suggestions.find((draft) => draft.id === entry.proposal_id));
    if (known && live.current === scope) await openDraft(known);
  };

  // After the server changed labels: re-read the open image when it is the one that changed, and the drafts.
  const afterLabelChange = async (draft: LabelSuggestion, startedScope: string) => {
    const annotation = useAnnotationStore.getState();
    if (annotation.currentImage?.file_path === draft.image_path) {
      await annotation.loadAnnotationsForCurrent();
      // Undo must not bring back the labels as they were before this server change.
      useAnnotationStore.setState({ history: [], future: [] });
    }
    if (live.current !== startedScope) return;
    await useDatasetStore.getState().annotationsChanged();
    bumpWorkflowEvidence();
    setReloads((value) => value + 1);
  };

  const sameImageDirty = (draft: LabelSuggestion) => isDirty && image?.file_path === draft.image_path;

  // The panel hangs below its row: keep it within what is still on screen above the step footer, wherever the row is
  // scrolled to, so the adopt and take-back buttons stay reachable.
  useEffect(() => {
    if (!open) return;
    const fit = () => {
      const row = rowRef.current;
      if (!row) return;
      let bottom = window.innerHeight;
      for (let node = row.parentElement; node; node = node.parentElement) {
        if (getComputedStyle(node).overflowY !== 'visible') bottom = Math.min(bottom, node.getBoundingClientRect().bottom);
      }
      setRoom(Math.max(240, Math.floor(bottom - row.getBoundingClientRect().bottom - 12)));
    };
    fit();
    window.addEventListener('resize', fit);
    window.addEventListener('scroll', fit, true);
    return () => { window.removeEventListener('resize', fit); window.removeEventListener('scroll', fit, true); };
  }, [open]);

  // Opening re-reads the drafts and models, so a model that finished training meanwhile is offered.
  const toggle = () => {
    if (!open) setReloads((value) => value + 1);
    setOpen(!open);
  };

  const review = async (decision: 'accept' | 'reject') => {
    if (!selected) return;
    if (!reviewerName.trim()) { setError('검토자 이름을 입력하세요.'); return; }
    if (decision === 'accept' && sameImageDirty(selected)) { setError('이 이미지의 수정 내용을 먼저 저장한 뒤 채택하세요.'); return; }
    const startedScope = scope;
    beginRun();
    setBusy(decision);
    setError(null);
    setNotice(null);
    try {
      const updated = await datasetWorkflow.review(selected.id, decision, decision === 'accept' ? [...chosen] : [], reviewerName);
      if (live.current !== startedScope) return;
      setDrafts((previous) => previous.map((draft) => draft.id === updated.id ? updated : draft));
      if (decision === 'accept') {
        await afterLabelChange(updated, startedScope);
        setNotice(`${updated.accepted_candidate_ids.length}개를 라벨로 채택했습니다(기존 라벨에 덧붙임). 캔버스에서 고친 뒤 저장할 수 있고(남은 초안을 먼저 검토하세요. 고쳐 저장하면 남은 초안은 다시 만들어야 합니다), 고치기 전이면 '이 채택 되돌리기'로 이 채택만 뺄 수 있습니다. 채택 전 데이터 버전 ${updated.backup_version_id}.`);
      } else {
        setReloads((value) => value + 1);
        setNotice('초안을 거절했습니다. 라벨은 바뀌지 않았습니다.');
      }
      setSelectedId('');
    } catch (cause) {
      if (live.current === startedScope) setError(draftError(cause));
    } finally {
      endRun();
      if (live.current === startedScope) setBusy(null);
    }
  };

  const takeBack = async (draft: LabelSuggestion) => {
    if (!reviewerName.trim()) { setError('검토자 이름을 입력하세요.'); return; }
    if (sameImageDirty(draft)) { setError('이 이미지의 수정 내용을 먼저 저장하세요.'); return; }
    const startedScope = scope;
    beginRun();
    setBusy('undo');
    setError(null);
    setNotice(null);
    try {
      const updated = await datasetWorkflow.undoAdoption(draft.id, reviewerName);
      if (live.current !== startedScope) return;
      setDrafts((previous) => previous.map((item) => item.id === updated.id ? updated : item));
      await afterLabelChange(updated, startedScope);
      setNotice(`${fileName(draft.image_path)}에서 이 채택의 도형만 뺐습니다. 되돌리기 직전 데이터 버전 ${updated.revert_backup_version_id}.`);
    } catch (cause) {
      if (live.current === startedScope) setError(draftError(cause));
    } finally {
      endRun();
      if (live.current === startedScope) setBusy(null);
    }
  };

  if (!projectDir || !project?.source_dataset_dir) return null;
  return (
    // Open, the row sits above the rows below it so the panel's header and close button are not covered.
    <div ref={rowRef} className={`relative ${open ? 'z-[75]' : 'z-[68]'} flex h-10 shrink-0 items-center justify-between border-b border-[#2B3547] bg-[#0F1A24] px-4 text-xs text-slate-300`}>
      <div className="flex min-w-0 items-center gap-2">
        <ClipboardCheck className="h-4 w-4 text-emerald-400" />
        <span className="font-semibold text-slate-100">초안 검토</span>
        <span className="truncate text-slate-500">모델이 만든 라벨 초안은 사람이 채택해야 라벨이 됩니다</span>
      </div>
      <button id="draft-review-queue" type="button" onClick={toggle} aria-expanded={open} aria-label="초안 검토 패널"
        className="ml-3 flex shrink-0 items-center gap-1.5 rounded-md border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 font-semibold text-emerald-200 hover:bg-emerald-900/50">
        검토 대기 {waiting}장
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <aside style={{ maxHeight: room }} className="absolute right-3 top-full z-50 mt-1 flex max-h-[calc(100vh-195px)] w-[540px] max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-xl border border-slate-600 bg-[#151D2A] shadow-2xl" aria-label="초안 검토">
          <div className="flex items-start justify-between border-b border-slate-700 px-4 py-3">
            <div>
              <h3 className="text-sm font-bold text-white">라벨 초안 검토</h3>
              <p className="mt-0.5 text-[11px] text-slate-400">검토 대기 {waiting}장 · 채택은 기존 라벨에 덧붙이고, 채택 직전 데이터 버전을 자동으로 남깁니다</p>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label="초안 검토 닫기" className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"><X className="h-4 w-4" /></button>
          </div>
          <div className="space-y-4 overflow-y-auto p-4">
            {error && <p role="alert" className="rounded border border-red-800 bg-red-950/40 p-2 text-xs text-red-200">{error}</p>}
            {notice && <p role="status" className="rounded border border-emerald-800 bg-emerald-950/30 p-2 text-xs text-emerald-200">{notice}</p>}

            <section className="space-y-2 rounded-lg border border-slate-700 bg-[#101722] p-3" aria-label="초안 만들기">
              <h4 className="font-semibold text-slate-100">1. 초안 만들기</h4>
              {models.length === 0 ? <p className="text-[11px] text-slate-400">지금 데이터로 학습을 마친 모델이 없습니다. 몇 장을 직접 라벨한 뒤 3단계에서 학습하면, 남은 이미지의 초안을 만들 수 있습니다.</p> : <>
                <label className="block text-[11px] text-slate-400">초안을 만들 모델
                  <select aria-label="초안 모델" value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={!!busy || batchRunning} className="mt-1 w-full rounded border border-slate-600 bg-[#1D2938] px-2 py-2 text-xs text-slate-100 disabled:opacity-50">
                    {models.map((model) => <option value={model.job_id} key={model.job_id}>{model.job_id} · {model.task}</option>)}
                  </select>
                </label>
                <label className="block text-[11px] text-slate-400">후보 기준 신뢰도 <span className="font-mono text-cyan-300">{threshold.toFixed(2)}</span>
                  <input aria-label="초안 기준 신뢰도" type="range" min="0.05" max="0.95" step="0.05" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} disabled={!!busy || batchRunning} className="mt-1 block w-full accent-cyan-500" />
                </label>
                <BulkLabelAssist execution={EXECUTION} projectDir={projectDir} modelId={modelId} threshold={threshold} keywords={[]}
                  disabled={!!busy || isDirty} onOpenEntry={openBatchEntry}
                  onRunningChange={setBatchRunning} />
                <p className="text-[11px] leading-relaxed text-slate-500">초안 하나를 채택하면 데이터가 바뀌어 이 모델로 새 초안은 만들 수 없게 됩니다(다시 학습 필요). 같은 묶음으로 만든 초안은 이어서 채택할 수 있으니 남은 이미지를 한 번에 만드세요. 라벨을 직접 고쳐 저장하면 남은 초안도 채택할 수 없게 되니, 남은 초안을 먼저 검토한 뒤 고치세요.</p>
              </>}
            </section>

            <section className="space-y-2" aria-label="검토 대기 초안">
              <div className="flex items-center justify-between gap-2">
                <h4 className="font-semibold text-slate-100">2. 검토 대기 {pending.length}건</h4>
                {busy === 'loading' && <Loader2 className="h-4 w-4 animate-spin text-cyan-300" />}
                <select aria-label="초안 순서" value={order} onChange={(event) => setOrder(event.target.value as DraftOrder)} className="rounded border border-slate-600 bg-[#1D2938] px-2 py-1 text-[11px] text-slate-100">
                  <option value="uncertain">덜 확신하는 것부터</option>
                  <option value="confident">확실한 것부터</option>
                </select>
              </div>
              <p className="text-[11px] text-slate-500">순서 기준: {DRAFT_ORDER_TEXT[order]}</p>
              {pending.length === 0 && <p className="rounded border border-slate-700 p-3 text-center text-slate-400">검토할 초안이 없습니다.</p>}
              <ul className="max-h-40 space-y-1 overflow-y-auto">
                {pending.map((draft) => <li key={draft.id}>
                  <button type="button" onClick={() => void openDraft(draft)} aria-pressed={draft.id === selectedId}
                    className={`flex w-full items-center justify-between gap-2 rounded border px-2 py-1.5 text-left ${draft.id === selectedId ? 'border-cyan-500 bg-cyan-900/30 text-cyan-100' : 'border-slate-700 bg-slate-800/50 text-slate-200 hover:border-slate-500'}`}>
                    <span className="min-w-0 truncate">{fileName(draft.image_path)}</span>
                    <span className="shrink-0 text-[11px] text-slate-400">후보 {draft.candidates.length}개 · 최고 {Math.round(topConfidence(draft) * 100)}%</span>
                  </button>
                </li>)}
              </ul>
              {selected && selected.status === 'pending' && <div className="space-y-2 rounded-lg border border-cyan-900 bg-[#0F1722] p-3" aria-label="선택한 초안">
                <SuggestionPreview proposal={selected} selectedIds={chosen} />
                <div className="max-h-36 space-y-1 overflow-y-auto">
                  {selected.candidates.length === 0 && <p className="rounded border border-slate-700 p-2 text-center text-slate-400">이 이미지에는 후보가 없습니다. 거절하고 직접 라벨하세요.</p>}
                  {selected.candidates.map((candidate) => <label key={candidate.id} className="flex items-center gap-2 rounded border border-slate-700 bg-slate-800/40 p-1.5">
                    <input type="checkbox" checked={chosen.has(candidate.id)} disabled={!!busy} onChange={(event) => setChosen((previous) => {
                      const next = new Set(previous);
                      if (event.target.checked) next.add(candidate.id); else next.delete(candidate.id);
                      return next;
                    })} className="accent-cyan-500" />
                    <span className="grow text-slate-100">{candidate.annotation.label} <span className="text-[10px] text-slate-400">{candidate.annotation.type}</span></span>
                    <span className="font-mono tabular-nums text-cyan-300">{Math.round(candidate.confidence * 100)}%</span>
                  </label>)}
                </div>
                <label className="block text-[11px]">검토자<input aria-label="초안 검토자 이름" value={reviewerName} onChange={(event) => setReviewerName(event.target.value)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5" /></label>
                <div className="flex gap-2">
                  <button type="button" onClick={() => void review('reject')} disabled={!!busy} className="flex grow items-center justify-center gap-1 rounded border border-slate-600 px-3 py-2 font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-40"><X className="h-3.5 w-3.5" /> 거절</button>
                  <button type="button" onClick={() => void review('accept')} disabled={!!busy || batchRunning || chosen.size === 0} className="flex grow items-center justify-center gap-1 rounded bg-emerald-600 px-3 py-2 font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40">{busy === 'accept' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 선택 후보 채택 (덧붙이기)</button>
                </div>
                {batchRunning && <p className="text-[11px] text-amber-300">초안을 만드는 중에는 채택할 수 없습니다. 끝난 뒤 채택하세요.</p>}
              </div>}
            </section>

            <section className="space-y-2" aria-label="채택 기록">
              <h4 className="font-semibold text-slate-100">3. 채택 기록</h4>
              {history.length === 0 && <p className="text-[11px] text-slate-500">아직 채택한 초안이 없습니다.</p>}
              <ul className="max-h-40 space-y-1 overflow-y-auto">
                {history.map((draft) => <li key={draft.id} className="flex items-center justify-between gap-2 rounded border border-slate-700 bg-slate-800/40 px-2 py-1.5">
                  <span className="min-w-0 truncate">{fileName(draft.image_path)} · {draft.status === 'reverted' ? '되돌림' : `${draft.accepted_candidate_ids.length}개 채택`}{draft.reviewer ? ` · ${draft.reviewer}` : ''}</span>
                  {canTakeBack(draft) && <button type="button" onClick={() => void takeBack(draft)} disabled={!!busy || batchRunning} aria-label={`${fileName(draft.image_path)} 채택 되돌리기`}
                    className="flex shrink-0 items-center gap-1 rounded border border-amber-700 px-2 py-1 text-[11px] font-semibold text-amber-200 hover:bg-amber-900/30 disabled:opacity-40">
                    {busy === 'undo' ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />} 이 채택 되돌리기
                  </button>}
                </li>)}
              </ul>
              <p className="text-[11px] leading-relaxed text-slate-500">되돌리기는 이 채택으로 더한 도형만 뺍니다. 그 도형을 고쳤다면 되돌리지 않고, 채택 전에 남긴 데이터 버전으로 복원하도록 안내합니다.</p>
            </section>
          </div>
        </aside>
      )}
    </div>
  );
};

export default DraftReviewQueue;
