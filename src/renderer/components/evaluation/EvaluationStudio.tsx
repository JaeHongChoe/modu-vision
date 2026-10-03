/**
 * src/renderer/components/evaluation/EvaluationStudio.tsx
 * Step 4: Quality Evaluation & Overkill Studio.
 * Features High-Contrast Heatmap Confusion Matrix with marginal metrics, 4-tab industrial sample filtering,
 * interactive Zero-Escape tau* tradeoff curve, and synchronized dual-viewport defect heatmap.
 */

import {useTaskHandoff} from '../training/useTaskHandoff';
import React, { useEffect, useState, useMemo } from 'react';
import {
  Activity,
  FileText,
  Download,
  ExternalLink,
  CheckCircle2,
  RefreshCw,
  Target,
  AlertTriangle,
  Zap,
  Loader2,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import type { FlowModelTask } from '../../types';
import {
  useEvaluationStore,
  computeSampleVerdict,
  SampleVerdict,
  isDefectLabel,
  isNormalLabel,
} from '../../stores/useEvaluationStore';
import { resolveApiUrl } from '../../services/api';
import { host } from '../../services/hostAdapter';
import { OperatorGuidanceBanner } from '../common/OperatorGuidanceBanner';
import { JargonTooltip } from '../common/JargonTooltip';
import { GuardrailBanner } from '../common/GuardrailBanner';
import { ZeroEscapeTradeoffChart } from './ZeroEscapeTradeoffChart';
import { SynchronizedDualViewport } from './SynchronizedDualViewport';
import { DetectionEvaluationGrains, summarizeDetectionGrains } from './DetectionEvaluationGrains';
import { ModelComparisonPanel } from './ModelComparisonPanel';
import { ModelDeploymentPanel } from './ModelDeploymentPanel';
import { EvaluationHistoryPanel } from './EvaluationHistoryPanel';
import {EvaluationEvidencePanel} from './EvaluationEvidencePanel';

export const SampleVerdictBadge: React.FC<{ verdict: SampleVerdict; compact?: boolean }> = ({
  verdict,
}) => {
  switch (verdict) {
    case 'ESCAPE':
      return (
        <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded-[4px] text-[10px] bg-[#450A0A] text-[#FCA5A5] border border-[#EF4444] font-mono font-bold">
          <span className="w-1.5 h-1.5 rounded-full bg-[#EF4444] animate-pulse" />
          <span>🚨 FN 미검 (Escape)</span>
        </span>
      );
    case 'OVERKILL':
      return (
        <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded-[4px] text-[10px] bg-[#451A03] text-[#FCD34D] border border-[#F59E0B] font-mono font-bold">
          <span className="w-1.5 h-1.5 rounded-full bg-[#F59E0B]" />
          <span>⚠️ FP 과검 (Overkill)</span>
        </span>
      );
    case 'CORRECT_NG':
      return (
        <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded-[4px] text-[10px] bg-[#083344] text-[#67E8F9] border border-[#06B6D4] font-mono font-semibold">
          <span className="w-1.5 h-1.5 rounded-full bg-[#06B6D4]" />
          <span>🎯 TP 결함검출</span>
        </span>
      );
    case 'CORRECT_OK':
      return (
        <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded-[4px] text-[10px] bg-[#064E3B] text-[#6EE7B7] border border-[#10B981] font-mono font-semibold">
          <span className="w-1.5 h-1.5 rounded-full bg-[#10B981]" />
          <span>🟢 TN 정상합격</span>
        </span>
      );
    case 'REVIEW':
    default:
      return <span className="rounded border border-slate-500 px-1.5 py-0.5 text-[10px] text-slate-300">확인 필요 (REVIEW)</span>;
  }
};

export const EvaluationStudio: React.FC = () => {
  const handoff=useTaskHandoff();
  const { language, setStep, task } = useProjectStore();
  const projectDir = useProjectStore((state) => state.projectDir);
  const warmCandidateJobId = useTrainingStore((state) => state.status === 'completed' && state.warmStartParentJobId ? state.jobId : null);
  const warmParentJobId = useTrainingStore((state) => state.status === 'completed' ? state.warmStartParentJobId : null);
  const folderPath = useDatasetStore((state) => state.folderPath);
  const datasetKey = useDatasetStore((state) => state.datasetKey);
  const datasetIsLoading = useDatasetStore((state) => state.isLoading);
  const importError = useDatasetStore((state) => state.importError);
  const sourceFolder = !datasetIsLoading && !importError && datasetKey === `${folderPath}\0${task}` ? folderPath : '';
  const [evalTab, setEvalTab] = useState<'matrix' | 'overkill'>('matrix');
  const [comparisonTask, setComparisonTask] = useState<FlowModelTask>(task);
  const [reportError, setReportError] = useState<string | null>(null);
  const [hoveredCell, setHoveredCell] = useState<{
    i: number;
    j: number;
    trueClass: string;
    predClass: string;
    val: number;
    norm: number;
  } | null>(null);

  const {
    jobId,
    isLoading,
    metrics,
    classSemantics,
    confusionMatrix,
    selectedCell,
    testPredictions,
    filteredPredictions,
    selectedPrediction,
    confidenceThreshold,
    heatmapOverlayBase64,
    heatmapLoading,
    isExportingReport,
    exportedReportPath,
    errorMessage,
    overkillAnalysis,
    sampleFilter,
    setSampleFilter,
    isCalibrating,
    calibrationMessage,
    loadEvaluation,
    selectCell,
    clearCellSelection,
    selectPrediction,
    setConfidenceThreshold,
    exportReport,
    loadOverkillUnderkill,
    applyOptimalThreshold,
    calibrateZeroEscape,
  } = useEvaluationStore();

  useEffect(() => {
    loadEvaluation(handoff?.step===4&&handoff.family===task?handoff.jobId:undefined, sourceFolder ? { folderPath: sourceFolder, task } : undefined).catch(() => {});
    loadOverkillUnderkill().catch(() => {});
  }, [loadEvaluation, loadOverkillUnderkill, sourceFolder, task,handoff?.jobId,handoff?.selectionId]);

  useEffect(() => { setComparisonTask(task); }, [task]);

  const handleExportHtml = async () => {
    setReportError(null);
    try {
      const filePath = await exportReport('html');
      // The desktop app opens the saved report; a browser cannot open a file on the backend's computer, so it says where it is.
      if (host.can('openPaths')) await host.openPath(filePath);
      else setReportError(`HTML 보고서를 저장했습니다: ${filePath}. 브라우저에서는 이 파일을 열 수 없습니다.`);
    } catch (error) {
      setReportError(error instanceof Error ? error.message : 'HTML 보고서 내보내기 실패');
    }
  };

  const handleExportJson = async () => {
    setReportError(null);
    try {
      await exportReport('json');
    } catch (error) {
      setReportError(error instanceof Error ? error.message : 'JSON 보고서 내보내기 실패');
    }
  };

  const classes = confusionMatrix?.classes || confusionMatrix?.class_names || [];
  const matrix = confusionMatrix?.matrix || [];
  const normMatrix = confusionMatrix?.normalized_matrix || [];

  // Helper for technical metric keys
  const getMetricTermKey = (key: string): string => {
    const k = key.toLowerCase();
    if (k.includes('map_50') || k.includes('map50')) return 'map_50';
    if (k.includes('auroc') || k.includes('auc')) return 'auroc';
    if (k.includes('dice') || k.includes('mdice')) return 'dice';
    if (k.includes('iou') || k.includes('miou')) return 'iou';
    if (k.includes('p95') || k.includes('latency')) return 'p95_latency';
    return '';
  };

  // Compute live counts for 4-quadrant verification
  const sampleCounts = useMemo(() => {
    let escapes = 0;
    let overkills = 0;
    let normalOks = 0;
    let correctNgs = 0;

    testPredictions.forEach((p) => {
      const v = computeSampleVerdict(p, confidenceThreshold, overkillAnalysis, classSemantics?.roles);
      if (v === 'ESCAPE') escapes++;
      else if (v === 'OVERKILL') overkills++;
      else if (v === 'CORRECT_OK') normalOks++;
      else if (v === 'CORRECT_NG') correctNgs++;
    });

    return {
      all: testPredictions.length,
      escape: escapes,
      overkill: overkills,
      normal: normalOks,
      defect: correctNgs,
    };
  }, [testPredictions, confidenceThreshold, overkillAnalysis, classSemantics]);
  const detectionGrains = useMemo(() => task === 'detection' && testPredictions.length > 0
    ? summarizeDetectionGrains({
      matrix,
      verdicts: testPredictions.map((prediction) => computeSampleVerdict(prediction, confidenceThreshold, overkillAnalysis, classSemantics?.roles)),
      threshold: confidenceThreshold,
      map50: metrics.mAP_50,
    })
    : null,
  [task, testPredictions, matrix, confidenceThreshold, overkillAnalysis, classSemantics, metrics.mAP_50]);
  const hasDefectSamples = testPredictions.some((p) => isDefectLabel(p.ground_truth, classSemantics?.roles));
  const hasNormalSamples = testPredictions.some((p) => isNormalLabel(p.ground_truth, classSemantics?.roles));
  const hasCalibrationEvidence = metrics.score_spec?.domain !== 'distance' && Boolean(jobId && hasDefectSamples && hasNormalSamples);
  const hasReportableResult = Boolean(jobId && Object.keys(metrics).length > 0 && testPredictions.length > 0);
  const reportAvailabilityHint = !jobId
    ? (language === 'ko' ? '평가가 완료된 모델이 있어야 리포트를 내보낼 수 있습니다.' : 'Load an evaluated model before exporting a report.')
    : !hasReportableResult
    ? (language === 'ko' ? '평가 결과와 검증 이미지가 있어야 리포트를 내보낼 수 있습니다.' : 'Evaluation results and validation images are required for a report.')
    : undefined;

  // Compute marginal row metrics (Support & Recall)
  const rowMetrics = useMemo(() => {
    return matrix.map((row, i) => {
      const support = row.reduce((sum, val) => sum + val, 0);
      const correct = row[i] || 0;
      const recall = support > 0 ? (correct / support) * 100 : 0;
      return { support, recall };
    });
  }, [matrix]);

  // Compute marginal column metrics (Total Pred & Precision)
  const colMetrics = useMemo(() => {
    if (matrix.length === 0) return [];
    return classes.map((_, j) => {
      let predTotal = 0;
      for (let i = 0; i < matrix.length; i++) {
        predTotal += matrix[i]?.[j] || 0;
      }
      const correct = matrix[j]?.[j] || 0;
      const precision = predTotal > 0 ? (correct / predTotal) * 100 : 0;
      return { predTotal, precision };
    });
  }, [classes, matrix]);

  // Compute overall accuracy & total verification samples
  const totalSamples = useMemo(() => {
    return matrix.reduce((acc, row) => acc + row.reduce((sum, val) => sum + val, 0), 0);
  }, [matrix]);

  const overallAccuracy = useMemo(() => {
    if (totalSamples === 0) return 0;
    let correct = 0;
    for (let i = 0; i < matrix.length; i++) {
      correct += matrix[i]?.[i] || 0;
    }
    return (correct / totalSamples) * 100;
  }, [matrix, totalSamples]);

  // High-Contrast Heatmap Color Mapper
  const getCellStyling = (i: number, j: number, val: number, normVal: number, isSelected: boolean) => {
    const isDiag = i === j;
    const trueClass = classes[i];
    const predClass = classes[j];
    const isTrueOk = isNormalLabel(trueClass, classSemantics?.roles);
    const isPredOk = isNormalLabel(predClass, classSemantics?.roles);

    let baseBg = '';
    let textColor = '';
    let borderColor = 'border-[#1E2638]';

    if (isSelected) {
      return 'bg-[#1E293B] border-[#38BDF8] ring-1 ring-[#38BDF8] text-white font-bold scale-[1.02] z-20 shadow-none';
    }

    if (isDiag) {
      if (normVal >= 0.95) {
        baseBg = 'bg-[#065F46]';
        textColor = 'text-[#ECFDF5]';
        borderColor = 'border-[#059669]/60';
      } else if (normVal >= 0.80) {
        baseBg = 'bg-[#047857]';
        textColor = 'text-[#D1FAE5]';
        borderColor = 'border-[#047857]/50';
      } else if (normVal >= 0.50) {
        baseBg = 'bg-[#064E3B]';
        textColor = 'text-[#A7F3D0]';
        borderColor = 'border-[#065F46]/40';
      } else if (normVal > 0) {
        baseBg = 'bg-[#0E3826]';
        textColor = 'text-[#6EE7B7]';
        borderColor = 'border-[#0E3826]/30';
      } else {
        baseBg = 'bg-[#1A212E]';
        textColor = 'text-[#64748B]';
      }
    } else {
      if (val === 0) {
        baseBg = 'bg-[#0E121B]';
        textColor = 'text-[#475569]';
        borderColor = 'border-[#1E2638]';
      } else {
        const isEscape = task !== 'detection' && !isTrueOk && isPredOk;
        if (isEscape) {
          baseBg = 'bg-[#450A0A]';
          textColor = 'text-[#FCA5A5]';
          borderColor = 'border-[#EF4444] animate-pulse';
        } else {
          if (normVal >= 0.25) {
            baseBg = 'bg-[#701A2D]';
            textColor = 'text-[#FFE4E6]';
            borderColor = 'border-[#BE123C]/70';
          } else if (normVal >= 0.10) {
            baseBg = 'bg-[#3B131D]';
            textColor = 'text-[#FB7185]';
            borderColor = 'border-[#881337]/60';
          } else {
            baseBg = 'bg-[#261016]';
            textColor = 'text-[#FDA4AF]';
            borderColor = 'border-[#4C121A]/50';
          }
        }
      }
    }

    return `${baseBg} ${textColor} ${borderColor} hover:border-[#38BDF8]/70`;
  };

  const selectedVerdict = selectedPrediction
    ? computeSampleVerdict(selectedPrediction, confidenceThreshold, overkillAnalysis, classSemantics?.roles)
    : null;

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-100 overflow-hidden select-none">
      <OperatorGuidanceBanner step={4} />

      {/* Top Evaluation Toolbar */}
      <div className="h-14 bg-[#131822] border-b border-[#2B3547] px-6 flex items-center justify-between text-xs">
        <div className="flex items-center space-x-3">
          <Activity className="w-4 h-4 text-blue-400" />
          <h2 className="font-bold text-sm text-slate-200">
            {language === 'ko' ? '검증 및 품질 분석 스튜디오' : 'Quality Evaluation & Inspection Studio'}
          </h2>
          {jobId && <span className="text-slate-400 font-mono text-[11px]">[{jobId}]</span>}
        </div>

        {/* Export Buttons */}
        <div className="flex items-center space-x-3">
          <button
            type="button"
            onClick={() => loadEvaluation(handoff?.step===4&&handoff.family===task?handoff.jobId:undefined, sourceFolder ? { folderPath: sourceFolder, task } : undefined)}
            disabled={isLoading}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#1A212E] hover:bg-[#2B3547] rounded-[4px] text-slate-300 font-medium border border-[#2B3547] cursor-pointer transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>{language === 'ko' ? '새로고침' : 'Refresh'}</span>
          </button>

          <button
            type="button"
            onClick={handleExportHtml}
            disabled={isExportingReport || isLoading || !hasReportableResult}
            title={reportAvailabilityHint}
            className="flex items-center space-x-1.5 px-4 py-1.5 bg-[#2563EB] hover:bg-[#1D4ED8] active:bg-[#1E40AF] rounded-[4px] text-white font-semibold border border-[#3B82F6] cursor-pointer transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <FileText className="w-3.5 h-3.5" />
            <span>{language === 'ko' ? 'HTML 리포트 내보내기' : 'Export HTML Report'}</span>
            <ExternalLink className="w-3 h-3 ml-0.5" />
          </button>

          <button
            type="button"
            onClick={handleExportJson}
            disabled={isExportingReport || isLoading || !hasReportableResult}
            title={reportAvailabilityHint}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#1A212E] hover:bg-[#2B3547] rounded-[4px] text-slate-300 font-medium border border-[#2B3547] cursor-pointer transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Download className="w-3.5 h-3.5" />
            <span>JSON</span>
          </button>
        </div>
      </div>

      {exportedReportPath && <p className="mx-6 mb-3 break-all text-xs text-emerald-300">보고서 저장 완료: {exportedReportPath}</p>}
      {(errorMessage || reportError) && (
        <div role="alert" className="mx-6 mt-3 rounded border border-amber-600 bg-amber-950/40 p-2 text-xs text-amber-200">
          {reportError || errorMessage}
        </div>
      )}

      {/* In-Page Guardrail if no evaluation results */}
      {!isLoading && !jobId && testPredictions.length === 0 && (
        <div className="p-6 pb-0">
          <GuardrailBanner
            type="warning"
            stepContext="4단계 품질 분석 안내"
            title="평가할 학습 완료 모델이 없습니다"
            description="혼동 행렬과 과검/미검 분석을 수행하려면 먼저 모델을 학습하고 평가해야 합니다."
            shopFloorTip="빠른 프로토타입은 기능 연결을 확인하는 용도입니다. 모델 품질은 별도 검증이 필요합니다."
            actions={[
              {
                label: '3단계(오토딥러닝) 이동하여 빠른 학습 시작',
                icon: Zap,
                variant: 'primary',
                onClick: () => setStep(3),
              },
              {
                label: '기존 완료된 평가 결과 다시 불러오기',
                icon: RefreshCw,
                variant: 'secondary',
                loadingText: '평가 결과 확인 중...',
                onClick: async () => {
                  await loadEvaluation(handoff?.step===4&&handoff.family===task?handoff.jobId:undefined, sourceFolder ? { folderPath: sourceFolder, task } : undefined);
                },
              },
            ]}
          />
        </div>
      )}

      {/* Main Studio View: Left Matrix/Tradeoff + Right Drawer & Dual Viewport */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Side: Metrics & Clickable Confusion Matrix */}
        <div className="w-[500px] bg-[#131822] border-r border-[#2B3547] p-4 flex flex-col space-y-4 overflow-y-auto">
          <div className="rounded border border-[#3B5269] bg-[#111C2A] p-2.5 text-xs text-slate-300">
            <div className="mb-2 font-semibold text-white">후보 모델 비교 유형</div>
            <div className="grid grid-cols-2 gap-2" role="group" aria-label="후보 모델 비교 유형">
              <button type="button" onClick={() => setComparisonTask(task)} aria-pressed={comparisonTask === task}
                className={`rounded border px-2 py-1.5 ${comparisonTask === task ? 'border-cyan-400 bg-cyan-700/25 text-white' : 'border-[#415970] text-slate-300'}`}>프로젝트 모델</button>
              <button type="button" onClick={() => setComparisonTask('patch_classification')} aria-pressed={comparisonTask === 'patch_classification'}
                className={`rounded border px-2 py-1.5 ${comparisonTask === 'patch_classification' ? 'border-cyan-400 bg-cyan-700/25 text-white' : 'border-[#415970] text-slate-300'}`}>패치 분류 모델</button>
            </div>
          </div>
          <ModelComparisonPanel
            projectDir={projectDir}
            sourceFolder={sourceFolder}
            task={comparisonTask}
            preferredJobId={warmCandidateJobId || jobId}
            preferredParentJobId={warmParentJobId}
            language={language}
          />
          <ModelDeploymentPanel taskOverride={comparisonTask} />
          <EvaluationHistoryPanel sourceFolder={sourceFolder} task={comparisonTask} jobId={jobId} />
          <EvaluationEvidencePanel samples={testPredictions} onSelect={path=>{const sample=testPredictions.find(item=>item.file_path===path);if(sample)void selectPrediction(sample);}}/>
          {/* 1-Click Zero-Escape Calibration Prominent Card */}
          <div className="p-3.5 bg-[#1A212E] rounded-[6px] border border-[#2B3547] space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Target className="w-4 h-4 text-[#38BDF8] shrink-0" />
                <span className="font-bold text-xs text-slate-100">
                  {language === 'ko' ? '평가 이미지 임계값 분석' : 'Evaluation Image Threshold Analysis'}
                </span>
                <JargonTooltip termKey="optimal_threshold" />
              </div>
              {sampleCounts.all === 0 ? (
                <span className="px-2 py-0.5 rounded text-[10px] border border-slate-600 text-slate-300">검증 예측 없음</span>
              ) : sampleCounts.escape === 0 ? (
                <span className="px-2 py-0.5 rounded-[4px] text-[10px] font-bold bg-[#064E3B] text-[#6EE7B7] border border-[#10B981] flex items-center space-x-1 font-mono">
                  <CheckCircle2 className="w-3 h-3" />
                  <span>평가 표본 내 미검 0건</span>
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded-[4px] text-[10px] font-bold bg-[#450A0A] text-[#FCA5A5] border border-[#EF4444] animate-pulse flex items-center space-x-1 font-mono">
                  <AlertTriangle className="w-3 h-3" />
                  <span>미검 {sampleCounts.escape}건 감지</span>
                </span>
              )}
            </div>

            <div className="flex items-center justify-between text-xs">
              <div className="space-y-0.5">
                <div className="text-[10px] text-slate-400 font-mono">
                  현재 임계값 ({metrics.score_spec?.unit || 'probability'}): <span className="text-slate-100 font-bold tabular-nums">{confidenceThreshold.toFixed(2)}</span>
                </div>
                <div className="text-[10px] text-[#38BDF8] font-mono">
                  평가 표본 탐색 임계값:{' '}
                  <span className="font-bold font-mono tabular-nums text-white">
                    {hasCalibrationEvidence && overkillAnalysis?.optimal_threshold !== undefined
                      ? `τ* = ${overkillAnalysis.optimal_threshold.toFixed(4)}`
                      : 'NG·OK 평가 이미지 필요'}
                  </span>
                </div>
              </div>

              <button
                type="button"
                onClick={() => calibrateZeroEscape()}
                disabled={isCalibrating || !hasCalibrationEvidence}
                className="px-4 py-2 bg-[#0284C7] hover:bg-[#0369A1] active:bg-[#075985] text-white font-bold text-xs rounded-[4px] border border-[#38BDF8]/40 transition-all flex items-center space-x-1.5 cursor-pointer disabled:opacity-50"
              >
                {isCalibrating ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Zap className="w-3.5 h-3.5 fill-current" />
                )}
                <span>{isCalibrating ? '계산 중...' : '평가 임계값 적용'}</span>
              </button>
            </div>

            {calibrationMessage && (
              <div className="p-2 bg-[#0B0E14] border border-[#2B3547] rounded-[3px] text-[11px] text-[#38BDF8] font-mono">
                {calibrationMessage}
              </div>
            )}
          </div>

          {/* Primary Metric KPI Cards */}
          <div>
            {hasReportableResult && <div className="mb-3 rounded border border-[#3B5269] bg-[#111C2A] p-3 text-xs leading-relaxed text-slate-300" role="status">
              <p className="font-semibold text-slate-100">
                {language === 'ko' ? '평가 데이터: ' : 'Evaluation data: '}
                {metrics.evaluated_split === 'test' ? (language === 'ko' ? '시험 분할 (Test)' : 'Test partition')
                  : metrics.evaluated_split === 'val' ? (language === 'ko' ? '검증 분할 (Validation)' : 'Validation partition')
                  : (language === 'ko' ? '분할 근거 미확인' : 'Partition evidence unavailable')}
              </p>
              {metrics.selection_overlap && <p className="mt-1 text-amber-200">{language === 'ko'
                ? '모델 선택에 사용한 검증 데이터의 결과입니다. 별도 시험 데이터에서도 확인하세요.'
                : 'This validation data was also used for model selection. Verify the model on a separate test set.'}</p>}
              {metrics.score_spec?.domain === 'distance' && <p className="mt-1 text-cyan-200">저장된 거리 보정 임계값으로 평가합니다. 임계값 보정에는 학습·검증 데이터를 사용하세요.</p>}
              {metrics.threshold_search_available === false && <p className="mt-1 text-amber-200">{language === 'ko'
                ? '정상 또는 불량 정답이 없어 AUROC와 최적 임계값을 산출할 수 없습니다. 판정은 저장된 모델 임계값을 사용합니다.'
                : 'AUROC and threshold search require both normal and defect truth. Verdicts use the saved model threshold.'}</p>}
              {metrics.threshold_basis === 'evaluation_threshold_search' && <p className="mt-1 text-amber-200">{language === 'ko'
                ? '이 평가의 판정은 평가 데이터에서 탐색한 임계값을 사용합니다. 플로우의 판정 규칙과 적용 임계값을 함께 확인하세요.'
                : 'These verdicts use a threshold searched on evaluation data. Check the flow rules and applied threshold as well.'}</p>}
              {metrics.threshold_calibration_applied && <p className="mt-1 text-amber-200">{language === 'ko'
                ? '이 데이터는 임계값 보정에도 사용했습니다. 보정 효과는 별도 시험 데이터에서 확인하세요.'
                : 'This data was also used for threshold calibration. Validate the calibration on a separate test set.'}</p>}
            </div>}
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              {language === 'ko' ? '주요 품질 지표' : 'Primary Quality Metrics'}
            </h3>
            <div className="grid grid-cols-2 gap-2 text-xs font-mono">
              {Object.entries(metrics).map(([k, v]) => {
                const unavailable = v === null && (k === 'image_auroc' || k === 'pixel_auroc');
                if (typeof v !== 'number' && !unavailable) return null;
                const termKey = getMetricTermKey(k);
                return (
                  <div key={k} className="p-2.5 bg-[#1A212E] rounded-[4px] border border-[#2B3547]">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] text-slate-400 block uppercase">
                        {k.replace(/_/g, ' ')}
                      </span>
                      {termKey && <JargonTooltip termKey={termKey} />}
                    </div>
                    <span className="text-base font-bold text-slate-100 tabular-nums">{unavailable
                      ? (language === 'ko' ? '산출 불가' : 'Unavailable') : v.toFixed(4)}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {detectionGrains && <DetectionEvaluationGrains summary={detectionGrains} language={language} />}

          {/* Sub-Tab Selector: Confusion Matrix vs Overkill / Underkill */}
          <div className="flex items-center space-x-1 bg-[#0B0E14] p-1 rounded-[4px] border border-[#2B3547]">
            <button
              type="button"
              onClick={() => setEvalTab('matrix')}
              className={`flex-1 py-1.5 rounded-[3px] text-xs font-semibold cursor-pointer transition-all ${
                evalTab === 'matrix' ? 'bg-[#1A212E] text-[#38BDF8] border border-[#2B3547]' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {task === 'detection'
                ? (language === 'ko' ? '상위 박스 클래스 표' : 'Top Box Class Table')
                : (language === 'ko' ? '혼동 행렬' : 'Confusion Matrix')}
            </button>
            <button
              type="button"
              onClick={() => setEvalTab('overkill')}
              className={`flex-1 py-1.5 rounded-[3px] text-xs font-semibold cursor-pointer transition-all flex items-center justify-center space-x-1 ${
                evalTab === 'overkill' ? 'bg-[#1A212E] text-[#10B981] border border-[#2B3547]' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Target className="w-3.5 h-3.5" />
              <span>{language === 'ko' ? '과검/미검 최적화 (τ*)' : 'Zero-Escape (τ*)'}</span>
            </button>
          </div>

          {/* TAB 1: Task-specific class comparison table */}
          {evalTab === 'matrix' ? (
            <div className="flex-1 flex flex-col bg-[#1A212E] p-3.5 rounded-[6px] border border-[#2B3547]">
              <div className="flex items-center justify-between mb-2 pb-2 border-b border-[#2B3547]">
                <div className="flex items-center space-x-2">
                  <h3 className="text-xs font-bold text-slate-200 tracking-wide">
                    {task === 'detection'
                      ? (language === 'ko' ? '최고 점수 박스 클래스 (τ·IoU 미적용)' : 'Highest-scoring box class (no τ or IoU)')
                      : (language === 'ko' ? '혼동 행렬' : 'Confusion Matrix')}
                  </h3>
                  {task !== 'detection' && <JargonTooltip termKey="confusion_matrix" />}
                </div>

                {selectedCell && (
                  <div className="flex items-center space-x-2 bg-[#0B0E14] px-2 py-0.5 rounded-[3px] border border-[#38BDF8]/60 text-xs">
                    <span className="text-[10px] text-[#38BDF8] font-mono">
                      {selectedCell.trueClass} ➔ {selectedCell.predClass}
                    </span>
                    <button
                      type="button"
                      onClick={clearCellSelection}
                      className="text-[10px] text-slate-400 hover:text-white cursor-pointer ml-1"
                      title="필터 해제"
                    >
                      ✕
                    </button>
                  </div>
                )}
              </div>

              {classes.length === 0 ? (
                <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono">
                  {language === 'ko' ? '평가 결과 데이터가 없습니다.' : 'No confusion matrix available.'}
                </div>
              ) : (
                <div className="overflow-x-auto relative">
                  <table className="w-full text-center text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-[#2B3547]">
                        <th className="p-1.5 text-slate-400 font-mono text-[10px] uppercase text-left">
                          True \ Pred
                        </th>
                        {classes.map((c) => (
                          <th key={c} className="p-1.5 font-mono font-semibold text-slate-300 max-w-[80px] truncate" title={c}>
                            {c}
                          </th>
                        ))}
                        <th className="p-1.5 font-mono font-semibold text-slate-400 bg-[#131822] border-l border-[#2B3547] text-[10px]">
                          Support
                        </th>
                        <th className="p-1.5 font-mono font-semibold text-[#6EE7B7] bg-[#131822] text-[10px]">
                          {task === 'detection' ? (language === 'ko' ? '행 일치율' : 'Row match') : 'Recall'}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {matrix.map((row, i) => {
                        const trueClass = classes[i];
                        const { support, recall } = rowMetrics[i] || { support: 0, recall: 0 };

                        return (
                          <tr key={trueClass} className="border-b border-[#2B3547]/50">
                            <th className="p-1.5 font-mono font-semibold text-slate-300 text-left max-w-[90px] truncate text-[11px]" title={trueClass}>
                              {trueClass}
                            </th>

                            {row.map((val, j) => {
                              const predClass = classes[j];
                              const normVal = normMatrix[i]?.[j] ?? (support > 0 ? val / support : 0);
                              const isSelected =
                                selectedCell?.trueClass === trueClass && selectedCell?.predClass === predClass;

                              return (
                                <td
                                  key={predClass}
                                  onClick={() => {
                                    if (isSelected) {
                                      clearCellSelection();
                                    } else {
                                      selectCell(trueClass, predClass);
                                    }
                                  }}
                                  onMouseEnter={() =>
                                    setHoveredCell({ i, j, trueClass, predClass, val, norm: normVal })
                                  }
                                  onMouseLeave={() => setHoveredCell(null)}
                                  className={`p-1.5 rounded-[3px] cursor-pointer transition-all border ${getCellStyling(
                                    i,
                                    j,
                                    val,
                                    normVal,
                                    isSelected
                                  )}`}
                                >
                                  <div className="text-xs font-mono font-bold tabular-nums">{val}</div>
                                  <div className="text-[9px] font-mono tabular-nums opacity-80">
                                    {(normVal * 100).toFixed(0)}%
                                  </div>
                                </td>
                              );
                            })}

                            <td className="p-1.5 font-mono text-slate-300 bg-[#131822] border-l border-[#2B3547] tabular-nums text-[11px]">
                              {support}
                            </td>

                            <td
                              className={`p-1.5 font-mono tabular-nums font-bold text-[11px] bg-[#131822] ${
                                recall >= 90 ? 'text-[#10B981]' : recall >= 80 ? 'text-[#F59E0B]' : 'text-[#EF4444]'
                              }`}
                            >
                              {recall.toFixed(1)}%
                            </td>
                          </tr>
                        );
                      })}

                      {/* Bottom Marginal Row: Column Total & Precision */}
                      <tr className="border-t border-[#2B3547] bg-[#131822]">
                        <th className="p-1.5 font-mono text-slate-400 text-left text-[10px] uppercase">
                          Pred Total
                        </th>
                        {colMetrics.map((col, idx) => (
                          <td key={classes[idx]} className="p-1.5 font-mono text-slate-300 tabular-nums text-[11px]">
                            {col.predTotal}
                          </td>
                        ))}
                        <td className="p-1.5 font-mono text-slate-400 border-l border-[#2B3547] tabular-nums text-[10px]">
                          Total: {totalSamples}
                        </td>
                        <td className="p-1.5 font-mono text-slate-400 text-[10px]">
                          Overall
                        </td>
                      </tr>

                      <tr className="border-t border-[#2B3547]/50 bg-[#131822]">
                        <th className="p-1.5 font-mono text-slate-400 text-left text-[10px] uppercase">
                          {task === 'detection' ? (language === 'ko' ? '열 일치율' : 'Column match') : 'Precision'}
                        </th>
                        {colMetrics.map((col, idx) => (
                          <td
                            key={classes[idx]}
                            className={`p-1.5 font-mono tabular-nums font-bold text-[11px] ${
                              col.precision >= 90 ? 'text-[#10B981]' : col.precision >= 80 ? 'text-[#F59E0B]' : 'text-[#EF4444]'
                            }`}
                          >
                            {col.precision.toFixed(1)}%
                          </td>
                        ))}
                        <td colSpan={2} className="p-1.5 font-mono font-bold text-xs text-white bg-[#0E1624] border-l border-[#2B3547] tabular-nums">
                          {task === 'detection'
                            ? `${overallAccuracy.toFixed(1)}% ${language === 'ko' ? '클래스 일치' : 'class match'}`
                            : `${overallAccuracy.toFixed(1)}% Acc`}
                        </td>
                      </tr>
                    </tbody>
                  </table>

                  {/* Interactive Inspection Hover HUD Card */}
                  {hoveredCell && (
                    <div className="mt-2.5 p-2 bg-[#0B0E14] border border-[#2B3547] rounded-[3px] text-[10px] font-mono flex items-center justify-between">
                      <div className="space-x-2 text-slate-300">
                        <span className="text-[#38BDF8] font-bold">
                          True [{hoveredCell.trueClass}] ➔ Pred [{hoveredCell.predClass}]
                        </span>
                        <span className="text-slate-400">|</span>
                        <span>
                          표본: <strong className="text-white tabular-nums">{hoveredCell.val}건</strong> ({ (hoveredCell.norm * 100).toFixed(1) }%)
                        </span>
                      </div>
                      <span className="text-[10px] text-slate-400">
                        클릭하여 검증 목록 필터 ➔
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            /* TAB 2: Zero-Escape Tradeoff Chart & Optimizer */
            <div className="flex-1 flex flex-col space-y-3">
              {hasCalibrationEvidence && overkillAnalysis ? (
                <ZeroEscapeTradeoffChart
                  tradeoffCurve={overkillAnalysis.tradeoff_curve || []}
                  currentThreshold={confidenceThreshold}
                  optimalThreshold={overkillAnalysis.optimal_threshold}
                  onThresholdChange={setConfidenceThreshold}
                  onApplyOptimal={applyOptimalThreshold}
                />
              ) : (
                <div className="p-3 rounded border border-amber-500/40 bg-amber-950/30 text-xs text-amber-200">
                  NG와 OK 검증 예측이 모두 있어야 과검·미검 최적 임계값을 계산하고 적용할 수 있습니다.
                </div>
              )}

              {/* Status Breakdown KPI Cards */}
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div
                  className={`p-3 rounded-[4px] border ${
                    sampleCounts.escape > 0
                      ? 'bg-[#450A0A] border-[#EF4444] text-[#FCA5A5]'
                      : 'bg-[#064E3B] border-[#10B981] text-[#6EE7B7]'
                  }`}
                >
                  <div className="text-[10px] uppercase font-semibold flex items-center justify-between">
                    <span className="flex items-center space-x-1">
                      <span>{language === 'ko' ? '미검 (불량 유출)' : 'Underkill (Escape)'}</span>
                      <JargonTooltip termKey="underkill" />
                    </span>
                    {sampleCounts.escape > 0 ? (
                      <AlertTriangle className="w-3.5 h-3.5 text-[#EF4444] animate-pulse" />
                    ) : (
                      <CheckCircle2 className="w-3.5 h-3.5 text-[#10B981]" />
                    )}
                  </div>
                  <div className="text-base font-bold font-mono tabular-nums mt-1">
                    {sampleCounts.escape}건
                  </div>
                  <div className="text-[10px] opacity-75">
                    {sampleCounts.escape > 0
                      ? language === 'ko'
                        ? '⚠️ 즉시 임계값 하향 필요'
                        : 'Action Required'
                      : language === 'ko'
                      ? '✅ 유출 위험 0%'
                      : 'Zero Escape Risk'}
                  </div>
                </div>

                <div className="p-3 bg-[#1A212E] rounded-[4px] border border-[#2B3547] text-slate-300">
                  <div className="text-[10px] uppercase font-semibold text-slate-400 flex items-center justify-between">
                    <span className="flex items-center space-x-1">
                      <span>{language === 'ko' ? '과검 (수율 손실)' : 'Overkill (Scrap)'}</span>
                      <JargonTooltip termKey="overkill" />
                    </span>
                  </div>
                  <div className="text-base font-bold font-mono tabular-nums text-slate-100 mt-1">
                    {sampleCounts.overkill}건
                  </div>
                  <div className="text-[10px] text-slate-400">
                    {language === 'ko' ? '정상인데 불량 판정' : 'False Alarms'}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Center/Right Side: Filtered Sample Drawer & Dual Viewport */}
        <div className="flex-1 flex overflow-hidden">
          {/* Sample Thumbnails Drawer */}
          <div className="w-80 bg-[#131822] border-r border-[#2B3547] p-3 flex flex-col overflow-y-auto space-y-2">
            <div className="text-xs font-semibold text-slate-400 px-1 mb-1 flex items-center justify-between">
              <span>
                {language === 'ko'
                  ? `평가 이미지 목록 (${filteredPredictions.length})`
                  : `Samples (${filteredPredictions.length})`}
              </span>
              {selectedCell && (
                <button
                  type="button"
                  onClick={clearCellSelection}
                  className="text-[10px] font-mono text-[#38BDF8] hover:underline cursor-pointer"
                >
                  필터 초기화
                </button>
              )}
            </div>

            {/* Standard 4-Tab Industrial Sample Filtering (+ ALL) */}
            <div className="flex flex-col space-y-1 p-1 bg-[#0B0E14] rounded-[4px] border border-[#2B3547] text-[11px] mb-2 font-medium">
              <div className="grid grid-cols-3 gap-1">
                <button
                  type="button"
                  onClick={() => setSampleFilter('all')}
                  className={`py-1 px-1 rounded-[3px] transition-all cursor-pointer text-center truncate font-mono text-[10px] ${
                    sampleFilter === 'all'
                      ? 'bg-[#1A212E] text-slate-100 font-bold border border-[#38BDF8]'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  전체 ({sampleCounts.all})
                </button>
                <button
                  type="button"
                  onClick={() => setSampleFilter('escape')}
                  className={`py-1 px-1 rounded-[3px] transition-all cursor-pointer text-center truncate font-mono text-[10px] flex items-center justify-center space-x-1 ${
                    sampleFilter === 'escape' || sampleFilter === 'fn_escape'
                      ? 'bg-[#450A0A] text-[#FCA5A5] font-bold border border-[#EF4444]'
                      : sampleCounts.escape > 0
                      ? 'text-[#EF4444] font-bold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>FN 미검</span>
                  <span className="tabular-nums">({sampleCounts.escape})</span>
                </button>
                <button
                  type="button"
                  onClick={() => setSampleFilter('overkill')}
                  className={`py-1 px-1 rounded-[3px] transition-all cursor-pointer text-center truncate font-mono text-[10px] flex items-center justify-center space-x-1 ${
                    sampleFilter === 'overkill' || sampleFilter === 'fp_overkill'
                      ? 'bg-[#451A03] text-[#FCD34D] font-bold border border-[#F59E0B]'
                      : sampleCounts.overkill > 0
                      ? 'text-[#F59E0B] font-semibold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>FP 과검</span>
                  <span className="tabular-nums">({sampleCounts.overkill})</span>
                </button>
              </div>
              <div className="grid grid-cols-2 gap-1">
                <button
                  type="button"
                  onClick={() => setSampleFilter('defect')}
                  className={`py-1 px-1 rounded-[3px] transition-all cursor-pointer text-center truncate font-mono text-[10px] flex items-center justify-center space-x-1 ${
                    sampleFilter === 'defect' || sampleFilter === 'tp_defect'
                      ? 'bg-[#083344] text-[#67E8F9] font-bold border border-[#06B6D4]'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>🎯 TP 결함</span>
                  <span className="tabular-nums">({sampleCounts.defect})</span>
                </button>
                <button
                  type="button"
                  onClick={() => setSampleFilter('normal')}
                  className={`py-1 px-1 rounded-[3px] transition-all cursor-pointer text-center truncate font-mono text-[10px] flex items-center justify-center space-x-1 ${
                    sampleFilter === 'normal' || sampleFilter === 'tn_normal'
                      ? 'bg-[#064E3B] text-[#6EE7B7] font-bold border border-[#10B981]'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>🟢 TN 정상</span>
                  <span className="tabular-nums">({sampleCounts.normal})</span>
                </button>
              </div>
            </div>

            {/* Empty State for Filter Tabs */}
            {filteredPredictions.length === 0 && (
              <div className="p-4 text-center text-xs text-slate-400 space-y-2 my-auto bg-[#0B0E14] rounded-[4px] border border-[#2B3547]">
                {sampleCounts.all === 0 ? (
                  <div>검증 예측이 없습니다.</div>
                ) : sampleFilter === 'escape' || sampleFilter === 'fn_escape' ? (
                  <>
                    <div className="text-xl">🛡️</div>
                    <div className="font-bold text-slate-200">{hasDefectSamples ? '현재 검증 표본에서 미검 0건' : 'NG 검증 표본 없음'}</div>
                    <p className="text-[11px] text-slate-400 leading-relaxed">
                      표본 밖의 결함 검출 성능이나 현장 유출률은 검증되지 않았습니다.
                    </p>
                  </>
                ) : sampleFilter === 'overkill' || sampleFilter === 'fp_overkill' ? (
                  <>
                    <div className="text-xl">✨</div>
                    <div className="font-bold text-slate-200">{hasNormalSamples ? '현재 검증 표본에서 과검 0건' : 'OK 검증 표본 없음'}</div>
                    <p className="text-[11px] text-slate-400 leading-relaxed">
                      실제 정상 제품의 과검률을 판단하려면 OK 검증 표본이 필요합니다.
                    </p>
                  </>
                ) : (
                  <div className="text-slate-400 font-mono text-[11px]">일치하는 검증 샘플이 없습니다.</div>
                )}
              </div>
            )}

            {/* Cards List */}
            {filteredPredictions.map((pred) => {
              const isSelected = selectedPrediction?.image_id === pred.image_id;
              const thumbUrl = resolveApiUrl(pred.thumbnail_url);
              const verdict = computeSampleVerdict(pred, confidenceThreshold, overkillAnalysis, classSemantics?.roles);
              const isEscape = verdict === 'ESCAPE';
              const isOverkill = verdict === 'OVERKILL';

              let borderLeft = 'border-l-2 border-l-[#10B981]';
              if (isEscape) borderLeft = 'border-l-2 border-l-[#EF4444]';
              else if (isOverkill) borderLeft = 'border-l-2 border-l-[#F59E0B]';
              else if (verdict === 'CORRECT_NG') borderLeft = 'border-l-2 border-l-[#06B6D4]';
              else if (verdict === 'REVIEW') borderLeft = 'border-l-2 border-l-slate-500';

              return (
                <div
                  key={pred.image_id}
                  onClick={() => selectPrediction(pred)}
                  className={`p-2.5 rounded-[4px] border cursor-pointer transition-all flex flex-col space-y-1.5 ${borderLeft} ${
                    isSelected
                      ? 'bg-[#1A2639] border-[#38BDF8] text-white'
                      : isEscape
                      ? 'bg-[#1A1116] border-[#4C121A] hover:bg-[#25131C]'
                      : isOverkill
                      ? 'bg-[#1D170D] border-[#472E08] hover:bg-[#281F12]'
                      : 'bg-[#131822] border-[#2B3547] hover:bg-[#1A212E]'
                  }`}
                >
                  <div className="flex items-center space-x-2.5">
                    <img
                      src={thumbUrl}
                      alt={pred.file_name}
                      className="w-10 h-10 rounded-[3px] object-cover bg-black border border-[#2B3547] shrink-0"
                    />
                    <div className="min-w-0 flex-1 text-xs">
                      <span className="block font-mono text-slate-200 truncate font-semibold text-[11px]" title={pred.file_name}>
                        {pred.file_name}
                      </span>
                      <div className="flex items-center space-x-1.5 mt-0.5">
                        <span className="text-[10px] text-slate-400 font-mono tabular-nums">
                          {task === 'detection' ? '최고 박스 점수' : '점수'}: {(((pred as any).defect_score ?? pred.confidence) * 100).toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center justify-between pt-1 border-t border-[#1E2638]">
                    <SampleVerdictBadge verdict={verdict} />
                    <button type="button" disabled={pred.labeling_supported === false}
                      title={pred.labeling_supported === false ? '원본 이미지 연결 근거가 없어 라벨 화면을 열 수 없습니다.' : '이 평가 이미지의 원본 라벨 열기'} onClick={(event) => {
                      event.stopPropagation();
                      void useProjectStore.getState().openImageForLabeling(pred.source_image_id || pred.image_id, pred.file_path).then((opened) => {
                        if (!opened) setReportError('선택한 이미지의 라벨 화면을 열지 못했습니다. 데이터 출처와 저장 상태를 확인하세요.');
                      }).catch((cause) => setReportError(cause instanceof Error ? cause.message : '라벨 화면을 열지 못했습니다.'));
                    }} className="rounded border border-cyan-700 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-950 disabled:opacity-50 disabled:cursor-not-allowed">라벨 수정</button>
                    <span className="text-[10px] text-slate-400 font-mono">
                      GT: {pred.ground_truth}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Synchronized Dual-Viewport Inspection Panel */}
          <div className="flex-1 flex flex-col bg-[#0B0E14] overflow-hidden">
            {/* Critical Alert Banner if Current Sample is an ESCAPE */}
            {selectedVerdict === 'ESCAPE' && (
              <div className="mx-4 mt-3 p-3 bg-[#450A0A] border border-[#EF4444] rounded-[4px] flex items-center justify-between text-xs text-[#FCA5A5] animate-pulse">
                <div className="flex items-center space-x-2">
                  <AlertTriangle className="w-4 h-4 text-[#EF4444] shrink-0" />
                  <span>
                    <strong>검증 샘플 미검</strong>: 현재 임계값({confidenceThreshold.toFixed(2)})에서 NG 샘플이 OK로 판정되었습니다.
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => calibrateZeroEscape()}
                  disabled={!hasCalibrationEvidence || isCalibrating}
                  className="px-3 py-1 bg-[#EF4444] hover:bg-[#DC2626] text-white font-bold rounded-[3px] text-xs cursor-pointer shrink-0 transition-all disabled:opacity-50"
                >
                  검증 임계값 적용
                </button>
              </div>
            )}

            {/* Synchronized Pan/Zoom Dual Viewport */}
            <SynchronizedDualViewport
              task={task}
              prediction={selectedPrediction}
              heatmapOverlayBase64={heatmapOverlayBase64}
              heatmapLoading={heatmapLoading}
              confidenceThreshold={confidenceThreshold}
              scoreSpec={metrics.score_spec}
              onThresholdChange={setConfidenceThreshold}
              defectScore={selectedPrediction?.defect_score}
              groundTruth={selectedPrediction?.ground_truth}
              predictedClass={selectedPrediction?.predicted_class}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default EvaluationStudio;
