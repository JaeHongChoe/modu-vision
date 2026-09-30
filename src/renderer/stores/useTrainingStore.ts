/**
 * src/renderer/stores/useTrainingStore.ts
 * AutoML Training state: presets, progress, live loss curves, and hardware telemetry.
 */

import { create } from 'zustand';
import type { HardwareStats, TrainingPreset, VisionTask } from '../types';
import { api } from '../services/api';
import { useComputeStore } from './useComputeStore';
import { useDatasetStore } from './useDatasetStore';

export type TrainingStatus = 'idle' | 'queued' | 'preparing' | 'transferring' | 'running'
  | 'stopping' | 'syncing' | 'disconnected' | 'completed' | 'aborted' | 'failed';

const ACTIVE_STATUSES: TrainingStatus[] = [
  'queued', 'preparing', 'transferring', 'running', 'stopping', 'syncing', 'disconnected',
];

function isTrainingStatus(value: unknown): value is TrainingStatus {
  return typeof value === 'string' && ([...ACTIVE_STATUSES, 'idle', 'completed', 'aborted', 'failed'] as string[]).includes(value);
}

function statusFromJob(job: any): TrainingStatus {
  if (isTrainingStatus(job?.status)) return job.status;
  if (isTrainingStatus(job?.phase)) return job.phase;
  return job?.status === 'started' ? 'running' : 'idle';
}

function transferPercent(job: any): number | null {
  const raw = job?.transfer_progress ?? job?.upload_progress;
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return Math.max(0, Math.min(100, raw <= 1 ? raw * 100 : raw));
  }
  const done = job?.bytes_transferred ?? job?.transferred_bytes;
  const total = job?.bytes_total ?? job?.total_bytes;
  if (typeof done === 'number' && typeof total === 'number' && total > 0) {
    return Math.max(0, Math.min(100, (done / total) * 100));
  }
  return null;
}

function computeLabel(id: string | null, fallback?: string | null): string {
  if (!id) return 'This computer';
  return fallback || useComputeStore.getState().profiles.find((profile) => profile.id === id)?.name || id;
}

function polledHistory(job: any): LossPoint[] | null {
  if (!Array.isArray(job?.loss_history)) return null;
  const rows = new Map<number, LossPoint>();
  for (const row of job.loss_history) {
    if (!Number.isInteger(row?.epoch) || row.epoch < 1
        || !Number.isFinite(row.train_loss) || !Number.isFinite(row.val_loss)) continue;
    rows.set(row.epoch, { epoch: row.epoch, trainLoss: row.train_loss, valLoss: row.val_loss,
      ...(Number.isFinite(row.lr) ? { lr: row.lr } : {}) });
  }
  return [...rows.values()].sort((a, b) => a.epoch - b.epoch);
}

export interface LossPoint {
  epoch: number;
  trainLoss: number;
  valLoss: number;
  lr?: number;
}

export interface StepLossPoint {
  step: number;
  loss: number;
}

export interface TrainingRecoverySource {
  projectId: string;
  projectDir: string;
  labelsetId: string;
  folderPath: string;
  task: VisionTask;
}

interface TrainingState {
  jobId: string | null;
  warmStartParentJobId: string | null;
  jobComputeProfileId: string | null;
  jobComputeLabel: string;
  jobDeviceName: string | null;
  jobPhase: string | null;
  transferProgress: number | null;
  startError: string | null;
  jobStatusError: string | null;
  isCurrentData: boolean;
  status: TrainingStatus;
  isTraining: boolean;
  isRecoveringTraining: boolean;
  isStopRequestPending: boolean;
  stopError: string | null;
  preset: TrainingPreset;
  currentEpoch: number;
  totalEpochs: number;
  currentStep: number;
  totalSteps: number;
  trainLoss: number | null;
  valLoss: number | null;
  epochEtaSeconds: number | null;
  totalEtaSeconds: number | null;
  bestMetric: number | null;
  metrics: Record<string, number>;
  lossHistory: LossPoint[];
  stepHistory: StepLossPoint[];
  hardware: HardwareStats;

  setPreset: (preset: TrainingPreset) => void;
  startTraining: (datasetPath: string, task: VisionTask, warmStartParentJobId?: string) => Promise<void>;
  stopTraining: () => Promise<void>;
  recoverActiveJob: (source?: TrainingRecoverySource) => Promise<void>;
  refreshCurrentJob: () => Promise<void>;
  reconnectCurrentJob: () => Promise<void>;
  updateFromTelemetry: (event: string, data: any) => void;
  invalidateForDataChange: () => void;
  resetTraining: () => void;
}

