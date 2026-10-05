/** Wizard state and durable project workspace switching. */

import { create } from 'zustand';
import type { BackendStatus } from '../../types/electron';
import type { ErrorCatalogItem, Language, VisionTask } from '../types';
import { api, getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration, setCachedPort, type ProjectBackupResult, type ProjectConfig, type RecentProject } from '../services/api';
import { useAnnotationStore } from './useAnnotationStore';
import { datasetWorkflow, workflowError } from '../services/datasetWorkflow';
import { useDatasetStore } from './useDatasetStore';
import { useFlowchartStore } from './useFlowchartStore';
import { useTrainingStore } from './useTrainingStore';
import { useInspectionRunStore } from './useInspectionRunStore';
import { useModelAssistRunStore } from './useModelAssistRunStore';
import { projectViewScope, readProjectStep, rememberProjectStep } from './projectViewState';

type WizardStep = 1 | 2 | 3 | 4 | 5 | 6;

export type TaskChangeOutcome =
  | { ok: true; task: VisionTask; changed: boolean }
  | { ok: false; task: VisionTask; error: string; applied: boolean };

// A project sync requested while a task change holds the lock runs once it settles.
let taskChangeInFlight = false;
let syncAfterTaskChange = false;
let deferredSync = false;
/** Stage choices made by the user (setStep), counted so a sync can tell a choice made while it was in flight. */
let stepChoices = 0;

// A failed task change stays reported only in its own project and server scope: a refusal
// through the sync deferred behind it, a change already applied on the server until the
// import for the active task is verified again or a later change succeeds.
type TaskFailure = { scope: string; message: string; applied: boolean };
let taskFailure: TaskFailure | null = null;

function importVerified(task: VisionTask): boolean {
  const dataset = useDatasetStore.getState();
  const key = `${dataset.folderPath}\0${task}`;
  return Boolean(dataset.hasSelectedFolder && !dataset.isLoading && !dataset.importError
    && dataset.datasetKey === key && dataset.lastImportedKey === key);
}

/** The failure reported for the current scope; a failure of another project or server is dropped. */
function currentTaskFailure(state: { project: ProjectConfig | null; projectDir: string | null }): TaskFailure | null {
  if (taskFailure && taskFailure.scope !== taskChangeScope(state)) taskFailure = null;
  return taskFailure;
}

/** An unverified applied failure of this scope stays visible; see settledProjectError for verification. */
function carriedProjectError(state: { project: ProjectConfig | null; projectDir: string | null }): string | null {
  const failure = currentTaskFailure(state);
  return failure?.applied ? failure.message : null;
}

/** After a project action succeeds: a verified import for the active task clears the failure; the rest stays. */
function settledProjectError(state: { project: ProjectConfig | null; projectDir: string | null }, task: VisionTask): string | null {
  if (currentTaskFailure(state)?.applied && importVerified(task)) taskFailure = null;
  return carriedProjectError(state);
}

/** The accepted authority and project a task change belongs to; the task itself changes. */
export function taskChangeScope(state: { project: ProjectConfig | null; projectDir: string | null }): string {
  return [getApiPersistenceIdentity(), getProjectContextGeneration(), JSON.stringify(getProjectContext()),
    state.project?.id ?? '', state.project?.project_dir ?? '', state.projectDir ?? ''].join('\0');
}

/** Edits made while a selection request was in flight still belong to the visible project; never switch over them. */
function refuseEditsMadeDuringSelection(): void {
  if (useAnnotationStore.getState().isDirty || useFlowchartStore.getState().pipelineDirty) {
    throw new Error('프로젝트를 전환하는 동안 새 편집이 생겼습니다. 현재 프로젝트에 먼저 저장한 뒤 다시 시도하세요.');
  }
}

/**
 * Accepts a selected project after the store's guards: the request context (HTTP and telemetry) and the
 * visible project change in one synchronous step, before any dataset, annotation or flow read for it. A
 * refused or stale acceptance binds nothing, so the UI and the API both stay on the previous project.
 */
function acceptSelectedProject(project: ProjectConfig, view: { activeStep?: WizardStep } = {}): void {
  // acceptContext validates the selection, binds its request context, runs this synchronous setter and only
  // then notifies telemetry; a stale selection or a failing setter throws and leaves the previous binding.
  api.project.acceptContext(project, () => useProjectStore.setState({
    project, projectName: project.name, projectDir: project.project_dir, task: project.task, ...view,
  }));
}

function viewStorage(): Storage | undefined {
  try { return typeof localStorage === 'undefined' ? undefined : localStorage; }
  catch { return undefined; }
}

