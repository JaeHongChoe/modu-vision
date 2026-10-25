/**
 * src/renderer/stores/useEvaluationStore.ts
 * Evaluation state: interactive confusion matrix filtering, defect heatmaps,
 * overkill vs escape visual feedback, and 1-click zero-escape calibration.
 */

import { create } from 'zustand';
import type {
  BenchmarkResult,
  EvaluationResults,
  ConfusionMatrixData,
  OverkillUnderkillAnalysis,
  TestPredictionItem,
  VisionTask,
} from '../types';
import { api, getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration, type RemoteEvaluationOperation, type RemoteEvaluationResult } from '../services/api';
import { useTrainingStore } from './useTrainingStore';
import { useProjectStore } from './useProjectStore';
import { useDatasetStore } from './useDatasetStore';
import { classRole, isDefectClass, type ClassRoles } from '../utils/classSemantics';

export type SampleVerdict = 'ESCAPE' | 'OVERKILL' | 'CORRECT_NG' | 'CORRECT_OK' | 'REVIEW';
export type SampleFilter =
  | 'all'
  | 'escape'
  | 'saved_escape'
  | 'overkill'
  | 'normal'
  | 'defect'
  | 'fn_escape'
  | 'fp_overkill'
  | 'tp_defect'
  | 'tn_normal';

let heatmapDebounceTimer: ReturnType<typeof setTimeout> | null = null;
let currentHeatmapRequestId = 0;
let evaluationGeneration = 0;

function sameHeatmapImage(a: TestPredictionItem | null, b: TestPredictionItem | null): boolean {
  return a?.image_id === b?.image_id && a?.file_path === b?.file_path;
}
function effectiveHeatmapThreshold(state: EvaluationState): number {
  return state.sampleFilter === 'saved_escape' && state.persistedThreshold !== null ? state.persistedThreshold : state.confidenceThreshold;
}
/** Revoke pixels and request ownership before changing their image/decision authority. */
function invalidateHeatmap() {
  currentHeatmapRequestId += 1;
  if (heatmapDebounceTimer) clearTimeout(heatmapDebounceTimer);
  heatmapDebounceTimer = null;
  useEvaluationStore.setState({heatmapOverlayBase64: null, heatmapLoading: false, heatmapError: null});
}


/** Shared class meaning; pass roles recorded by the backend when the response has them. */
export function isDefectLabel(label?: string | number, roles?: ClassRoles | null): boolean {
  if (label === undefined || label === null) return false;
  return isDefectClass(label, roles);
}

/** Missing or unresolved labels cannot provide a normal calibration sample. */
export function isNormalLabel(label?: string | number, roles?: ClassRoles | null): boolean {
  return label !== undefined && label !== null && String(label).trim() !== '' && classRole(label, roles) === 'normal';
}

export function computeSampleVerdict(
  item: TestPredictionItem,
  threshold: number,
  overkillAnalysis?: OverkillUnderkillAnalysis | null,
  roles?: ClassRoles | null
): SampleVerdict {
  // 1. Check overkillAnalysis.sample_details if available
  const sampleDetails = (overkillAnalysis as any)?.sample_details;
  if (sampleDetails && Array.isArray(sampleDetails)) {
    const detail = sampleDetails.find(
      (d: any) => (!!item.image_id && d.image_id === item.image_id)
        || (!!item.file_name && d.file_name === item.file_name)
        || (!!item.file_path && d.file_path === item.file_path)
    );
    if (detail && typeof detail.is_defect === 'boolean' && Number.isFinite(detail.defect_score)) {
      const isDefect = detail.is_defect;
      const score = detail.defect_score;
      const predictedNg = score >= threshold;
      if (isDefect) {
        return predictedNg ? 'CORRECT_NG' : 'ESCAPE';
      } else {
        return predictedNg ? 'OVERKILL' : 'CORRECT_OK';
      }
    }
  }

  // 2. Preserve per-image backend truth (for example, detection with multiple
  // classes); use the recorded class meaning only when that evidence is absent.
  const imageTruth = (item as TestPredictionItem & { is_defect?: boolean }).is_defect;
  const hasImageTruth = typeof imageTruth === 'boolean';
  if (!hasImageTruth && (item.ground_truth === undefined || item.ground_truth === null || String(item.ground_truth).trim() === '')) {
    return 'REVIEW';
  }
  const isDefect = hasImageTruth ? imageTruth : isDefectLabel(item.ground_truth, roles);

  let defectScore: number;
  if ((item as any).defect_score !== undefined) {
    defectScore = (item as any).defect_score;
  } else {
    if (item.predicted_class === undefined || item.predicted_class === null || !Number.isFinite(item.confidence)) return 'REVIEW';
    const isPredDefect = isDefectLabel(item.predicted_class, roles);
    defectScore = isPredDefect ? item.confidence : 1.0 - item.confidence;
  }
  if (!Number.isFinite(defectScore)) return 'REVIEW';

  const predictedNg = item.map_semantics === 'patch_score' ? defectScore > threshold : defectScore >= threshold;
  if (isDefect) {
    return predictedNg ? 'CORRECT_NG' : 'ESCAPE';
  } else {
    return predictedNg ? 'OVERKILL' : 'CORRECT_OK';
  }
}

