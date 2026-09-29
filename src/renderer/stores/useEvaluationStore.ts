/**
 * src/renderer/stores/useEvaluationStore.ts
 * Evaluation state: interactive confusion matrix filtering, defect heatmaps,
 * overkill vs escape visual feedback, and 1-click zero-escape calibration.
 */

import { create } from 'zustand';
import type {
  BenchmarkResult,
  ConfusionMatrixData,
  OverkillUnderkillAnalysis,
  TestPredictionItem,
} from '../types';
import { api, getApiBaseUrl } from '../services/api';

export type SampleVerdict = 'ESCAPE' | 'OVERKILL' | 'CORRECT_NG' | 'CORRECT_OK';
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

export function isDefectLabel(label?: string | number): boolean {
  if (label === undefined || label === null) return false;
  if (typeof label === 'number') return label !== 0;
  const clean = String(label).trim().toLowerCase();
  const normalSet = new Set(['ok', 'normal', 'pass', 'good', '0', 'background', 'ok_normal', 'true_ok', 'ok_chip']);
  return !normalSet.has(clean);
}

export function computeSampleVerdict(
  item: TestPredictionItem,
  threshold: number,
  overkillAnalysis?: OverkillUnderkillAnalysis | null
): SampleVerdict {
  // 1. Check overkillAnalysis.sample_details if available
  const sampleDetails = (overkillAnalysis as any)?.sample_details;
  if (sampleDetails && Array.isArray(sampleDetails)) {
    const detail = sampleDetails.find(
      (d: any) => d.image_id === item.image_id || d.file_name === item.file_name || d.file_path === item.file_path
    );
    if (detail) {
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

  // 2. Fallback heuristic
  const isDefect = (item as any).is_defect !== undefined
    ? Boolean((item as any).is_defect)
    : isDefectLabel(item.ground_truth);

  let defectScore: number;
  if ((item as any).defect_score !== undefined) {
    defectScore = (item as any).defect_score;
  } else {
    const isPredDefect = isDefectLabel(item.predicted_class);
    defectScore = isPredDefect ? item.confidence : 1.0 - item.confidence;
  }

  const predictedNg = defectScore >= threshold;
  if (isDefect) {
    return predictedNg ? 'CORRECT_NG' : 'ESCAPE';
  } else {
    return predictedNg ? 'OVERKILL' : 'CORRECT_OK';
  }
}

interface EvaluationState {
  jobId: string | null;
  isLoading: boolean;
  metrics: Record<string, any>;
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

  // F33: Sample Filtering & Verdicts
  sampleFilter: SampleFilter;
  setSampleFilter: (filter: SampleFilter) => void;

  // Neuro-T Industrial Overkill/Underkill Analysis
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

  loadEvaluation: (jobId?: string) => Promise<void>;
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
  jobId: null,
  isLoading: false,
  metrics: {},
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
    const { testPredictions, selectedCell, confusionMatrix, sampleFilter, confidenceThreshold, overkillAnalysis } = get();

    // 1. Confusion Matrix cell filter (Robust Dual-Key Matching)
    let list = testPredictions;
    if (selectedCell && confusionMatrix) {
      const key = `${selectedCell.trueClass}:${selectedCell.predClass}`;
      const allowedPaths = new Set(confusionMatrix.cell_samples?.[key] || []);
      if (allowedPaths.size > 0) {
        list = list.filter(
          (item) =>
            allowedPaths.has(item.file_path) ||
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
        const v = computeSampleVerdict(item, confidenceThreshold, overkillAnalysis);
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

  loadEvaluation: async (jobId) => {
    set({ isLoading: true });
    try {
      const res = await api.evaluation.getResults(jobId);
      set({
        jobId: res.job_id,
        metrics: res.metrics || {},
        confusionMatrix: res.confusion_matrix || null,
        testPredictions: res.test_predictions || [],
        filteredPredictions: res.test_predictions || [],
        selectedCell: null,
        selectedPrediction: res.test_predictions?.[0] || null,
        isLoading: false,
      });

      // Also proactively load overkill/underkill analysis
      get().loadOverkillUnderkill().catch(() => {});

      if (res.test_predictions?.[0]) {
        get().updateHeatmap();
      }
    } catch (e) {
      set({ isLoading: false });
      throw e;
    }
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
    const { selectedPrediction, confidenceThreshold, jobId } = get();
    if (!selectedPrediction) return;
    const requestId = ++currentHeatmapRequestId;
    set({ heatmapLoading: true });
    try {
      const res = await api.evaluation.getHeatmap(
        selectedPrediction.image_id,
        jobId || undefined,
        confidenceThreshold,
        selectedPrediction.file_path
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
    set({ isExportingReport: true });
    try {
      const res = await api.report.export({
        job_id: get().jobId || 'latest',
        format,
        include_images: true,
      });
      set({ isExportingReport: false, exportedReportPath: res.file_path });
      return res.file_path;
    } catch (e) {
      set({ isExportingReport: false });
      throw e;
    }
  },

  loadOverkillUnderkill: async (targetUnderkill, escapeCost, scrapCost) => {
    const { jobId, confidenceThreshold } = get();
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
      set({ overkillAnalysis: res, isAnalyzingTradeoff: false });
      get().computeFilteredList();
    } catch (e) {
      console.error('Failed to load overkill/underkill analysis:', e);
      set({ isAnalyzingTradeoff: false });
    }
  },

  applyOptimalThreshold: () => {
    const { overkillAnalysis } = get();
    if (overkillAnalysis?.optimal_threshold !== undefined) {
      get().setConfidenceThreshold(overkillAnalysis.optimal_threshold);
    }
  },

  calibrateZeroEscape: async (jobIdOverride) => {
    const { jobId, costEscape, costScrap, confidenceThreshold } = get();
    const activeJob = jobIdOverride || jobId;
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
      console.error('Zero-escape calibration API failed, falling back:', err);
      // Fallback to local analysis optimal threshold if available
      const fallbackTh = get().overkillAnalysis?.optimal_threshold;
      if (fallbackTh !== undefined) {
        set({
          confidenceThreshold: fallbackTh,
          isCalibrating: false,
          calibrationSuccess: true,
          calibrationMessage: `분석 권장 임계값 적용: τ* = ${fallbackTh.toFixed(4)} (미검 0건 달성)`,
        });
        get().computeFilteredList();
        await get().updateHeatmap();
      } else {
        set({
          isCalibrating: false,
          calibrationSuccess: false,
          calibrationMessage: err?.message || '미검 제로화 요청 실패',
        });
      }
    }
  },

  runBenchmark: async (iterations = 25, resolution = 256) => {
    const { jobId } = get();
    set({ isBenchmarking: true });
    try {
      const res = await api.evaluation.runBenchmark({
        job_id: jobId || undefined,
        iterations,
        resolution,
      });
      set({ benchmarkResult: res, isBenchmarking: false });
    } catch (e) {
      console.error('Benchmark failed:', e);
      set({ isBenchmarking: false });
    }
  },
}));
