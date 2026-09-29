/**
 * src/renderer/stores/useTrainingStore.ts
 * AutoML Training state: presets, progress, live loss curves, and hardware telemetry.
 */

import { create } from 'zustand';
import type { HardwareStats, TrainingPreset, VisionTask } from '../types';
import { api } from '../services/api';

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
  status: 'idle' | 'running' | 'completed' | 'aborted' | 'failed';
  isTraining: boolean;
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
  updateFromTelemetry: (event: string, data: any) => void;
  resetTraining: () => void;
}

export const useTrainingStore = create<TrainingState>((set, get) => ({
  jobId: null,
  status: 'idle',
  isTraining: false,
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
    set({
      status: 'running',
      isTraining: true,
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
      const res = await api.training.start({
        task,
        preset: get().preset,
        dataset_path: datasetPath,
      });
      set({ jobId: res.job_id });
    } catch (e) {
      set({ status: 'failed', isTraining: false });
      throw e;
    }
  },

  stopTraining: async () => {
    try {
      await api.training.stop(get().jobId || undefined);
      set({ status: 'aborted', isTraining: false });
    } catch {
      set({ status: 'aborted', isTraining: false });
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
      set({
        status: 'completed',
        isTraining: false,
        bestMetric: data.best_metric ?? null,
      });
    } else if (event === 'training_aborted') {
      set({ status: 'aborted', isTraining: false });
    } else if (event === 'training_error') {
      set({ status: 'failed', isTraining: false });
    }
  },

  resetTraining: () =>
    set({
      status: 'idle',
      isTraining: false,
      currentEpoch: 0,
      totalEpochs: 0,
      currentStep: 0,
      totalSteps: 0,
      trainLoss: null,
      valLoss: null,
      lossHistory: [],
      stepHistory: [],
    }),
}));
