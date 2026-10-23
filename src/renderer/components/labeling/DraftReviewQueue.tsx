import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, CheckCheck, ChevronDown, ChevronLeft, ChevronRight, ClipboardCheck, Loader2, RotateCcw, X } from 'lucide-react';
import { api, getProjectContext, type LabelSuggestion, type LabelSuggestionBatchEntry, type LabelSuggestionModel } from '../../services/api';
import { datasetWorkflow } from '../../services/datasetWorkflow';
import { teamDataApi } from '../../services/teamDataApi';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { readDatasetAuthority, useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useModelAssistRunStore } from '../../stores/useModelAssistRunStore';
import { draftReviewScope, useDraftReviewStore } from '../../stores/useDraftReviewStore';
import { bumpWorkflowEvidence, subscribeWorkflowEvidence } from '../../stores/workflowEvidence';
import { reportPanelError } from '../../stores/assistContext';
import { BulkLabelAssist } from './BulkLabelAssist';
import { PhraseDraftCreate } from './PhraseDraftCreate';
import type { LabelAssistExecution } from './LabelAssistDeviceSizes';
import { SuggestionPreview } from './SuggestionPreview';
import { DraftGrid } from './DraftGrid';
import { imageLeaseToken } from './teamDataWorkflow';
import { Explain } from '../common/Explain';
import { CHANGED_MODEL_MARK, DRAFT_ORDER_TEXT, MIXED_SOURCE_NOTE, OPEN_REQUEST_MISSING_TEXT, adoptable, adoptionHistory, adoptionLine, adoptionSummary, adoptionTitle, canTakeBack,
  closedDraftText, draftError, draftSourceText, gridPages, modelStats, multiAdoptionRefusal, orderDrafts, pageAdoptionRefusal, pendingImageCount, sourcesMixed, staleBatchRefusal,
  supportLimitsText, thresholdAdvice, topConfidence, defaultChoice, emptyPendingDrafts, unknownClassCandidate, UNKNOWN_CLASS_MARK, type AdoptionResult, type DraftOrder, type ModelAdoptionStats } from './draftReview';

const EXECUTION: LabelAssistExecution = { device: 'cpu', min_area: 0, min_width: 0, min_height: 0 };
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;
const stem = (path: string) => fileName(path).replace(/\.[^.]+$/, '');
const PANEL = '초안 검토';

/** With team editing on, a label save needs the image's edit lease. The canvas lease covers the open image; any other
 *  image gets a lease of its own for the one adoption (or take-back) and gives it back right after. */
async function leaseFor(draft: LabelSuggestion, actor: string): Promise<{ token?: string; release: () => Promise<void> }> {
  const state = useAnnotationStore.getState();
  if (!state.teamEditingEnabled) return { release: async () => {} };
  const own = imageLeaseToken(state.editLease, draft.image_uuid);
  if (own) return { token: own, release: async () => {} };
  if (!draft.image_uuid) throw new Error('이 초안에는 이미지 식별 정보가 없어 팀 편집 중에는 채택할 수 없습니다. 초안을 새로 만드세요.');
  const uuid = draft.image_uuid;
  const lease = await teamDataApi.acquire((await teamDataApi.image(uuid)).image, actor);
  const token = lease.lease_token;
  return {
    token,
    release: async () => {
      if (!token) return;
      try { await teamDataApi.release((await teamDataApi.image(uuid)).image, actor, token); } catch { /* the lease lapses on its own */ }
    },
  };
}

/** Step 2's draft review: machine label drafts for the whole project, reviewed one image at a time or several at
 *  once. A draft is never a label until a person adopts it; adoption adds (never replaces) shapes, keeps a backup and
 *  the shapes' origin, and one adoption can be taken back while its shapes are unchanged. Only drafts the person has
 *  looked at in the preview can go into a multi-adoption. It stays visible in focus mode. */
