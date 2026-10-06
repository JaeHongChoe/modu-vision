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
import { api, getApiBaseUrl, type RemoteEvaluationOperation, type RemoteEvaluationResult } from '../services/api';
import { useTrainingStore } from './useTrainingStore';
import { classRole, isDefectClass, type ClassRoles } from '../utils/classSemantics';

export type SampleVerdict = 'ESCAPE' | 'OVERKILL' | 'CORRECT_NG' | 'CORRECT_OK' | 'REVIEW';
export type SampleFilter =
  | 'all'
  | 'escape'
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

export interface EvaluationSource {
  folderPath: string; task: VisionTask; labelsetId?: string;
  evaluationDatasetVersionId?: string; computeProfileId?: string; device?: string;
  executionTarget?: 'local'|'selected_compute';
  isCurrent?: () => boolean;
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
  heatmapOverlayBase64: string | null;
  heatmapLoading: boolean;
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
  heatmapOverlayBase64: null,
  heatmapLoading: false,
  isExportingReport: false,
  exportedReportPath: null,
  errorMessage: null,

  sampleFilter: 'all',
  setSampleFilter: (sampleFilter) => {
    set({ sampleFilter });
    get().computeFilteredList();
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
    const { testPredictions, selectedCell, confusionMatrix, sampleFilter, confidenceThreshold, overkillAnalysis, classSemantics } = get();

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
        const v = computeSampleVerdict(item, confidenceThreshold, overkillAnalysis, classSemantics?.roles);
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

    set({
      filteredPredictions: list,
      selectedPrediction: newSelected,
    });

    if (newSelected && (!currentSelected || newSelected.image_id !== currentSelected.image_id)) {
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
    const training = useTrainingStore.getState();
    const completedCurrentJob = training.isCurrentData && training.status === 'completed' ? training.jobId : null;
    const requestedJob = jobId || completedCurrentJob || get().jobId;
    const explicitScopedJob=Boolean(jobId&&source?.folderPath);
    if (!explicitScopedJob&&((training.isCurrentData && !completedCurrentJob)
        || (!get().allowLatestRecovery && requestedJob !== completedCurrentJob)
        || (!requestedJob && (!get().allowLatestRecovery || !source?.folderPath)))) {
      evaluationGeneration += 1;
      currentHeatmapRequestId += 1;
      set({ executionEvidence:null, remoteRequest:null, remoteOperation:null, remoteOperationError:null, isLoading: false, jobId: null, metrics: {}, classSemantics: null, confusionMatrix: null,
        testPredictions: [], filteredPredictions: [], selectedPrediction: null, selectedCell: null,
        overkillAnalysis: null, heatmapOverlayBase64: null, heatmapLoading: false,
        errorMessage: '현재 데이터로 학습한 모델이 없습니다. 3단계에서 학습을 완료하세요.' });
      return;
    }
    const generation = ++evaluationGeneration;
    // The renderer store is transient. Let the backend resolve its latest
    // completed checkpoint when this window has lost the training job ID.
    set({ isLoading: true, errorMessage: null, classSemantics: null, overkillAnalysis: null,
      executionEvidence:null, remoteOperation:null, remoteOperationError:null,
      remoteRequest: !source?.executionTarget && requestedJob && source?.evaluationDatasetVersionId && source.computeProfileId
        ? {jobId:requestedJob, source, generation} : null });
    try {
      const native=Boolean(source?.executionTarget && source.evaluationDatasetVersionId);
      if(native && (!requestedJob || !source?.folderPath || !source.device
        || (source.executionTarget==='selected_compute')!==Boolean(source.computeProfileId)))throw new Error('평가 모델과 실행 위치를 명시적으로 선택하세요.');
      const res = native ? await api.evaluation.runCoreCohort({task:source!.task,stage:'evaluate',execution_target:source!.executionTarget!,
        device:source!.device!,...(source!.computeProfileId?{compute_profile_id:source!.computeProfileId}:{}),
        params:{job_id:requestedJob!,dataset_path:source!.folderPath,evaluation_dataset_version_id:source!.evaluationDatasetVersionId!}})
      : await api.evaluation.getResults(requestedJob || undefined, source?.folderPath ? {
        sourceDatasetPath: source.folderPath,
        sourceTask: source.task,
        evaluationDatasetVersionId: source.evaluationDatasetVersionId, computeProfileId: source.computeProfileId, device: source.device,
      } : undefined);
      if (generation !== evaluationGeneration || source?.isCurrent?.() === false) return;
      if (source?.evaluationDatasetVersionId && (res.common_cohort?.dataset_version_id !== source.evaluationDatasetVersionId || (res.compute_profile_id||null) !== (source.computeProfileId||null) || res.device !== source.device)) throw new Error("반환된 코호트 버전/실행 대상이 요청과 다릅니다.");
      if(native && (res.execution_target!==source!.executionTarget || res.execution?.execution_target!==source!.executionTarget
        || (res.execution?.compute_profile_id||null)!==(source!.computeProfileId||null) || res.execution?.device!==source!.device
        || !res.execution?.receipt_id || !/^[0-9a-f]{64}$/.test(res.execution.evidence_sha256)))throw new Error('저장된 평가 실행 기록의 대상이 요청과 다릅니다.');
      if(jobId&&res.job_id!==jobId)throw new Error('요청한 평가 작업과 반환된 작업 ID가 다릅니다. 작업 센터에서 다시 확인하세요.');
      set({
        executionEvidence:res, remoteRequest:null, remoteOperation:null,
        jobId: res.job_id,
        metrics: res.metrics || {},
        confidenceThreshold: Number.isFinite(res.metrics?.active_threshold) ? res.metrics.active_threshold : .5,
        classSemantics: res.class_semantics || null,
        confusionMatrix: res.confusion_matrix || null,
        testPredictions: res.test_predictions || [],
        filteredPredictions: res.test_predictions || [],
        selectedCell: null,
        selectedPrediction: res.test_predictions?.[0] || null,
        isLoading: false,
        errorMessage: null,
      });
      get().computeFilteredList();

      // Also proactively load overkill/underkill analysis
      if (!res.common_cohort) get().loadOverkillUnderkill().catch(() => {});

      if (res.test_predictions?.[0]) {
        get().updateHeatmap();
      }
    } catch (e) {
      if (generation !== evaluationGeneration || source?.isCurrent?.() === false) return;
      set({ executionEvidence:null, remoteRequest:null, remoteOperation:null, isLoading: false, jobId: null, metrics: {}, classSemantics: null, confusionMatrix: null, testPredictions: [],
        filteredPredictions: [], selectedPrediction: null, selectedCell: null, overkillAnalysis: null,
        errorMessage: e instanceof Error ? e.message : '평가 결과를 불러올 수 없습니다.' });
      throw e;
    }
  },

  invalidateForDataChange: (allowSourceRecovery = false) => {
    set({executionEvidence:null, remoteRequest:null, remoteOperation:null, remoteOperationError:null});
    evaluationGeneration += 1;
    currentHeatmapRequestId += 1;
    if (heatmapDebounceTimer) clearTimeout(heatmapDebounceTimer);
    heatmapDebounceTimer = null;
    set({
      jobId: null, allowLatestRecovery: allowSourceRecovery, isLoading: false,
      metrics: {}, classSemantics: null, confusionMatrix: null, selectedCell: null,
      testPredictions: [], filteredPredictions: [], selectedPrediction: null,
      heatmapOverlayBase64: null, heatmapLoading: false,
      isExportingReport: false, exportedReportPath: null, errorMessage: null,
      sampleFilter: 'all', overkillAnalysis: null, isAnalyzingTradeoff: false,
      isCalibrating: false, calibrationMessage: null, calibrationSuccess: false,
      benchmarkResult: null, isBenchmarking: false,
    });
  },

  selectCell: (trueClass, predClass) => {
    set({ selectedCell: { trueClass, predClass } });
    get().computeFilteredList();
  },

  clearCellSelection: () => {
    set({ selectedCell: null });
    get().computeFilteredList();
  },

  selectPrediction: async (selectedPrediction) => {
    set({ selectedPrediction });
    await get().updateHeatmap();
  },

  setConfidenceThreshold: (confidenceThreshold) => {
    if(!Number.isFinite(confidenceThreshold)||confidenceThreshold<0||(get().metrics.score_spec?.domain!=='distance'&&confidenceThreshold>1))return;
    set({ confidenceThreshold });
    get().computeFilteredList();

    if (heatmapDebounceTimer) {
      clearTimeout(heatmapDebounceTimer);
    }
    heatmapDebounceTimer = setTimeout(() => {
      get().updateHeatmap();
    }, 120);
  },

  updateHeatmap: async () => {
    if (get().executionEvidence?.common_cohort) { set({errorMessage:'선택 코호트의 추가 분석/보정/속도 측정은 아직 지원되지 않습니다. 반환된 고정 평가 결과를 확인하세요.'}); return; }
    const { selectedPrediction, confidenceThreshold, jobId } = get();
    if (!selectedPrediction) return;
    const requestId = ++currentHeatmapRequestId;
    set({ heatmapLoading: true });
    try {
      const res = await api.evaluation.getHeatmap(
        selectedPrediction.image_id,
        jobId || undefined,
        confidenceThreshold,
        selectedPrediction.file_path,
        get().metrics.score_spec ? {...get().metrics.score_spec,threshold:confidenceThreshold} : undefined
      );
      if (requestId === currentHeatmapRequestId) {
        set({ heatmapOverlayBase64: res.overlay_base64, heatmapLoading: false });
      }
    } catch {
      if (requestId === currentHeatmapRequestId) {
        set({ heatmapLoading: false });
      }
    }
  },

  exportReport: async (format) => {
    if (get().executionEvidence?.common_cohort) throw new Error('선택 코호트 보고서 내보내기는 아직 지원되지 않습니다. 원래 학습 코호트 보고서로 대체하지 않습니다.');
    if (!get().jobId) throw new Error('평가를 완료한 모델이 있어야 보고서를 내보낼 수 있습니다.');
    set({ isExportingReport: true });
    try {
      const res = await api.report.export({
        job_id: get().jobId || undefined,
        format,
        include_images: true,
      });
      set({ isExportingReport: false, exportedReportPath: res.file_path });
      return res.file_path;
    } catch (e) {
      set({ isExportingReport: false, errorMessage: e instanceof Error ? e.message : '보고서 내보내기 실패' });
      throw e;
    }
  },

  loadOverkillUnderkill: async (targetUnderkill, escapeCost, scrapCost) => {
    if (get().executionEvidence?.common_cohort) { set({errorMessage:'선택 코호트의 추가 분석/보정/속도 측정은 아직 지원되지 않습니다. 반환된 고정 평가 결과를 확인하세요.'}); return; }
    const generation = evaluationGeneration;
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
        target_max_underkill: tu,
        cost_escape: ce,
        cost_scrap: cs,
        current_threshold: confidenceThreshold,
      });
      if (generation !== evaluationGeneration) return;
      set({ overkillAnalysis: res, isAnalyzingTradeoff: false });
      get().computeFilteredList();
    } catch (e) {
      if (generation !== evaluationGeneration) return;
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
    const generation = evaluationGeneration;
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
      const base = await getApiBaseUrl();
      const res = await fetch(`${base}/api/evaluation/zero-escape-calibrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: activeJob || undefined,
          target_max_underkill: 0,
          cost_escape: costEscape,
          cost_scrap: costScrap,
          current_threshold: confidenceThreshold,
          apply_to_eval_results: true,
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data: OverkillUnderkillAnalysis = await res.json();
      if (generation !== evaluationGeneration) return;
      const optimalTh = data.optimal_threshold ?? 0.50;

      set({
        overkillAnalysis: data,
        confidenceThreshold: optimalTh,
        isCalibrating: false,
        calibrationSuccess: true,
        calibrationMessage: (data as any).message || `미검 제로화 완료: 최적 임계값 τ* = ${optimalTh.toFixed(4)}`,
      });

      get().computeFilteredList();
      await get().updateHeatmap();
    } catch (err: any) {
      if (generation !== evaluationGeneration) return;
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