/** Saved decision authority, distinct from interactive and recommendation thresholds. */
function validThreshold(value: unknown, metrics: Record<string, any>): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    && (metrics.score_spec?.domain === 'distance' || value <= 1);
}
function calibrationThreshold(data: { optimal_threshold?: unknown; calibration_evidence?: { threshold?: unknown } | null }, metrics: Record<string, any>): number | null {
  const threshold = data.optimal_threshold;
  if (!validThreshold(threshold, metrics)) return null;
  if (data.calibration_evidence !== undefined && data.calibration_evidence?.threshold !== threshold) return null;
  return threshold;
}
function savedEvaluationThreshold(result: EvaluationResults, task?: VisionTask): number | null {
  const metrics = result.metrics || {};
  if (result.zero_underkill_calibrated === true) return calibrationThreshold(result, metrics);
  if (Object.hasOwn(metrics, 'active_threshold')) return validThreshold(metrics.active_threshold, metrics) ? metrics.active_threshold : null;
  // Older probability classification/detection evaluations used the fixed .50 baseline.
  return result.task === 'anomaly' || task === 'anomaly' || metrics.anomaly_mode || metrics.score_spec?.domain === 'distance' ? null : .5;
}

export interface EvaluationSource {
  folderPath: string; task: VisionTask; labelsetId?: string;
  evaluationDatasetVersionId?: string; computeProfileId?: string; device?: string;
  executionTarget?: 'local'|'selected_compute';
  isCurrent?: () => boolean;
}
// Keep follow-up analysis on the exact cohort accepted by the evaluation read.
// The authority epoch also fences leaving and returning to the same project.
interface CalibrationSource {
  source: EvaluationSource; jobId: string; generation: number; isCurrent: () => boolean;
}
let calibrationSource: CalibrationSource | null = null;
let analysisRequest = 0;
let calibrationRequest = 0;
function captureCalibrationSource(source: EvaluationSource): (() => boolean) | null {
  const selected = {...source};
  const scope = () => {
    const state = useProjectStore.getState(), project = state.project, dataset = useDatasetStore.getState();
    const authority = getProjectContext();
    if (!project?.id || (authority && authority.project_id !== project.id) || !state.projectDir || state.isProjectBusy || dataset.isLoading || dataset.importError
      || !dataset.hasSelectedFolder || dataset.folderPath !== selected.folderPath
      || dataset.datasetKey !== `${selected.folderPath}\0${selected.task}`
      || state.projectDir !== project.project_dir || state.task !== selected.task || project.task !== selected.task
      || project.source_dataset_dir !== selected.folderPath
      || (selected.labelsetId && selected.labelsetId !== (project.active_labelset_id || 'default'))) return null;
    return JSON.stringify([project.id, project.project_dir, project.task, project.active_labelset_id || 'default', selected.folderPath]);
  };
  const expected = scope(), epoch = getProjectContextGeneration(), transport = getApiPersistenceIdentity();
  const context = JSON.stringify(getProjectContext());
  if (!expected || selected.isCurrent?.() === false) return null;
  return () => scope() === expected && getProjectContextGeneration() === epoch && getApiPersistenceIdentity() === transport
    && JSON.stringify(getProjectContext()) === context && selected.isCurrent?.() !== false;
}
/** Read the accepted evaluation carrier for explanation only; never load, calibrate or apply a threshold here. */
export function currentEvaluationGuidanceSource(): {source: EvaluationSource; jobId: string} | null {
  const binding = calibrationSource;
  if (!binding || binding.generation !== evaluationGeneration || binding.jobId !== useEvaluationStore.getState().jobId || !binding.isCurrent()) return null;
  return {source: {...binding.source}, jobId: binding.jobId};
}
interface EvaluationState {
  executionEvidence: RemoteEvaluationResult | null;
  remoteOperation: RemoteEvaluationOperation | null;
  remoteRequest: {jobId: string; source: EvaluationSource; generation: number} | null;
  remoteOperationError: string | null;
  refreshRemoteOperation: () => Promise<void>;
  cancelRemoteEvaluation: () => Promise<void>;
  jobId: string | null;
  allowLatestRecovery: boolean;
  isLoading: boolean;
  metrics: Record<string, any>;
  classSemantics: EvaluationResults['class_semantics'] | null;
  confusionMatrix: ConfusionMatrixData | null;
  selectedCell: { trueClass: string; predClass: string } | null;
  testPredictions: TestPredictionItem[];
  filteredPredictions: TestPredictionItem[];
  selectedPrediction: TestPredictionItem | null;
  confidenceThreshold: number;
  persistedThreshold: number | null;
  heatmapOverlayBase64: string | null;
  heatmapLoading: boolean;
  heatmapError: string | null;
  isExportingReport: boolean;
  exportedReportPath: string | null;
  errorMessage: string | null;

