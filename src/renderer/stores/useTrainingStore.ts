/**
 * src/renderer/stores/useTrainingStore.ts
 * AutoML Training state: presets, progress, live loss curves, and hardware telemetry.
 */

import { create } from 'zustand';
import type { HardwareStats, TrainingPreset, VisionTask } from '../types';
import { api } from '../services/api';
import { useDatasetStore } from './useDatasetStore';

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

interface TrainingState {
  jobId: string | null;
  isCurrentData: boolean;
  status: 'idle' | 'running' | 'stopping' | 'completed' | 'aborted' | 'failed';
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
  startTraining: (datasetPath: string, task: VisionTask) => Promise<void>;
  stopTraining: () => Promise<void>;
  recoverActiveJob: () => Promise<void>;
  updateFromTelemetry: (event: string, data: any) => void;
  invalidateForDataChange: () => void;
  resetTraining: () => void;
}

let startRequest: ReturnType<typeof api.training.start> | null = null;
let pendingStartEvents: Array<{ event: string; data: any }> = [];

async function waitForStoppedJob(jobId: string): Promise<'completed' | 'aborted' | 'failed'> {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const job = await api.training.getStatus(jobId);
    if (job.status === 'completed' || job.status === 'aborted' || job.status === 'failed') {
      return job.status;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('학습 중단을 요청했지만 종료 확인이 지연되고 있습니다. 상태를 다시 확인하세요.');
}

export const useTrainingStore = create<TrainingState>((set, get) => ({
  jobId: null,
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

  startTraining: async (datasetPath, task) => {
    if (useDatasetStore.getState().isSplitting) {
      throw new Error('데이터 분할이 진행 중입니다. 완료 후 학습을 시작하세요.');
    }
    pendingStartEvents = [];
    set({
      jobId: null,
      isCurrentData: true,
      status: 'running',
      isTraining: true,
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
      });
      const res = await startRequest;
      set({ jobId: res.job_id });
      const queued = pendingStartEvents;
      pendingStartEvents = [];
      for (const item of queued) {
        if (item.data.job_id === res.job_id) get().updateFromTelemetry(item.event, item.data);
      }
    } catch (e) {
      pendingStartEvents = [];
      set({ status: 'failed', isTraining: false, isStopRequestPending: false });
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
      if (get().isCurrentData) set({ status, isTraining: false, stopError: null });
      else get().resetTraining();
    } catch (error) {
      const message = error instanceof Error ? error.message : '학습 중단 상태를 확인할 수 없습니다.';
      set((state) => ({
        status: state.jobId ? (stopAccepted ? 'stopping' : 'running') : 'failed',
        isTraining: Boolean(state.jobId),
        stopError: message,
      }));
    } finally {
      set({ isStopRequestPending: false });
    }
  },

  recoverActiveJob: async () => {
    if (get().isTraining || get().isRecoveringTraining || startRequest) return;
    set({ isRecoveringTraining: true });
    try {
      const active = await api.training.getStatus();
      if (!get().isTraining && !startRequest && active?.job_id
          && (active.status === 'running' || active.status === 'stopping')) {
        // A recovered job remains cancellable even if its source data changed.
        // Step 4 recovers its result separately only after provenance checks.
        set({
          jobId: active.job_id,
          isCurrentData: false,
          status: active.status,
          isTraining: true,
          isStopRequestPending: false,
          stopError: null,
          currentEpoch: active.current_epoch || 0,
          totalEpochs: active.total_epochs || 0,
          currentStep: active.current_step || 0,
          totalSteps: active.total_steps || 0,
          trainLoss: active.current_train_loss ?? null,
          valLoss: active.current_val_loss ?? null,
        });
      }
    } catch {
      // The backend may still be starting; normal start/stop errors remain visible.
    } finally {
      set({ isRecoveringTraining: false });
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
    if (event === 'step_progress') {
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
        isTraining: false,
        bestMetric: data.best_metric ?? null,
      });
    } else if (event === 'training_aborted') {
      if (get().status === 'stopping') return;
      if (!get().isCurrentData) { get().resetTraining(); return; }
      set({ status: 'aborted', isTraining: false });
    } else if (event === 'training_error') {
      if (get().status === 'stopping') return;
      if (!get().isCurrentData) { get().resetTraining(); return; }
      set({ status: 'failed', isTraining: false });
    }
  },

  invalidateForDataChange: () => {
    const state = get();
    if (state.isTraining || state.status === 'running' || state.status === 'stopping') {
      // Keep the active job ID so its Stop button can still cancel the old run.
      set({ isCurrentData: false });
    } else {
      get().resetTraining();
    }
  },

  resetTraining: () => {
    pendingStartEvents = [];
    set({
      jobId: null,
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
