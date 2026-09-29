/**
 * src/renderer/components/training/TrainingController.tsx
 * Step 3: AutoML Training Controller adhering to Cognex VisionPro & Keyence standards.
 * Features CRT Phosphor Oscilloscope loss curves, Keyence discrete 16-segment LED telemetry,
 * factory line recipe presets, and Cognex Deep Steel chassis theme.
 */

import React from 'react';
import {
  Play,
  Square,
  Clock,
  ArrowLeft,
  Sparkles,
  Sliders,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { OperatorGuidanceBanner } from '../common/OperatorGuidanceBanner';
import { GuardrailBanner } from '../common/GuardrailBanner';
import { LedAnnunciator, LedState } from '../common/LedAnnunciator';
import { OscilloscopeLossCurve } from './OscilloscopeLossCurve';
import { HardwareTelemetryPanel } from './HardwareTelemetryPanel';
import { RecipePresetSelector } from './RecipePresetSelector';

export const TrainingController: React.FC = () => {
  const { task, language, setStep } = useProjectStore();
  const { folderPath, totalImages, split, applySplit } = useDatasetStore();
  const {
    status,
    isTraining,
    isStopRequestPending,
    stopError,
    preset,
    setPreset,
    startTraining,
    stopTraining,
    currentEpoch,
    totalEpochs,
    currentStep,
    totalSteps,
    trainLoss,
    valLoss,
    epochEtaSeconds,
    totalEtaSeconds,
    bestMetric,
    lossHistory,
    hardware,
  } = useTrainingStore();

  const handleStart = async () => {
    await startTraining(folderPath, task);
  };

  const handleAbort = async () => {
    await stopTraining();
  };

  // Format seconds to mm:ss
  const formatTime = (secs: number | null): string => {
    if (secs === null || secs <= 0) return '--:--';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  // Map status string to LedState
  const getStatusLedState = (): LedState => {
    if (status === 'running' || status === 'stopping') return 'running';
    if (status === 'completed') return 'pass';
    if (status === 'aborted') return 'standby';
    if (status === 'failed') return 'fail';
    return 'offline';
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-100 overflow-y-auto select-none">
      <OperatorGuidanceBanner step={3} />
      <div className="max-w-7xl w-full mx-auto space-y-5 p-6">
        {/* In-Page Guardrails with 1-Click Remediation */}
        {totalImages === 0 ? (
          <GuardrailBanner
            type="critical"
            stepContext="3단계 학습 필수 안내"
            title="학습 데이터가 없습니다"
            description="데이터셋이 비어 있어 딥러닝 모델 학습을 시작할 수 없습니다. 1단계에서 데이터를 먼저 불러오세요."
            actions={[
              {
                label: '1단계에서 검사 데이터 폴더 선택',
                icon: Sparkles,
                variant: 'primary',
                onClick: () => setStep(1),
              },
              {
                label: '1단계(데이터 관리)로 이동',
                icon: ArrowLeft,
                variant: 'secondary',
                onClick: () => setStep(1),
              },
            ]}
          />
        ) : totalImages > 0 && (split.val === 0 || split.train === 0) ? (
          <GuardrailBanner
            type="warning"
            stepContext="3단계 학습 가드레일"
            title="검증 데이터 분할(Validation Split)이 필요합니다"
            description={`현재 총 ${totalImages}장의 이미지 중 검증용 데이터가 0장으로 설정되어 있습니다. 검증 데이터가 없으면 과적합(Overfitting)을 방지할 수 없고 실시간 검증 손실 측정이 불가능합니다.`}
            shopFloorTip="산업 표준 추천 비율은 학습 80% : 검증 20% 입니다. 아래 버튼을 누르면 즉시 자동 분할됩니다."
            actions={[
              {
                label: '80:20 기본 검증 분할 즉시 적용 (추천)',
                icon: Sliders,
                variant: 'primary',
                loadingText: '80:20 데이터 분할 적용 중...',
                onClick: async () => {
                  await applySplit(0.8);
                },
              },
            ]}
          />
        ) : null}

        {/* Industrial Recipe Presets Section */}
        <RecipePresetSelector
          preset={preset}
          setPreset={setPreset}
          isTraining={isTraining}
          language={language}
        />

        {/* Cognex Deep Steel Execution Control Toolbar */}
        <div className="p-3 bg-[#131822] rounded-[4px] border border-[#2B3547] flex items-center justify-between">
          <div className="flex items-center space-x-4">
            {!isTraining ? (
              <button
                type="button"
                onClick={handleStart}
                disabled={totalImages === 0}
                className="flex items-center space-x-2 px-5 py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] active:bg-[#1E40AF] rounded-[4px] border border-[#3B82F6] text-xs font-bold text-white uppercase tracking-wider disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-white" />
                <span>{language === 'ko' ? 'AutoML 원클릭 학습 시작' : 'Start Auto Training'}</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={handleAbort}
                disabled={isStopRequestPending}
                className="flex items-center space-x-2 px-5 py-2.5 bg-[#DC2626] hover:bg-[#B91C1C] active:bg-[#991B1B] rounded-[4px] border border-[#EF4444] text-xs font-bold text-white uppercase tracking-wider cursor-pointer transition-all disabled:opacity-50"
              >
                <Square className="w-3.5 h-3.5 fill-white" />
                <span>{status === 'stopping'
                  ? (isStopRequestPending ? '중단 중...' : '중단 상태 다시 확인')
                  : (language === 'ko' ? '학습 중단 (Abort)' : 'Abort Training')}</span>
              </button>
            )}

            {/* Live Progress & Status Annunciator */}
            <div className="flex items-center space-x-3 text-xs">
              <LedAnnunciator
                state={getStatusLedState()}
                size="md"
                label="STATUS"
                value={status.toUpperCase()}
                pulse={isTraining}
              />

              {isTraining && (
                <div className="flex items-center space-x-3 text-slate-300 font-mono text-[11px] tabular-nums">
                  <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">
                    EPOCH {currentEpoch.toString().padStart(2, '0')}/{totalEpochs.toString().padStart(2, '0')}
                  </span>
                  <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">
                    STEP {currentStep}/{totalSteps}
                  </span>
                  <span className="flex items-center space-x-1 text-amber-400 bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">
                    <Clock className="w-3 h-3" />
                    <span>ETA: {formatTime(totalEtaSeconds || epochEtaSeconds)}</span>
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Metric Summary Badge if completed */}
          {bestMetric !== null && (
            <div className="flex items-center space-x-2">
              <span className="text-[10px] text-slate-400 uppercase font-semibold font-mono">BEST CONVERGENCE:</span>
              <span className="text-sm font-mono tabular-nums font-bold text-emerald-400 bg-[#0B0E14] px-2.5 py-1 rounded-[3px] border border-[#2B3547]">
                {bestMetric.toFixed(4)}
              </span>
            </div>
          )}
        </div>

        {stopError && (
          <div role="alert" className="rounded border border-amber-600 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
            {stopError}
          </div>
        )}

        {/* Dual Telemetry Split Grid: CRT Oscilloscope (66%) + Keyence Telemetry (34%) */}
        <div className="grid grid-cols-12 gap-5">
          {/* Left Column: CRT Phosphor Oscilloscope Loss Curve */}
          <div className="col-span-8">
            <OscilloscopeLossCurve
              lossHistory={lossHistory}
              trainLoss={trainLoss}
              valLoss={valLoss}
              currentEpoch={currentEpoch}
              totalEpochs={totalEpochs}
              currentStep={currentStep}
              totalSteps={totalSteps}
              isTraining={isTraining}
              etaSeconds={totalEtaSeconds || epochEtaSeconds}
              language={language}
            />
          </div>

          {/* Right Column: Keyence Hardware Telemetry Panel */}
          <div className="col-span-4">
            <HardwareTelemetryPanel
              hardware={hardware}
              isTraining={isTraining}
              language={language}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default TrainingController;
