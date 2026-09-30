/** Wizard state and durable project workspace switching. */

import { create } from 'zustand';
import type { BackendStatus } from '../../types/electron';
import type { ErrorCatalogItem, Language, VisionTask } from '../types';
import { api, setCachedPort, type ProjectBackupResult, type ProjectConfig, type RecentProject } from '../services/api';
import { useAnnotationStore } from './useAnnotationStore';
import { useDatasetStore } from './useDatasetStore';
import { useFlowchartStore } from './useFlowchartStore';
import { useTrainingStore } from './useTrainingStore';
import { useInspectionRunStore } from './useInspectionRunStore';
import { useModelAssistRunStore } from './useModelAssistRunStore';
import { getFlowchartModelReferences } from '../components/flowchart/flowchartStartup';
import { projectFlowRecipe } from './projectFlowRecipe';

type WizardStep = 1 | 2 | 3 | 4 | 5 | 6;

interface ProjectState {
  activeStep: WizardStep;
  task: VisionTask;
  language: Language;
  backendPort: number | null;
  backendStatus: BackendStatus;
  activeError: ErrorCatalogItem | null;
  project: ProjectConfig | null;
  projectName: string;
  projectDir: string | null;
  recentProjects: RecentProject[];
  isProjectBusy: boolean;
  projectError: string | null;

  setStep: (step: WizardStep) => Promise<void>;
  openImageForLabeling: (imageId: string, filePath: string) => Promise<boolean>;
  setTask: (task: VisionTask) => Promise<void>;
  setLanguage: (lang: Language) => void;
  setBackendStatus: (status: BackendStatus) => void;
  showError: (error: ErrorCatalogItem) => void;
  clearError: () => void;
  clearProjectError: () => void;
  syncCurrentProject: () => Promise<void>;
  loadRecentProjects: () => Promise<void>;
  createProject: (data: { name: string; task: VisionTask; project_dir?: string; description?: string }) => Promise<boolean>;
  openProject: (projectDir: string) => Promise<boolean>;
  activateLabelset: (id: string) => Promise<boolean>;
  createAndActivateLabelset: (name: string) => Promise<boolean>;
  backupProject: (destinationDir: string) => Promise<ProjectBackupResult | null>;
  restoreProject: (archivePath: string, targetDir: string) => Promise<boolean>;
}

function projectErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object') {
    const detail = (error as Record<string, unknown>).detail;
    if (typeof detail === 'string') return detail;
  }
  return '프로젝트를 열지 못했습니다.';
}