  // F33: Sample Filtering & Verdicts
  sampleFilter: SampleFilter;
  setSampleFilter: (filter: SampleFilter) => void;

  // Industrial Overkill/Underkill Analysis
  overkillAnalysis: OverkillUnderkillAnalysis | null;
  targetMaxUnderkill: number;
  costEscape: number;
  costScrap: number;
  isAnalyzingTradeoff: boolean;

  // F33: 1-Click Calibration
  isCalibrating: boolean;
  calibrationMessage: string | null;
  calibrationSuccess: boolean;
  calibrateZeroEscape: (jobIdOverride?: string) => Promise<void>;

  // Inference Center Benchmark
  benchmarkResult: BenchmarkResult | null;
  isBenchmarking: boolean;

  loadEvaluation: (jobId?: string, source?: EvaluationSource) => Promise<void>;
  invalidateForDataChange: (allowSourceRecovery?: boolean) => void;
  selectCell: (trueClass: string, predClass: string) => void;
  clearCellSelection: () => void;
  selectPrediction: (item: TestPredictionItem) => Promise<void>;
  setConfidenceThreshold: (th: number) => void;
  updateHeatmap: () => Promise<void>;
  exportReport: (format: 'html' | 'json') => Promise<string>;
  loadOverkillUnderkill: (targetUnderkill?: number, escapeCost?: number, scrapCost?: number) => Promise<void>;
  applyOptimalThreshold: () => void;
  runBenchmark: (iterations?: number, resolution?: number) => Promise<void>;
  computeFilteredList: () => void;
}

