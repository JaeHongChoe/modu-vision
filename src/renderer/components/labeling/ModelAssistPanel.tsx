import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Cpu, Loader2, RefreshCw, Sparkles, X } from 'lucide-react';
import { api, type LabelSuggestion, type LabelSuggestionBatchEntry, type LabelSuggestionCandidate, type LabelSuggestionModel } from '../../services/api';
import type { ImageMeta } from '../../types';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { draftReviewScope, useDraftReviewStore } from '../../stores/useDraftReviewStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useModelAssistRunStore } from '../../stores/useModelAssistRunStore';
import {LabelAssistDeviceSizes,type LabelAssistExecution} from './LabelAssistDeviceSizes';
import { BulkLabelAssist } from './BulkLabelAssist';
import {KoreanConditionLabeler} from './KoreanConditionLabeler';
import { CandidateProviderControls } from './CandidateProviderControls';
import type { FoundationProposal } from '../../services/foundationLabelingApi';
import { datasetWorkflow, type CandidateProposal } from '../../services/datasetWorkflow';
import { SuggestionPreview } from './SuggestionPreview';
import { CHANGED_MODEL_MARK, draftError } from './draftReview';
import { usePopoverRoom } from './popoverRoom';
import { Explain } from '../common/Explain';
import {startFoundationQuickPointController} from './foundationQuickPointController';

// The server's refusals in the draft queue's words (an older English refusal still reads in Korean).
const errorText = draftError;

function candidateDetail(candidate: LabelSuggestionCandidate): string {
  const annotation = candidate.annotation;
  if (annotation.type === 'bbox' && annotation.bbox) {
    const [x1, y1, x2, y2] = annotation.bbox;
    return `X ${Math.round(x1)} · Y ${Math.round(y1)} · ${Math.round(x2 - x1)} × ${Math.round(y2 - y1)} px`;
  }
  if (annotation.type === 'brush_mask') return '원본 픽셀 mask · 브러시/지우개로 편집 가능';
  if (annotation.type === 'polygon') return `${annotation.polygon?.length || 0}개 꼭짓점`;
  return annotation.is_normal ? '정상 이미지 태그' : '이미지 전체 태그';
}

function currentAssistContext(): string {
  const state = useProjectStore.getState();
  return JSON.stringify([state.projectDir, useAnnotationStore.getState().currentImage?.file_path,
    state.project?.active_labelset_id || 'default', state.project?.task, state.project?.source_dataset_dir]);
}

