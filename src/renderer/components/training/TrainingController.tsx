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
import { api,getApiPersistenceIdentity } from '../../services/api';
import { ModelFamilyCatalog } from './ModelFamilyCatalog';
import { dinoSyntheticDefaults, modelChoices, trainingModelOverrides, type DinoSyntheticTrainingOptions } from './modelTrainingOptions';
import { DinoSyntheticOptions } from './DinoSyntheticOptions';
import { trainingComputeReadiness } from '../../utils/trainingComputeReadiness';
import { PatchClassificationWorkbench } from './PatchClassificationWorkbench';
import { RotationWorkbench } from './RotationWorkbench';
import { AutoDLWorkbench } from './AutoDLWorkbench';
import type { ModelFamily } from '../../services/modelTrainingProgram';
import type { VisionTask } from '../../types';
import {TaskChangeImpactDialog,taskPreviewScope} from '../wizard/TaskChangeImpactDialog';
import {TrainingPreparationPanel} from './TrainingPreparationPanel';
import {useTaskHandoff} from './useTaskHandoff';
import {clearTaskHandoff} from './taskHandoff';
import {trainingPresetBatchSize} from '../common/errorActions';

export const TrainingController: React.FC = () => {
  const [actionError, setActionError] = useState<string | null>(null);
  const [warmParentId, setWarmParentId] = useState('');
  const [warmParents, setWarmParents] = useState<Array<{ job_id: string; checkpoint_sha256: string }>>([]);
  const [warmParentsError, setWarmParentsError] = useState<string | null>(null);
  const { task, language, setStep, projectDir, project, isProjectBusy } = useProjectStore();
  const handoff=useTaskHandoff();
  const [taskProposal,setTaskProposal]=useState<{task:VisionTask;scope:string}|null>(null);
  const [selectedFamily, setSelectedFamily] = useState<ModelFamily>(()=>handoff?.family||task);
  const [trainingBackbone, setTrainingBackbone] = useState(modelChoices[task][0].value);
  const [pretrainedCheckpoint, setPretrainedCheckpoint] = useState('');
  const [anomalyPurpose,setAnomalyPurpose] = useState<'image'|'region'>('image');
  const [syntheticOptions, setSyntheticOptions] = useState<DinoSyntheticTrainingOptions>({...dinoSyntheticDefaults});
  const selectedBackbone = modelChoices[task].some(choice => choice.value === trainingBackbone) ? trainingBackbone : modelChoices[task][0].value;
  const syntheticAnomaly = task === 'anomaly' && selectedBackbone === 'dino_synthetic';
  const statisticalRefit = task === 'anomaly' && !syntheticAnomaly;
  let modelOptions: Record<string, unknown> = {};
  let modelOptionsError: string | null = null;
  try { modelOptions = trainingModelOverrides(task, selectedBackbone, pretrainedCheckpoint, syntheticOptions, anomalyPurpose); }
  catch (error) { modelOptionsError = error instanceof Error ? error.message : String(error); }
  const modelOptionsKey = JSON.stringify(modelOptions);
  const { folderPath, totalImages, split, isLoading, isSplitting, importError, splitError,
    splitSupported, splitUnavailableReason, datasetKey } = useDatasetStore();
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
    nextBatchSize,nextDevice,setNextSettings,remediationNotice,
  } = useTrainingStore();
  const {
    profiles, selectedProfileId,transportRevision, isLoaded: isComputeLoaded, isLoading: isComputeLoading,
    loadError: computeLoadError, error: computeError, probeResults, probePendingId, probeProfile,
  } = useComputeStore();

  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId);
  const selectedProbe = selectedProfileId ? probeResults[selectedProfileId] : null;
  const jobProfile = profiles.find((profile) => profile.id === jobComputeProfileId);
  const jobGpuSelector = jobProfile?.gpu_selector?.trim();
  const selectedReadiness = modelOptionsError ? {ready: false, reason: modelOptionsError}
    : trainingComputeReadiness(selectedProbe, task, preset, modelOptions, !!warmParentId);
  const completedJobId = status === 'completed' ? jobId : null;
  const displayedJobLabel = jobComputeProfileId && jobComputeLabel === jobComputeProfileId
    ? jobProfile?.name || jobComputeLabel
    : jobComputeLabel;
  const computeReady = isComputeLoaded && !isComputeLoading && !computeLoadError &&
    (!selectedProfileId || (Boolean(selectedProfile) && selectedReadiness.ready));
  const warmStartSupported = true;
  const sourceReady = Boolean(projectDir && folderPath && project?.source_dataset_dir===folderPath && datasetKey === `${folderPath}\0${task}`);

  useEffect(() => {
    setTrainingBackbone(modelChoices[task][0].value);
    setPretrainedCheckpoint('');
    setSyntheticOptions({...dinoSyntheticDefaults});
  }, [task, projectDir, project?.id, project?.active_labelset_id, folderPath]);

  useEffect(() => {if(!taskProposal||taskProposal.scope!==taskPreviewScope())setSelectedFamily(handoff?.family||task);}, [task, projectDir,project?.source_dataset_dir,project?.active_labelset_id,transportRevision,selectedProfileId]);
  useEffect(()=>{if(handoff?.family)setSelectedFamily(handoff.family);},[handoff?.jobId,handoff?.selectionId]);
  const chooseFamily = (family: ModelFamily) => {
    const data = useDatasetStore.getState();
    if (data.isLoading || data.isSplitting || useProjectStore.getState().isProjectBusy) return;
    if (['classification', 'segmentation', 'detection', 'anomaly'].includes(family) && family !== task) {
      setTaskProposal({task:family as VisionTask,scope:taskPreviewScope()});return;
    }
    clearTaskHandoff(localStorage,{...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()});
    setSelectedFamily(family);
  };

  useEffect(() => {
    let valid = true;
    setWarmParents([]);
    setWarmParentId('');
    setWarmParentsError(null);
    if (!sourceReady || !warmStartSupported || modelOptionsError) return () => { valid = false; };
    api.training.warmStartParents(folderPath, task, preset, modelOptions).then((result) => {
      if (valid) setWarmParents(result.parents);
    }).catch((error) => {
      if (valid) setWarmParentsError(error instanceof Error ? error.message : String(error));
    });
    return () => { valid = false; };
  }, [sourceReady, folderPath, task, preset, selectedProfileId, warmStartSupported, projectDir, project?.active_labelset_id, selectedBackbone, completedJobId, modelOptionsKey, modelOptionsError]);

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
    !isProjectBusy && sourceReady && !isLoading && !isSplitting && !isRecoveringTraining && !importError && !modelOptionsError && computeReady;
  const startBlocker = isProjectBusy || isLoading || isSplitting ? '프로젝트·데이터 변경이 진행 중입니다.'
    : !sourceReady || importError ? '현재 데이터 원본을 다시 가져와야 합니다.'
    : totalImages<1 || split.train<1 || split.val<1 ? '저장된 Train·Val 분할과 검수 이미지를 준비하세요.'
    : isRecoveringTraining ? '이전 학습의 실행 상태를 확인 중입니다.'
    : modelOptionsError || (!computeReady ? computeLoadError || selectedReadiness.reason || '실행 자원을 확인해야 합니다.' : null);
  const requiresSourcePartitions = isSplitUnavailable(task, splitSupported);

  const handleStart = async () => {
    if (!canStart || useProjectStore.getState().isProjectBusy) return;
    setActionError(null);
    try { await startTraining(folderPath, task, warmParentId || undefined, modelOptions); }
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
    if (['disconnected', 'interrupted', 'unverified'].includes(status)) return 'fail';
    if (status === 'completed') return 'pass';
    if (['aborted', 'cancelled', 'stopped'].includes(status)) return 'standby';
    if (status === 'failed') return 'fail';
    return 'offline';
  };

  const phaseLabels: Record<string, string> = {
    queued: '대기 중', preparing: '데이터 준비 중', transferring: '서버로 전송 중', reconnecting: '서버 상태 다시 확인 중',
    running: '학습 중', stopping: '중단 확인 중', syncing: '결과 동기화 중',
    completed: '완료', aborted: '중단됨', failed: '실패', disconnected: '연결 끊김 · 상태 미확인',
    cancelled: '취소 확인', stopped: '중단 확인', interrupted: '실행 중단 · 복구 확인 필요', unverified: '작업 상태 확인 필요',
  };
  const currentPhase = jobPhase || status;

  return (<>
    {taskProposal&&<TaskChangeImpactDialog nextTask={taskProposal.task} scope={taskProposal.scope} onClose={()=>setTaskProposal(null)} onResult={outcome=>{setTaskProposal(null);if(outcome.ok){clearTaskHandoff(localStorage,{...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()});setSelectedFamily(outcome.task);}else setActionError(`모델 종류를 바꾸지 못했습니다: ${outcome.error}`);}}/>}
    <div id="workflow-training" tabIndex={-1} className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-100 overflow-y-auto select-none">
      {startBlocker&&<div className="workspace-record m-3 text-xs" role="note" aria-label="학습 시작 준비도"><p id="training-start-reason">학습 시작 보류: {startBlocker}</p><button className="workspace-button mt-2" onClick={()=>{if(!sourceReady||totalImages<1||split.train<1||split.val<1)void setStep(1);else document.getElementById('workflow-training')?.focus();}}>{!sourceReady||totalImages<1||split.train<1||split.val<1?'데이터·분할 확인 (1단계)':'실행 자원·학습 설정 확인 (3단계)'}</button></div>}
      <OperatorGuidanceBanner step={3} />
      <div className="max-w-7xl w-full mx-auto space-y-5 p-6">
        <ModelFamilyCatalog selectedFamily={selectedFamily} onSelect={chooseFamily} disabled={isLoading||isSplitting||isProjectBusy} />
        {selectedFamily===task&&<TrainingPreparationPanel family={selectedFamily} model={syntheticAnomaly ? syntheticOptions.anomaly_backbone : selectedBackbone} preset={preset} checkpoint={pretrainedCheckpoint} onCheckpointChange={setPretrainedCheckpoint} device={nextDevice==='mps'||nextDevice==='cuda' ? nextDevice : 'cpu'} config={modelOptions} warmStartJobId={warmParentId||undefined} />}
        {remediationNotice&&<p role="status" className="rounded border border-cyan-800 bg-cyan-950/30 p-3 text-sm text-cyan-100">{remediationNotice}</p>}
        {selectedFamily === task && <>
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
            title={split.train === 0 ? '사용 가능한 학습 이미지가 없습니다' : '사용 가능한 검증 이미지가 없습니다'}
            description={requiresSourcePartitions
              ? splitUnavailableReason || '이 작업 유형은 화면 재분할을 지원하지 않습니다. 원본 train/val/test 폴더 구성을 확인하세요.'
              : `현재 학습 대상: Train ${split.train}장 · Val ${split.val}장 · Test ${split.test}장. 저장된 분할과 이미지의 미사용·검수 상태를 확인하세요.`}
            shopFloorTip="검수 정책으로 제외된 이미지는 먼저 검수하세요. 기존 시험 분할을 유지하고, 분할 변경이 필요하면 1단계에서 확인 후 적용하세요."
            actions={[
            {
              label: '1단계에서 데이터·분할 확인',
              icon: ArrowLeft,
              variant: 'primary',
              onClick: () => setStep(1),
            },
              {
                label: '2단계에서 라벨·검수 확인',
                icon: ArrowLeft,
                variant: 'secondary',
                onClick: () => setStep(2),
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
          {task==='anomaly'&&<div className="mt-3"><label className="block text-sm text-slate-200">이상탐지 검사 목적<select aria-label="이상탐지 검사 목적" value={anomalyPurpose} disabled={isTraining} onChange={event=>setAnomalyPurpose(event.target.value as 'image'|'region')} className="mt-1 w-full rounded border border-slate-600 bg-[#0B1520] p-2"><option value="image">이미지 단위 정상·이상 판정</option><option value="region">이상 위치·영역 검토</option></select></label><p className="mt-2 text-slate-300">{anomalyPurpose==='image'?'평가 프로필: 이미지 점수 AUROC·임계값·혼동행렬. 정상·결함 시험 이미지가 모두 필요합니다.':'평가 프로필: 정답 마스크 기반 영역 지표. 정상 학습 이미지와 독립 결함 시험 마스크를 준비하세요.'}{syntheticAnomaly&&' DINOv3 출력은 패치 점수 맵이며 픽셀 정답 마스크와 구분합니다.'}</p></div>}
          {syntheticAnomaly && <DinoSyntheticOptions options={syntheticOptions} disabled={isTraining}
            onChange={options => { setSyntheticOptions(options); setWarmParentId(''); }} />}
          {modelOptionsError && <p role="alert" className="mt-2 text-amber-300">{modelOptionsError}</p>}
          {(selectedBackbone.startsWith('dinov3') || selectedBackbone.startsWith('yolo') || syntheticAnomaly) && <details className="mt-3"><summary className="cursor-pointer text-slate-300">사전학습 파일 가져오기 · 상세 설정</summary><label className="mt-3 block text-slate-300">사전학습 가중치 파일 (선택)
            <input aria-label="사전학습 가중치 파일" value={pretrainedCheckpoint} disabled={isTraining} onChange={event => setPretrainedCheckpoint(event.target.value)}
              placeholder="기본 가중치를 사용하거나 로컬 파일의 절대 경로를 입력하세요"
              className="mt-1 w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2" />
          </label></details>}
          {statisticalRefit && <p className="mt-2 text-slate-300">이상탐지는 정상 이미지로 특징 통계를 구성합니다. 부모 모델 사용 시 검증된 특징 추출기로 통계를 다시 구성합니다.</p>}
        </div>

        {warmStartSupported && <div className="rounded border border-[#3B5269] bg-[#111C2A] p-3 text-xs text-slate-200">
          <div className="font-semibold text-white">이전 모델에서 재학습</div>
          <p className="mt-1 text-slate-400">{statisticalRefit ? '완료된 같은 출처의 특징 추출기를 검증하고, 현재 정상 데이터로 통계를 다시 구성합니다.' : syntheticAnomaly ? '같은 백본·원본 패치 크기·패치 간격·헤드 구조의 완료 모델 전체 가중치에서 다시 학습합니다.' : '완료된 같은 프로젝트·데이터 출처·구조의 체크포인트를 초기 가중치로 사용합니다.'} 새 결과는 후보 모델로 저장됩니다.</p>
          <label className="mt-2 block text-slate-300">시작 모델
              <select aria-label="재학습 시작 모델" value={warmParentId} onChange={(event) => setWarmParentId(event.target.value)}
                disabled={!sourceReady || isTraining || !!modelOptionsError} className="mt-1 w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2 text-white disabled:opacity-50">
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

        <details className="rounded border border-slate-600 bg-[#111C2A] p-3 text-sm text-slate-200"><summary className="cursor-pointer">다음 학습 배치·로컬 장치 설정</summary><div className="mt-3 grid gap-3 sm:grid-cols-2"><label>배치 크기<input aria-label="다음 학습 배치 크기" type="number" min={1} max={128} disabled={isTraining} value={nextBatchSize ?? trainingPresetBatchSize(preset)} onChange={event=>setNextSettings({batchSize:Number(event.target.value)})} className="mt-1 w-full rounded border border-slate-600 bg-[#0B1520] p-2" /></label><label>로컬 장치<select aria-label="다음 학습 로컬 장치" disabled={isTraining} value={nextDevice || 'auto'} onChange={event=>setNextSettings({device:event.target.value})} className="mt-1 w-full rounded border border-slate-600 bg-[#0B1520] p-2"><option value="auto">자동 감지</option><option value="cpu">CPU</option><option value="mps">Apple Metal / MPS</option><option value="cuda">CUDA</option></select></label></div><p className="mt-2 text-slate-400">새 학습에 적용됩니다. 서버를 선택하면 서버 설정의 실행 장치를 사용합니다.</p></details>
        {/* Dark Steel Execution Control Toolbar */}
        <div className="p-3 bg-[#131822] rounded-[4px] border border-[#2B3547] flex items-center justify-between">
          <div className="flex items-center space-x-4">
            {!isTraining ? (
              <button
                type="button"
                onClick={handleStart}
                disabled={!canStart} aria-describedby={startBlocker?"training-start-reason":undefined}
                className="flex items-center space-x-2 px-5 py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] active:bg-[#1E40AF] rounded-[4px] border border-[#3B82F6] text-xs font-bold text-white uppercase tracking-wider disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-all"
              >
                <Play className="w-3.5 h-3.5 fill-white" />
                <span>{isRecoveringTraining ? '기존 학습 확인 중...' :
                  language === 'ko' ? '선택 설정으로 학습 시작' : 'Start Auto Training'}</span>
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
              <span title={task === 'anomaly' ? '저장된 모델 선택 지표 · 검증 수치와 학습 손실은 아래 기록에서 확인' : undefined}
                className="text-[10px] text-slate-400 uppercase font-semibold font-mono">{task === 'anomaly' ? '저장 지표' : 'BEST VAL LOSS:'}</span>
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
            ) : jobId && status === 'completed' ? (
              <div className="h-full rounded border border-[#2B3547] bg-[#131822] p-4 text-xs">
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">완료 작업 장치</div>
                <div className="mt-3 font-mono font-semibold text-slate-100">{jobDeviceName || '장치 기록 없음'}</div>
                <div className="mt-3 text-slate-400">저장된 학습 작업의 장치 기록입니다.</div>
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
        <AutoDLWorkbench task={task} anomalyPurpose={anomalyPurpose} />
        </>}
        {selectedFamily === 'patch_classification' && <PatchClassificationWorkbench />}
        {selectedFamily === 'rotation' && <RotationWorkbench />}
        {selectedFamily === 'ocr' && <OCRWorkbench />}
        {selectedFamily === 'rotated_detection' && <RotatedDetectionPanel />}
        {selectedFamily === 'defect_gan' && <DefectGANWorkbench />}
        {selectedFamily === 'enhancement' && <EnhancementWorkbench />}
      </div>
    </div>
  </>);
};

export default TrainingController;
