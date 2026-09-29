/**
 * src/renderer/stores/useProjectStore.ts
 * Central wizard state, active vision task, backend lifecycle, and global errors.
 */

import { create } from 'zustand';
import type { BackendStatus } from '../../types/electron';
import type { ErrorCatalogItem, Language, VisionTask } from '../types';
import { api, setCachedPort } from '../services/api';

interface ProjectState {
  activeStep: 1 | 2 | 3 | 4 | 5 | 6;
  task: VisionTask;
  language: Language;
  backendPort: number | null;
  backendStatus: BackendStatus;
  activeError: ErrorCatalogItem | null;
  projectName: string;
  projectDir: string | null;

  setStep: (step: 1 | 2 | 3 | 4 | 5 | 6) => void;
  setTask: (task: VisionTask) => void;
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

  setStep: (step) => set({ activeStep: step }),
  setTask: (task) => {
    set({ task });
    api.project.update({ task }).catch(() => {});
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