export const ModelAssistPanel: React.FC = () => {
  const image = useAnnotationStore((state) => state.currentImage);
  const isDirty = useAnnotationStore((state) => state.isDirty);
  const annotationLoadStatus = useAnnotationStore((state) => state.annotationLoadStatus);
  const projectDir = useProjectStore((state) => state.projectDir);
  const labelsetId = useProjectStore((state) => state.project?.active_labelset_id || 'default');
  const task = useProjectStore((state) => state.project?.task);
  const sourceDatasetDir = useProjectStore((state) => state.project?.source_dataset_dir);
  const beginModelAssist = useModelAssistRunStore((state) => state.begin);
  const endModelAssist = useModelAssistRunStore((state) => state.end);
  const [open, setOpen] = useState(false);
  // The review panel hangs below this row over the canvas: like the draft queue's, it keeps to the room left on screen.
  const rowRef = useRef<HTMLDivElement>(null);
  const room = usePopoverRoom(open, rowRef);
  // One right-side popover at a time (see useDraftReviewStore.sidePanel).
  const sidePanel = useDraftReviewStore((state) => state.sidePanel);
  useEffect(() => { if (open) useDraftReviewStore.getState().showSidePanel('assist'); }, [open]);
  useEffect(() => { if (sidePanel && sidePanel !== 'assist') setOpen(false); }, [sidePanel]);
  const assistRequest = useDraftReviewStore((state) => state.assistRequest);
  useEffect(() => { if (assistRequest) { setOpen(true); useDraftReviewStore.getState().clearAssistRequest(); } }, [assistRequest]);
  const [models, setModels] = useState<LabelSuggestionModel[]>([]);
  const [modelId, setModelId] = useState('');
  const [threshold, setThreshold] = useState(0.5);
  const [execution,setExecution]=useState<LabelAssistExecution>({device:'cpu',min_area:0,min_width:0,min_height:0});
  const [keywords, setKeywords] = useState('');
  const reviewerName = useAnnotationStore(state => state.reviewerName);
  const setReviewerName = useAnnotationStore(state => state.setReviewerName);
  const [suggestions, setSuggestions] = useState<LabelSuggestion[]>([]);
  const [selectedProposalId, setSelectedProposalId] = useState('');
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(new Set());
  const [busyState, setBusy] = useState<'loading' | 'generate' | 'accept' | 'reject' | null>(null);
  const [quickRunning,setQuickRunning]=useState(false);
  const busy=busyState||(quickRunning?'generate':null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const requestedProposalId = useRef('');
  const operationId = useRef(0);
  const quickError=useRef<string|null>(null);
  const quickNotice=useRef<{context:string;proposalId:string;text:string}|null>(null);
  const quickBlocked=useRef(false);
  quickBlocked.current=!!busyState||batchRunning;
  // Closing the popover unmounts its controls. The point runner belongs to this labeling visit instead.
  useEffect(()=>startFoundationQuickPointController({
    isBlocked:()=>quickBlocked.current,onRunning:setQuickRunning,
    onError:cause=>{quickError.current=errorText(cause);quickNotice.current=null;setNotice(null);setError(quickError.current);setOpen(true);},
    onCreated:next=>{requestedProposalId.current=next.id;setSuggestions(previous=>[next,...previous]);
      setSelectedProposalId(next.id);setSelectedCandidateIds(new Set(next.candidates.map(candidate=>candidate.id)));
      const text=`후보 ${next.candidates.length}개를 만들었습니다. 검토 후 선택하세요.`;
      quickNotice.current={context:currentAssistContext(),proposalId:next.id,text};
      setError(null);setNotice(text);setOpen(true);},
  }),[]);

  const proposal = useMemo(() => suggestions.find((item) => item.id === selectedProposalId), [suggestions, selectedProposalId]);
  const pendingCount = suggestions.filter((item) => item.status === 'pending').length;
  const selectedModel = models.find((item) => item.job_id === modelId);

  useEffect(() => {
    const token = ++operationId.current;
    setBusy(null);
    setSuggestions([]);
    setSelectedProposalId('');
    setSelectedCandidateIds(new Set());
    setError(quickError.current);quickError.current=null;
    // Automatic opening refreshes persisted proposals. Carry its just-created notice through that one refresh,
    // only for the originating image/project/label set; closing or changing context consumes it.
    const pendingNotice=quickNotice.current;quickNotice.current=null;
    setNotice(open&&pendingNotice?.context===currentAssistContext()&&pendingNotice.proposalId===requestedProposalId.current?pendingNotice.text:null);
    if (!open || !projectDir) return;
    let cancelled = false;
    setBusy('loading');
    Promise.all([api.labelSuggestions.models(), image?.file_path
      ? api.labelSuggestions.list(image.file_path) : Promise.resolve({ suggestions: [] as LabelSuggestion[] })])
      .then(([modelResult, suggestionResult]) => {
        if (cancelled || operationId.current !== token) return;
        setModels(modelResult.models);
        setModelId((previous) => modelResult.models.some((item) => item.job_id === previous) ? previous : modelResult.models[0]?.job_id || '');
        setSuggestions(suggestionResult.suggestions);
        const first = suggestionResult.suggestions.find((item) => item.id === requestedProposalId.current)
          || suggestionResult.suggestions.find((item) => item.status === 'pending') || suggestionResult.suggestions[0];
        requestedProposalId.current = '';
        setSelectedProposalId(first?.id || '');
        setSelectedCandidateIds(new Set(first?.candidates.map((item) => item.id) || []));
      })
      .catch((cause) => { if (!cancelled && operationId.current === token) setError(errorText(cause)); })
      .finally(() => { if (!cancelled && operationId.current === token) setBusy(null); });
    return () => { cancelled = true; };
  }, [open, image?.file_path, projectDir, labelsetId, task, sourceDatasetDir]);

  const selectProposal = (item: LabelSuggestion) => {
    setSelectedProposalId(item.id);
    setSelectedCandidateIds(new Set(item.status === 'pending' ? item.candidates.map((candidate) => candidate.id) : item.accepted_candidate_ids));
    setError(null);
    setNotice(null);
  };

  const openBatchEntry = async (entry: LabelSuggestionBatchEntry) => {
    if (!entry.proposal_id || !projectDir) return;
    const startedProjectDir = projectDir;
    requestedProposalId.current = entry.proposal_id;
    const annotation = useAnnotationStore.getState();
    if (annotation.currentImage?.file_path === entry.image_path) {
      const selected = suggestions.find((item) => item.id === entry.proposal_id);
      if (selected) selectProposal(selected);
      else {
        const refreshed = await api.labelSuggestions.list(entry.image_path);
        if (useProjectStore.getState().projectDir !== startedProjectDir) return;
        setSuggestions(refreshed.suggestions);
        const target = refreshed.suggestions.find((item) => item.id === entry.proposal_id);
        if (target) selectProposal(target);
      }
      requestedProposalId.current = '';
      return;
    }
    const known = useDatasetStore.getState().images.find((item) => item.file_path === entry.image_path);
    const target: ImageMeta = known || {
      image_id: entry.image_id,
      file_name: entry.image_path.split(/[\\/]/).pop() || entry.image_id,
      file_path: entry.image_path,
      split: 'train',
      thumbnail_url: `/api/dataset/thumbnail/${encodeURIComponent(entry.image_id)}?file_path=${encodeURIComponent(entry.image_path)}&size=128`,
    };
    await annotation.setActiveImage(target);
    if (useProjectStore.getState().projectDir !== startedProjectDir) return;
    if (useAnnotationStore.getState().currentImage?.file_path !== entry.image_path) {
      setError('현재 이미지의 편집 내용을 저장하지 못해 검토 이미지로 이동하지 않았습니다.');
    }
  };

  const generate = async () => {
    if (!image || !modelId || !projectDir) return;
    const startedContext = currentAssistContext();
    const token = ++operationId.current;
    const sameContext = () => operationId.current === token && currentAssistContext() === startedContext;
    beginModelAssist();
    setBusy('generate');
    setError(null);
    setNotice(null);
    try {
      const next = await datasetWorkflow.generateModel(modelId, image.file_path, threshold, keywords.split(",").map(k => k.trim()).filter(Boolean),execution);
      if (!sameContext()) return;
      setSuggestions((previous) => [next, ...previous]);
      selectProposal(next);
      setNotice(next.candidates.length ? '제안을 만들었습니다. 항목을 확인하고 채택할 후보를 선택하세요.' : '제안된 라벨이 없습니다. 임계값이나 모델을 바꿔 다시 시도할 수 있습니다.');
    } catch (cause) {
      if (sameContext()) setError(errorText(cause));
    } finally {
      endModelAssist();
      // Gallery refresh may replace the same image object; only the operation that
      // owns the busy state may clear it, regardless of refreshed object identity.
      if (operationId.current === token) setBusy(null);
    }
  };

  const review = async (decision: 'accept' | 'reject') => {
    if (!proposal || !image || !projectDir) return;
    if (!reviewerName.trim()) { setError("작업자·검토자 이름을 입력하세요."); return; }
    const startedContext = currentAssistContext();
    const token = ++operationId.current;
    const sameContext = () => operationId.current === token && currentAssistContext() === startedContext;
    beginModelAssist();
    setBusy(decision);
    setError(null);
    setNotice(null);
    try {
      const updated = await datasetWorkflow.review(proposal.id, decision, [...selectedCandidateIds], reviewerName);
      if (!sameContext()) return;
      setSuggestions((previous) => previous.map((item) => item.id === updated.id ? updated : item));
      if (decision === 'accept') {
        await useAnnotationStore.getState().loadAnnotationsForCurrent();
        if (!sameContext()) return;
        await useDatasetStore.getState().annotationsChanged();
        if (!sameContext()) return;
        setNotice(`${updated.accepted_candidate_ids.length}개 후보를 채택했습니다. mask는 브러시/지우개, 다각형은 꼭짓점으로 편집하고 저장 후 이미지 검토에서 승인하세요. 채택 전 데이터 버전 ${updated.backup_version_id}이 저장되었습니다.`);
      } else {
        setNotice('제안을 거절했습니다. 라벨은 변경되지 않았습니다.');
      }
    } catch (cause) {
      if (sameContext()) setError(errorText(cause));
    } finally {
      endModelAssist();
      // Gallery refresh may replace the same image object; only the operation that
      // owns the busy state may clear it, regardless of refreshed object identity.
      if (operationId.current === token) setBusy(null);
    }
  };

  return (
    <div ref={rowRef} className="relative z-[70] flex h-10 shrink-0 items-center justify-between border-b border-[#2B3547] bg-[#101722] px-4 text-xs text-slate-300">
      <div className="flex min-w-0 items-center gap-2">
        <Sparkles className="h-4 w-4 text-cyan-400" />
        <span className="font-semibold text-slate-100">모델 보조 라벨링</span>
        <span className="truncate text-slate-500">완료된 모델의 예측을 검토한 뒤 선택해 채택</span>
      </div>
      <button type="button" onClick={() => setOpen((value) => !value)} disabled={!projectDir} aria-expanded={open} aria-label="모델 보조 라벨링 패널" className="ml-3 flex shrink-0 items-center gap-1.5 rounded-md border border-cyan-800 bg-cyan-950/40 px-3 py-1.5 font-semibold text-cyan-200 hover:bg-cyan-900/50 disabled:cursor-not-allowed disabled:opacity-40">
        <Cpu className="h-3.5 w-3.5" /> 제안 검토 {pendingCount > 0 && <span className="rounded bg-cyan-800 px-1.5 text-[10px]">{pendingCount}</span>}
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <aside style={{ maxHeight: room }} className="absolute right-3 top-full z-50 mt-1 flex max-h-[calc(100vh-195px)] w-[520px] max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-xl border border-slate-600 bg-[#151D2A] shadow-2xl" aria-label="모델 보조 라벨링 검토">
          <div className="flex items-start justify-between border-b border-slate-700 px-4 py-3">
            <div>
              <h3 className="text-sm font-bold text-white">예측 라벨 검토</h3>
              <p className="mt-0.5 text-[11px] text-slate-400">현재 이미지 · {image?.file_name || '선택 안 됨'}</p>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label="닫기" className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-white"><X className="h-4 w-4" /></button>
          </div>
          <div className="overflow-y-auto p-4 space-y-4">
            {error && <p role="alert" className="rounded border border-red-800 bg-red-950/40 p-2 text-xs text-red-200">{error}</p>}
            {notice && <p role="status" className="rounded border border-emerald-800 bg-emerald-950/30 p-2 text-xs text-emerald-200">{notice}</p>}
            <section className="space-y-2 rounded-lg border border-slate-700 bg-[#101722] p-3">
              <div className="flex items-center justify-between">
                <h4 className="font-semibold text-slate-100">1. 모델로 후보 생성</h4>
                {busy === 'loading' && <Loader2 className="h-4 w-4 animate-spin text-cyan-300" />}
              </div>
              <label className="block text-[11px] text-slate-400">완료된 학습 모델
                <select value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={!!busy} className="mt-1 w-full rounded border border-slate-600 bg-[#1D2938] px-2 py-2 text-xs text-slate-100 disabled:opacity-50">
                  {models.length === 0 && <option value="">이 데이터로 학습을 마친 모델 없음</option>}
                  {models.map((item) => <option value={item.job_id} key={item.job_id}>{item.job_id} · {item.task}{item.data_state === 'changed' ? ` · ${CHANGED_MODEL_MARK}` : ''}</option>)}
                </select>
              </label>
              {selectedModel?.data_state === 'changed' && <p className="text-[11px] text-amber-200">{CHANGED_MODEL_MARK} — 초안만 만들 수 있고, 채택할 때의 검사는 그대로입니다.</p>}
              <label className="block text-[11px] text-slate-400">클래스 키워드 필터 (선택 사항, 쉼표 구분)<input aria-label="모델 클래스 키워드" value={keywords} onChange={event => setKeywords(event.target.value)} placeholder="scratch, crack" className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2 text-slate-100" /></label>
              <p className="text-[10px] text-slate-500">실제 모델이 예측한 클래스 이름에 포함되는 키워드만 남깁니다.</p>
              <LabelAssistDeviceSizes value={execution} onChange={setExecution} disabled={!!busy||batchRunning}/>
              <div className="flex items-center gap-3">
                <label className="grow text-[11px] text-slate-400">검출 임계값 <span className="font-mono text-cyan-300">{threshold.toFixed(2)}</span>
                  <input type="range" min="0.05" max="0.95" step="0.05" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} disabled={!!busy || !modelId} className="mt-1 block w-full accent-cyan-500" />
                </label>
                <button type="button" onClick={() => void generate()} disabled={!!busy || batchRunning || !modelId || !image || isDirty || annotationLoadStatus !== 'ready'} className="flex items-center gap-1.5 rounded bg-cyan-600 px-3 py-2 font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40">
                  {busy === 'generate' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} 제안 생성
                </button>
              </div>
              {isDirty && <p className="text-[11px] text-amber-300">수정 중인 라벨을 저장한 뒤 제안을 생성하세요.</p>}
              {!models.length && busy !== 'loading' && <p className="text-[11px] text-slate-400">3단계에서 이 데이터와 작업 유형으로 학습을 마치면 모델을 선택할 수 있습니다.</p>}
              <p className="text-[11px] leading-relaxed text-slate-500">예측은 후보로만 저장됩니다. 기존 라벨과 원본 파일은 생성 시 변경되지 않습니다.</p>
            </section>

            <KoreanConditionLabeler disabled={isDirty||!!busy||batchRunning||annotationLoadStatus!=="ready"} onCreated={next=>{setSuggestions(previous=>[next,...previous]);selectProposal(next);setNotice(`${next.candidates.length}개 한국어 조건 후보를 만들었습니다. 선택 후 명시적으로 채택하세요.`);}}/>
            <CandidateProviderControls onOpenProposal={async(path,id)=>{ await openBatchEntry({image_path:path,image_id:path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/,''),proposal_id:id,status:'generated',candidate_count:0}); }} disabled={isDirty || !!busy || batchRunning || annotationLoadStatus !== "ready"} onCreated={next => { setSuggestions(previous => [next, ...previous]); selectProposal(next); setNotice(`후보 ${next.candidates.length}개를 만들었습니다. 검토 후 선택하세요.`); }} />

            {projectDir && <BulkLabelAssist execution={execution} projectDir={projectDir} modelId={modelId} threshold={threshold} keywords={keywords.split(",").map(k => k.trim()).filter(Boolean)}
              disabled={isDirty || !!busy || !useDatasetStore.getState().hasSelectedFolder}
              onOpenEntry={openBatchEntry} onRunningChange={setBatchRunning} />}

            {suggestions.length > 0 && <section className="space-y-2">
              <h4 className="font-semibold text-slate-100">현재 이미지 제안 검토</h4>
              <div className="flex max-w-full gap-1 overflow-x-auto pb-1">
                {suggestions.map((item) => <button key={item.id} type="button" onClick={() => selectProposal(item)} className={`shrink-0 rounded border px-2 py-1 text-[11px] ${item.id === selectedProposalId ? 'border-cyan-500 bg-cyan-900/40 text-cyan-100' : 'border-slate-700 bg-slate-800 text-slate-300 hover:border-slate-500'}`}>
                  {new Date(item.created_at).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })} · {item.status === 'pending' ? '검토 대기' : item.status === 'accepted' ? '채택' : '거절'}
                </button>)}
              </div>
              {proposal && <>
                <SuggestionPreview proposal={proposal} selectedIds={selectedCandidateIds} />
                <p className="text-[11px] text-slate-400">{({foundation:"SAM2 + DINOv3 / Grounding DINO",grounding_dino:"텍스트 검출 · Grounding DINO",template_match:"예시 템플릿 매칭",vlm:"한국어 조건 · 이미지 VLM",trained_model:"완료 학습 모델"} as Record<string,string>)[(proposal as CandidateProposal).backend || "trained_model"]} · {proposal.candidates.length}개 후보</p>
                {(proposal as CandidateProposal).support_limits && <p className="text-[10px] text-amber-200">{(proposal as CandidateProposal).support_limits}</p>}
                <details className="text-[10px] text-slate-400"><summary className="cursor-pointer">후보 출처·버전</summary><p className="break-all">라벨 세트 {(proposal as FoundationProposal).labelset_id||labelsetId} · 버전 {(proposal as FoundationProposal).labelset_version||'모델 제안 기록'}</p>{(proposal as FoundationProposal).candidates.map(c=><pre key={c.id} className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({id:c.id,source:c.source,area:c.area,provenance:c.provenance},null,2)}</pre>)}</details>
                <label className="block text-[11px]">검토자<input aria-label="후보 검토자 이름" value={reviewerName} onChange={e => setReviewerName(e.target.value)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5" /></label>
                <div className="max-h-44 space-y-1 overflow-y-auto">
                  {proposal.candidates.length === 0 && <p className="rounded border border-slate-700 p-3 text-center text-slate-400">이 이미지에는 제안된 라벨이 없습니다.</p>}
                  {proposal.candidates.map((candidate) => <label key={candidate.id} className={`flex items-start gap-2 rounded border p-2 ${selectedCandidateIds.has(candidate.id) ? 'border-cyan-800 bg-cyan-950/20' : 'border-slate-700 bg-slate-800/40'}`}>
                    <input type="checkbox" checked={selectedCandidateIds.has(candidate.id)} disabled={!!busy || proposal.status !== 'pending'} onChange={(event) => setSelectedCandidateIds((previous) => {
                      const next = new Set(previous);
                      if (event.target.checked) next.add(candidate.id); else next.delete(candidate.id);
                      return next;
                    })} className="mt-0.5 accent-cyan-500" />
                    <span className="min-w-0 grow"><span className="block font-semibold text-slate-100">{candidate.annotation.label} <span className="ml-1 rounded bg-slate-700 px-1 text-[10px] font-normal text-slate-300">{candidate.annotation.type}</span></span><span className="block truncate text-[11px] text-slate-400">{candidateDetail(candidate)}</span></span>
                    <span className="font-mono tabular-nums text-cyan-300">{Math.round(candidate.confidence * 100)}%</span>
                  </label>)}
                </div>
                {proposal.status === 'pending' ? <div className="flex gap-2">
                  <button type="button" onClick={() => void review('reject')} disabled={!!busy} className="flex grow items-center justify-center gap-1 rounded border border-slate-600 px-3 py-2 font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-40"><X className="h-3.5 w-3.5" /> 거절</button>
                  {/* Every source's drafts are adopted in the one draft queue (origin, backup, take-back, team lease), with the
                      candidates kept here and only in this project. */}
                  <button type="button" onClick={() => useDraftReviewStore.getState().requestOpen(proposal.id, [...selectedCandidateIds], draftReviewScope(useProjectStore.getState()))} disabled={!!busy} className="flex grow items-center justify-center gap-1 rounded bg-emerald-600 px-3 py-2 font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"><Check className="h-3.5 w-3.5" /> 초안 검토에서 채택</button>
                </div> : <p className="rounded border border-slate-700 p-2 text-center text-slate-300">{proposal.status === 'accepted' ? `채택 완료 · 이전 버전 ${proposal.backup_version_id}` : proposal.status === 'reverted' ? '채택을 되돌림' : '거절 완료'}</p>}
                {proposal.status === 'pending' && <Explain text="어디서 왔든 초안은 '초안 검토' 패널에서 채택합니다. 채택 직전에 라벨 버전이 자동 저장되고, 고른 후보만 현재 라벨에 덧붙습니다." />}
              </>}
            </section>}
          </div>
        </aside>
      )}
    </div>
  );
};

export default ModelAssistPanel;
