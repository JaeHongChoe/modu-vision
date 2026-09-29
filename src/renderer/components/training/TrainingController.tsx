/**
 * src/renderer/components/training/TrainingController.tsx
 * Step 3: AutoML Training Controller adhering to Cognex VisionPro & Keyence standards.
 * Features CRT Phosphor Oscilloscope loss curves, Keyence discrete 16-segment LED telemetry,
 * factory line recipe presets, and Cognex Deep Steel chassis theme.
 */

import React, { useEffect, useState } from 'react';
import {
  Play,
  Square,
  Clock,
  ArrowLeft,
  Sparkles,
  Sliders,
  RefreshCw,
  Server,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { OperatorGuidanceBanner } from '../common/OperatorGuidanceBanner';
import { GuardrailBanner } from '../common/GuardrailBanner';
import { LedAnnunciator, LedState } from '../common/LedAnnunciator';
import { OscilloscopeLossCurve } from './OscilloscopeLossCurve';
import { HardwareTelemetryPanel } from './HardwareTelemetryPanel';
import { RecipePresetSelector } from './RecipePresetSelector';
import { isSplitUnavailable } from '../../utils/datasetSplitCapability';

export const TrainingController: React.FC = () => {
  const [actionError, setActionError] = useState<string | null>(null);
  const { task, language, setStep } = useProjectStore();
  const { folderPath, totalImages, split, isLoading, isSplitting, importError, splitError,
    splitSupported, splitUnavailableReason, applySplit } = useDatasetStore();
  const {
    jobId,
    jobComputeProfileId,
    jobComputeLabel,
    jobDeviceName,
    jobPhase,
    transferProgress,
    startError,
    jobStatusError,
    status,
    isTraining,
    isRecoveringTraining,
    isStopRequestPending,
    stopError,
    preset,
    setPreset,
    startTraining,
    stopTraining,
    recoverActiveJob,
    refreshCurrentJob,
    reconnectCurrentJob,
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
  const {
    profiles, selectedProfileId, isLoaded: isComputeLoaded, isLoading: isComputeLoading,
    loadError: computeLoadError, error: computeError, probeResults, probePendingId, probeProfile,
  } = useComputeStore();

  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId);
  const selectedProbe = selectedProfileId ? probeResults[selectedProfileId] : null;
  const jobProfile = profiles.find((profile) => profile.id === jobComputeProfileId);
  const jobGpuSelector = jobProfile?.gpu_selector?.trim();
  const displayedJobLabel = jobComputeProfileId && jobComputeLabel === jobComputeProfileId
    ? jobProfile?.name || jobComputeLabel
    : jobComputeLabel;
  const computeReady = isComputeLoaded && !isComputeLoading && !computeLoadError &&
    (!selectedProfileId || (Boolean(selectedProfile) && selectedProbe?.ready === true));

  useEffect(() => {
    void recoverActiveJob();
  }, [recoverActiveJob]);

  useEffect(() => {
    if (!jobId || !isTraining) return;
    const timer = window.setInterval(() => { void refreshCurrentJob(); }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId, isTraining, refreshCurrentJob]);

  const canStart = totalImages > 0 && split.train > 0 && split.val > 0 &&
    !isLoading && !isSplitting && !isRecoveringTraining && !importError && computeReady;
  const requiresSourcePartitions = isSplitUnavailable(task, splitSupported);

  const handleStart = async () => {
    if (!canStart) return;
    setActionError(null);
    try { await startTraining(folderPath, task); }
    catch (error) { setActionError(error instanceof Error ? error.message : '학습 시작에 실패했습니다.'); }
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
    if (['queued', 'preparing', 'transferring', 'running', 'stopping', 'syncing'].includes(status)) return 'running';
    if (status === 'disconnected') return 'fail';
    if (status === 'completed') return 'pass';
    if (status === 'aborted') return 'standby';
    if (status === 'failed') return 'fail';
    return 'offline';
  };

  const phaseLabels: Record<string, string> = {
    queued: '대기 중', preparing: '데이터 준비 중', transferring: '서버로 전송 중', reconnecting: '서버 상태 다시 확인 중',
    running: '학습 중', stopping: '중단 확인 중', syncing: '결과 동기화 중',
    completed: '완료', aborted: '중단됨', failed: '실패', disconnected: '연결 끊김 · 상태 미확인',
  };
  const currentPhase = jobPhase || status;

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-100 overflow-y-auto select-none">
      <OperatorGuidanceBanner step={3} />
      <div className="max-w-7xl w-full mx-auto space-y-5 p-6">
        <div className="rounded border border-[#2B3547] bg-[#131822] p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-blue-400" />
              <span className="font-semibold text-slate-400">새 학습 대상</span>
              <span className="font-bold text-slate-100">{!isComputeLoaded ? '설정 확인 중' : selectedProfileId ? (selectedProfile?.name || `설정 없음: ${selectedProfileId}`) : 'This computer'}</span>
              {selectedProfileId && <span className={selectedProbe?.ready ? 'text-emerald-400' : 'text-amber-300'}>
                {selectedProbe?.ready ? `준비 완료 · ${selectedProbe.device_name || selectedProbe.device_type || '서버'}` : '연결 검사 필요'}
              </span>}
            </div>
            {selectedProfileId && <button type="button" onClick={() => void probeProfile(selectedProfileId).catch(() => {})}
              disabled={!selectedProfile || probePendingId === selectedProfileId}
              className="inline-flex items-center gap-1 rounded border border-[#4B5D77] px-2 py-1 text-[11px] text-slate-200 disabled:opacity-50">
              <RefreshCw className="h-3 w-3" /> {probePendingId === selectedProfileId ? '검사 중...' : '연결 검사'}
            </button>}
          </div>
          {(computeLoadError || computeError) && <div role="alert" className="mt-2 text-red-300">서버 설정 확인 실패: {computeLoadError || computeError}</div>}
          {jobId && (
            <div className="mt-3 border-t border-[#2B3547] pt-3">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span><span className="text-slate-400">현재 작업 위치:</span> <strong className="text-slate-100">{displayedJobLabel}</strong></span>
                {jobComputeProfileId && jobGpuSelector && <span><span className="text-slate-400">프로필 GPU 선택자:</span> {jobGpuSelector}</span>}
                <span><span className="text-slate-400">{jobComputeProfileId ? '작업 내부 장치:' : '장치:'}</span> {jobDeviceName || (jobComputeProfileId ? '서버 장치 확인 중' : hardware.gpu_name)}</span>
                <span><span className="text-slate-400">단계:</span> <strong className={status === 'disconnected' ? 'text-amber-300' : 'text-blue-300'}>{phaseLabels[currentPhase] || currentPhase}</strong></span>
              </div>
              {jobComputeProfileId && <p className="mt-1 text-[11px] text-slate-500">서버 선택 변경은 새 작업에만 적용됩니다.</p>}
              {jobGpuSelector && jobDeviceName?.startsWith('cuda:') && (
                <p className="mt-1 text-[11px] text-slate-400">프로필 GPU 선택자는 서버 번호이고, {jobDeviceName}은 CUDA_VISIBLE_DEVICES 적용 후 작업 내부 번호입니다.</p>
              )}
              {transferProgress !== null && (status === 'transferring' || currentPhase === 'transferring') && (
                <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-300">
                  <span>전송</span><div className="h-1.5 flex-1 overflow-hidden rounded bg-[#2B3547]"><div className="h-full bg-blue-500" style={{ width: `${transferProgress}%` }} /></div>
                  <span className="font-mono">{Math.round(transferProgress)}%</span>
                </div>
              )}
              {status === 'disconnected' && (
                <div className="mt-2 flex flex-wrap items-center gap-2 text-amber-200">
                  <span>원격 작업 상태를 확인할 수 없습니다. 같은 작업 ID에 다시 연결하여 확인하세요.</span>
                  <button type="button" onClick={() => void reconnectCurrentJob().catch(() => {})} className="inline-flex items-center gap-1 rounded border border-amber-600 px-2 py-1 font-semibold">
                    <RefreshCw className="h-3 w-3" /> 연결 다시 확인
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
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
            description={requiresSourcePartitions
              ? splitUnavailableReason || '이 작업 유형은 화면 재분할을 지원하지 않습니다. 원본 train/val/test 폴더 구성을 확인하세요.'
              : `현재 총 ${totalImages}장의 이미지에 대해 학습·검증 분할이 완료되지 않았습니다. 데이터나 라벨을 바꾼 뒤에는 분할을 다시 적용해야 합니다.`}
            shopFloorTip={requiresSourcePartitions ? undefined : '산업 표준 추천 비율은 학습 80% : 검증 20% 입니다. 아래 버튼을 누르면 즉시 자동 분할됩니다.'}
            actions={requiresSourcePartitions ? [{
              label: '1단계에서 데이터 폴더 확인',
              icon: ArrowLeft,
              variant: 'primary',
              onClick: () => setStep(1),
            }] : [
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

        {splitError && (
          <div role="alert" className="rounded border border-red-700/60 bg-red-950/40 px-3 py-2 text-xs text-red-200">
            데이터 분할 실패: {splitError}
          </div>
        )}

        {(actionError || startError || jobStatusError) && <div role="alert" className="rounded border border-amber-600 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
          {actionError || startError || jobStatusError}
        </div>}

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
                disabled={!canStart}
                className="flex items-center space-x-2 px-5 py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] active:bg-[#1E40AF] rounded-[4px] border border-[#3B82F6] text-xs font-bold text-white uppercase tracking-wider disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-white" />
                <span>{isRecoveringTraining ? '기존 학습 확인 중...' :
                  language === 'ko' ? 'AutoML 원클릭 학습 시작' : 'Start Auto Training'}</span>
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
              <span className="text-[10px] text-slate-400 uppercase font-semibold font-mono">BEST VAL LOSS:</span>
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
            {jobId && jobComputeProfileId ? (
              <div className="h-full rounded border border-[#2B3547] bg-[#131822] p-4 text-xs">
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">원격 장치</div>
                <div className="mt-3 font-semibold text-slate-100">{displayedJobLabel}</div>
                {jobGpuSelector && <div className="mt-1 break-words font-mono text-slate-300">프로필 GPU 선택자: {jobGpuSelector}</div>}
                <div className="mt-1 break-words font-mono text-blue-300">작업 내부 장치: {jobDeviceName || '서버 장치 확인 중'}</div>
                <div className="mt-3 text-slate-400">CPU 및 메모리 계측 값은 이 서버에서 제공되지 않습니다.</div>
              </div>
            ) : !jobId && selectedProfileId ? (
              <div className="h-full rounded border border-[#2B3547] bg-[#131822] p-4 text-xs">
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">선택한 원격 장치</div>
                <div className="mt-3 font-semibold text-slate-100">{selectedProfile?.name || '원격 서버'}</div>
                <div className="mt-1 break-words font-mono text-blue-300">
                  {selectedProbe?.ready ? (selectedProbe.device_name || selectedProbe.device_type || '서버 장치') : '연결 검사 후 장치 확인'}
                </div>
                <div className="mt-3 text-slate-400">학습이 시작되면 이 서버에서 손실 곡선과 작업 상태를 받습니다.</div>
              </div>
            ) : (
              <HardwareTelemetryPanel
                hardware={hardware}
                isTraining={isTraining}
                language={language}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default TrainingController;
