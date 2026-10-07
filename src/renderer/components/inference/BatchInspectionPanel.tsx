import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Download, History, Image as ImageIcon, Play, Save, Square, Layers } from 'lucide-react';
import { api, getApiBaseUrl, resolveApiUrl, type SavedFlowVersion } from '../../services/api';
import { flowExecutionOptions, type FlowExecutionChoice } from '../flowchart/flowExecution';
import { useComputeStore } from '../../stores/useComputeStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useInspectionRunStore } from '../../stores/useInspectionRunStore';
import type { FlowchartPipeline } from '../../types';
import { activeSavedVersion, savedFlowIdentity, type SavedFlowIdentity } from '../flowchart/flowHandoff';
import { SavedFlowIdentityCard } from '../flowchart/SavedFlowIdentityCard';
import { ReviewQueuePanel } from './ReviewQueuePanel';
import {useTaskHandoff} from '../training/useTaskHandoff';
import {openSelectedInspectionRun} from './inspectionHistorySelection';
import {EvidenceImageViewer} from '../common/EvidenceImageViewer';
import {inspectionEvidence, safeSnapshot, type EvidenceView} from '../common/evidenceViewer';
import {inspectionPreview,inspectionThumbnail} from './inspectionImage';
import {
  batchSourceResetKey, filterBatchRows, isBatchSourceCurrent, isBatchSourceReady,
  isInspectionHistoryContextCurrent, inspectionRunMatchesSource, createInspectionHistoryContext, createInspectionRunExitGuard,
  runBatchInspection, stopInspectionRunKeepalive, summarizeBatch,
  type BatchSourceState,
  type BatchFilter, type BatchInspectionReport, type BatchScope, type BatchStopReason,
  type InspectionHistoryRun, type InspectionRunSummary,
  type InspectionHistoryContext,
} from './batchInspection';

const scopeNames: Record<BatchScope, string> = {
  test: '테스트 분할', val: '검증 분할', train: '학습 분할', all: '전체 이미지',
};
const stateNames: Record<string, string> = {
  pending: '대기', running: '검사 중', skipped: '미실행',
  OK: 'OK', NG: 'NG', REVIEW: '검토', error: '실패',
};
const stateColors: Record<string, string> = {
  pending: 'text-slate-400 border-slate-600',
  running: 'text-cyan-300 border-cyan-600',
  skipped: 'text-slate-400 border-slate-600',
  OK: 'text-emerald-300 border-emerald-600',
  NG: 'text-rose-300 border-rose-600',
  REVIEW: 'text-amber-300 border-amber-600',
  error: 'text-red-300 border-red-600',
};
let activeBatchOperation = 0;