// Language is a device preference, not a project mutation or permission grant.
const LANGUAGE_KEY = 'modu:user-language:v1';
function readUserLanguage(storage?: Pick<Storage, 'getItem'>): Language {
  try { return storage?.getItem(LANGUAGE_KEY) === 'en' ? 'en' : 'ko'; }
  catch { return 'ko'; }
}
function rememberUserLanguage(storage: Pick<Storage, 'setItem'> | undefined, language: Language): void {
  try { storage?.setItem(LANGUAGE_KEY, language); }
  catch { /* Keep the choice for this session when persistence is unavailable. */ }
}

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
  openImageForLabeling: (imageId: string, filePath: string, expected?:{imageSha256:string;revision:number;isCurrent?:()=>boolean}) => Promise<boolean>;
  /** Changes the project task; the outcome always names the task that is actually active. */
  updateTask: (task: VisionTask) => Promise<TaskChangeOutcome>;
  setTask: (task: VisionTask) => Promise<TaskChangeOutcome>;
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

export async function saveOpenEdits(): Promise<void> {
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
      // The daemon reopened the project being edited; bind its requests to that same project.
      api.project.acceptContext(reopened);
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
    // The saved source is resolved (a symlink, or a mapped drive's network path on Windows): the loaded dataset is the
    // same folder, so follow the saved spelling, as an import does, or training would keep waiting for the source.
    const canonical = refreshed.source_dataset_dir;
    const picked = dataset.datasetKey;
    if (canonical && canonical !== dataset.folderPath) {
      const adopted = `${canonical}\0${currentProject.task}`;
      // Only while the same pick is loaded and settled (a re-import of it in progress keeps its own state).
      useDatasetStore.setState((state) => state.datasetKey !== picked || state.isLoading ? {} : {
        folderPath: canonical,
        datasetKey: adopted,
        lastImportedKey: state.lastImportedKey === picked ? adopted : state.lastImportedKey,
        staleDatasetKeys: state.staleDatasetKeys.includes(picked) ? [...new Set([...state.staleDatasetKeys, adopted])] : state.staleDatasetKeys,
      });
    }
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
    // Workspace navigation preserves editable graphs independently of strict
    // executable versions. An unfinished model connection is valid draft work.
    const draftSaved = await flow.saveDraft();
    const saved = useFlowchartStore.getState();
    if (!draftSaved || saved.pipeline !== pipeline || saved.pipelineDirty || saved.errorMessage || saved.isSaving
        || useProjectStore.getState().project?.id !== project.id
        || useDatasetStore.getState().folderPath !== folderPath) {
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
  language: readUserLanguage(viewStorage()),
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
    const apiIdentity = getApiPersistenceIdentity();
    const startedScope = projectViewScope(get().project, apiIdentity);
    if (get().activeStep === 2 && useAnnotationStore.getState().isDirty) {
      const saved = await useAnnotationStore.getState().saveAnnotations();
      if (!saved || useAnnotationStore.getState().isDirty) return;
    }
    if (projectViewScope(get().project, getApiPersistenceIdentity()) !== startedScope) return;
    set({ activeStep: step });
    stepChoices += 1;
    rememberProjectStep(viewStorage(), get().project, apiIdentity, step);
  },

  openImageForLabeling: async (imageId, filePath, expected) => {
    const startedProject = get().projectDir;
    const startedAuthority = taskChangeScope(get());
    const startedTask = get().task;
    const startedLabelset = get().project?.active_labelset_id || 'default';
    const dataset = useDatasetStore.getState();
    const startedSource = dataset.folderPath;
    const sameContext = () => taskChangeScope(get()) === startedAuthority && (!expected?.isCurrent || expected.isCurrent())
      && get().projectDir === startedProject && get().task === startedTask
      && (get().project?.active_labelset_id || 'default') === startedLabelset
      && useDatasetStore.getState().folderPath === startedSource;
    let images = dataset.images;
    let index = images.findIndex(image => image.image_id === imageId && image.file_path === filePath);
    try {
      if(expected){
        if(startedSource!==get().project?.source_dataset_dir)throw new Error('현재 데이터 출처와 프로젝트의 라벨 출처가 다릅니다.');
        const metadata=await datasetWorkflow.image(filePath);
        if(!sameContext())return false;
        if(metadata.file_path!==filePath||metadata.content_hash!==expected.imageSha256||metadata.revision!==expected.revision)
          throw new Error('현재 원본·라벨 수정 버전이 편집 진입 확인 후 바뀌었습니다. 다시 확인하세요.');
      }
      if (index < 0) {
        const metadata = await datasetWorkflow.image(filePath);
        if (!sameContext() || metadata.file_path !== filePath) return false;
        const fileName = filePath.split(/[\\/]/).pop() || imageId;
        const exactId = fileName.replace(/\.[^.]+$/, '');
        if (exactId !== imageId) throw new Error('결과의 이미지 이름과 원본 경로가 일치하지 않습니다.');
        images = [...images, { image_id: exactId, file_name: fileName, file_path: metadata.file_path,
          width: metadata.width, height: metadata.height, split: 'train',
          thumbnail_url: `/api/dataset/thumbnail/${encodeURIComponent(exactId)}?file_path=${encodeURIComponent(filePath)}&size=128` }];
        index = images.length - 1;
      }
      if (!sameContext()) return false;
      // Mark an exact result selection with the image, before its annotation read
      // yields to the mounted labeling view's gallery synchronization.
      const externalSelectionPath = dataset.images.some(image => image.file_path === filePath) ? null : filePath;
      const opened = await useAnnotationStore.getState().setImages(images, index, externalSelectionPath);
      if (!opened || !sameContext()) return false;
      if(expected){
        const state=useAnnotationStore.getState();
        if(state.annotationLoadStatus!=='ready'||state.metadata?.file_path!==filePath
          ||state.metadata.content_hash!==expected.imageSha256||state.metadata.revision!==expected.revision)
          throw new Error('현재 라벨 조회가 확인한 원본·수정 버전과 다릅니다. 다시 확인하세요.');
      }
      await get().setStep(2);
      return sameContext() && get().activeStep === 2 && useAnnotationStore.getState().currentImage?.file_path === filePath;
    } catch (error) {
      if (sameContext()) {
        set({ projectError: workflowError(error) });
        if(expected&&useAnnotationStore.getState().currentImage?.file_path===filePath)
          useAnnotationStore.setState({annotationLoadStatus:'error',annotationLoadError:workflowError(error)});
      }
      return false;
    }
  },

  updateTask: async (task) => {
    if (task === get().task) return { ok: true, task, changed: false };
    // Like other project changes this holds the project lock: a second task
    // change or a project switch is refused until this one settles.
    if (get().isProjectBusy) {
      return { ok: false, task: get().task, error: '다른 프로젝트 작업이 진행 중입니다. 완료 후 모델 종류를 바꾸세요.', applied: false };
    }
    const origin = get().project;
    const originScope = taskChangeScope(get());
    const sameScope = () => taskChangeScope(get()) === originScope;
    // A reply for a project or server that is no longer open never touches the current one.
    const leftScope = (applied: boolean): TaskChangeOutcome => {
      // Current recovery guidance belongs to the accepted namespace; an old reply cannot clear it.
      // A different project also drops a known retained failure from this origin,
      // without replacing a new error that arrived while the request was pending.
      const changedProject = get().project?.id !== origin?.id || get().projectDir !== origin?.project_dir;
      if (changedProject && taskFailure?.scope === originScope && get().projectError === taskFailure.message) {
        taskFailure = null;
        set({ projectError: null });
      }
      return { ok: false, task: get().task, applied,
        error: '모델 종류를 바꾸는 동안 프로젝트·계정·연결이 바뀌어 결과를 적용하지 않았습니다. 이전 프로젝트를 다시 열어 모델 종류를 확인하세요.' };
    };
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
    taskChangeInFlight = true;
    let applied = false;
    try {
      await saveOpenEdits();
      if (!sameScope()) return leftScope(false);
      const project = await api.project.update({ task });
      applied = true;
      if (!sameScope() || (origin && (project.id !== origin.id || project.project_dir !== origin.project_dir))) return leftScope(true);
      set({ task: project.task, project, projectName: project.name });
      useAnnotationStore.getState().setTask(project.task);
      const dataset = useDatasetStore.getState();
      if (dataset.hasSelectedFolder) {
        await dataset.importFolder(dataset.folderPath, project.task);
      }
      if (!sameScope()) return leftScope(true);
      taskFailure = null;
      set({ projectError: null });
      return { ok: true, task: project.task, changed: true };
    } catch (error) {
      if (!sameScope()) return leftScope(applied);
      const message = projectErrorMessage(error);
      set({ projectError: message });
      // A retry that changed nothing is reported now, but an earlier unverified applied change
      // stays the recorded failure, so later syncs keep showing what is still unresolved.
      const earlierApplied = Boolean(taskFailure?.applied && taskFailure.scope === originScope);
      if (applied || !earlierApplied) taskFailure = { scope: originScope, message, applied };
      // A refused change keeps the previous task; a later failure keeps the new one.
      return { ok: false, task: get().task, error: message, applied };
    } finally {
      taskChangeInFlight = false;
      set({ isProjectBusy: false });
      if (syncAfterTaskChange) {
        syncAfterTaskChange = false;
        deferredSync = true;
        void get().syncCurrentProject();
      }
    }
  },
  setTask: (task) => get().updateTask(task),

  setLanguage: (language) => { rememberUserLanguage(viewStorage(), language); set({ language }); },
  setBackendStatus: (status) => {
    setCachedPort(status.port);
    set({ backendStatus: status, backendPort: status.port });
  },
  showError: (activeError) => set({ activeError }),
  clearError: () => set({ activeError: null }),
  clearProjectError: () => set({ projectError: settledProjectError(get(), get().task) }),

  syncCurrentProject: async () => {
    // Consumed first, so the flag never carries over to a later, unrelated sync.
    const deferred = deferredSync;
    deferredSync = false;
    if (get().isProjectBusy) {
      // A task change holds the lock only for one request; do not drop a server-side switch.
      if (taskChangeInFlight) syncAfterTaskChange = true;
      return;
    }
    settledProjectError(get(), get().task);
    const failure = currentTaskFailure(get());
    const kept = failure && (failure.applied || deferred) ? failure.message : null;
    set({ isProjectBusy: true, projectError: kept });
    const choicesAtStart = stepChoices;
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
      const identity = getApiPersistenceIdentity();
      // A stage the user chose while the app's first sync was in flight is kept (and remembered for this project)
      // instead of the stage remembered from an earlier session.
      const chosenMeanwhile = !previous && stepChoices !== choicesAtStart;
      acceptSelectedProject(project, { activeStep: chosenMeanwhile || (previous && projectViewScope(previous, identity) === projectViewScope(project, identity))
        ? get().activeStep : readProjectStep(viewStorage(), project, identity) });
      if (chosenMeanwhile) rememberProjectStep(viewStorage(), project, identity, get().activeStep);
      await applyProject(project, previous, true);
      if (project.source_dataset_dir && get().activeStep !== 1) {
        // Later stages also need the daemon's restored source and effective split;
        // DatasetStudio is not mounted when returning directly to those stages.
        await useDatasetStore.getState().ensureImported(project.task);
      }
      // A refusal is shown through the sync deferred behind it; another open project drops the
      // failure; otherwise only a verified import for the active task clears an applied failure.
      const refusal = failure && !failure.applied ? failure : null;
      if (refusal) taskFailure = null;
      set({ projectError: changed ? carriedProjectError(get())
        : refusal && deferred ? refusal.message : settledProjectError(get(), project.task) });
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
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
    try {
      await saveOpenEdits();
      const project = await api.project.create(data);
      const previous = get().project;
      refuseEditsMadeDuringSelection();
      acceptSelectedProject(project, { activeStep: 1 });
      await applyProject(project, previous);
      set({ projectError: settledProjectError(get(), project.task) });
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
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
    try {
      await saveOpenEdits();
      const project = await api.project.open(projectDir);
      const previous = get().project;
      refuseEditsMadeDuringSelection();
      acceptSelectedProject(project, { activeStep: readProjectStep(viewStorage(), project, getApiPersistenceIdentity()) });
      await applyProject(project, previous);
      if (project.source_dataset_dir && get().activeStep !== 1) {
        await useDatasetStore.getState().ensureImported(project.task);
      }
      // Reopening the same project does not hide a change applied on the server until its import is verified.
      set({ projectError: settledProjectError(get(), project.task) });
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
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
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
      set({ projectError: settledProjectError(get(), project.task) });
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
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
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
      set({ projectError: settledProjectError(get(), project.task) });
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
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
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
    set({ isProjectBusy: true, projectError: settledProjectError(get(), get().task) });
    try {
      await saveOpenEdits();
      const project = await api.project.restore(archivePath, targetDir);
      const previous = get().project;
      refuseEditsMadeDuringSelection();
      acceptSelectedProject(project, { activeStep: 1 });
      await applyProject(project, previous, true);
      await get().loadRecentProjects();
      if (project.source_dataset_dir) {
        await useDatasetStore.getState().importFolder(project.source_dataset_dir, project.task);
      }
      set({ projectError: settledProjectError(get(), project.task) });
      return true;
    } catch (error) {
      set({ projectError: projectErrorMessage(error) });
      return false;
    } finally {
      set({ isProjectBusy: false });
    }
  },
}));
