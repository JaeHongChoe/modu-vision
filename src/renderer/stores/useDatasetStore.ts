/**
 * src/renderer/stores/useDatasetStore.ts
 * Dataset management: folder importing, synthetic generation, train/val split, and thumbnails.
 */

import { create } from 'zustand';
import type { ImageMeta, VisionTask } from '../types';
import { api } from '../services/api';
import { useTrainingStore } from './useTrainingStore';
import { useEvaluationStore } from './useEvaluationStore';
import { useFlowchartStore } from './useFlowchartStore';

interface DatasetState {
  folderPath: string;
  hasSelectedFolder: boolean;
  datasetKey: string | null;
  lastImportedKey: string | null;
  staleDatasetKeys: string[];
  importError: string | null;
  sourceSaveError: string | null;
  splitError: string | null;
  splitSupported: boolean | null;
  splitUnavailableReason: string | null;
  totalImages: number;
  sourceImages: number;
  unlabeledImages: number;
  classes: Record<string, number>;
  split: { train: number; val: number; test: number };
  images: ImageMeta[];
  totalImagesCount: number;
  page: number;
  pageSize: number;
  activeSplitFilter: 'all' | 'train' | 'val' | 'test';
  activeClassFilter: string | null;
  activeLabelFilter: 'all' | 'labeled' | 'unlabeled';
  trainRatio: number;
  isLoading: boolean;
  isGenerating: boolean;
  isSplitting: boolean;
  showGeneratorModal: boolean;
  corruptedImages: any[];

  setFolderPath: (path: string) => void;
  setShowGeneratorModal: (show: boolean) => void;
  setSplitFilter: (split: 'all' | 'train' | 'val' | 'test') => void;
  setClassFilter: (className: string | null) => void;
  setLabelFilter: (status: 'all' | 'labeled' | 'unlabeled') => void;
  setTrainRatio: (ratio: number) => void;
  importFolder: (folder: string, task: VisionTask, allowRecoveryOverride?: boolean) => Promise<void>;
  ensureImported: (task: VisionTask) => Promise<void>;
  generateSynthetic: (params: {
    task: VisionTask;
    num_samples: number;
    modality: 'pcb' | 'wafer' | 'metal';
    split_ratio: number;
  }) => Promise<void>;
  applySplit: (ratio: number, valRatio?: number, testRatio?: number) => Promise<void>;
  annotationsChanged: () => Promise<void>;
  loadImages: (page?: number) => Promise<void>;
}

let latestImageRequest = 0;
let latestImportRequest = 0;
let latestSplitRequest = 0;

const importKey = (folder: string, task: VisionTask) => `${folder}\0${task}`;

function invalidateDownstream(allowSourceRecovery = false): void {
  useTrainingStore.getState().invalidateForDataChange();
  useEvaluationStore.getState().invalidateForDataChange(allowSourceRecovery);
  useFlowchartStore.getState().invalidateForDataChange();
}

function importErrorMessage(error: unknown, fallback = 'Dataset import failed'): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object') {
    for (const key of ['details', 'message_ko', 'detail', 'message']) {
      const value = (error as Record<string, unknown>)[key];
      if (typeof value === 'string' && value) return value;
      if (key === 'detail' && value && typeof value === 'object') {
        return importErrorMessage(value, fallback);
      }
    }
  }
  return fallback;
}