export const BatchInspectionPanel: React.FC = () => {
  const handoff=useTaskHandoff();const requestedRun=handoff?.kind==='inspection'?handoff.jobId:undefined;
  const executionChoiceOverride = useFlowchartStore(state => state.executionChoiceOverride);
  const setExecutionChoice = useFlowchartStore(state => state.setExecutionChoice);
  const { profiles, selectedProfileId, isLoaded: computeLoaded, load: loadCompute } = useComputeStore();
  const executionChoice = executionChoiceOverride || (selectedProfileId ? 'selected_compute' : 'local_cpu');
  const executionProfile = profiles.find(profile => profile.id === selectedProfileId);
  const executionReady = (computeLoaded || executionChoiceOverride !== null)
    && (executionChoice !== 'selected_compute' || Boolean(executionProfile));
  useEffect(() => { void loadCompute().catch(() => {}); }, [loadCompute]);
  const task = useProjectStore((state) => state.task);
  const projectDir = useProjectStore((state) => state.projectDir);
  const project = useProjectStore((state) => state.project);
  const setStep = useProjectStore((state) => state.setStep);
  const hasUnsavedDraft = useFlowchartStore((state) => state.pipelineDirty);
  const { folderPath, datasetKey, hasSelectedFolder, importError, sourceSaveError, isLoading: datasetIsLoading,
    isSplitting, split, sourceImages } = useDatasetStore();
  const contextRevision = useFlowchartStore((state) => state.contextRevision);
  const [scope, setScope] = useState<BatchScope>('test');
  const [report, setReport] = useState<BatchInspectionReport | null>(null);
  const [filter, setFilter] = useState<BatchFilter>('all');
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<InspectionRunSummary[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [savedFlow, setSavedFlow] = useState<{ version: SavedFlowVersion; pipeline: FlowchartPipeline; identity: SavedFlowIdentity } | null>(null);
  const [savedFlowLoading, setSavedFlowLoading] = useState(false);
  const [savedFlowError, setSavedFlowError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem('inspection-reviewer') || 'operator');
  const [reviewReason, setReviewReason] = useState('');
  const [reviewSaving, setReviewSaving] = useState(false);
  const [reviewQueueRevision, setReviewQueueRevision] = useState(0);
  const [evidenceView,setEvidenceView]=useState<{scope:string;view:EvidenceView}|null>(null);
  const evidenceRequest=useRef(0);
  const stopReasonRef = useRef<BatchStopReason>(null);
  const exitGuardRef = useRef<ReturnType<typeof createInspectionRunExitGuard> | null>(null);
  const ownedOperationRef = useRef<number | null>(null);
  const historyRequestRef = useRef(0);
  const reviewRequestRef = useRef(0);
  const currentBatchSource = (): BatchSourceState => {
    const dataset = useDatasetStore.getState();
    return {
      folderPath: dataset.folderPath,
      projectDir: useProjectStore.getState().projectDir,
      task: useProjectStore.getState().task,
      datasetKey: dataset.datasetKey,
      contextRevision: useFlowchartStore.getState().contextRevision,
      hasSelectedFolder: dataset.hasSelectedFolder,
      importError: dataset.importError,
      sourceSaveError: dataset.sourceSaveError,
      isLoading: dataset.isLoading,
      isSplitting: dataset.isSplitting,
    };
  };
  const renderedSource: BatchSourceState = {
    folderPath, projectDir, task, datasetKey, contextRevision, hasSelectedFolder,
    importError, sourceSaveError, isLoading: datasetIsLoading, isSplitting,
  };
  const sourceReady = isBatchSourceReady(renderedSource);
  const resetKey = batchSourceResetKey(renderedSource);
  const historyContext = createInspectionHistoryContext(renderedSource, project);
  const currentHistoryContext = () => createInspectionHistoryContext(
    currentBatchSource(), useProjectStore.getState().project,
  );

  useEffect(() => {
    const stopOwnedRun = () => {
      stopReasonRef.current = 'source_changed';
      exitGuardRef.current?.close();
    };
    window.addEventListener('pagehide', stopOwnedRun);
    return () => {
      window.removeEventListener('pagehide', stopOwnedRun);
      stopOwnedRun();
      if (ownedOperationRef.current !== null && ownedOperationRef.current === activeBatchOperation) {
        activeBatchOperation += 1;
        useInspectionRunStore.getState().setRunning(false);
      }
      ownedOperationRef.current = null;
    };
  }, []);

  useEffect(() => {
    setSavedFlow(null);
    setSavedFlowError(null);
    setSavedFlowLoading(false);
    if (!sourceReady) return;
    let active = true;
    setSavedFlowLoading(true);
    api.flowchart.listPipelines(folderPath).then(async ({ pipelines }) => {
      const version = activeSavedVersion(pipelines);
      if (!version) throw new Error('5단계에서 현재 검사 플로우를 저장하세요.');
      const pipeline = await api.flowchart.getPipelineVersion(version.version_id);
      const identity = await savedFlowIdentity(version, pipeline);
      if (active) setSavedFlow({ version, pipeline, identity });
    }).catch((cause) => {
      if (active) setSavedFlowError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => { if (active) setSavedFlowLoading(false); });
    return () => { active = false; };
  }, [resetKey]);

  useEffect(() => {
    historyRequestRef.current += 1;
    evidenceRequest.current++;setEvidenceView(null);
    reviewRequestRef.current += 1;
    stopReasonRef.current = 'source_changed';
    setStopRequested(false);
    setReport(null);
    setSelectedPath(null);
    setFilter('all');
    setScope('test');
    setError(null);
    setReviewSaving(false);
  }, [resetKey]);

  useEffect(() => {
    setReport(null);
    setSelectedPath(null);
    if (!sourceReady) { setHistory([]); setHistoryLoading(false); return; }
    const started = historyContext;
    let active = true;
    const isCurrent = () => active && isInspectionHistoryContextCurrent(currentHistoryContext(), started);
    setHistoryLoading(true);
    setHistoryError(null);
    api.inspections.listRuns(folderPath, task).then(async ({ runs }) => {
      if (!isCurrent()) return;
      setHistory(runs);
      const latest=await openSelectedInspectionRun(runs,requestedRun,api.inspections.getRun);
      if(latest){
        if(!inspectionRunMatchesSource(latest,started))throw new Error('선택한 검사 기록의 원본 소스가 현재 프로젝트와 다릅니다.');
        if (isCurrent()) {
          setReport(latest);
          setScope(latest.scope);
          setSelectedPath(latest.rows.find((row) => row.result)?.image.file_path ?? latest.rows[0]?.image.file_path ?? null);
        }
      }
    }).catch((cause) => {
      if (isCurrent()) setHistoryError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => { if (isCurrent()) setHistoryLoading(false); });
    return () => { active = false; };
  }, [folderPath, task, projectDir, historyContext.canonicalSourceFolder, sourceReady,requestedRun,handoff?.taskKey,handoff?.selectionId]);

  const openHistoryRun = async (runId: string, preferredImagePath?: string) => {
    const started = historyContext;
    const requestId = ++historyRequestRef.current;
    const isCurrent = () => requestId === historyRequestRef.current
      && isInspectionHistoryContextCurrent(currentHistoryContext(), started);
    try {
      setHistoryError(null);
      const previous = await api.inspections.getRun(runId);
      if (!isCurrent() || !inspectionRunMatchesSource(previous, started)) return;
      setReport(previous);
      setScope(previous.scope);
      setFilter('all');
      setSelectedPath(previous.rows.find((row) => row.image.file_path === preferredImagePath)?.image.file_path
        ?? previous.rows.find((row) => row.result)?.image.file_path ?? previous.rows[0]?.image.file_path ?? null);
      setReviewReason('');
    } catch (cause) {
      if (isCurrent()) setHistoryError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const refreshHistory = async (started: InspectionHistoryContext) => {
    const { runs } = await api.inspections.listRuns(started.folderPath, started.task);
    if (isInspectionHistoryContextCurrent(currentHistoryContext(), started)) {
      setHistory(runs);
    }
  };

  const handleStart = async () => {
    const startedSource = currentBatchSource();
    const startedHistory = currentHistoryContext();
    if (!isBatchSourceReady(startedSource) || !savedFlow || isRunning || !executionReady || !project) return;
    const execution = flowExecutionOptions(executionChoice, selectedProfileId);
    const executionConfig = { execution_target: execution.executionTarget, device: execution.device,
      compute_profile_id: execution.computeProfileId, project_id: project.id };
    const sourceFolder = startedSource.folderPath;
    const sourceTask = startedSource.task;
    const selectedScope = scope;
    const currentSource = () => isBatchSourceCurrent(currentBatchSource(), startedSource);
    const operation = ++activeBatchOperation;
    ownedOperationRef.current = operation;
    let runGuard: ReturnType<typeof createInspectionRunExitGuard> | null = null;
    let createdRunId: string | null = null;
    stopReasonRef.current = null;
    setStopRequested(false);
    setIsRunning(true);
    useInspectionRunStore.getState().setRunning(true);
    setReport(null);
    setSelectedPath(null);
    setFilter('all');
    setError(null);
    try {
      const { pipelines } = await api.flowchart.listPipelines(sourceFolder);
      const currentActive = activeSavedVersion(pipelines);
      if (!currentSource()) return;
      if (currentActive?.version_id !== savedFlow.version.version_id) {
        throw new Error('활성 검사 플로우가 바뀌었습니다. 6단계를 다시 열어 저장 버전을 확인하세요.');
      }
      const currentPipeline = await api.flowchart.getPipelineVersion(currentActive.version_id);
      const currentIdentity = await savedFlowIdentity(currentActive, currentPipeline);
      if (!currentSource()) return;
      if (currentIdentity.pipelineHash !== savedFlow.identity.pipelineHash) {
        throw new Error('저장 버전의 그래프가 변경되었습니다. 6단계를 다시 열어 확인하세요.');
      }
      const baseUrl = await getApiBaseUrl();
      if (!currentSource() || stopReasonRef.current || operation !== activeBatchOperation) return;
      runGuard = createInspectionRunExitGuard((runId) => {
        void stopInspectionRunKeepalive(runId, baseUrl).catch(() => {
          // A backend restart also marks an abandoned running run as stopped.
        });
      });
      exitGuardRef.current = runGuard;
      const finished = await runBatchInspection({
        sourceFolder, task: sourceTask, scope: selectedScope,
        pipeline: savedFlow.pipeline,
        onRunCreated: (runId) => {
          createdRunId = runId;
          runGuard?.created(runId);
        },
        stopReason: () => currentSource() ? stopReasonRef.current : 'source_changed',
        onUpdate: (next) => {
          if (!currentSource()) return;
          setReport(next);
          setSelectedPath((current) => current || next.rows.find((row) =>
            row.state === 'OK' || row.state === 'NG' || row.state === 'REVIEW' || row.state === 'error'
          )?.image.file_path || null);
        },
      }, {
        getActivePipeline: api.flowchart.getActivePipeline,
        getPipeline: api.flowchart.getPipeline,
        verifyModels: api.flowchart.verifyModels,
        getImages: api.dataset.getImages,
        run: api.flowchart.run,
        executeRow: api.inspections.executeRow,
        createRun: async (pending, pipeline) => {
          const created = await api.inspections.createRun(pending, pipeline, executionConfig);
          pending.saved_version_id = created.saved_version_id;
          pending.pipeline_hash = created.pipeline_hash;
          pending.model_sha256 = created.model_sha256;
          return created.run_id;
        },
        recordRow: (runId, row) => api.inspections.recordRow(runId, row),
        finishRun: (runId, status) => api.inspections.finishRun(runId, status),
      });
      if (currentSource()) {
        const persisted = finished.run_id ? await api.inspections.getRun(finished.run_id) : finished;
        if (!currentSource()) return;
        setReport(persisted);
        await refreshHistory(startedHistory);
        setReviewQueueRevision((value) => value + 1);
      }
    } catch (cause) {
      if (currentSource() && stopReasonRef.current !== 'user_stop') {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (createdRunId) runGuard?.release(createdRunId);
      if (exitGuardRef.current === runGuard) exitGuardRef.current = null;
      if (operation === activeBatchOperation) {
        ownedOperationRef.current = null;
        setIsRunning(false);
        useInspectionRunStore.getState().setRunning(false);
      }
    }
  };

  const rows = report?.rows ?? [];
  const summary = summarizeBatch(rows);
  const visibleRows = filterBatchRows(rows, filter);
  const selected = visibleRows.find((row) => row.image.file_path === selectedPath) ?? visibleRows[0] ?? null;
  const previewSource=inspectionPreview(selected?.result?.annotated_image,selected?.image.thumbnail_url);
  const previewIsResult=!!selected?.result?.annotated_image&&safeSnapshot(selected.result.annotated_image);
  const closeEvidence=()=>{evidenceRequest.current++;setEvidenceView(null);};
  const openEvidence=async()=>{
    if(!selected||!report)return;
    const choice=++evidenceRequest.current;const started=historyContext;
    const view=inspectionEvidence(selected.result,{key:`${report.run_id||'current'}:${selected.image.file_path}:${selected.image_sha256||''}`,
      title:selected.image.file_name,imagePath:selected.image.file_path,imageSha256:selected.image_sha256||undefined,
      runId:report.run_id,versionId:report.saved_version_id||undefined,
      facts:{state:selected.state,rejection_reason:selected.result?.rejection_reason,pipeline_hash:report.pipeline_hash,model_sha256:report.model_sha256},
      warning:'원본 해시를 확인하고 있습니다. 저장된 판정은 읽기 전용입니다.'});
    setEvidenceView({scope:resetKey,view});
    if(!report.run_id||!selected.image_sha256){setEvidenceView({scope:resetKey,view:{...view,warning:'이 실행에는 원본 해시가 없습니다. 저장된 판정 이미지만 표시합니다.'}});return;}
    try{
      const original=await api.inspections.evidenceImage(report.run_id,selected.image.file_path);
      if(choice!==evidenceRequest.current||!isInspectionHistoryContextCurrent(currentHistoryContext(),started))return;
      if(original.run_id!==report.run_id||original.saved_version_id!==(report.saved_version_id||null)||original.image_path!==selected.image.file_path||original.image_sha256!==selected.image_sha256||original.read_only!==true)throw new Error('원본 응답이 선택한 실행·버전·이미지 해시와 일치하지 않습니다.');
      setEvidenceView({scope:resetKey,view:{...view,warning:undefined,layers:[{id:'original',label:'해시 확인된 원본',image:original.image,space:'source',size:original.original_size},...view.layers]}});
    }catch(cause){if(choice===evidenceRequest.current&&isInspectionHistoryContextCurrent(currentHistoryContext(),started))setEvidenceView({scope:resetKey,view:{...view,warning:cause instanceof Error?cause.message:String(cause)}});}
  };
  const selectFilter = (next: BatchFilter) => {
    setFilter(next);
    setSelectedPath(filterBatchRows(rows, next)[0]?.image.file_path ?? null);
  };
  const progress = summary.total ? summary.processed + summary.errors : 0;
  const submitReview = async (verdict: 'OK' | 'NG' | 'REVIEW') => {
    if (!report?.run_id || !selected || !selected.result || !reviewReason.trim() || !reviewer.trim()) return;
    const started = historyContext;
    const runId = report.run_id;
    const imagePath = selected.image.file_path;
    const requestId = ++reviewRequestRef.current;
    const isCurrent = () => requestId === reviewRequestRef.current
      && isInspectionHistoryContextCurrent(currentHistoryContext(), started);
    setReviewSaving(true);
    setError(null);
    try {
      await api.inspections.reviewRow(runId, {
        image_path: imagePath,
        final_verdict: verdict,
        reason: reviewReason.trim(),
        reviewer: reviewer.trim(),
      });
      if (!isCurrent()) return;
      localStorage.setItem('inspection-reviewer', reviewer.trim());
      const refreshed: InspectionHistoryRun = await api.inspections.getRun(runId);
      if (!isCurrent() || !inspectionRunMatchesSource(refreshed, started)) return;
      setReport(refreshed);
      setSelectedPath(imagePath);
      setReviewReason('');
      setReviewQueueRevision((value) => value + 1);
    } catch (cause) {
      if (isCurrent()) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (isCurrent()) setReviewSaving(false);
    }
  };
  const exportHistory = async (format: 'csv' | 'json') => {
    if (!report?.run_id) return;
    const started = historyContext;
    const runId = report.run_id;
    try {
      const exported = await api.inspections.exportRun(runId, format);
      if (!isInspectionHistoryContextCurrent(currentHistoryContext(), started)) return;
      const blob = new Blob([exported.content], { type: format === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = exported.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      if (isInspectionHistoryContextCurrent(currentHistoryContext(), started)) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    }
  };

  return (
    <section className="border border-[#2B3547] bg-[#131822] rounded p-4 space-y-4" aria-label="실제 이미지 일괄 검사">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#2B3547] pb-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-100">
            <Layers className="w-4 h-4 text-cyan-400" /> 실제 이미지 일괄 검사
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            5단계에서 저장한 플로우를 선택한 실행 위치에서 이미지별로 검사합니다. 실행 위치와 모델 원판정, 중간 노드 기록을 함께 보관합니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-slate-300">실행 위치
            <select aria-label="일괄 검사 실행 위치" value={executionChoice} disabled={isRunning}
              onChange={event => setExecutionChoice(event.target.value as FlowExecutionChoice)}
              className="max-w-56 rounded border border-[#364357] bg-[#0B0E14] px-2 py-1.5 text-xs text-white">
              <option value="selected_compute" disabled={!executionProfile}>선택 서버 · {executionProfile?.name || '서버 미선택'} (CUDA{executionProfile?.gpu_selector ? ` GPU ${executionProfile.gpu_selector}` : ''})</option>
              <option value="local_cpu">이 컴퓨터 · CPU</option>
              <option value="local_mps">이 컴퓨터 · Apple MPS</option>
              <option value="local_cuda">이 컴퓨터 · CUDA</option>
              <option value="model_compute">모델 학습 서버 · 기존 방식</option>
            </select>
          </label>
          <label htmlFor="batch-inspection-scope" className="text-xs text-slate-300">검사 범위</label>
          <select
            id="batch-inspection-scope"
            value={scope}
            onChange={(event) => setScope(event.target.value as BatchScope)}
            disabled={isRunning}
            className="rounded border border-[#364357] bg-[#0B0E14] px-2 py-1.5 text-xs text-white disabled:opacity-50"
          >
            <option value="test">테스트 ({split.test})</option>
            <option value="val">검증 ({split.val})</option>
            <option value="train">학습 ({split.train})</option>
            <option value="all">전체 ({sourceImages}) · 직접 선택</option>
          </select>
          <button
            type="button"
            onClick={handleStart}
            disabled={!sourceReady || !savedFlow || savedFlowLoading || !executionReady || !project || isRunning}
            className="flex items-center gap-1.5 rounded border border-cyan-600 bg-cyan-900/50 px-3 py-1.5 text-xs font-semibold text-cyan-100 hover:bg-cyan-800/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play className="w-3.5 h-3.5" /> 검사 시작
          </button>
          {isRunning && (
            <button
              type="button"
              onClick={() => { stopReasonRef.current = 'user_stop'; setStopRequested(true); }}
              disabled={stopRequested}
              className="flex items-center gap-1.5 rounded border border-amber-600 bg-amber-950/50 px-3 py-1.5 text-xs font-semibold text-amber-200 disabled:opacity-50"
            >
              <Square className="w-3.5 h-3.5" /> 중단
            </button>
          )}
        </div>
      </div>

      {hasUnsavedDraft && <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-700/70 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
        <span>5단계의 미저장 초안은 일괄 검사에 반영되지 않습니다. 초안을 사용하려면 5단계에서 저장하세요.</span>
        <button type="button" onClick={() => setStep(5)} className="rounded border border-amber-600 px-2 py-1 font-semibold hover:bg-amber-900/40">5단계에서 저장</button>
      </div>}
      {savedFlow && <div className="space-y-1">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-cyan-300">다음 검사에 사용할 활성 플로우</p>
        <SavedFlowIdentityCard identity={savedFlow.identity} isActive={savedFlow.version.is_active} />
      </div>}
      {savedFlowLoading && <p role="status" className="text-xs text-sky-300">활성 저장 플로우를 확인하는 중입니다.</p>}
      {savedFlowError && <p role="alert" className="text-xs text-amber-300">{savedFlowError}</p>}

      {!sourceReady && (
        <div role="status" className="text-xs text-amber-300">1단계에서 현재 작업 유형의 데이터 폴더를 불러오면 실제 이미지 검사를 시작할 수 있습니다.</div>
      )}
      {scope === 'all' && !isRunning && (
        <div className="rounded border border-amber-700/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
          전체 이미지는 선택한 폴더의 모든 이미지를 검사하며, 원격 서버에서는 이미지마다 왕복 시간이 듭니다.
        </div>
      )}
      {isRunning && (
        <div role="status" className="text-xs text-cyan-300">
          {stopRequested
            ? '현재 이미지 검사 후 중단합니다. 남은 이미지는 미실행으로 표시됩니다.'
            : `검사 진행 중 ${progress} / ${summary.total || '목록 준비 중'}`}
        </div>
      )}
      {error && <div role="alert" className="flex items-start gap-2 rounded border border-red-700 bg-red-950/30 p-2 text-xs text-red-200"><AlertTriangle className="h-4 w-4 shrink-0" />{error}</div>}

      <ReviewQueuePanel sourceFolder={sourceReady ? folderPath : ''} task={task} projectDir={projectDir}
        refreshKey={reviewQueueRevision} disabled={isRunning}
        onOpen={(runId, imagePath) => { void openHistoryRun(runId, imagePath); }} />

      <div className="rounded-lg border border-[#2B3547] bg-[#0E1420] px-3 py-3" aria-label="검사 이력">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
            <History className="h-4 w-4 text-sky-400" /> 검사 이력
            <span className="text-[11px] font-normal text-slate-400">모델 원판정과 작업자 판정을 따로 보관합니다.</span>
          </div>
          {report?.run_id && <div className="flex gap-1.5">
            <button type="button" onClick={() => exportHistory('csv')} className="flex items-center gap-1 rounded border border-[#364357] px-2 py-1 text-[11px] text-slate-200 hover:bg-[#222B3D]"><Download className="h-3 w-3" /> CSV</button>
            <button type="button" onClick={() => exportHistory('json')} className="flex items-center gap-1 rounded border border-[#364357] px-2 py-1 text-[11px] text-slate-200 hover:bg-[#222B3D]"><Download className="h-3 w-3" /> JSON</button>
          </div>}
        </div>
        {historyLoading && <p className="text-[11px] text-slate-400">저장된 검사 기록을 읽는 중입니다.</p>}
        {historyError && <p role="alert" className="text-[11px] text-red-300">{historyError}</p>}
        {!historyLoading && history.length === 0 && !historyError && <p className="text-[11px] text-slate-500">이 데이터의 검사 기록이 없습니다.</p>}
        {history.length > 0 && <div className="flex gap-2 overflow-x-auto pb-1">
          {history.map((item) => <button
            type="button" key={item.run_id} onClick={() => openHistoryRun(item.run_id)} disabled={isRunning}
            aria-pressed={report?.run_id === item.run_id}
            className={`min-w-[180px] rounded-md border px-2.5 py-2 text-left transition-colors disabled:opacity-50 ${report?.run_id === item.run_id ? 'border-sky-500 bg-sky-500/10' : 'border-[#2B3547] bg-[#131B29] hover:border-[#5B6B84]'}`}
          >
            <span className="block truncate text-[11px] font-semibold text-slate-100">{item.pipeline_name}</span>
            <span className="mt-1 block truncate font-mono text-[10px] text-sky-300" title={item.saved_version_id || '이전 형식의 검사 기록'}>
              {item.saved_version_id ? `저장 버전 ${item.saved_version_id.slice(0, 8)}` : '저장 버전 미기록'} · 그래프 {item.pipeline_hash.slice(0, 8)}
            </span>
            <span className="mt-1 block text-[10px] text-slate-400">{new Date(item.created_at).toLocaleString('ko-KR')} · {item.status === 'completed' ? '완료' : item.status === 'stopped' ? '중단' : '진행 중'}</span>
            <span className="mt-1 block text-[10px] text-slate-300">{item.total}장 · NG {item.counts.NG || 0} · 검토 {item.counts.REVIEW || 0}</span>
          </button>)}
        </div>}
      </div>

      {report && (
        <>
          {report.run_id && <div className="rounded-lg border border-sky-700/50 bg-[#111D2D] px-3 py-3 text-[11px] text-slate-300" aria-label="선택한 검사 실행 식별자">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <strong className="text-xs text-sky-200">선택한 검사 실행의 기록</strong>
              <span className="font-mono text-slate-400" title={report.run_id}>실행 ID {report.run_id.slice(0, 8)}</span>
            </div>
            {report.execution_config && <p className="mb-2 text-sky-100">
              실행 위치: {report.execution_config.execution_target === 'model_compute' ? '모델 학습 위치 · 기존 방식'
                : report.execution_config.execution_target === 'local' ? '이 컴퓨터'
                : report.execution_config.profile?.name || report.execution_config.compute_profile_name
                  || report.execution_config.compute_profile_id || '선택 서버'}
              {report.execution_config.execution_target !== 'model_compute' && ` · ${report.execution_config.device.toUpperCase()}`}
              {report.execution_config.execution_target === 'model_compute' && ' · 실제 장치는 이미지별 기록에서 확인'}
            </p>}
            <div className="grid gap-1.5 sm:grid-cols-2">
              <span className="break-all">저장 플로우 버전 <strong className="font-mono text-slate-100">{report.saved_version_id || '이전 기록 · 미기록'}</strong></span>
              <span className="break-all">그래프 SHA-256 <strong className="font-mono text-slate-100" title={report.pipeline_hash}>{report.pipeline_hash || '이전 기록 · 미기록'}</strong></span>
            </div>
            {Object.entries(report.model_sha256 || {}).length > 0 ? <div className="mt-2 space-y-1 border-t border-sky-800/50 pt-2">
              <span className="font-semibold text-sky-200">검사 시작 시 검증한 체크포인트</span>
              {Object.entries(report.model_sha256 || {}).map(([jobId, digest]) => <div key={jobId} className="grid gap-0.5 sm:grid-cols-[minmax(150px,0.35fr)_minmax(0,1fr)]">
                <span className="truncate font-mono" title={jobId}>{jobId}</span>
                <span className="break-all font-mono text-slate-100">SHA-256 {digest}</span>
              </div>)}
            </div> : <p className="mt-2 text-slate-500">이전 형식의 기록에는 체크포인트 해시가 없습니다.</p>}
          </div>}
          <div className="space-y-2">
            <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-300">
              <span>플로우: <strong className="text-slate-100">{report.pipeline_name}</strong> · 범위: {scopeNames[report.scope]}</span>
              <span>{report.status === 'completed' ? '완료' : report.status === 'stopped' ? '중단' : '실행 중'} · {progress}/{summary.total} 확인</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-[#0B0E14]" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={summary.total}>
              <div className="h-full bg-cyan-500 transition-all" style={{ width: `${summary.total ? progress / summary.total * 100 : 0}%` }} />
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5" aria-label="검사 결과 필터">
            {([
              ['all', `전체 ${summary.total}`], ['OK', `OK ${summary.ok}`], ['NG', `NG ${summary.ng}`],
              ['REVIEW', `검토 ${summary.review}`], ['error', `실패 ${summary.errors}`], ['unrun', `미실행 ${summary.unrun}`],
            ] as Array<[BatchFilter, string]>).map(([key, label]) => (
              <button
                key={key} type="button" onClick={() => selectFilter(key)}
                aria-pressed={filter === key}
                className={`rounded border px-2 py-1 text-[11px] ${filter === key ? 'border-cyan-500 bg-cyan-900/40 text-cyan-100' : 'border-[#364357] text-slate-300 hover:bg-[#222B3D]'}`}
              >{label}</button>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(280px,0.85fr)_minmax(0,1.15fr)]">
            <div className="max-h-[380px] overflow-y-auto rounded border border-[#2B3547] bg-[#0B0E14]" aria-label="이미지별 검사 결과">
              {visibleRows.length === 0 && <p className="p-3 text-xs text-slate-400">이 필터에 해당하는 이미지가 없습니다.</p>}
              {visibleRows.map((row) => (
                <button
                  type="button" key={row.image.file_path}
                  onClick={() => setSelectedPath(row.image.file_path)}
                  className={`flex w-full items-center gap-2 border-b border-[#243043] px-2 py-1.5 text-left hover:bg-[#1A212E] ${selected?.image.file_path === row.image.file_path ? 'bg-[#1A212E]' : ''}`}
                >
                  <img src={inspectionThumbnail(row.image.thumbnail_url)?resolveApiUrl(row.image.thumbnail_url):undefined} alt="" className="h-9 w-12 shrink-0 rounded object-contain bg-black" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs text-slate-100" title={row.image.file_path}>{row.image.file_name}</span>
                    <span className="block truncate text-[10px] text-slate-400">{row.image.split} · {row.error || row.result?.rejection_reason || row.image.file_path}</span>
                  </span>
                  <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${stateColors[row.state]}`}>{stateNames[row.state]}</span>
                </button>
              ))}
            </div>
            <div className="min-h-[260px] rounded border border-[#2B3547] bg-[#0B0E14] p-3">
              {selected ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex min-w-0 items-center gap-2"><ImageIcon className="h-4 w-4 shrink-0 text-cyan-400" /><strong className="truncate text-slate-100" title={selected.image.file_path}>{selected.image.file_name}</strong></div>
                    <span className={`${stateColors[selected.state]}`}>{stateNames[selected.state]}</span>
                  </div>
                  <p className="break-all text-[10px] text-slate-400">원본: {selected.image.file_path}</p>
                  <button type="button" onClick={()=>void openEvidence()} className="rounded border border-sky-700 px-3 py-2 text-xs text-sky-200">원본·판정 근거 보기</button>
                  {evidenceView?.scope===resetKey&&<EvidenceImageViewer evidence={evidenceView.view} onClose={closeEvidence} returnLabel="검사 결과로 돌아가기"/>}
                  <button type="button" disabled={isRunning} onClick={() => {
                    void useProjectStore.getState().openImageForLabeling(selected.image.image_id, selected.image.file_path).then((opened) => {
                      if (!opened) setError('선택한 이미지의 라벨 화면을 열지 못했습니다. 검사 데이터 출처와 저장 상태를 확인하세요.');
                    }).catch((cause) => setError(cause instanceof Error ? cause.message : '라벨 화면을 열지 못했습니다.'));
                  }} className="rounded border border-cyan-700 px-3 py-1 text-xs text-cyan-200 hover:bg-cyan-950 disabled:opacity-40">이 이미지 라벨 수정</button>
                  {selected.image_sha256 && <p className="break-all font-mono text-[10px] text-slate-500">검사 원본 SHA-256: {selected.image_sha256}</p>}
                  <div className="flex h-48 items-center justify-center overflow-hidden rounded border border-[#243043] bg-black">
                    {previewSource
                      ? <img src={resolveApiUrl(previewSource)} alt={`${selected.image.file_name} ${previewIsResult?'검사 미리보기':'원본 썸네일'}`} className="h-full w-full object-contain" />
                      : <span className="text-xs text-slate-500">미리보기가 없습니다.</span>}
                  </div>
                  {selected.error && <div role="alert" className="rounded border border-red-700 bg-red-950/30 p-2 text-xs text-red-200">{selected.error}</div>}
                  {selected.state === 'skipped' && <p className="text-xs text-slate-400">이 이미지는 검사되지 않았습니다. 판정 결과가 없습니다.</p>}
                  {selected.result && (
                    <>
                      <div className="flex flex-wrap gap-3 text-xs text-slate-300">
                        <span>판정 <strong className={stateColors[selected.state]}>{selected.result.final_verdict}</strong></span>
                        <span>ROI {selected.result.roi_count}</span>
                        <span>결함 ROI {selected.result.defective_roi_count}</span>
                        <span>모델 검사 {selected.result.total_latency_ms.toFixed(1)}ms</span>
                        {selected.result.execution_device && <span>실행 {selected.result.compute_profile_name
                          || selected.result.compute_profile_id || '이 컴퓨터'} · {selected.result.execution_device}</span>}
                      </div>
                      <div>
                        <h4 className="mb-1 text-xs font-semibold text-slate-200">중간 노드 결과</h4>
                        <div className="max-h-40 space-y-1 overflow-y-auto">
                          {selected.result.execution_steps.map((step, index) => (
                            <div key={`${step.node_id}-${index}`} className="flex items-center justify-between rounded border border-[#243043] px-2 py-1 text-[11px] text-slate-300">
                              <span className="truncate" title={step.node_id}>{step.name || step.node_id}</span>
                              <span className="shrink-0 font-mono">{step.status} · {Number(step.latency_ms).toFixed(1)}ms</span>
                            </div>
                          ))}
                        </div>
                      </div>
                      {selected.result.crops.length > 0 && (
                        <div>
                          <h4 className="mb-1 text-xs font-semibold text-slate-200">검사 영역 ({selected.result.crops.length})</h4>
                          <div className="flex max-h-36 gap-2 overflow-auto">
                            {selected.result.crops.map((crop) => (
                              <div key={crop.roi_id} className="w-24 shrink-0 rounded border border-[#243043] p-1 text-[10px] text-slate-300">
                                {crop.crop_thumbnail && <img src={resolveApiUrl(crop.crop_thumbnail)} alt={crop.label} className="h-16 w-full object-contain" />}
                                <div className="truncate">{crop.label}</div><div>{crop.verdict} · {Number(crop.defect_score).toFixed(2)}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {report.run_id && <div className="rounded-md border border-[#34465F] bg-[#121F32] p-3 text-xs" aria-label="작업자 최종 판정">
                        <div className="flex items-center justify-between gap-2">
                          <h4 className="font-semibold text-sky-200">작업자 재검</h4>
                          <span className="text-[11px] text-slate-400">모델 원판정 {selected.result.final_verdict}</span>
                        </div>
                        {selected.review && <p className="mt-2 rounded border border-sky-600/40 bg-sky-900/20 px-2 py-1.5 text-sky-100">
                          최종 {selected.review.final_verdict} · {selected.review.reviewer} · {selected.review.reason}
                        </p>}
                        <div className="mt-2 grid gap-2 sm:grid-cols-[140px_1fr]">
                          <label className="text-[11px] text-slate-300">작업자
                            <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} maxLength={100}
                              className="mt-1 w-full rounded border border-[#364357] bg-[#0B0E14] px-2 py-1.5 text-xs text-white" />
                          </label>
                          <label className="text-[11px] text-slate-300">재검 사유
                            <input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} maxLength={2000}
                              placeholder="원본·현미경 재검 근거를 입력하세요" className="mt-1 w-full rounded border border-[#364357] bg-[#0B0E14] px-2 py-1.5 text-xs text-white" />
                          </label>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {(['OK', 'NG', 'REVIEW'] as const).map((verdict) => <button type="button" key={verdict}
                            onClick={() => submitReview(verdict)} disabled={reviewSaving || !reviewReason.trim() || !reviewer.trim()}
                            className="flex items-center gap-1 rounded border border-[#45617D] px-2.5 py-1 text-[11px] font-semibold text-sky-100 hover:bg-[#254466] disabled:cursor-not-allowed disabled:opacity-40">
                            <Save className="h-3 w-3" /> {verdict} 확정
                          </button>)}
                        </div>
                        {selected.reviews && selected.reviews.length > 1 && <p className="mt-2 text-[10px] text-slate-400">판정 이력 {selected.reviews.length}건 · 이전 기록은 삭제되지 않습니다.</p>}
                      </div>}
                    </>
                  )}
                </div>
              ) : <p className="py-24 text-center text-xs text-slate-400">이미지를 선택하면 검사 근거를 볼 수 있습니다.</p>}
            </div>
          </div>
        </>
      )}
    </section>
  );
};

export default BatchInspectionPanel;