export const useEvaluationStore = create<EvaluationState>((set, get) => ({
  executionEvidence: null, remoteOperation: null, remoteRequest: null, remoteOperationError: null,
  jobId: null,
  allowLatestRecovery: true,
  isLoading: false,
  metrics: {},
  classSemantics: null,
  confusionMatrix: null,
  selectedCell: null,
  testPredictions: [],
  filteredPredictions: [],
  selectedPrediction: null,
  confidenceThreshold: 0.5,
  persistedThreshold: null,
  heatmapOverlayBase64: null,
  heatmapLoading: false,
  heatmapError: null,
  isExportingReport: false,
  exportedReportPath: null,
  errorMessage: null,

  sampleFilter: 'all',
  setSampleFilter: (sampleFilter) => {
    const previous = get();
    if (sampleFilter === 'saved_escape' && (previous.isLoading || previous.persistedThreshold === null)) return;
    if ((sampleFilter === 'saved_escape') !== (previous.sampleFilter === 'saved_escape')) invalidateHeatmap();
    set({ sampleFilter, ...(sampleFilter === 'saved_escape' ? {selectedCell: null} : {}) });
    get().computeFilteredList();
    if ((sampleFilter === 'saved_escape') !== (previous.sampleFilter === 'saved_escape')
        && get().selectedPrediction?.image_id === previous.selectedPrediction?.image_id
        && !get().executionEvidence?.common_cohort) get().updateHeatmap();
  },

  overkillAnalysis: null,
  targetMaxUnderkill: 0,
  costEscape: 500,
  costScrap: 25,
  isAnalyzingTradeoff: false,

  isCalibrating: false,
  calibrationMessage: null,
  calibrationSuccess: false,

  benchmarkResult: null,
  isBenchmarking: false,

  computeFilteredList: () => {
    const { testPredictions, selectedCell, confusionMatrix, sampleFilter, confidenceThreshold, persistedThreshold, overkillAnalysis, classSemantics } = get();

    // 1. Confusion Matrix cell filter (Robust Dual-Key Matching)
    let list = testPredictions;
    if (selectedCell && confusionMatrix) {
      const key = `${selectedCell.trueClass}:${selectedCell.predClass}`;
      const allowedPaths = new Set(confusionMatrix.cell_samples?.[key] || []);
      if (allowedPaths.size > 0) {
        list = list.filter(
          (item) =>
            allowedPaths.has(item.file_path) ||
            (!!item.evaluation_file_path && allowedPaths.has(item.evaluation_file_path)) ||
            allowedPaths.has(item.image_id) ||
            allowedPaths.has(item.file_name)
        );
      } else {
        // Fallback: semantic matching if cell_samples mapping was not populated
        list = list.filter(
          (item) =>
            item.ground_truth === selectedCell.trueClass &&
            item.predicted_class === selectedCell.predClass
        );
      }
    }

    // 2. Verdict filter (4-Quadrant verification)
    if (sampleFilter !== 'all') {
      list = list.filter((item) => {
        const savedReview = sampleFilter === 'saved_escape';
        if (savedReview && persistedThreshold === null) return false;
        const v = computeSampleVerdict(item, savedReview ? persistedThreshold! : confidenceThreshold, savedReview ? null : overkillAnalysis, classSemantics?.roles);
        if (savedReview) return v === 'ESCAPE';
        if (sampleFilter === 'escape' || sampleFilter === 'fn_escape') return v === 'ESCAPE';
        if (sampleFilter === 'overkill' || sampleFilter === 'fp_overkill') return v === 'OVERKILL';
        if (sampleFilter === 'normal' || sampleFilter === 'tn_normal') return v === 'CORRECT_OK';
        if (sampleFilter === 'defect' || sampleFilter === 'tp_defect') return v === 'CORRECT_NG';
        return true;
      });
    }

    const currentSelected = get().selectedPrediction;
    const stillSelected = currentSelected && list.some((it) => it.image_id === currentSelected.image_id);
    const newSelected = stillSelected ? currentSelected : list[0] || null;

    if (!sameHeatmapImage(newSelected, currentSelected)) invalidateHeatmap();
    set({
      filteredPredictions: list,
      selectedPrediction: newSelected,
    });

    if (newSelected && !sameHeatmapImage(newSelected, currentSelected)) {
      get().updateHeatmap();
    }
  },

  refreshRemoteOperation: async () => {
    const pending = get().remoteRequest;
    if (!pending || pending.generation !== evaluationGeneration || pending.source.isCurrent?.() === false) return;
    try {
      const response = await api.evaluation.getRemoteOperations(pending.jobId);
      if (pending.generation !== evaluationGeneration || pending.source.isCurrent?.() === false) return;
      const source = pending.source;
      const row = response.operations.find(row => row.job_id === pending.jobId
        && row.dataset_version_id === source.evaluationDatasetVersionId
        && row.compute_profile_id === source.computeProfileId && row.device === source.device
        && row.task === source.task && row.labelset_id === (source.labelsetId || 'default')
        && ['preparing','launching','launched','running','stopping','cancel_requested'].includes(row.state));
      set({remoteOperation: row || null, remoteOperationError: null});
    } catch (error) {
      if (pending.generation === evaluationGeneration && pending.source.isCurrent?.() !== false)
        set({remoteOperation: null, remoteOperationError: error instanceof Error ? error.message : String(error)});
    }
  },
  cancelRemoteEvaluation: async () => {
    const pending = get().remoteRequest, row = get().remoteOperation;
    if (!pending || !row || pending.generation !== evaluationGeneration || pending.source.isCurrent?.() === false) return;
    const response = await api.evaluation.cancelRemoteOperation(row.op_id, {job_id:row.job_id,
      cohort_sha256:row.cohort_sha256, evaluation_binding_sha256:row.evaluation_binding_sha256});
    if (pending.generation !== evaluationGeneration || pending.source.isCurrent?.() === false) return;
    if (response.op_id !== row.op_id || response.evaluation_binding_sha256 !== row.evaluation_binding_sha256) throw new Error('취소 응답의 평가 바인딩이 다릅니다.');
    set({remoteOperation: response});
  },
  loadEvaluation: async (jobId, source) => {
    calibrationSource = null;
    const selectedSource = source ? {...source} : undefined;
    // Keep selectors fixed while optional completed-model discovery is pending.
    source = selectedSource;
    const sourceCurrent = selectedSource?.folderPath ? captureCalibrationSource(selectedSource) : null;
    if (selectedSource?.folderPath && !sourceCurrent) return;
    const training = useTrainingStore.getState();
    const completedCurrentJob = training.isCurrentData && training.status === 'completed' ? training.jobId : null;
    const requestedJob = jobId || completedCurrentJob || get().jobId;
    const explicitScopedJob=Boolean(jobId&&source?.folderPath);
    if (!explicitScopedJob&&((training.isCurrentData && !completedCurrentJob)
        || (!get().allowLatestRecovery && requestedJob !== completedCurrentJob)
        || (!requestedJob && (!get().allowLatestRecovery || !source?.folderPath)))) {
      evaluationGeneration += 1;
      currentHeatmapRequestId += 1;
      set({ executionEvidence:null, remoteRequest:null, remoteOperation:null, remoteOperationError:null, isLoading: false, jobId: null, persistedThreshold: null, sampleFilter: get().sampleFilter === 'saved_escape' ? 'all' : get().sampleFilter, metrics: {}, classSemantics: null, confusionMatrix: null,
        testPredictions: [], filteredPredictions: [], selectedPrediction: null, selectedCell: null,
        overkillAnalysis: null, heatmapOverlayBase64: null, heatmapLoading: false, heatmapError: null,
        errorMessage: '현재 데이터로 학습한 모델이 없습니다. 3단계에서 학습을 완료하세요.' });
      return;
    }
    const generation = ++evaluationGeneration;
    invalidateHeatmap();
    // The renderer store is transient. Let the backend resolve its latest
    // completed checkpoint when this window has lost the training job ID.
    set({ isLoading: true, persistedThreshold: null, sampleFilter: get().sampleFilter === 'saved_escape' ? 'all' : get().sampleFilter, errorMessage: null, classSemantics: null, overkillAnalysis: null,
      executionEvidence:null, remoteOperation:null, remoteOperationError:null,
      remoteRequest: !source?.executionTarget && requestedJob && source?.evaluationDatasetVersionId && source.computeProfileId
        ? {jobId:requestedJob, source, generation} : null });
    try {
      const native=Boolean(source?.executionTarget && source.evaluationDatasetVersionId);
      if(native && (!requestedJob || !source?.folderPath || !source.device
        || (source.executionTarget==='selected_compute')!==Boolean(source.computeProfileId)))throw new Error('평가 모델과 실행 위치를 명시적으로 선택하세요.');
      if (!requestedJob && source?.folderPath) {
        // The catalog is an optional discovery read: no completed matching
        // checkpoint is a 200 empty list, not a missing selected-job error.
        // Keep selected/current/stored job reads on the authoritative evaluator.
        const catalog = await api.flowchart.modelCatalog(source.folderPath);
        if (generation !== evaluationGeneration || source.isCurrent?.() === false || (sourceCurrent && !sourceCurrent())) return;
        if (!catalog.models.some(model => model.task === source!.task)) {
          currentHeatmapRequestId += 1;
          set({executionEvidence:null, remoteRequest:null, remoteOperation:null, remoteOperationError:null,
            isLoading:false, jobId:null, persistedThreshold:null, metrics:{}, classSemantics:null, confusionMatrix:null,
            testPredictions:[], filteredPredictions:[], selectedPrediction:null, selectedCell:null,
            overkillAnalysis:null, heatmapOverlayBase64:null, heatmapLoading:false, heatmapError:null,
            errorMessage:'현재 데이터로 학습한 모델이 없습니다. 3단계에서 학습을 완료하세요.'});
          return;
        }
      }
      const res = native ? await api.evaluation.runCoreCohort({task:source!.task,stage:'evaluate',execution_target:source!.executionTarget!,
        device:source!.device!,...(source!.computeProfileId?{compute_profile_id:source!.computeProfileId}:{}),
        params:{job_id:requestedJob!,dataset_path:source!.folderPath,evaluation_dataset_version_id:source!.evaluationDatasetVersionId!}})
      : await api.evaluation.getResults(requestedJob || undefined, source?.folderPath ? {
        sourceDatasetPath: source.folderPath,
        sourceTask: source.task,
        evaluationDatasetVersionId: source.evaluationDatasetVersionId, computeProfileId: source.computeProfileId, device: source.device,
      } : undefined);
      if (generation !== evaluationGeneration || source?.isCurrent?.() === false || (sourceCurrent && !sourceCurrent())) return;
      if (source?.evaluationDatasetVersionId && (res.common_cohort?.dataset_version_id !== source.evaluationDatasetVersionId || (res.compute_profile_id||null) !== (source.computeProfileId||null) || res.device !== source.device)) throw new Error("반환된 코호트 버전/실행 대상이 요청과 다릅니다.");
      if(native && (res.execution_target!==source!.executionTarget || res.execution?.execution_target!==source!.executionTarget
        || (res.execution?.compute_profile_id||null)!==(source!.computeProfileId||null) || res.execution?.device!==source!.device
        || !res.execution?.receipt_id || !/^[0-9a-f]{64}$/.test(res.execution.evidence_sha256)))throw new Error('저장된 평가 실행 기록의 대상이 요청과 다릅니다.');
      if(jobId&&res.job_id!==jobId)throw new Error('요청한 평가 작업과 반환된 작업 ID가 다릅니다. 작업 센터에서 다시 확인하세요.');
      const persistedThreshold = savedEvaluationThreshold(res, source?.task);
      set({
        executionEvidence:res, remoteRequest:null, remoteOperation:null,
        jobId: res.job_id,
        metrics: res.metrics || {},
        persistedThreshold,
        confidenceThreshold: persistedThreshold ?? .5,
        classSemantics: res.class_semantics || null,
        confusionMatrix: res.confusion_matrix || null,
        testPredictions: res.test_predictions || [],
        filteredPredictions: res.test_predictions || [],
        selectedCell: null,
        selectedPrediction: res.test_predictions?.[0] || null,
        isLoading: false,
        errorMessage: null,
      });
      calibrationSource = selectedSource && sourceCurrent ? {source:selectedSource, jobId:res.job_id, generation, isCurrent:sourceCurrent} : null;
      get().computeFilteredList();

      // Also proactively load overkill/underkill analysis
      if (!res.common_cohort) get().loadOverkillUnderkill().catch(() => {});

      if (res.test_predictions?.[0]) {
        get().updateHeatmap();
      }
    } catch (e) {
      if (generation !== evaluationGeneration || source?.isCurrent?.() === false || (sourceCurrent && !sourceCurrent())) return;
      set({ executionEvidence:null, remoteRequest:null, remoteOperation:null, isLoading: false, jobId: null, persistedThreshold: null, sampleFilter: get().sampleFilter === 'saved_escape' ? 'all' : get().sampleFilter, metrics: {}, classSemantics: null, confusionMatrix: null, testPredictions: [],
        filteredPredictions: [], selectedPrediction: null, selectedCell: null, overkillAnalysis: null,
        errorMessage: e instanceof Error ? e.message : '평가 결과를 불러올 수 없습니다.' });
      throw e;
    }
  },

  invalidateForDataChange: (allowSourceRecovery = false) => {
    calibrationSource = null;
    set({executionEvidence:null, remoteRequest:null, remoteOperation:null, remoteOperationError:null});
    evaluationGeneration += 1;
    currentHeatmapRequestId += 1;
    if (heatmapDebounceTimer) clearTimeout(heatmapDebounceTimer);
    heatmapDebounceTimer = null;
    set({
      jobId: null, persistedThreshold: null, allowLatestRecovery: allowSourceRecovery, isLoading: false,
      metrics: {}, classSemantics: null, confusionMatrix: null, selectedCell: null,
      testPredictions: [], filteredPredictions: [], selectedPrediction: null,
      heatmapOverlayBase64: null, heatmapLoading: false, heatmapError: null,
      isExportingReport: false, exportedReportPath: null, errorMessage: null,
      sampleFilter: 'all', overkillAnalysis: null, isAnalyzingTradeoff: false,
      isCalibrating: false, calibrationMessage: null, calibrationSuccess: false,
      benchmarkResult: null, isBenchmarking: false,
    });
  },

  selectCell: (trueClass, predClass) => {
    if (get().sampleFilter === 'saved_escape') get().setSampleFilter('escape');
    set({ selectedCell: { trueClass, predClass } });
    get().computeFilteredList();
  },

  clearCellSelection: () => {
    set({ selectedCell: null });
    get().computeFilteredList();
  },

  selectPrediction: async (selectedPrediction) => {
    if (!sameHeatmapImage(selectedPrediction, get().selectedPrediction)) invalidateHeatmap();
    set({ selectedPrediction });
    await get().updateHeatmap();
  },

  setConfidenceThreshold: (confidenceThreshold) => {
    if(!Number.isFinite(confidenceThreshold)||confidenceThreshold<0||(get().metrics.score_spec?.domain!=='distance'&&confidenceThreshold>1))return;
    if (confidenceThreshold !== get().confidenceThreshold || get().sampleFilter === 'saved_escape') invalidateHeatmap();
    set({ confidenceThreshold, ...(get().sampleFilter === 'saved_escape' ? {sampleFilter: 'escape' as const} : {}) });
    get().computeFilteredList();

    if (heatmapDebounceTimer) {
      clearTimeout(heatmapDebounceTimer);
    }
    heatmapDebounceTimer = setTimeout(() => {
      get().updateHeatmap();
    }, 120);
  },

  updateHeatmap: async () => {
    invalidateHeatmap();
    if (get().executionEvidence?.common_cohort) { set({errorMessage:'선택 코호트의 추가 분석/보정/속도 측정은 아직 지원되지 않습니다. 반환된 고정 평가 결과를 확인하세요.'}); return; }
    const state = get(), { selectedPrediction, jobId } = state;
    const heatmapThreshold = effectiveHeatmapThreshold(state);
    if (!selectedPrediction) return;
    const requestId = currentHeatmapRequestId, generation = evaluationGeneration;
    const epoch = getProjectContextGeneration(), transport = getApiPersistenceIdentity(), binding = calibrationSource;
    const savedReview = state.sampleFilter === 'saved_escape';
    const current = () => {
      const latest = get();
      return requestId === currentHeatmapRequestId && generation === evaluationGeneration
        && epoch === getProjectContextGeneration() && transport === getApiPersistenceIdentity()
        && (!binding || binding === calibrationSource && binding.isCurrent())
        && jobId === latest.jobId && sameHeatmapImage(selectedPrediction, latest.selectedPrediction)
        && savedReview === (latest.sampleFilter === 'saved_escape') && heatmapThreshold === effectiveHeatmapThreshold(latest);
    };
    set({ heatmapLoading: true });
    try {
      const res = await api.evaluation.getHeatmap(
        selectedPrediction.image_id,
        jobId || undefined,
        heatmapThreshold,
        selectedPrediction.file_path,
        state.metrics.score_spec ? {...state.metrics.score_spec,threshold:heatmapThreshold} : undefined
      );
      if (current()) {
        set({ heatmapOverlayBase64: res.overlay_base64 || null, heatmapLoading: false, heatmapError: null });
      }
    } catch {
      if (current()) {
        set({ heatmapOverlayBase64: null, heatmapLoading: false, heatmapError: '현재 기준의 열지도를 불러오지 못했습니다.' });
      }
    }
  },

  exportReport: async (format) => {
    if (get().executionEvidence?.common_cohort) throw new Error('선택 코호트 보고서 내보내기는 아직 지원되지 않습니다. 원래 학습 코호트 보고서로 대체하지 않습니다.');
    if (!get().jobId) throw new Error('평가를 완료한 모델이 있어야 보고서를 내보낼 수 있습니다.');
    const jobId = get().jobId, generation = evaluationGeneration;
    const sameScope = captureCalibrationSource({folderPath: useDatasetStore.getState().folderPath, task: useProjectStore.getState().task});
    if (!sameScope) throw new Error('현재 프로젝트의 원본과 평가 결과를 확인한 뒤 보고서를 다시 저장하세요.');
    const current = () => generation === evaluationGeneration && sameScope() && get().jobId === jobId;
    set({ isExportingReport: true, errorMessage: null });
    try {
      const res = await api.report.export({
        job_id: jobId || undefined,
        format,
        include_images: true,
      });
      if (current()) set({ isExportingReport: false, exportedReportPath: res.file_path });
      return res.file_path;
    } catch (e) {
      if (current()) set({ isExportingReport: false, errorMessage: e instanceof Error ? e.message : '보고서 내보내기 실패' });
      throw e;
    }
  },

  loadOverkillUnderkill: async (targetUnderkill, escapeCost, scrapCost) => {
    if (get().executionEvidence?.common_cohort) { set({errorMessage:'선택 코호트의 추가 분석/보정/속도 측정은 아직 지원되지 않습니다. 반환된 고정 평가 결과를 확인하세요.'}); return; }
    const binding = calibrationSource;
    const valid = () => binding !== null && binding === calibrationSource && binding.generation === evaluationGeneration
      && binding.jobId === get().jobId && binding.isCurrent();
    if (get().isLoading || !valid()) return;
    const requestId = ++analysisRequest;
    const current = () => requestId === analysisRequest && valid();
    const { jobId, confidenceThreshold } = get();
    if (!jobId || get().metrics.score_spec?.domain === 'distance') {
      set({ overkillAnalysis: null, isAnalyzingTradeoff: false });
      return;
    }
    const tu = targetUnderkill !== undefined ? targetUnderkill : get().targetMaxUnderkill;
    const ce = escapeCost !== undefined ? escapeCost : get().costEscape;
    const cs = scrapCost !== undefined ? scrapCost : get().costScrap;

    set({ isAnalyzingTradeoff: true, targetMaxUnderkill: tu, costEscape: ce, costScrap: cs });
    try {
      const res = await api.evaluation.getOverkillUnderkill({
        job_id: jobId || undefined,
        source_dataset_path: binding!.source.folderPath,
        source_task: binding!.source.task,
        target_max_underkill: tu,
        cost_escape: ce,
        cost_scrap: cs,
        current_threshold: confidenceThreshold,
      });
      if (!current()) return;
      set({ overkillAnalysis: res, isAnalyzingTradeoff: false });
      get().computeFilteredList();
    } catch (e) {
      if (!current()) return;
      console.error('Failed to load overkill/underkill analysis:', e);
      set({ isAnalyzingTradeoff: false, overkillAnalysis: null });
    }
  },

  applyOptimalThreshold: () => {
    if (get().isLoading) return;
    const { overkillAnalysis, testPredictions, classSemantics } = get();
    const hasBothClasses = testPredictions.some((p) => isDefectLabel(p.ground_truth, classSemantics?.roles))
      && testPredictions.some((p) => isNormalLabel(p.ground_truth, classSemantics?.roles));
    if (hasBothClasses && overkillAnalysis?.optimal_threshold !== undefined) {
      get().setConfidenceThreshold(overkillAnalysis.optimal_threshold);
    }
  },

  calibrateZeroEscape: async (jobIdOverride) => {
    if (get().isLoading) return;
    if (get().executionEvidence?.common_cohort) { set({errorMessage:'선택 코호트의 추가 분석/보정/속도 측정은 아직 지원되지 않습니다. 반환된 고정 평가 결과를 확인하세요.'}); return; }
    const binding = calibrationSource;
    const valid = () => binding !== null && binding === calibrationSource && binding.generation === evaluationGeneration
      && binding.jobId === get().jobId && binding.isCurrent();
    if (!valid() || (jobIdOverride && jobIdOverride !== binding!.jobId)) return;
    const requestId = ++calibrationRequest;
    const current = () => requestId === calibrationRequest && valid();
    const { jobId, costEscape, costScrap, confidenceThreshold, testPredictions, classSemantics } = get();
    const activeJob = jobIdOverride || jobId;
    if(get().metrics.anomaly_mode && get().metrics.evaluated_split==='test'){
      set({calibrationSuccess:false,calibrationMessage:'이상탐지 시험 데이터는 평가에만 사용합니다. 저장된 검증·보정 임계값을 유지하세요.'});return;
    }
    if(get().metrics.score_spec?.domain==='distance'){
      set({calibrationSuccess:false,calibrationMessage:'거리 점수는 학습·검증 데이터로 보정하세요. 현재 모델 임계값 또는 보정 식별자가 연결된 수동 임계값을 사용합니다.'});return;
    }
    if (!activeJob || !testPredictions.some((p) => isDefectLabel(p.ground_truth, classSemantics?.roles))
        || !testPredictions.some((p) => isNormalLabel(p.ground_truth, classSemantics?.roles))) {
      set({ calibrationMessage: 'NG와 OK 검증 예측이 모두 있어야 임계값을 적용할 수 있습니다.', calibrationSuccess: false });
      return;
    }
    set({ isCalibrating: true, calibrationMessage: null, calibrationSuccess: false });
    try {
      const data: OverkillUnderkillAnalysis = await api.evaluation.calibrateZeroEscape({
        job_id: activeJob || undefined,
        source_dataset_path: binding!.source.folderPath,
        source_task: binding!.source.task,
        target_max_underkill: 0,
        cost_escape: costEscape,
        cost_scrap: costScrap,
        current_threshold: confidenceThreshold,
        apply_to_eval_results: true,
      });
      if (!current()) return;
      // A successful POST has already persisted apply_to_eval_results; malformed
      // numeric/evidence carriers must still leave the prior authority untouched.
      const optimalTh = calibrationThreshold(data, get().metrics);
      if (optimalTh === null) throw new Error('저장된 보정 임계값을 확인할 수 없습니다. 평가 결과를 새로고침하세요.');

      invalidateHeatmap();
      set({
        overkillAnalysis: data,
        confidenceThreshold: optimalTh,
        persistedThreshold: optimalTh,
        isCalibrating: false,
        calibrationSuccess: true,
        calibrationMessage: (data as any).message || `미검 제로화 완료: 최적 임계값 τ* = ${optimalTh.toFixed(4)}`,
      });

      get().computeFilteredList();
      await get().updateHeatmap();
    } catch (err: any) {
      if (!current()) return;
      console.error('Zero-escape calibration API failed, falling back:', err);
      set({ isCalibrating: false, calibrationSuccess: false,
        calibrationMessage: err?.message || '미검 제로화 요청 실패' });
    }
  },

  runBenchmark: async (iterations = 25, resolution = 256) => {
    if (get().executionEvidence?.common_cohort) { set({errorMessage:'선택 코호트의 추가 분석/보정/속도 측정은 아직 지원되지 않습니다. 반환된 고정 평가 결과를 확인하세요.'}); return; }
    const generation = evaluationGeneration;
    const training = useTrainingStore.getState();
    const jobId = (training.isCurrentData ? training.jobId : null) || get().jobId;
    if (!jobId) {
      set({ benchmarkResult: null, errorMessage: '학습 모델이 없어 속도를 측정할 수 없습니다.' });
      return;
    }
    set({ isBenchmarking: true });
    try {
      const res = await api.evaluation.runBenchmark({
        job_id: jobId || undefined,
        iterations,
        resolution,
      });
      if (generation !== evaluationGeneration) return;
      set({ benchmarkResult: res, isBenchmarking: false });
    } catch (e) {
      if (generation !== evaluationGeneration) return;
      console.error('Benchmark failed:', e);
      set({ isBenchmarking: false, benchmarkResult: null,
        errorMessage: e instanceof Error ? e.message : '속도 측정 실패' });
    }
  },
}));