let startRequest: ReturnType<typeof api.training.start> | null = null;
let pendingStartEvents: Array<{ event: string; data: any }> = [];
let trainingRecoverySequence = 0;

async function waitForStoppedJob(jobId: string): Promise<'completed' | 'aborted' | 'failed'> {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const job = await api.training.getStatus(jobId);
    if (job.status === 'completed' || job.status === 'aborted' || job.status === 'failed') {
      return job.status;
    }
    if (job.status === 'disconnected') {
      throw new Error('서버 연결이 끊겨 중단 결과를 확인할 수 없습니다. 다시 연결하여 상태를 확인하세요.');
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('학습 중단을 요청했지만 종료 확인이 지연되고 있습니다. 상태를 다시 확인하세요.');
}

export const useTrainingStore = create<TrainingState>((set, get) => ({
  jobId: null,
  warmStartParentJobId: null,
  jobComputeProfileId: null,
  jobComputeLabel: 'This computer',
  jobDeviceName: null,
  jobPhase: null,
  transferProgress: null,
  startError: null,
  jobStatusError: null,
  isCurrentData: false,
  status: 'idle',
  isTraining: false,
  isRecoveringTraining: false,
  isStopRequestPending: false,
  stopError: null,
  preset: 'fast',
  currentEpoch: 0,
  totalEpochs: 0,
  currentStep: 0,
  totalSteps: 0,
  trainLoss: null,
  valLoss: null,
  epochEtaSeconds: null,
  totalEtaSeconds: null,
  bestMetric: null,
  metrics: {},
  lossHistory: [],
  stepHistory: [],
  hardware: {
    cpu_percent: 0,
    memory_percent: 0,
    gpu_name: 'Detecting...',
    gpu_memory_used_mb: 0,
    device_type: 'cpu',
  },

  setPreset: (preset) => set({ preset }),

  startTraining: async (datasetPath, task, warmStartParentJobId) => {
    if (useDatasetStore.getState().isSplitting) {
      throw new Error('데이터 분할이 진행 중입니다. 완료 후 학습을 시작하세요.');
    }
    const compute = useComputeStore.getState();
    if (!compute.isLoaded) {
      throw new Error('컴퓨팅 위치를 아직 확인하지 못했습니다. 서버 설정을 다시 불러오세요.');
    }
    if (compute.loadError) {
      throw new Error(`컴퓨팅 위치를 확인할 수 없습니다: ${compute.loadError}`);
    }
    const selectedProfileId = compute.selectedProfileId;
    const profile = selectedProfileId ? compute.getSelectedProfile() : undefined;
    if (selectedProfileId && (!profile || compute.probeResults[selectedProfileId]?.ready !== true)) {
      throw new Error('선택한 서버가 학습 준비 상태가 아닙니다. 연결 검사를 완료하세요.');
    }
    trainingRecoverySequence += 1;
    pendingStartEvents = [];
    set({
      jobId: null,
      warmStartParentJobId: warmStartParentJobId || null,
      jobComputeProfileId: selectedProfileId,
      jobComputeLabel: computeLabel(selectedProfileId, profile?.name),
      jobDeviceName: selectedProfileId ? compute.probeResults[selectedProfileId]?.device_name || null : null,
      jobPhase: selectedProfileId ? 'preparing' : null,
      transferProgress: null,
      startError: null,
      jobStatusError: null,
      isCurrentData: true,
      status: selectedProfileId ? 'preparing' : 'running',
      isTraining: true,
      isRecoveringTraining: false,
      isStopRequestPending: false,
      stopError: null,
      currentEpoch: 0,
      currentStep: 0,
      lossHistory: [],
      stepHistory: [],
      trainLoss: null,
      valLoss: null,
      bestMetric: null,
      metrics: {},
    });
    try {
      startRequest = api.training.start({
        task,
        preset: get().preset,
        dataset_path: datasetPath,
        ...(warmStartParentJobId ? { warm_start_job_id: warmStartParentJobId } : {}),
        ...(selectedProfileId ? { compute_profile_id: selectedProfileId } : {}),
      });
      const res = await startRequest;
      if (selectedProfileId && res.compute_profile_id !== selectedProfileId) {
        const message = '선택한 서버와 학습 시작 응답의 위치가 다릅니다. 작업 ID로 상태를 다시 확인하세요.';
        set({ jobId: res.job_id, status: 'disconnected', isTraining: true,
          jobStatusError: message, startError: message });
        throw new Error(message);
      }
      set({
        jobId: res.job_id,
        status: res.phase ? statusFromJob(res) : selectedProfileId ? 'preparing' : 'running',
        jobPhase: res.phase || (selectedProfileId ? 'preparing' : null),
      });
      const queued = pendingStartEvents;
      pendingStartEvents = [];
      for (const item of queued) {
        if (item.data.job_id === res.job_id) get().updateFromTelemetry(item.event, item.data);
      }
    } catch (e) {
      pendingStartEvents = [];
      if (!get().jobId) {
        set({ status: 'failed', isTraining: false, isStopRequestPending: false,
          startError: e instanceof Error ? e.message : '학습 시작에 실패했습니다.' });
      }
      throw e;
    } finally {
      startRequest = null;
    }
  },

  stopTraining: async () => {
    if (get().isStopRequestPending) return;
    const wasStarting = startRequest;
    let stopAccepted = false;
    set({ status: 'stopping', isTraining: true, isStopRequestPending: true, stopError: null });
    try {
      const jobId = get().jobId || (wasStarting ? (await wasStarting).job_id : null);
      if (!jobId) throw new Error('중단할 학습 작업 ID를 찾지 못했습니다.');
      set({ jobId });
      const response = await api.training.stop(jobId);
      if (response.status !== 'stopping' && response.status !== 'not_running') {
        throw new Error(`학습 중단 응답을 확인할 수 없습니다: ${response.status}`);
      }
      stopAccepted = true;
      const status = await waitForStoppedJob(jobId);
      if (get().isCurrentData) set({ status, jobPhase: status, isTraining: false, stopError: null });
      else get().resetTraining();
    } catch (error) {
      const message = error instanceof Error ? error.message : '학습 중단 상태를 확인할 수 없습니다.';
      set((state) => ({
        status: state.jobId
          ? (state.jobComputeProfileId ? 'disconnected' : stopAccepted ? 'stopping' : 'running')
          : 'failed',
        isTraining: Boolean(state.jobId),
        stopError: message,
      }));
    } finally {
      set({ isStopRequestPending: false });
    }
  },

  recoverActiveJob: async (source) => {
    if (get().isTraining || startRequest || (get().isRecoveringTraining && !source)) return;
    if (get().isCurrentData && get().status === 'completed' && get().jobId) return;
    const sequence = ++trainingRecoverySequence;
    const stillIdle = () => sequence === trainingRecoverySequence && !get().isTraining && !startRequest;
    const sourceCurrent = () => {
      const dataset = useDatasetStore.getState();
      return source && stillIdle() && dataset.folderPath === source.folderPath
        && dataset.datasetKey === `${source.folderPath}\0${source.task}`
        && !dataset.isLoading && !dataset.isSplitting && !dataset.importError;
    };
    const projectMatches = (project: Awaited<ReturnType<typeof api.project.getCurrent>>) => source
      && project.id === source.projectId && project.project_dir === source.projectDir
      && (project.active_labelset_id || 'default') === source.labelsetId
      && project.task === source.task && project.source_dataset_dir === source.folderPath;
    set({ isRecoveringTraining: true });
    try {
      const active = await api.training.getStatus();
      if (stillIdle() && active?.job_id
          && ACTIVE_STATUSES.includes(statusFromJob(active))) {
        // A recovered job remains cancellable even if its source data changed.
        // Step 4 recovers its result separately only after provenance checks.
        set({
          jobId: active.job_id,
          jobComputeProfileId: active.compute_profile_id || null,
          jobComputeLabel: computeLabel(active.compute_profile_id || null, active.compute_profile_name),
          jobDeviceName: active.device_name || active.remote_device_name || null,
          jobPhase: active.phase || active.status,
          transferProgress: transferPercent(active),
          jobStatusError: null,
          isCurrentData: false,
          status: statusFromJob(active),
          isTraining: true,
          isStopRequestPending: false,
          stopError: null,
          currentEpoch: active.current_epoch || 0,
          totalEpochs: active.total_epochs || 0,
          currentStep: active.current_step || 0,
          totalSteps: active.total_steps || 0,
          trainLoss: active.current_train_loss ?? null,
          valLoss: active.current_val_loss ?? null,
          lossHistory: polledHistory(active) || [],
        });
        return;
      }
      if (!sourceCurrent() || !source) return;
      if (!projectMatches(await api.project.getCurrent()) || !sourceCurrent()) return;
      // The catalog verifies project ownership, task and the current label/split
      // fingerprint. It does not need an existing evaluation or run inference.
      const catalog = await api.flowchart.modelCatalog(source.folderPath);
      if (!sourceCurrent()) return;
      const candidate = catalog.models.find(model => model.task === source.task);
      if (!candidate) return;
      const completed = await api.training.getStatus(candidate.job_id);
      if (!sourceCurrent() || completed.job_id !== candidate.job_id
          || completed.task !== source.task || completed.status !== 'completed') return;
      if (!projectMatches(await api.project.getCurrent()) || !sourceCurrent()) return;
      set({
        jobId: completed.job_id,
        jobComputeProfileId: completed.compute_profile_id || null,
        jobComputeLabel: computeLabel(completed.compute_profile_id || null, completed.compute_profile_name),
        jobDeviceName: completed.device_name || completed.remote_device_name || null,
        jobPhase: 'completed', transferProgress: null, jobStatusError: null, startError: null,
        isCurrentData: true, status: 'completed', isTraining: false, isStopRequestPending: false, stopError: null,
        currentEpoch: completed.current_epoch || 0, totalEpochs: completed.total_epochs || 0,
        currentStep: completed.current_step || 0, totalSteps: completed.total_steps || 0,
        trainLoss: completed.current_train_loss ?? null, valLoss: completed.current_val_loss ?? null,
        bestMetric: completed.best_metric ?? null, metrics: completed.metrics || {},
        lossHistory: polledHistory(completed) || [], stepHistory: [],
      });
    } catch {
      // The backend may still be starting; normal start/stop errors remain visible.
    } finally {
      if (sequence === trainingRecoverySequence) set({ isRecoveringTraining: false });
    }
  },

  refreshCurrentJob: async () => {
    const jobId = get().jobId;
    if (!jobId) return;
    try {
      const job = await api.training.getStatus(jobId);
      if (get().jobId !== jobId) return;
      if (job?.job_id !== jobId) {
        throw new Error('학습 상태 응답의 작업 ID가 다릅니다.');
      }
      if (job.compute_profile_id !== undefined && job.compute_profile_id !== get().jobComputeProfileId) {
        throw new Error('학습 상태 응답의 서버 위치가 원래 작업과 다릅니다.');
      }
      const status = statusFromJob(job);
      if (!get().isCurrentData && !ACTIVE_STATUSES.includes(status)) {
        get().resetTraining();
        return;
      }
      const profileId = job.compute_profile_id !== undefined
        ? job.compute_profile_id : get().jobComputeProfileId;
      set((state) => ({
        status,
        isTraining: ACTIVE_STATUSES.includes(status),
        jobComputeProfileId: profileId,
        jobComputeLabel: computeLabel(profileId, job.compute_profile_name || state.jobComputeLabel),
        jobDeviceName: job.device_name || job.remote_device_name || state.jobDeviceName,
        jobPhase: ACTIVE_STATUSES.includes(status) ? (job.phase || job.status || state.jobPhase) : status,
        transferProgress: transferPercent(job) ?? state.transferProgress,
        jobStatusError: null,
        currentEpoch: job.current_epoch ?? state.currentEpoch,
        totalEpochs: job.total_epochs ?? state.totalEpochs,
        currentStep: job.current_step ?? state.currentStep,
        totalSteps: job.total_steps ?? state.totalSteps,
        trainLoss: job.current_train_loss ?? state.trainLoss,
        valLoss: job.current_val_loss ?? state.valLoss,
        bestMetric: job.best_metric ?? state.bestMetric,
        metrics: job.metrics || state.metrics,
        lossHistory: polledHistory(job) ?? state.lossHistory,
        startError: status === 'failed' ? (job.error?.message || job.result?.error || '학습 작업이 실패했습니다.') : null,
      }));
    } catch (error) {
      if (get().jobId !== jobId) return;
      const message = error instanceof Error ? error.message : '학습 상태를 확인할 수 없습니다.';
      set((state) => ({
        jobStatusError: message,
        ...(state.jobComputeProfileId ? { status: 'disconnected' as const, isTraining: true } : {}),
      }));
    }
  },

  reconnectCurrentJob: async () => {
    const { jobId, jobComputeProfileId } = get();
    if (!jobId || !jobComputeProfileId) throw new Error('다시 연결할 원격 작업이 없습니다.');
    try {
      const response = await api.training.reconnect(jobId);
      if (response.job_id !== jobId || response.compute_profile_id !== jobComputeProfileId) {
        throw new Error('다시 연결한 작업 ID 또는 서버가 원래 작업과 다릅니다.');
      }
      set({ status: 'running', isTraining: true, jobPhase: 'reconnecting', jobStatusError: null });
      await get().refreshCurrentJob();
    } catch (error) {
      const message = error instanceof Error ? error.message : '원격 작업에 다시 연결할 수 없습니다.';
      if (get().jobId === jobId) set({ status: 'disconnected', isTraining: true, jobStatusError: message });
      throw error;
    }
  },

  updateFromTelemetry: (event, data) => {
    if (event === 'hardware_stats') {
      set({
        hardware: {
          cpu_percent: Math.round(data.cpu_percent || 0),
          memory_percent: Math.round(data.memory_percent || 0),
          gpu_name: data.gpu_name || 'Hardware Accelerator',
          gpu_memory_used_mb: Math.round(data.gpu_memory_used_mb || 0),
          device_type: data.device_type || 'cpu',
        },
      });
      return;
    }
    if (!data?.job_id) return;
    if (!get().jobId) {
      if (startRequest && ['training_completed', 'training_aborted', 'training_error'].includes(event)) {
        pendingStartEvents.push({ event, data });
      }
      return;
    }
    if (data.job_id !== get().jobId) return;
    if (event === 'training_status' || event === 'remote_status') {
      const status = statusFromJob(data);
      if (status !== 'idle') {
        set((state) => ({
          status,
          isTraining: ACTIVE_STATUSES.includes(status),
          jobPhase: ACTIVE_STATUSES.includes(status) ? (data.phase || data.status || state.jobPhase) : status,
          transferProgress: transferPercent(data) ?? state.transferProgress,
          jobDeviceName: data.device_name || state.jobDeviceName,
        }));
      }
    } else if (event === 'step_progress') {
      const step = data.step || 0;
      const loss = typeof data.current_loss === 'number' ? data.current_loss : 0;
      set((s) => ({
        currentStep: step,
        totalSteps: data.total_steps || s.totalSteps,
        trainLoss: loss,
        epochEtaSeconds: data.epoch_eta_seconds ?? null,
        stepHistory: [...s.stepHistory.slice(-50), { step, loss }],
      }));
    } else if (event === 'epoch_progress') {
      const ep = data.epoch || 0;
      const tLoss = data.train_loss || 0;
      const vLoss = data.val_loss || 0;
      set((s) => ({
        currentEpoch: ep,
        totalEpochs: data.total_epochs || s.totalEpochs,
        trainLoss: tLoss,
        valLoss: vLoss,
        totalEtaSeconds: data.eta_seconds ?? null,
        metrics: data.metrics || s.metrics,
        lossHistory: [
          ...s.lossHistory,
          { epoch: ep, trainLoss: tLoss, valLoss: vLoss, lr: data.lr },
        ],
      }));
    } else if (event === 'training_completed') {
      if (get().status === 'stopping') return;
      if (!get().isCurrentData) { get().resetTraining(); return; }
      set({
        status: 'completed',
        jobPhase: 'completed',
        isTraining: false,
        bestMetric: data.best_metric ?? null,
      });
    } else if (event === 'training_aborted') {
      if (get().status === 'stopping') return;
      if (!get().isCurrentData) { get().resetTraining(); return; }
      set({ status: 'aborted', jobPhase: 'aborted', isTraining: false });
    } else if (event === 'training_error') {
      if (get().status === 'stopping') return;
      if (!get().isCurrentData) { get().resetTraining(); return; }
      set({ status: 'failed', jobPhase: 'failed', isTraining: false,
        startError: data.message || data.error?.message || data.error || '학습 작업이 실패했습니다.' });
    }
  },

  invalidateForDataChange: () => {
    trainingRecoverySequence += 1;
    const state = get();
    if (state.isTraining || ACTIVE_STATUSES.includes(state.status)) {
      // Keep the active job ID so its Stop button can still cancel the old run.
      set({ isCurrentData: false });
    } else {
      get().resetTraining();
    }
  },

  resetTraining: () => {
    trainingRecoverySequence += 1;
    pendingStartEvents = [];
    set({
      jobId: null,
      warmStartParentJobId: null,
      jobComputeProfileId: null,
      jobComputeLabel: 'This computer',
      jobDeviceName: null,
      jobPhase: null,
      transferProgress: null,
      startError: null,
      jobStatusError: null,
      isCurrentData: false,
      status: 'idle',
      isTraining: false,
      isRecoveringTraining: false,
      isStopRequestPending: false,
      stopError: null,
      currentEpoch: 0,
      totalEpochs: 0,
      currentStep: 0,
      totalSteps: 0,
      trainLoss: null,
      valLoss: null,
      bestMetric: null,
      metrics: {},
      lossHistory: [],
      stepHistory: [],
    });
  },
}));