export const useDatasetStore = create<DatasetState>((set, get) => ({
  folderPath: './datasets/synthetic',
  hasSelectedFolder: false,
  datasetKey: null,
  lastImportedKey: null,
  staleDatasetKeys: [],
  importError: null,
  sourceSaveError: null,
  splitError: null,
  splitSupported: null,
  splitUnavailableReason: null,
  totalImages: 0,
  sourceImages: 0,
  unlabeledImages: 0,
  classes: {},
  split: { train: 0, val: 0, test: 0 },
  images: [],
  totalImagesCount: 0,
  page: 1,
  pageSize: 48,
  activeSplitFilter: 'all',
  activeClassFilter: null,
  activeLabelFilter: 'all',
  trainRatio: 0.8,
  isLoading: false,
  isGenerating: false,
  isSplitting: false,
  showGeneratorModal: false,
  corruptedImages: [],

  setFolderPath: (folderPath) => {
    if (folderPath === get().folderPath) return;
    invalidateDownstream();
    latestImportRequest += 1;
    latestImageRequest += 1;
    latestSplitRequest += 1;
    set({
      folderPath, hasSelectedFolder: Boolean(folderPath), datasetKey: null, importError: null, sourceSaveError: null, splitError: null,
      splitSupported: null, splitUnavailableReason: null,
      totalImages: 0, sourceImages: 0, unlabeledImages: 0,
      classes: {}, split: { train: 0, val: 0, test: 0 },
      images: [], totalImagesCount: 0, corruptedImages: [],
      activeSplitFilter: 'all', activeClassFilter: null, activeLabelFilter: 'all', page: 1, isLoading: false, isSplitting: false,
    });
  },
  setShowGeneratorModal: (showGeneratorModal) => set({ showGeneratorModal }),
  setSplitFilter: (activeSplitFilter) => {
    set({ activeSplitFilter, page: 1 });
    get().loadImages(1);
  },
  setClassFilter: (activeClassFilter) => {
    set({ activeClassFilter, page: 1 });
    get().loadImages(1);
  },
  setLabelFilter: (activeLabelFilter) => {
    set({ activeLabelFilter, page: 1 });
    get().loadImages(1);
  },
  setTrainRatio: (trainRatio) => set({ trainRatio }),

  importFolder: async (folder, task, allowRecoveryOverride) => {
    const key = importKey(folder, task);
    const previous = get().lastImportedKey;
    const previousFolder = previous?.slice(0, previous.lastIndexOf('\0'));
    const changedTaskInSameFolder = previousFolder === folder && previous !== key;
    if (changedTaskInSameFolder && previous) {
      set((state) => ({ staleDatasetKeys: [...new Set([...state.staleDatasetKeys, previous, key])] }));
    }
    const allowSourceRecovery = allowRecoveryOverride ?? (
      !get().staleDatasetKeys.includes(key)
      && !changedTaskInSameFolder
      && (!previous || previousFolder !== folder)
    );
    invalidateDownstream(allowSourceRecovery);
    const requestId = ++latestImportRequest;
    latestImageRequest += 1;
    latestSplitRequest += 1;
    set({
      folderPath: folder, hasSelectedFolder: true, datasetKey: key, importError: null, sourceSaveError: null, splitError: null,
      splitSupported: null, splitUnavailableReason: null,
      images: [], totalImagesCount: 0, totalImages: 0,
      sourceImages: 0, unlabeledImages: 0, classes: {}, corruptedImages: [],
      split: { train: 0, val: 0, test: 0 },
      activeSplitFilter: 'all', activeClassFilter: null, activeLabelFilter: 'all', page: 1, isLoading: true, isSplitting: false,
    });
    try {
      const res = await api.dataset.import({ folder_path: folder, task, validate_images: true });
      if (requestId !== latestImportRequest || get().datasetKey !== key) return;
      set({
        totalImages: res.total_images,
        sourceImages: res.source_images ?? res.total_images,
        unlabeledImages: res.unlabeled_images ?? 0,
        splitSupported: res.split_supported ?? null,
        splitUnavailableReason: res.split_unavailable_reason ?? null,
        classes: res.classes || {},
        split: {
          train: res.split.train,
          val: res.split.val,
          test: res.split.test || 0,
        },
        corruptedImages: res.corrupted_images || [],
        lastImportedKey: key,
        page: 1,
      });
      try {
        const project = await api.project.update({ source_dataset_dir: folder });
        if (requestId !== latestImportRequest || get().datasetKey !== key) return;
        // The project API is the authority for the canonical source path. Keep
        // the renderer's project state aligned before allowing a switch.
        const { useProjectStore } = await import('./useProjectStore');
        useProjectStore.setState((state) => state.project?.id === project.id
          ? { project, projectName: project.name, projectDir: project.project_dir }
          : {});
      } catch (error) {
        // The in-memory dataset remains usable, but a restart cannot restore its source.
        if (requestId === latestImportRequest && get().datasetKey === key) {
          set({ sourceSaveError: `데이터는 불러왔지만 프로젝트에 경로를 저장하지 못했습니다: ${importErrorMessage(error)}` });
        }
      }
      if (requestId !== latestImportRequest || get().datasetKey !== key) return;
      set({ isLoading: false });
      await get().loadImages(1);
    } catch (err) {
      if (requestId === latestImportRequest && get().datasetKey === key) {
        set({ isLoading: false, importError: importErrorMessage(err) });
      }
      throw err;
    }
  },

  ensureImported: async (task) => {
    const { folderPath, hasSelectedFolder, datasetKey } = get();
    if (!folderPath || !hasSelectedFolder || datasetKey === importKey(folderPath, task)) return;
    await get().importFolder(folderPath, task);
  },

  generateSynthetic: async (params) => {
    invalidateDownstream();
    set({ isGenerating: true });
    try {
      const res = await api.dataset.generate({
        task: params.task,
        num_samples: params.num_samples,
        output_dir: `./datasets/synthetic_${params.modality}`,
        modality: params.modality,
        split_ratio: params.split_ratio,
        seed: 42,
      });
      set({ isGenerating: false, showGeneratorModal: false });
      set((state) => ({
        staleDatasetKeys: [...new Set([...state.staleDatasetKeys, importKey(res.output_dir, params.task)])],
      }));
      await get().importFolder(res.output_dir, params.task, false);
    } catch (err) {
      set({ isGenerating: false });
      throw err;
    }
  },

  applySplit: async (ratio, valRatio, testRatio = 0) => {
    const requestId = ++latestSplitRequest;
    const { folderPath, datasetKey } = get();
    const task = datasetKey?.slice(datasetKey.lastIndexOf('\0') + 1) as VisionTask | undefined;
    set({ isSplitting: true, splitError: null });
    try {
      const res = await api.dataset.split({
        folder_path: folderPath,
        task,
        train_ratio: ratio,
        val_ratio: valRatio,
        test_ratio: testRatio,
        seed: 42,
      });
      if (requestId !== latestSplitRequest || get().datasetKey !== datasetKey) return;
      if (datasetKey) {
        set((state) => ({ staleDatasetKeys: [...new Set([...state.staleDatasetKeys, datasetKey])] }));
      }
      invalidateDownstream();
      set({
        isSplitting: false,
        trainRatio: ratio,
        split: res.split,
      });
      await get().loadImages(1);
    } catch (err) {
      if (requestId === latestSplitRequest && get().datasetKey === datasetKey) {
        set({ isSplitting: false, splitError: importErrorMessage(err, 'Dataset split failed') });
      }
      throw err;
    }
  },

  annotationsChanged: async () => {
    const currentKey = get().datasetKey;
    if (currentKey) {
      set((state) => ({ staleDatasetKeys: [...new Set([...state.staleDatasetKeys, currentKey])] }));
    }
    invalidateDownstream();
    const { folderPath, datasetKey } = get();
    if (!datasetKey) return;
    // Until the refreshed manifest is read, never show the old split as trainable.
    set({ split: { train: 0, val: 0, test: 0 }, splitError: null });
    const task = datasetKey.slice(datasetKey.lastIndexOf('\0') + 1) as VisionTask;
    const requestId = ++latestImportRequest;
    try {
      const res = await api.dataset.import({ folder_path: folderPath, task, validate_images: false });
      if (requestId !== latestImportRequest || get().datasetKey !== datasetKey) return;
      set({
        totalImages: res.total_images,
        sourceImages: res.source_images ?? res.total_images,
        unlabeledImages: res.unlabeled_images ?? 0,
        splitSupported: res.split_supported ?? null,
        splitUnavailableReason: res.split_unavailable_reason ?? null,
        classes: res.classes || {},
        split: { train: res.split.train, val: res.split.val, test: res.split.test || 0 },
        importError: null,
        page: 1,
      });
      await get().loadImages(1);
    } catch (error) {
      if (requestId === latestImportRequest && get().datasetKey === datasetKey) {
        set({ importError: importErrorMessage(error) });
      }
    }
  },

  loadImages: async (pageArg) => {
    if (get().isLoading || get().importError) return;
    const requestId = ++latestImageRequest;
    const { folderPath, datasetKey, pageSize, activeSplitFilter, activeClassFilter, activeLabelFilter } = get();
    const task = datasetKey?.slice(datasetKey.lastIndexOf('\0') + 1) as VisionTask | undefined;
    const p = pageArg || get().page;
    const offset = (p - 1) * pageSize;
    try {
      const res = await api.dataset.getImages({
        folder_path: folderPath,
        task,
        limit: pageSize,
        offset,
        split: activeSplitFilter === 'all' ? undefined : activeSplitFilter,
        class_name: activeClassFilter || undefined,
        label_status: activeLabelFilter === 'all' ? undefined : activeLabelFilter,
      });
      if (requestId !== latestImageRequest || get().folderPath !== folderPath || get().datasetKey !== datasetKey) return;
      set({
        images: res.items || [],
        totalImagesCount: res.total || 0,
        page: p,
      });
    } catch {
      // non-blocking fallback
    }
  },
}));
