/**
 * src/renderer/components/training/TrainingController.tsx
 * Step 3: AutoML Training Controller.
 * Features CRT Phosphor Oscilloscope loss curves, discrete 16-segment LED telemetry,
 * factory line recipe presets, and a dark steel chassis theme.
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
import { OCRWorkbench } from './OCRWorkbench';
import { RotatedDetectionPanel } from './RotatedDetectionPanel';
import { DefectGANWorkbench } from './DefectGANWorkbench';
import { EnhancementWorkbench } from './EnhancementWorkbench';
import { isSplitUnavailable } from '../../utils/datasetSplitCapability';
import { api } from '../../services/api';
import { ModelFamilyCatalog } from './ModelFamilyCatalog';
import { modelChoices, trainingModelOverrides } from './modelTrainingOptions';
import { trainingComputeReadiness } from '../../utils/trainingComputeReadiness';

export const TrainingController: React.FC = () => {
  const [actionError, setActionError] = useState<string | null>(null);
  const [warmParentId, setWarmParentId] = useState('');
  const [warmParents, setWarmParents] = useState<Array<{ job_id: string; checkpoint_sha256: string }>>([]);
  const [warmParentsError, setWarmParentsError] = useState<string | null>(null);
  const { task, language, setStep, projectDir, project } = useProjectStore();
  const [trainingBackbone, setTrainingBackbone] = useState(modelChoices[task][0].value);
  const [pretrainedCheckpoint, setPretrainedCheckpoint] = useState('');
  const selectedBackbone = modelChoices[task].some(choice => choice.value === trainingBackbone) ? trainingBackbone : modelChoices[task][0].value;
  const { folderPath, totalImages, split, isLoading, isSplitting, importError, splitError,
    splitSupported, splitUnavailableReason, applySplit, datasetKey } = useDatasetStore();
  const {
    jobId,
    warmStartParentJobId,
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
  const selectedReadiness = trainingComputeReadiness(selectedProbe, task, preset, trainingModelOverrides(task, selectedBackbone), !!warmParentId);
  const completedJobId = status === 'completed' ? jobId : null;
  const displayedJobLabel = jobComputeProfileId && jobComputeLabel === jobComputeProfileId
    ? jobProfile?.name || jobComputeLabel
    : jobComputeLabel;
  const computeReady = isComputeLoaded && !isComputeLoading && !computeLoadError &&
    (!selectedProfileId || (Boolean(selectedProfile) && selectedReadiness.ready));
  const warmStartSupported = true;
  const sourceReady = Boolean(projectDir && folderPath && datasetKey === `${folderPath}\0${task}`);

  useEffect(() => {
    setTrainingBackbone(modelChoices[task][0].value);
    setPretrainedCheckpoint('');
  }, [task, projectDir, project?.id, project?.active_labelset_id, folderPath]);

  useEffect(() => {
    let valid = true;
    setWarmParents([]);
    setWarmParentId('');
    setWarmParentsError(null);
    if (!sourceReady || !warmStartSupported) return () => { valid = false; };
    api.training.warmStartParents(folderPath, task, preset, trainingModelOverrides(task, selectedBackbone)).then((result) => {
      if (valid) setWarmParents(result.parents);
    }).catch((error) => {
      if (valid) setWarmParentsError(error instanceof Error ? error.message : String(error));
    });
    return () => { valid = false; };
  }, [sourceReady, folderPath, task, preset, selectedProfileId, warmStartSupported, projectDir, project?.active_labelset_id, selectedBackbone, completedJobId]);

  useEffect(() => {
    void recoverActiveJob(sourceReady && !isLoading && !importError && project && projectDir ? {
      projectId: project.id, projectDir, labelsetId: project.active_labelset_id || 'default', folderPath, task,
    } : undefined);
  }, [recoverActiveJob, sourceReady, isLoading, importError, project?.id, project?.active_labelset_id, projectDir, folderPath, task]);

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
    try { await startTraining(folderPath, task, warmParentId || undefined, trainingModelOverrides(task, selectedBackbone, pretrainedCheckpoint)); }
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
        <ModelFamilyCatalog />
        <div className="rounded border border-[#2B3547] bg-[#131822] p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-blue-400" />
              <span className="font-semibold text-slate-400">새 학습 대상</span>
              <span className="font-bold text-slate-100">{!isComputeLoaded ? '설정 확인 중' : selectedProfileId ? (selectedProfile?.name || `설정 없음: ${selectedProfileId}`) : 'This computer'}</span>
              {selectedProfileId && <span className={selectedReadiness.ready ? 'text-emerald-400' : 'text-amber-300'}>
                {selectedReadiness.ready ? `준비 완료 · ${selectedProbe?.device_name || selectedProbe?.device_type || '서버'}` : selectedReadiness.reason}
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

        <div className="rounded border border-[#3B5269] bg-[#111C2A] p-3 text-xs text-slate-200">
          <label className="font-semibold text-white">다음 학습 모델
            <select aria-label="학습 모델 구조" value={selectedBackbone} disabled={isTraining} onChange={event => { setTrainingBackbone(event.target.value); setWarmParentId(''); }}
              className="mt-2 block w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2 font-normal">
              {modelChoices[task].map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
            </select>
          </label>
          {selectedBackbone.startsWith('dinov3') && <p className="mt-2 text-slate-300">DINOv3 사전학습 특징을 사용하고 현재 라벨에 맞는 분류·분할 헤드를 학습합니다. 사전학습 가중치가 없으면 준비 오류를 안내합니다.</p>}
          {selectedBackbone.startsWith('yolo') && <p className="mt-2 text-slate-300">YOLO 사전학습 가중치에서 현재 객체 클래스로 학습합니다. 완료된 YOLO 후보는 ROI 검출 노드에 연결할 수 있습니다.</p>}
          {(selectedBackbone.startsWith('dinov3') || selectedBackbone.startsWith('yolo')) && <label className="mt-3 block text-slate-300">사전학습 가중치 파일 (선택)
            <input aria-label="사전학습 가중치 파일" value={pretrainedCheckpoint} disabled={isTraining} onChange={event => setPretrainedCheckpoint(event.target.value)}
              placeholder="기본 가중치를 사용하거나 로컬 파일의 절대 경로를 입력하세요"
              className="mt-1 w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2" />
          </label>}
          {task === 'anomaly' && <p className="mt-2 text-slate-300">이상탐지는 정상 이미지로 특징 통계를 구성합니다. 부모 모델 사용 시 검증된 특징 추출기로 통계를 다시 구성합니다.</p>}
        </div>

        {warmStartSupported && <div className="rounded border border-[#3B5269] bg-[#111C2A] p-3 text-xs text-slate-200">
          <div className="font-semibold text-white">이전 모델에서 재학습</div>
          <p className="mt-1 text-slate-400">{task === 'anomaly' ? '완료된 같은 출처의 특징 추출기를 검증하고, 현재 정상 데이터로 통계를 다시 구성합니다.' : '완료된 같은 프로젝트·데이터 출처·구조의 체크포인트를 초기 가중치로 사용합니다.'} 새 결과는 후보 모델로 저장됩니다.</p>
          <label className="mt-2 block text-slate-300">시작 모델
              <select aria-label="재학습 시작 모델" value={warmParentId} onChange={(event) => setWarmParentId(event.target.value)}
                disabled={!sourceReady || isTraining} className="mt-1 w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2 text-white disabled:opacity-50">
                <option value="">새 모델로 학습</option>
                {warmParents.map((parent) => <option key={parent.job_id} value={parent.job_id}>{parent.job_id} · SHA {parent.checkpoint_sha256.slice(0, 12)}</option>)}
              </select>
            </label>
          {selectedProfileId && warmParentId && <p className="mt-2 text-slate-400">부모 체크포인트의 해시와 구조를 검증한 뒤 선택 서버에 전송합니다.</p>}
          {warmParentsError && <p role="alert" className="mt-2 text-amber-300">재학습 모델 목록: {warmParentsError}</p>}
          {warmParentId && <p className="mt-2 text-cyan-200">학습 뒤 4단계에서 이전 모델과 같은 test 이미지로 비교하고 승인 기준을 확인하세요. 승인 전에는 활성 모델을 바꾸지 않습니다.</p>}
          {status === 'completed' && warmStartParentJobId && jobId && <button type="button" onClick={() => void setStep(4)}
            className="mt-2 rounded border border-cyan-500/50 bg-cyan-700/20 px-3 py-2 font-semibold text-cyan-100 hover:bg-cyan-700/40">
            4단계에서 {warmStartParentJobId} → {jobId} 비교
          </button>}
        </div>}

        {/* Dark Steel Execution Control Toolbar */}
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

        {/* Dual Telemetry Split Grid: CRT Oscilloscope (66%) + Hardware Telemetry (34%) */}
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

          {/* Right Column: Hardware Telemetry Panel */}
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
        <OCRWorkbench />
        {task === 'detection' && <RotatedDetectionPanel />}
        <DefectGANWorkbench />
        <EnhancementWorkbench />
      </div>
    </div>
  );
};

export default TrainingController;
