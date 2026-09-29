/**
 * src/renderer/stores/useDatasetStore.ts
 * Dataset management: folder importing, synthetic generation, train/val split, and thumbnails.
 */

import { create } from 'zustand';
import type { ImageMeta, VisionTask } from '../types';
import { api } from '../services/api';

interface DatasetState {
  folderPath: string;
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
  setTrainRatio: (ratio: number) => void;
  importFolder: (folder: string, task: VisionTask) => Promise<void>;
  generateSynthetic: (params: {
    task: VisionTask;
    num_samples: number;
    modality: 'pcb' | 'wafer' | 'metal';
    split_ratio: number;
  }) => Promise<void>;
  applySplit: (ratio: number, valRatio?: number, testRatio?: number) => Promise<void>;
  loadImages: (page?: number) => Promise<void>;
}

let latestImageRequest = 0;

export const useDatasetStore = create<DatasetState>((set, get) => ({
  folderPath: './datasets/synthetic',
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
  trainRatio: 0.8,
  isLoading: false,
  isGenerating: false,
  isSplitting: false,
  showGeneratorModal: false,
  corruptedImages: [],

  setFolderPath: (folderPath) => set({ folderPath }),
  setShowGeneratorModal: (showGeneratorModal) => set({ showGeneratorModal }),
  setSplitFilter: (activeSplitFilter) => {
    set({ activeSplitFilter, page: 1 });
    get().loadImages(1);
  },
  setClassFilter: (activeClassFilter) => {
    set({ activeClassFilter, page: 1 });
    get().loadImages(1);
  },
  setTrainRatio: (trainRatio) => set({ trainRatio }),

  importFolder: async (folder, task) => {
    latestImageRequest += 1;
    set({ folderPath: folder, images: [], totalImagesCount: 0, totalImages: 0, sourceImages: 0, unlabeledImages: 0,
      split: { train: 0, val: 0, test: 0 }, activeSplitFilter: 'all', page: 1, isLoading: true });
    try {
      const res = await api.dataset.import({ folder_path: folder, task, validate_images: true });
      if (get().folderPath !== folder) return;
      set({
        totalImages: res.total_images,
        sourceImages: res.source_images ?? res.total_images,
        unlabeledImages: res.unlabeled_images ?? 0,
        classes: res.classes || {},
        split: {
          train: res.split.train,
          val: res.split.val,
          test: res.split.test || 0,
        },
        corruptedImages: res.corrupted_images || [],
        isLoading: false,
        page: 1,
      });
      await get().loadImages(1);
    } catch (err) {
      set({ isLoading: false });
      throw err;
    }
  },

  generateSynthetic: async (params) => {
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
      await get().importFolder(res.output_dir, params.task);
    } catch (err) {
      set({ isGenerating: false });
      throw err;
    }
  },

  applySplit: async (ratio, valRatio, testRatio = 0) => {
    set({ isSplitting: true });
    try {
      const res = await api.dataset.split({
        folder_path: get().folderPath,
        train_ratio: ratio,
        val_ratio: valRatio,
        test_ratio: testRatio,
        seed: 42,
      });
      set({
        isSplitting: false,
        trainRatio: ratio,
        split: res.split,
      });
      await get().loadImages(1);
    } catch (err) {
      set({ isSplitting: false });
      throw err;
    }
  },

  loadImages: async (pageArg) => {
    const requestId = ++latestImageRequest;
    const { folderPath, pageSize, activeSplitFilter, activeClassFilter } = get();
    const p = pageArg || get().page;
    const offset = (p - 1) * pageSize;
    try {
      const res = await api.dataset.getImages({
        folder_path: folderPath,
        limit: pageSize,
        offset,
        split: activeSplitFilter === 'all' ? undefined : activeSplitFilter,
        class_name: activeClassFilter || undefined,
      });
      if (requestId !== latestImageRequest || get().folderPath !== folderPath) return;
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