async function saveOpenEdits(): Promise<void> {
  if (useModelAssistRunStore.getState().activeOperations > 0) {
    throw new Error('모델 보조 라벨링 요청이 진행 중입니다. 완료 후 프로젝트를 전환하세요.');
  }
  if (useInspectionRunStore.getState().isRunning) {
    throw new Error('일괄 검사가 진행 중입니다. 완료 후 프로젝트를 전환하세요.');
  }
  const dataset = useDatasetStore.getState();
  if (dataset.isLoading || dataset.isSplitting || dataset.isGenerating) {
    throw new Error('데이터 작업이 진행 중입니다. 완료 후 프로젝트를 전환하세요.');
  }
  const training = useTrainingStore.getState();
  if (training.isTraining) {
    throw new Error('학습이 진행 중입니다. 작업이 끝난 후 프로젝트를 전환하세요.');
  }
  const currentProject = useProjectStore.getState().project;
  if (currentProject) {
    const backendProject = await api.project.getCurrent();
    if (backendProject.id !== currentProject.id || backendProject.project_dir !== currentProject.project_dir) {
      // A daemon restart can restore a different active pointer while the
      // renderer still holds unsaved work from the previous project.
      const reopened = await api.project.open(currentProject.project_dir);
      if (reopened.id !== currentProject.id || reopened.project_dir !== currentProject.project_dir) {
        throw new Error('편집 중인 프로젝트를 백엔드에서 다시 열지 못해 저장을 중단했습니다.');
      }
    }
  }
  let verifiedSource = currentProject?.source_dataset_dir || null;
  if (currentProject && dataset.hasSelectedFolder && dataset.datasetKey === `${dataset.folderPath}\0${currentProject.task}`
      && !dataset.importError && (dataset.sourceSaveError || verifiedSource !== dataset.folderPath)) {
    // A successful import may have saved its source in the daemon while the
    // renderer still holds the previous config. Refresh it before checking
    // a dirty flow. Retry a failed source save instead of losing that link.
    const refreshed = await api.project.update({ source_dataset_dir: dataset.folderPath });
    if (refreshed.id !== currentProject.id) {
      throw new Error('데이터 출처를 저장하는 중 활성 프로젝트가 변경되었습니다.');
    }
    useProjectStore.setState({ project: refreshed, projectName: refreshed.name, projectDir: refreshed.project_dir });
    useDatasetStore.setState({ sourceSaveError: null });
    verifiedSource = refreshed.source_dataset_dir;
  }
  const flow = useFlowchartStore.getState();
  if (flow.isRunning) {
    throw new Error('플로우 실행이 진행 중입니다. 완료 후 프로젝트를 전환하세요.');
  }
  if (flow.isLoading || flow.isSaving) {
    throw new Error('플로우를 불러오거나 저장하는 중입니다. 완료 후 프로젝트를 전환하세요.');
  }
  // Saving a changed label invalidates flow state; preserve the edited graph first.
  if (flow.pipelineDirty) {
    const { project } = useProjectStore.getState();
    const source = verifiedSource;
    const folderPath = useDatasetStore.getState().folderPath;
    const pipeline = flow.pipeline;
    const datasetReady = useDatasetStore.getState().datasetKey === `${folderPath}\0${project?.task}`
      && !useDatasetStore.getState().importError;
    if (!pipeline || !project || !source || !datasetReady) {
      throw new Error('플로우의 데이터 출처가 확인되지 않아 프로젝트를 전환할 수 없습니다. 5단계에서 플로우를 확인해 저장하세요.');
    }
    let references;
    try {
      references = getFlowchartModelReferences(pipeline);
    } catch {
      throw new Error('플로우 모델 작업 유형을 확인할 수 없습니다. 5단계에서 모델 연결을 수정하세요.');
    }
    const modelNodes = pipeline.nodes.filter((node) =>
      node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop');
    if (!modelNodes.length || modelNodes.some((node) => !node.data.model_job_id) || references.length !== modelNodes.length) {
      throw new Error('모든 검사 노드에 완료된 모델을 연결한 뒤 5단계에서 플로우를 저장하세요.');
    }
    await api.flowchart.verifyModels({ source_dataset_path: source, models: references });
    if (useFlowchartStore.getState().pipeline !== pipeline ||
        useProjectStore.getState().project?.id !== project.id ||
        useDatasetStore.getState().folderPath !== folderPath) {
      throw new Error('플로우 또는 데이터가 검증 중 변경되었습니다. 프로젝트 전환을 다시 시도하세요.');
    }
    await flow.savePipeline(undefined, projectFlowRecipe(pipeline), source);
    const saved = useFlowchartStore.getState();
    if (saved.pipeline !== pipeline || saved.pipelineDirty || saved.errorMessage || saved.isSaving) {
      throw new Error(saved.errorMessage || '수정한 플로우의 저장 성공을 확인하지 못해 프로젝트 전환을 중단했습니다.');
    }
  }
  const annotations = useAnnotationStore.getState();
  if (annotations.isDirty) {
    const saved = await annotations.saveAnnotations();
    if (!saved || useAnnotationStore.getState().isDirty) {
      throw new Error(annotations.saveMessage || '수정한 라벨을 저장하지 못해 프로젝트 전환을 중단했습니다.');
    }
  }
}

async function applyProject(project: ProjectConfig, previous: ProjectConfig | null, forceReset = false): Promise<void> {
  const contextChanged = previous?.id !== project.id || previous?.project_dir !== project.project_dir
    || (previous?.active_labelset_id || 'default') !== (project.active_labelset_id || 'default');
  if (forceReset || contextChanged) {
    const cleared = await useAnnotationStore.getState().setImages([]);
    if (!cleared) throw new Error('라벨 화면을 정리하지 못했습니다.');
    useAnnotationStore.getState().setTask(project.task);
    if (contextChanged) {
      // Import/edit history belongs to one workspace and label set. A new
      // context may use the same source folder; recover its model through the
      // backend's source, task, ownership, and fingerprint checks.
      useDatasetStore.setState({ lastImportedKey: null, staleDatasetKeys: [] });
    }
    useDatasetStore.getState().setFolderPath('');
    if (project.source_dataset_dir) {
      useDatasetStore.getState().setFolderPath(project.source_dataset_dir);
    }
  }
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  activeStep: 1,
  task: 'classification',
  language: 'ko',
  backendPort: null,
  backendStatus: { port: null, healthy: false, pid: null },
  activeError: null,
  project: null,
  projectName: '프로젝트 불러오는 중',
  projectDir: null,
  recentProjects: [],
  isProjectBusy: false,
  projectError: null,

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
    set({ projectError: null });
    try {
      await saveOpenEdits();
      const project = await api.project.update({ task });
      set({ task: project.task, project, projectName: project.name });
      useAnnotationStore.getState().setTask(project.task);
      const dataset = useDatasetStore.getState();
      if (dataset.hasSelectedFolder) {
        await dataset.importFolder(dataset.folderPath, project.task);
      }
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
    }
  },

  setLanguage: (language) => set({ language }),
  setBackendStatus: (status) => {
    setCachedPort(status.port);
    set({ backendStatus: status, backendPort: status.port });
  },
  showError: (activeError) => set({ activeError }),
  clearError: () => set({ activeError: null }),
  clearProjectError: () => set({ projectError: null }),

  syncCurrentProject: async () => {
    if (get().isProjectBusy) return;
    set({ isProjectBusy: true, projectError: null });
    try {
      const project = await api.project.getCurrent();
      const previous = get().project;
      const changed = Boolean(previous && (previous.id !== project.id || previous.project_dir !== project.project_dir));
      if (changed && (useAnnotationStore.getState().isDirty || useFlowchartStore.getState().pipelineDirty)) {
        throw new Error('백엔드의 활성 프로젝트가 바뀌었지만 저장하지 않은 라벨 또는 플로우가 있습니다. 프로젝트 관리에서 전환하면 원래 프로젝트에 먼저 저장할 수 있습니다.');
      }
      if (useModelAssistRunStore.getState().activeOperations > 0) {
        throw new Error('모델 보조 라벨링 요청이 진행 중입니다. 완료 후 프로젝트를 동기화하세요.');
      }
      if (!changed && previous) await saveOpenEdits();
      // A daemon restart loses its imported dataset cache. Reconnect the saved source.
      if (changed && (useAnnotationStore.getState().isDirty || useFlowchartStore.getState().pipelineDirty)) {
        throw new Error('프로젝트 동기화 중 새 편집이 생겼습니다. 이전 프로젝트의 편집 내용을 먼저 저장하세요.');
      }
      await applyProject(project, previous, true);
      set({
        project, projectName: project.name, projectDir: project.project_dir,
        task: project.task, activeStep: get().project?.id === project.id ? get().activeStep : 1,
      });
      await get().loadRecentProjects();
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
    } finally {
      set({ isProjectBusy: false });
    }
  },

  loadRecentProjects: async () => {
    try {
      const result = await api.project.list();
      set({ recentProjects: result.projects });
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
    }
  },

  createProject: async (data) => {
    if (get().isProjectBusy) return false;
    set({ isProjectBusy: true, projectError: null });
    try {
      await saveOpenEdits();
      const project = await api.project.create(data);
      await applyProject(project, get().project);
      set({ project, projectName: project.name, projectDir: project.project_dir,
        task: project.task, activeStep: 1 });
      await get().loadRecentProjects();
      return true;
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return false;
    } finally {
      set({ isProjectBusy: false });
    }
  },

  openProject: async (projectDir) => {
    if (get().isProjectBusy) return false;
    set({ isProjectBusy: true, projectError: null });
    try {
      await saveOpenEdits();
      const project = await api.project.open(projectDir);
      await applyProject(project, get().project);
      set({ project, projectName: project.name, projectDir: project.project_dir,
        task: project.task, activeStep: 1 });
      await get().loadRecentProjects();
      return true;
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return false;
    } finally {
      set({ isProjectBusy: false });
    }
  },

  activateLabelset: async (id) => {
    if (get().isProjectBusy) return false;
    set({ isProjectBusy: true, projectError: null });
    try {
      await saveOpenEdits();
      const previous = get().project;
      if (!previous) throw new Error('열린 프로젝트가 없습니다.');
      const project = await api.project.activateLabelset(id);
      await applyProject(project, previous, true);
      set({ project, projectName: project.name, projectDir: project.project_dir, task: project.task });
      if (project.source_dataset_dir) {
        await useDatasetStore.getState().importFolder(project.source_dataset_dir, project.task);
      }
      return true;
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return false;
    } finally {
      set({ isProjectBusy: false });
    }
  },

  createAndActivateLabelset: async (name) => {
    if (get().isProjectBusy) return false;
    set({ isProjectBusy: true, projectError: null });
    try {
      await saveOpenEdits();
      const previous = get().project;
      if (!previous) throw new Error('열린 프로젝트가 없습니다.');
      const created = await api.project.createLabelset(name);
      const project = await api.project.activateLabelset(created.id);
      await applyProject(project, previous, true);
      set({ project, projectName: project.name, projectDir: project.project_dir, task: project.task });
      if (project.source_dataset_dir) {
        await useDatasetStore.getState().importFolder(project.source_dataset_dir, project.task);
      }
      return true;
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return false;
    } finally {
      set({ isProjectBusy: false });
    }
  },

  backupProject: async (destinationDir) => {
    if (get().isProjectBusy) return null;
    set({ isProjectBusy: true, projectError: null });
    try {
      await saveOpenEdits();
      return await api.project.backup(destinationDir);
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return null;
    } finally {
      set({ isProjectBusy: false });
    }
  },

  restoreProject: async (archivePath, targetDir) => {
    if (get().isProjectBusy) return false;
    set({ isProjectBusy: true, projectError: null });
    try {
      await saveOpenEdits();
      const project = await api.project.restore(archivePath, targetDir);
      await applyProject(project, get().project, true);
      set({ project, projectName: project.name, projectDir: project.project_dir,
        task: project.task, activeStep: 1 });
      await get().loadRecentProjects();
      if (project.source_dataset_dir) {
        await useDatasetStore.getState().importFolder(project.source_dataset_dir, project.task);
      }
      return true;
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return false;
    } finally {
      set({ isProjectBusy: false });
    }
  },
}));