export const DraftReviewQueue: React.FC = () => {
  const projectDir = useProjectStore((state) => state.projectDir);
  const project = useProjectStore((state) => state.project);
  const task = useProjectStore((state) => state.task);
  const image = useAnnotationStore((state) => state.currentImage);
  const isDirty = useAnnotationStore((state) => state.isDirty);
  const reviewerName = useAnnotationStore((state) => state.reviewerName);
  const setReviewerName = useAnnotationStore((state) => state.setReviewerName);
  const publish = useDraftReviewStore((state) => state.publish);
  const openRequest = useDraftReviewStore((state) => state.openRequest);
  const clearOpenRequest = useDraftReviewStore((state) => state.clearOpenRequest);
  const beginRun = useModelAssistRunStore((state) => state.begin);
  const endRun = useModelAssistRunStore((state) => state.end);
  const [open, setOpen] = useState(false);
  // One right-side popover at a time: opening this one closes 모델 보조, and 모델 보조 opening closes this one.
  const sidePanel = useDraftReviewStore((state) => state.sidePanel);
  useEffect(() => { if (open) useDraftReviewStore.getState().showSidePanel('drafts'); }, [open]);
  useEffect(() => { if (sidePanel && sidePanel !== 'drafts') setOpen(false); }, [sidePanel]);
  const [drafts, setDrafts] = useState<LabelSuggestion[]>([]);
  const [models, setModels] = useState<LabelSuggestionModel[]>([]);
  const [modelId, setModelId] = useState('');
  const [threshold, setThreshold] = useState(0.5);
  const [order, setOrder] = useState<DraftOrder>('uncertain');
  const [selectedId, setSelectedId] = useState('');
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  // Drafts whose preview the person has seen, the ones picked for a multi-adoption, and each one's candidate choice.
  const [viewed, setViewed] = useState<Set<string>>(new Set());
  const [picks, setPicks] = useState<Set<string>>(new Set());
  const [choices, setChoices] = useState<Map<string, Set<string>>>(new Map());
  const datasetClasses = useDatasetStore((state) => state.classes);
  const knownClasses = useMemo(() => new Set(Object.keys(datasetClasses ?? {})), [datasetClasses]);
  const choiceFor = (draft: LabelSuggestion) => choices.get(draft.id) ?? defaultChoice(draft, knownClasses);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [stats, setStats] = useState<ModelAdoptionStats[] | null>(null);
  // The contact-sheet review: a page of one batch's drafts; a tile counts as seen only once its image has shown.
  const [mode, setMode] = useState<'list' | 'grid'>('list');
  const [pageSize, setPageSize] = useState<12 | 24>(12);
  const [page, setPage] = useState(0);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [loadedOnPage, setLoadedOnPage] = useState<Set<string>>(new Set());
  const [wide, setWide] = useState(false);
  // The last list read that landed (its scope, and a count), and the open request already given its one extra read.
  const lastRead = useRef({ scope: '', count: 0 });
  const retriedOpen = useRef<{ nonce: number; after: number } | null>(null);
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
  const shownScope = useRef(scope);

  useEffect(() => {
    let current = true;
    setError(null);
    // Another project, source, label set or task: nothing of the previous one stays on screen while this one loads.
    if (shownScope.current !== scope) {
      shownScope.current = scope;
      setDrafts([]); setModels([]); setStats(null); setSelectedId('');
    }
    if (!projectDir || !project?.source_dataset_dir) {
      setDrafts([]); setModels([]); setStats(null);
      lastRead.current = { scope, count: lastRead.current.count + 1 };
      publish(scope, 0, 0);
      return;
    }
    setBusy((value) => value ?? 'loading');
    // The adoption numbers come on their own: a slow count never holds up the drafts, and a failed one shows nothing.
    api.labelSuggestions.stats().then((counted) => {
      if (current && live.current === scope) setStats((counted?.models as ModelAdoptionStats[] | undefined) ?? null);
    }).catch(() => { if (current && live.current === scope) setStats(null); });
    Promise.all([api.labelSuggestions.list(), api.labelSuggestions.models()]).then(([listed, catalog]) => {
      if (!current || live.current !== scope) return;
      lastRead.current = { scope, count: lastRead.current.count + 1 };
      setDrafts(listed.suggestions);
      setModels(catalog.models);
      setModelId((previous) => catalog.models.some((model) => model.job_id === previous) ? previous : catalog.models[0]?.job_id || '');
      publish(scope, pendingImageCount(listed.suggestions), catalog.models.length);
    }).catch((cause) => { if (current && live.current === scope) setError(draftError(cause)); })
      .finally(() => { if (current && live.current === scope) setBusy((value) => value === 'loading' ? null : value); });
    return () => { current = false; };
  }, [scope, reloads, projectDir, project?.source_dataset_dir, publish]);

  // Labels or drafts changed somewhere else (an adoption here, a class change, the operations cycle's drafts): read again.
  useEffect(() => subscribeWorkflowEvidence(() => setReloads((value) => value + 1)), []);

  // A batch that stopped leaves new drafts; read them once. The batch panel's callback must stay stable, since it
  // reports `false` on every cleanup.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !batchRunning) setReloads((value) => value + 1);
    wasRunning.current = batchRunning;
  }, [batchRunning]);

  useEffect(() => { reportPanelError(PANEL, error); return () => reportPanelError(PANEL, null); }, [error]);

  const pending = useMemo(() => orderDrafts(drafts, order), [drafts, order]);
  const history = useMemo(() => adoptionHistory(drafts).slice(0, 20), [drafts]);
  const selected = drafts.find((draft) => draft.id === selectedId);
  const waiting = pendingImageCount(drafts);
  // Images whose waiting draft found something; an empty draft does not hold its image back from the next phrase.
  const pendingImagePaths = useMemo(() => new Set(drafts.filter((draft) => draft.status === 'pending' && draft.candidates.length > 0).map((draft) => draft.image_path)), [drafts]);
  const empty = useMemo(() => emptyPendingDrafts(drafts), [drafts]);
  const picked = pending.filter((draft) => picks.has(draft.id));
  const refusal = multiAdoptionRefusal(picked);
  const unseenPicked = picked.filter((draft) => !viewed.has(draft.id)).length;
  const selectedModel = models.find((model) => model.job_id === modelId);
  const modelRow = modelStats(stats, modelId);
  const grid = useMemo(() => gridPages(pending, pageSize), [pending, pageSize]);
  const pageDrafts = grid.pages[Math.min(page, Math.max(0, grid.pages.length - 1))] ?? [];
  const pageRefusal = pageAdoptionRefusal(pageDrafts, excluded, loadedOnPage);
  // What keeps '이 페이지 채택' off, said next to it: the page itself first, then the missing reviewer name.
  const pageBlock = pageRefusal ?? (reviewerName.trim() ? null : '검토자 이름을 입력하면 이 페이지를 채택할 수 있습니다.');
  const authority = readDatasetAuthority();
  // With the grid's page adoption on screen, it is the one primary action and holds the one reviewer field.
  const gridShown = mode === 'grid' && pending.length > 0 && grid.pages.length > 0;
  const adoptStyle = gridShown ? 'border border-emerald-700 text-emerald-200 hover:bg-emerald-900/50' : 'bg-emerald-600 text-white hover:bg-emerald-500';
  const shownPage = useRef(0);
  shownPage.current = page;
  // Tiles read their image only while this page is on screen; leaving the page retires the reads.
  const tileCurrent = useRef<() => boolean>(() => false);
  tileCurrent.current = () => live.current === scope && open && mode === 'grid';
  const tileLive = useCallback(() => tileCurrent.current(), []);
  // Exclusions belong to the page, so a trip to the list (or a tile's '열기') keeps them; what has shown is read again
  // whenever the tiles come back.
  useEffect(() => setExcluded(new Set()), [page, pageSize, scope]);
  useEffect(() => setLoadedOnPage(new Set()), [page, mode, pageSize, scope]);
  useEffect(() => { if (page > 0 && page >= grid.pages.length) setPage(Math.max(0, grid.pages.length - 1)); }, [grid.pages.length, page]);

  // Another panel ('모델 보조') asked for one draft: open the popover on it, with only the candidates kept there. A
  // request from another project or label set is dropped. A draft the list lacks may be newer than the list, so it is
  // read once more; still missing after that read, the request ends with a notice. Every request is cleared once
  // handled, so coming back to step 2 never replays it.
  useEffect(() => {
    if (!openRequest) return;
    if (openRequest.scope !== scope) { clearOpenRequest(); return; }
    if (lastRead.current.scope !== scope) return; // this scope's list has not landed yet; its landing runs this again
    const draft = drafts.find((row) => row.id === openRequest.id);
    if (!draft) {
      const retry = retriedOpen.current;
      if (!retry || retry.nonce !== openRequest.nonce) {
        retriedOpen.current = { nonce: openRequest.nonce, after: lastRead.current.count };
        setReloads((value) => value + 1);
        return;
      }
      if (lastRead.current.count <= retry.after) return; // the extra read has not landed yet
      clearOpenRequest();
      setOpen(true);
      setSelectedId('');
      setError(null);
      setNotice(OPEN_REQUEST_MISSING_TEXT);
      return;
    }
    clearOpenRequest();
    setOpen(true);
    setMode('list');
    if (draft.status !== 'pending') { setSelectedId(''); setError(null); setNotice(closedDraftText(draft)); return; }
    choose(draft);
    if (openRequest.candidateIds) {
      const kept = new Set(openRequest.candidateIds.filter((id) => draft.candidates.some((candidate) => candidate.id === id)));
      setChosen(kept);
      setChoices((previous) => new Map(previous).set(draft.id, kept));
    }
  }, [openRequest, drafts, scope]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (draft: LabelSuggestion) => {
    setSelectedId(draft.id);
    setChosen(choiceFor(draft));
    setViewed((previous) => previous.has(draft.id) ? previous : new Set(previous).add(draft.id));
    setError(null);
    setNotice(null);
  };

  const chooseCandidate = (candidateId: string, checked: boolean) => {
    if (!selected) return;
    const next = new Set(chosen);
    if (checked) next.add(candidateId); else next.delete(candidateId);
    setChosen(next);
    setChoices((previous) => new Map(previous).set(selected.id, next));
  };

  // Show the draft's image on the canvas too (current edits are saved first, as with any image change).
  const openDraft = async (draft: LabelSuggestion) => {
    choose(draft);
    if (useAnnotationStore.getState().currentImage?.file_path === draft.image_path) return;
    const opened = await useProjectStore.getState().openImageForLabeling(stem(draft.image_path), draft.image_path);
    if (!opened && live.current === scope) setError('현재 이미지의 수정 내용을 저장하지 못해 초안 이미지로 이동하지 않았습니다.');
  };

  const openNext = async () => {
    const index = pending.findIndex((draft) => draft.id === selectedId);
    const next = pending[index + 1] ?? pending.find((draft) => draft.id !== selectedId);
    if (next) await openDraft(next);
  };

  const openBatchEntry = async (entry: LabelSuggestionBatchEntry) => {
    if (!entry.proposal_id) return;
    const known = drafts.find((draft) => draft.id === entry.proposal_id)
      || await api.labelSuggestions.list(entry.image_path).then((result) => result.suggestions.find((draft) => draft.id === entry.proposal_id));
    if (known && live.current === scope) await openDraft(known);
  };

  // After the server changed labels: re-read the open image when it is one that changed, then the counts and drafts
  // (the evidence bump makes this panel read again).
  const afterLabelChanges = async (changed: LabelSuggestion[], startedScope: string) => {
    const annotation = useAnnotationStore.getState();
    if (changed.some((draft) => annotation.currentImage?.file_path === draft.image_path)) {
      await annotation.loadAnnotationsForCurrent();
      // Undo must not bring back the labels as they were before this server change.
      useAnnotationStore.setState({ history: [], future: [] });
    }
    if (live.current !== startedScope) return;
    await useDatasetStore.getState().annotationsChanged();
    bumpWorkflowEvidence();
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
      let updated: LabelSuggestion;
      if (decision === 'accept') {
        const lease = await leaseFor(selected, reviewerName);
        try { updated = await datasetWorkflow.review(selected.id, decision, [...chosen], reviewerName, lease.token); } finally { await lease.release(); }
      } else {
        updated = await datasetWorkflow.review(selected.id, decision, [], reviewerName);
      }
      if (live.current !== startedScope) return;
      setDrafts((previous) => previous.map((draft) => draft.id === updated.id ? updated : draft));
      setPicks((previous) => { const next = new Set(previous); next.delete(updated.id); return next; });
      if (decision === 'accept') {
        await afterLabelChanges([updated], startedScope);
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

  // Drafts that found nothing are closed together: each is recorded as rejected under the reviewer (labels unchanged),
  // so the list and the footer count only drafts with something to review.
  const closeEmpty = async () => {
    if (!empty.length) return;
    if (!reviewerName.trim()) { setError('검토자 이름을 입력하세요.'); return; }
    const startedScope = scope;
    beginRun();
    setBusy('reject');
    setError(null);
    setNotice(null);
    let closed = 0;
    try {
      for (const draft of empty) {
        if (live.current !== startedScope) return;
        await datasetWorkflow.review(draft.id, 'reject', [], reviewerName);
        closed += 1;
      }
      setNotice(`후보 없는 초안 ${closed}건을 닫았습니다. 라벨은 바뀌지 않았습니다. 그 이미지들은 기준 신뢰도를 낮춰 다시 만들거나 직접 라벨하세요.`);
    } catch (cause) {
      if (live.current === startedScope) setError(draftError(cause));
    } finally {
      endRun();
      if (live.current === startedScope) { setBusy(null); setReloads((value) => value + 1); }
    }
  };

  // Several drafts of one batch, one after another: each adoption keeps its own backup and origin, so each can still
  // be taken back on its own. The loop stops when the server says the batch's data changed (the rest would be
  // refused the same way) and goes on past a refusal that concerns one image only.
  const adoptDrafts = async (targets: LabelSuggestion[]) => {
    if (!targets.length) return;
    if (!reviewerName.trim()) { setError('검토자 이름을 입력하세요.'); return; }
    const grouping = multiAdoptionRefusal(targets);
    if (grouping) { setError(grouping); return; }
    const unseen = targets.filter((draft) => !viewed.has(draft.id)).length;
    if (unseen) { setError(`미리보기로 확인하지 않은 초안 ${unseen}건이 있습니다. 열어서 확인한 뒤 채택하세요.`); return; }
    if (targets.some(sameImageDirty)) { setError('이 이미지의 수정 내용을 먼저 저장한 뒤 채택하세요.'); return; }
    const startedScope = scope;
    beginRun();
    setBusy('accept');
    setError(null);
    setNotice(null);
    setProgress({ done: 0, total: targets.length });
    const results: AdoptionResult[] = [];
    const adopted: LabelSuggestion[] = [];
    let stopped = false;
    try {
      for (const draft of targets) {
        if (live.current !== startedScope) return;
        const candidateIds = [...choiceFor(draft)];
        try {
          const lease = await leaseFor(draft, reviewerName);
          let updated: LabelSuggestion;
          try { updated = await datasetWorkflow.review(draft.id, 'accept', candidateIds, reviewerName, lease.token); } finally { await lease.release(); }
          if (live.current !== startedScope) return;
          setDrafts((previous) => previous.map((row) => row.id === updated.id ? updated : row));
          adopted.push(updated);
          results.push({ draft, adopted: updated.accepted_candidate_ids.length });
        } catch (cause) {
          results.push({ draft, adopted: 0, error: draftError(cause) });
          if (staleBatchRefusal(cause)) { stopped = true; break; }
        }
        setProgress({ done: results.length, total: targets.length });
      }
      if (live.current !== startedScope) return;
      setPicks((previous) => { const next = new Set(previous); for (const row of adopted) next.delete(row.id); return next; });
      if (adopted.some((row) => row.id === selectedId)) setSelectedId('');
      if (adopted.length) await afterLabelChanges(adopted, startedScope);
      if (live.current !== startedScope) return;
      setNotice(adoptionSummary(results, stopped));
    } finally {
      endRun();
      if (live.current === startedScope) { setBusy(null); setProgress(null); }
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
      const lease = await leaseFor(draft, reviewerName);
      let updated: LabelSuggestion;
      try { updated = await datasetWorkflow.undoAdoption(draft.id, reviewerName, lease.token); } finally { await lease.release(); }
      if (live.current !== startedScope) return;
      setDrafts((previous) => previous.map((item) => item.id === updated.id ? updated : item));
      await afterLabelChanges([updated], startedScope);
      setNotice(`${fileName(draft.image_path)}에서 이 채택의 도형만 뺐습니다. 되돌리기 직전 데이터 버전 ${updated.revert_backup_version_id}.`);
    } catch (cause) {
      if (live.current === startedScope) setError(draftError(cause));
    } finally {
      endRun();
      if (live.current === startedScope) setBusy(null);
    }
  };

  const adoptPicked = () => adoptDrafts(picked);
  const adoptPage = () => adoptDrafts(pageDrafts.filter((draft) => !excluded.has(draft.id)));
  const tileLoaded = (id: string) => {
    setLoadedOnPage((previous) => previous.has(id) ? previous : new Set(previous).add(id));
    setViewed((previous) => previous.has(id) ? previous : new Set(previous).add(id));
  };
  const pickAllViewed = () => setPicks(new Set(pending.filter((draft) => viewed.has(draft.id) && adoptable(draft)).map((draft) => draft.id)));

  if (!projectDir || !project?.source_dataset_dir) return null;
  return (
    // Open, the row sits above the rows below it so the panel's header and close button are not covered.
    <div ref={rowRef} className={`relative ${open ? 'z-[75]' : 'z-[68]'} flex h-10 shrink-0 items-center justify-between border-b border-[#2B3547] bg-[#0F1A24] px-4 text-xs text-slate-300`}>
      <div className="flex min-w-0 items-center gap-2">
        <ClipboardCheck className="h-4 w-4 text-emerald-400" />
        <span className="font-semibold text-slate-100">초안 검토</span>
        <span className="truncate text-slate-500">모델이 만든 라벨 초안은 사람이 채택해야 라벨이 됩니다</span>
      </div>
      {/* The footer's '초안 만들기' lands on the section inside this popover: it names this button as its opener. */}
      <button id="draft-review-queue" type="button" onClick={toggle} aria-expanded={open} aria-label="초안 검토 패널" data-workflow-opens="draft-create" data-workflow-open-on-focus
        className="ml-3 flex shrink-0 items-center gap-1.5 rounded-md border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 font-semibold text-emerald-200 hover:bg-emerald-900/50">
        검토 대기 {waiting}장
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <aside style={{ maxHeight: room }} onKeyDown={(event) => event.stopPropagation()} className={`absolute right-3 top-full z-50 mt-1 flex max-h-[calc(100vh-195px)] ${wide ? 'w-[min(1200px,calc(100vw-24px))]' : 'w-[540px]'} max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-xl border border-slate-600 bg-[#151D2A] shadow-2xl`} aria-label="초안 검토">
          <div className="flex items-start justify-between border-b border-slate-700 px-4 py-3">
            <div>
              <h3 className="text-sm font-bold text-white">라벨 초안 검토</h3>
              <p className="mt-0.5 text-[11px] text-slate-400">검토 대기 {waiting}장 · 채택은 기존 라벨에 덧붙이고, 채택 직전 데이터 버전을 자동으로 남깁니다</p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button type="button" onClick={() => setWide((value) => !value)} aria-pressed={wide} className="rounded border border-slate-600 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-700">{wide ? '좁게 보기' : '넓게 보기'}</button>
              <button type="button" onClick={() => setOpen(false)} aria-label="초안 검토 닫기" className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"><X className="h-4 w-4" /></button>
            </div>
          </div>
          <div className="space-y-4 overflow-y-auto p-4">
            {error && <p role="alert" className="rounded border border-red-800 bg-red-950/40 p-2 text-xs text-red-200">{error}</p>}
            {notice && <p role="status" className="rounded border border-emerald-800 bg-emerald-950/30 p-2 text-xs text-emerald-200">{notice}</p>}

            <section id="draft-create" tabIndex={-1} className="space-y-2 rounded-lg border border-slate-700 bg-[#101722] p-3" aria-label="초안 만들기">
              <h4 className="font-semibold text-slate-100">1. 초안 만들기</h4>
              {models.length === 0 ? <>
                {/* Design 3.3 ③: with no trained model, a phrase drafts the unlabeled images (detection and segmentation). */}
                {(task === 'detection' || task === 'segmentation') && <PhraseDraftCreate key={scope} task={task} disabled={!!busy || isDirty} pendingImages={pendingImagePaths} onRunningChange={setBatchRunning} />}
                <p className="text-[11px] text-slate-400">이 데이터로 학습을 마친 모델이 없습니다. 몇 장을 직접 라벨한 뒤 3단계에서 학습하면, 남은 이미지의 초안을 학습한 모델로도 만들 수 있습니다.</p>
              </> : <>
                <label className="block text-[11px] text-slate-400">초안을 만들 모델
                  <select aria-label="초안 모델" value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={!!busy || batchRunning} className="mt-1 w-full rounded border border-slate-600 bg-[#1D2938] px-2 py-2 text-xs text-slate-100 disabled:opacity-50">
                    {models.map((model) => <option value={model.job_id} key={model.job_id}>{model.job_id} · {model.task}{model.data_state === 'changed' ? ` · ${CHANGED_MODEL_MARK}` : ''}</option>)}
                  </select>
                </label>
                {selectedModel?.data_state === 'changed' && <p className="text-[11px] text-amber-200">{CHANGED_MODEL_MARK} — 초안만 만들 수 있고, 채택할 때의 검사는 그대로입니다.</p>}
                {stats && <p role="status" aria-label="모델 채택률" title={adoptionTitle(modelRow)} className="text-[11px] text-slate-400">
                  {adoptionLine(modelRow)}{thresholdAdvice(modelRow) ? ` ${thresholdAdvice(modelRow)}` : ''}
                  {modelRow?.advice.reason === 'suggestion' && modelRow.advice.threshold != null && <button type="button" onClick={() => setThreshold(modelRow.advice.threshold as number)} disabled={!!busy || batchRunning} className="ml-2 rounded border border-slate-600 px-1.5 py-0.5 text-slate-200 hover:bg-slate-700 disabled:opacity-40">기준 {(modelRow.advice.threshold as number).toFixed(2)} 적용</button>}
                </p>}
                <label className="block text-[11px] text-slate-400">후보 기준 신뢰도 <span className="font-mono text-cyan-300">{threshold.toFixed(2)}</span>
                  <input aria-label="초안 기준 신뢰도" type="range" min="0.05" max="0.95" step="0.05" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} disabled={!!busy || batchRunning} className="mt-1 block w-full accent-cyan-500" />
                </label>
                <BulkLabelAssist execution={EXECUTION} projectDir={projectDir} modelId={modelId} threshold={threshold} keywords={[]}
                  disabled={!!busy || isDirty} onOpenEntry={openBatchEntry}
                  onRunningChange={setBatchRunning} />
                <Explain text={`초안을 채택하면 데이터가 바뀝니다. 그 뒤에도 이 모델로 초안을 만들 수 있고, '${CHANGED_MODEL_MARK}'으로 표시됩니다(3단계 '빠른 이어 학습'으로 모델을 맞추면 초안이 더 정확해집니다). 같은 묶음으로 만든 초안은 이어서 채택할 수 있으니 남은 이미지를 한 번에 만드세요. 라벨을 직접 고쳐 저장하면 남은 초안은 채택할 수 없게 되니, 남은 초안을 먼저 검토한 뒤 고치세요.`} />
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
              {empty.length > 0 && <div className="flex flex-wrap items-center gap-2 rounded border border-slate-700 bg-slate-900/40 p-2 text-[11px] text-slate-300">
                <span>후보 없는 초안 {empty.length}건 · 검토할 것이 없어 대기 수에서 뺐습니다.</span>
                <button type="button" className="workspace-button" disabled={!!busy || batchRunning || !reviewerName.trim()} onClick={() => void closeEmpty()}
                  title={reviewerName.trim() ? '거절로 기록하고 닫습니다. 라벨은 바뀌지 않습니다.' : '검토자 이름을 입력하면 닫을 수 있습니다.'}>모두 닫기</button>
              </div>}
              <p className="text-[11px] text-slate-500">순서 기준: {DRAFT_ORDER_TEXT[order]}{sourcesMixed(drafts) ? ` · ${MIXED_SOURCE_NOTE}` : ''}</p>
              {pending.length > 1 && <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-slate-400">보기</span>
                <button type="button" aria-pressed={mode === 'list'} onClick={() => setMode('list')} className={`rounded border px-2 py-0.5 ${mode === 'list' ? 'border-cyan-500 bg-cyan-950/40 text-cyan-100' : 'border-slate-600 text-slate-200 hover:bg-slate-700'}`}>목록</button>
                <button type="button" aria-pressed={mode === 'grid'} onClick={() => { setMode('grid'); setWide(true); }} className={`rounded border px-2 py-0.5 ${mode === 'grid' ? 'border-cyan-500 bg-cyan-950/40 text-cyan-100' : 'border-slate-600 text-slate-200 hover:bg-slate-700'}`}>격자 (한눈 검토)</button>
                {mode === 'grid' && <select aria-label="격자 한 페이지 장수" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value) as 12 | 24); setPage(0); }} className="rounded border border-slate-600 bg-[#1D2938] px-2 py-0.5 text-slate-100">
                  <option value={12}>12장씩</option><option value={24}>24장씩</option>
                </select>}
              </div>}
              {pending.length === 0 && <p className="rounded border border-slate-700 p-3 text-center text-slate-400">검토할 초안이 없습니다.</p>}
              {mode === 'grid' && pending.length > 0 && <div className="space-y-2 rounded-lg border border-emerald-900 bg-[#0F1722] p-3" aria-label="격자 검토">
                {grid.pages.length === 0 ? <p className="text-[11px] text-slate-400">격자로 볼 묶음이 없습니다. 아래 목록에서 하나씩 채택하세요.</p> : <>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-300">
                    <span>페이지 {Math.min(page, grid.pages.length - 1) + 1} / {grid.pages.length} · 묶음 {pageDrafts[0]?.batch_id?.slice(-8) ?? '-'} · 이 페이지 {pageDrafts.length}장 · 보인 것 {pageDrafts.filter((draft) => loadedOnPage.has(draft.id)).length}장</span>
                    <span className="flex items-center gap-1">
                      <button type="button" aria-label="이전 페이지" onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page <= 0 || !!busy} className="rounded border border-slate-600 p-1 text-slate-200 hover:bg-slate-700 disabled:opacity-40"><ChevronLeft className="h-3.5 w-3.5" /></button>
                      <button type="button" aria-label="다음 페이지" onClick={() => setPage((value) => Math.min(grid.pages.length - 1, value + 1))} disabled={page >= grid.pages.length - 1 || !!busy} className="rounded border border-slate-600 p-1 text-slate-200 hover:bg-slate-700 disabled:opacity-40"><ChevronRight className="h-3.5 w-3.5" /></button>
                    </span>
                  </div>
                  <Explain text="한 페이지는 한 묶음입니다. 타일을 눌러 이 페이지 채택에서 빼거나 넣고, 모든 타일이 보인 뒤 한 번에 채택합니다. '열기'는 캔버스에서 한 장씩 봅니다." />
                  <DraftGrid drafts={pageDrafts} choices={new Map(pageDrafts.map((draft) => [draft.id, choiceFor(draft)]))} excluded={excluded} loaded={loadedOnPage} authority={authority} context={getProjectContext()} current={tileLive} disabled={busy === 'accept' || busy === 'reject' || busy === 'undo'}
                    onToggle={(id) => setExcluded((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; })}
                    onLoaded={tileLoaded} onOpen={(draft) => void openDraft(draft)} />
                  {pageBlock && <p className="text-[11px] text-amber-300">{pageBlock}</p>}
                  {progress && <p role="status" className="text-[11px] text-cyan-200">채택 중 {progress.done} / {progress.total}</p>}
                  <label className="block text-[11px]">검토자<input aria-label="초안 검토자 이름" value={reviewerName} onChange={(event) => setReviewerName(event.target.value)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5" /></label>
                  <button type="button" onClick={() => void adoptPage()} disabled={!!busy || batchRunning || !!pageBlock} title={pageBlock ?? undefined}
                    className="flex w-full items-center justify-center gap-1 rounded bg-emerald-700 px-3 py-2 font-semibold text-white hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-40">
                    {progress ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCheck className="h-3.5 w-3.5" />} 이 페이지 채택 ({pageDrafts.filter((draft) => !excluded.has(draft.id)).length}장, 덧붙이기)
                  </button>
                  {grid.singles.length > 0 && <p className="text-[11px] text-slate-500">격자에 들어가지 않는 초안 {grid.singles.length}건(묶음 없음·후보 없음·같은 이미지)은 아래 목록에서 하나씩 채택하세요.</p>}
                </>}
              </div>}
              {pending.length > 1 && <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                <span>여러 장을 한꺼번에 채택하려면 초안을 열어 미리보기로 확인한 뒤 왼쪽 상자를 고르세요. 본 것 {pending.filter((draft) => viewed.has(draft.id)).length}건 · 고른 것 {picked.length}건</span>
                <button type="button" onClick={pickAllViewed} disabled={!!busy} className="rounded border border-slate-600 px-2 py-0.5 text-slate-200 hover:bg-slate-700 disabled:opacity-40">본 초안 모두 고르기</button>
                {picked.length > 0 && <button type="button" onClick={() => setPicks(new Set())} disabled={!!busy} className="rounded border border-slate-600 px-2 py-0.5 text-slate-200 hover:bg-slate-700 disabled:opacity-40">고른 것 해제</button>}
              </div>}
              <ul className="max-h-40 space-y-1 overflow-y-auto">
                {pending.map((draft) => <li key={draft.id} className="flex items-center gap-1.5">
                  <input type="checkbox" aria-label={`${fileName(draft.image_path)} 한꺼번에 채택에 포함`} checked={picks.has(draft.id)} disabled={!!busy || !viewed.has(draft.id) || !adoptable(draft)}
                    title={!adoptable(draft) ? '후보가 없어 채택할 수 없습니다. 거절하세요.' : viewed.has(draft.id) ? undefined : '미리보기로 확인한 뒤 고를 수 있습니다'} className="accent-emerald-500 disabled:opacity-40"
                    onChange={(event) => setPicks((previous) => { const next = new Set(previous); if (event.target.checked) next.add(draft.id); else next.delete(draft.id); return next; })} />
                  <button type="button" onClick={() => void openDraft(draft)} aria-pressed={draft.id === selectedId}
                    className={`flex min-w-0 grow items-center justify-between gap-2 rounded border px-2 py-1.5 text-left ${draft.id === selectedId ? 'border-cyan-500 bg-cyan-900/30 text-cyan-100' : 'border-slate-700 bg-slate-800/50 text-slate-200 hover:border-slate-500'}`}>
                    <span className="min-w-0 truncate">{fileName(draft.image_path)}{viewed.has(draft.id) && draft.id !== selectedId ? <span className="ml-1 text-[10px] text-slate-500">· 봄</span> : null}</span>
                    <span className="shrink-0 text-[11px] text-slate-400"><span className="mr-1 rounded bg-slate-700 px-1 text-[10px] text-slate-300">{draftSourceText(draft)}</span>후보 {draft.candidates.length}개 · 최고 {Math.round(topConfidence(draft) * 100)}%</span>
                  </button>
                </li>)}
              </ul>
              {selected && selected.status === 'pending' && <div className="space-y-2 rounded-lg border border-cyan-900 bg-[#0F1722] p-3" aria-label="선택한 초안">
                <SuggestionPreview proposal={selected} selectedIds={chosen} />
                <p className="text-[11px] text-slate-400">출처: {draftSourceText(selected)}{supportLimitsText(selected.support_limits) ? <span className="ml-2 text-amber-200">{supportLimitsText(selected.support_limits)}</span> : null}</p>
                <div className="max-h-36 space-y-1 overflow-y-auto">
                  {selected.candidates.length === 0 && <p className="rounded border border-slate-700 p-2 text-center text-slate-400">이 이미지에는 후보가 없습니다. 거절하고 직접 라벨하세요.</p>}
                  {selected.candidates.map((candidate) => <label key={candidate.id} className="flex items-center gap-2 rounded border border-slate-700 bg-slate-800/40 p-1.5">
                    <input type="checkbox" checked={chosen.has(candidate.id)} disabled={!!busy} onChange={(event) => chooseCandidate(candidate.id, event.target.checked)} className="accent-cyan-500" />
                    <span className="grow text-slate-100">{candidate.annotation.label} <span className="text-[10px] text-slate-400">{candidate.annotation.type}</span>{unknownClassCandidate(selected, candidate.annotation.label, knownClasses) && <span className="ml-1 text-[11px] text-amber-200">{UNKNOWN_CLASS_MARK}</span>}</span>
                    <span className="font-mono tabular-nums text-cyan-300">{Math.round(candidate.confidence * 100)}%</span>
                  </label>)}
                </div>
                {!gridShown && <label className="block text-[11px]">검토자<input aria-label="초안 검토자 이름" value={reviewerName} onChange={(event) => setReviewerName(event.target.value)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5" /></label>}
                <div className="flex gap-2">
                  <button type="button" onClick={() => void review('reject')} disabled={!!busy} className="flex grow items-center justify-center gap-1 rounded border border-slate-600 px-3 py-2 font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-40"><X className="h-3.5 w-3.5" /> 거절</button>
                  <button type="button" onClick={() => void review('accept')} disabled={!!busy || batchRunning || chosen.size === 0} className={`flex grow items-center justify-center gap-1 rounded px-3 py-2 font-semibold ${adoptStyle} disabled:cursor-not-allowed disabled:opacity-40`}>{busy === 'accept' && !progress ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 선택 후보 채택 (덧붙이기)</button>
                  {pending.length > 1 && <button type="button" onClick={() => void openNext()} disabled={!!busy} aria-label="다음 초안 보기" title="다음 초안을 미리보기로 엽니다" className="flex items-center gap-1 rounded border border-slate-600 px-2 py-2 font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-40"><ChevronRight className="h-3.5 w-3.5" /> 다음</button>}
                </div>
                {batchRunning && <p className="text-[11px] text-amber-300">초안을 만드는 중에는 채택할 수 없습니다. 끝난 뒤 채택하세요.</p>}
              </div>}
              {picked.length > 0 && <div className="space-y-2 rounded-lg border border-emerald-900 bg-[#0F1722] p-3" aria-label="고른 초안 한꺼번에 채택">
                <p className="text-[11px] text-slate-300">고른 초안 {picked.length}건을 차례로 채택합니다. 채택마다 데이터 버전을 남기고, 하나씩 되돌릴 수 있습니다.</p>
                {refusal && <p className="text-[11px] text-amber-300">{refusal}</p>}
                {!refusal && unseenPicked > 0 && <p className="text-[11px] text-amber-300">미리보기로 확인하지 않은 초안 {unseenPicked}건이 있습니다.</p>}
                {progress && <p role="status" className="text-[11px] text-cyan-200">채택 중 {progress.done} / {progress.total}</p>}
                <button type="button" onClick={() => void adoptPicked()} disabled={!!busy || batchRunning || !!refusal || unseenPicked > 0 || !reviewerName.trim()}
                  className={`flex w-full items-center justify-center gap-1 rounded px-3 py-2 font-semibold ${gridShown ? adoptStyle : 'bg-emerald-700 text-white hover:bg-emerald-600'} disabled:cursor-not-allowed disabled:opacity-40`}>
                  {progress ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCheck className="h-3.5 w-3.5" />} 고른 초안 {picked.length}건 채택 (덧붙이기)
                </button>
                {!reviewerName.trim() && <p className="text-[11px] text-slate-400">{gridShown ? '검토자 이름은 위 격자의 검토자 칸에 입력합니다.' : '검토자 이름은 초안을 연 뒤 입력합니다.'}</p>}
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
              <Explain text="되돌리기는 이 채택으로 더한 도형만 뺍니다. 그 도형을 고쳤다면 되돌리지 않고, 채택 전에 남긴 데이터 버전으로 복원하도록 안내합니다." />
            </section>
          </div>
        </aside>
      )}
    </div>
  );
};

export default DraftReviewQueue;
