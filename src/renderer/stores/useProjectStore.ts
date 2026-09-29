/**
 * src/renderer/stores/useProjectStore.ts
 * Central wizard state, active vision task, backend lifecycle, and global errors.
 */

import { create } from 'zustand';
import type { BackendStatus } from '../../types/electron';
import type { ErrorCatalogItem, Language, VisionTask } from '../types';
import { api, setCachedPort } from '../services/api';
import { useAnnotationStore } from './useAnnotationStore';
import { useDatasetStore } from './useDatasetStore';

interface ProjectState {
  activeStep: 1 | 2 | 3 | 4 | 5 | 6;
  task: VisionTask;
  language: Language;
  backendPort: number | null;
  backendStatus: BackendStatus;
  activeError: ErrorCatalogItem | null;
  projectName: string;
  projectDir: string | null;

  setStep: (step: 1 | 2 | 3 | 4 | 5 | 6) => Promise<void>;
  openImageForLabeling: (imageId: string, filePath: string) => Promise<boolean>;
  setTask: (task: VisionTask) => Promise<void>;
  setLanguage: (lang: Language) => void;
  setBackendStatus: (status: BackendStatus) => void;
  showError: (error: ErrorCatalogItem) => void;
  clearError: () => void;
  syncCurrentProject: () => Promise<void>;
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  activeStep: 1,
  task: 'classification',
  language: 'ko',
  backendPort: null,
  backendStatus: { port: null, healthy: false, pid: null },
  activeError: null,
  projectName: 'Industrial Vision Project',
  projectDir: null,

  setStep: async (step) => {
    if (step === get().activeStep) return;
    if (get().activeStep === 2 && useAnnotationStore.getState().isDirty) {
      const saved = await useAnnotationStore.getState().saveAnnotations();
      if (!saved || useAnnotationStore.getState().isDirty) return;
    }
    set({ activeStep: step });
  },
  openImageForLabeling: async (imageId, filePath) => {
    const images = useDatasetStore.getState().images;
    const index = images.findIndex((image) => image.image_id === imageId && image.file_path === filePath);
    if (index < 0) return false;
    const opened = await useAnnotationStore.getState().setImages(images, index);
    if (!opened) return false;
    await get().setStep(2);
    return get().activeStep === 2;
  },
  setTask: async (task) => {
    if (task === get().task) return;
    if (useAnnotationStore.getState().isDirty) {
      const saved = await useAnnotationStore.getState().saveAnnotations();
      if (!saved || useAnnotationStore.getState().isDirty) return;
    }
    set({ task });
    api.project.update({ task }).catch(() => {});
    const dataset = useDatasetStore.getState();
    if (dataset.hasSelectedFolder) {
      await dataset.importFolder(dataset.folderPath, task).catch(() => {});
    }
  },
  setLanguage: (language) => set({ language }),
  setBackendStatus: (status) => {
    setCachedPort(status.port);
    set({ backendStatus: status, backendPort: status.port });
  },
  showError: (activeError) => set({ activeError }),
  clearError: () => set({ activeError: null }),

  syncCurrentProject: async () => {
    try {
      const proj = await api.project.getCurrent();
      if (proj) {
        set({
          projectName: proj.name || get().projectName,
          task: (proj.task as VisionTask) || get().task,
          projectDir: proj.project_dir || null,
        });
      }
    } catch {
      // non-critical scratch project fallback
    }
  },
}));
