/**
 * src/renderer/stores/useDatasetStore.ts
 * Dataset management: folder importing, synthetic generation, train/val split, and thumbnails.
 */

import { create } from 'zustand';
import type { ClassSplitCounts, ImageMeta, VisionTask } from '../types';
import { api, getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration, resolveApiUrl, type DatasetQuickValidation } from '../services/api';
import type {DatasetPartition} from '../services/projectPreferences';
import { useTrainingStore } from './useTrainingStore';
import { useEvaluationStore } from './useEvaluationStore';
import { useFlowchartStore } from './useFlowchartStore';

interface DatasetState {
  folderPath: string;
  hasSelectedFolder: boolean;
  datasetKey: string | null;
  /** Accepted source reads belong to this project epoch and transport. */
  sourceAuthority: string | null;
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
  classCountUnit: 'images' | 'objects' | null;
  classSplitCounts: ClassSplitCounts | null;
  split: { train: number; val: number; test: number };
  images: ImageMeta[];
  totalImagesCount: number;
  page: number;
  pageSize: number;
  activeSplitFilter: DatasetPartition;
  activeClassFilter: string | null;
  activeLabelFilter: 'all' | 'labeled' | 'unlabeled';
  trainRatio: number;
  isLoading: boolean;
  isGenerating: boolean;
  isSplitting: boolean;
  showGeneratorModal: boolean;
  corruptedImages: any[];
  /** Undecodable images the import kept out of the inventory, splits and training (source files untouched). */
  excludedUndecodable: { excluded: string[]; restored: string[]; recorded: boolean } | null;
  /** Where the split counts come from: saved, source train/val/test folders, automatic (not saved) or stale. */
  splitState: 'saved' | 'source_folders' | 'automatic' | 'stale' | null;
  /** Detection/segmentation folder with no labels yet, and the layout this task expects. */
  labelsMissing: boolean;
  expectedLayout: string;
  /** What the quick inspection decoded; null when it was not requested or the backend predates the field. */
  quickValidation: DatasetQuickValidation | null;

  setFolderPath: (path: string) => void;
  setShowGeneratorModal: (show: boolean) => void;
  setSplitFilter: (split: DatasetPartition) => void;
  setClassFilter: (className: string | null) => void;
  setLabelFilter: (status: 'all' | 'labeled' | 'unlabeled') => void;
  setTrainRatio: (ratio: number) => void;
  importFolder: (folder: string, task: VisionTask, allowRecoveryOverride?: boolean, restoreSaved?: boolean, isCurrent?: () => boolean) => Promise<void>;
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
function datasetAuthorityParts() {
  const context = getProjectContext();
  return {generation: getProjectContextGeneration(), identity: getApiPersistenceIdentity(), base: resolveApiUrl('/'),
    context: context ? [context.workspace_id, context.project_id, context.actor_id, context.mode] : null};
}
export function readDatasetAuthority(): string {
  const value = datasetAuthorityParts();
  return JSON.stringify([value.generation, value.identity, value.base, value.context]);
}
const readAuthority = readDatasetAuthority;

function invalidateDownstream(allowSourceRecovery = false): void {
  useTrainingStore.getState().invalidateForDataChange();
  useEvaluationStore.getState().invalidateForDataChange(allowSourceRecovery);
  useFlowchartStore.getState().invalidateForDataChange();
}

function importErrorMessage(error: unknown, fallback = 'Dataset import failed'): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object') {
    // A layout mismatch names the layout the task expects in Korean; the loader's own message is the technical detail.
    const record = error as Record<string, unknown>;
    if (record.error_code === 'ERR_DATA_LAYOUT' && typeof record.message_ko === 'string') {
      return typeof record.details === 'string' && record.details ? `${record.message_ko} (기술 상세: ${record.details})` : record.message_ko;
    }
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
  sourceAuthority: null,
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
  classCountUnit: null,
  classSplitCounts: null,
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
  excludedUndecodable: null,
  splitState: null,
  labelsMissing: false,
  expectedLayout: '',
  quickValidation: null,

  setFolderPath: (folderPath) => {
    if (folderPath === get().folderPath) return;
    invalidateDownstream();
    latestImportRequest += 1;
    latestImageRequest += 1;
    latestSplitRequest += 1;
    set({
      folderPath, hasSelectedFolder: Boolean(folderPath), datasetKey: null, sourceAuthority: null, importError: null, sourceSaveError: null, splitError: null,
      splitSupported: null, splitUnavailableReason: null,
      totalImages: 0, sourceImages: 0, unlabeledImages: 0,
      classes: {}, classCountUnit: null, classSplitCounts: null, split: { train: 0, val: 0, test: 0 },
      images: [], totalImagesCount: 0, corruptedImages: [], excludedUndecodable: null, splitState: null, labelsMissing: false, expectedLayout: '', quickValidation: null,
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

  importFolder: async (folder, task, allowRecoveryOverride, restoreSaved = false, isCurrent) => {
    const authority = datasetAuthorityParts();
    const sameAuthority = () => {
      const current = datasetAuthorityParts();
      // The first local port lookup may learn its base without changing the
      // accepted epoch. Preserve that original unknown-base allowance only.
      return authority.generation === current.generation && authority.identity === current.identity
        && (!authority.base || authority.base === current.base)
        && (authority.context === current.context || Boolean(authority.context && current.context
          && authority.context.every((value, index) => value === current.context![index])));
    };
    if (isCurrent && !isCurrent()) return;
    const pickedFolder = folder;
    let key = importKey(folder, task);
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
    const ownsRequest = () => requestId === latestImportRequest && get().datasetKey === key && sameAuthority();
    const current = () => ownsRequest() && (!isCurrent || isCurrent());
    latestImageRequest += 1;
    latestSplitRequest += 1;
    set({
      folderPath: folder, hasSelectedFolder: true, datasetKey: key, sourceAuthority: null, importError: null, sourceSaveError: null, splitError: null,
      splitSupported: null, splitUnavailableReason: null,
      images: [], totalImagesCount: 0, totalImages: 0,
      sourceImages: 0, unlabeledImages: 0, classes: {}, classCountUnit: null, classSplitCounts: null, corruptedImages: [], excludedUndecodable: null, splitState: null, labelsMissing: false, expectedLayout: '', quickValidation: null,
      split: { train: 0, val: 0, test: 0 },
      activeSplitFilter: 'all', activeClassFilter: null, activeLabelFilter: 'all', page: 1, isLoading: true, isSplitting: false,
    });
    try {
      // Validate the picked source before persisting it, so a rejected import
      // leaves the project's previously saved source intact.
      const res = restoreSaved
        ? await api.dataset.currentSummary({ folder_path: folder, task })
        : await api.dataset.import({ folder_path: folder, task, validate_images: true });
      if (!current()) return;
      if (!restoreSaved) try {
        const project = await api.project.update({ source_dataset_dir: folder });
        if (!current()) return;
        // The project API is the authority for the canonical source path. Keep
        // the renderer's project state aligned before allowing a switch.
        const { useProjectStore } = await import('./useProjectStore');
        if (!current()) return;
        useProjectStore.setState((state) => state.project?.id === project.id
          ? { project, projectName: project.name, projectDir: project.project_dir }
          : {});
        // The backend saves the source resolved (a symlinked folder, or a mapped network drive on Windows, gets another
        // spelling). Follow its spelling, or training would wait for "the current source" forever.
        const canonical = project.source_dataset_dir;
        if (canonical && canonical !== folder) {
          folder = canonical;
          key = importKey(folder, task);
          // The previous import was recorded with the saved spelling (or, after a failed save, the picked one): decide
          // "the same folder again" and "another task in the same folder" with both, so a re-pick through another
          // spelling is the same folder.
          const sameFolder = previousFolder === folder || previousFolder === pickedFolder;
          const sameFolderTaskChange = sameFolder && previous !== key && previous !== importKey(pickedFolder, task);
          if (sameFolderTaskChange && previous) {
            set((state) => ({ staleDatasetKeys: [...new Set([...state.staleDatasetKeys, previous, key])] }));
          }
          if (allowRecoveryOverride === undefined) {
            // A stale mark under either spelling counts (labels changed while a save had failed are still changed).
            const stale = get().staleDatasetKeys.includes(key) || get().staleDatasetKeys.includes(importKey(pickedFolder, task));
            const allow = !stale && !sameFolderTaskChange && (!previous || !sameFolder);
            if (allow !== allowSourceRecovery) useEvaluationStore.setState({ allowLatestRecovery: allow });
          }
          set({ folderPath: folder, datasetKey: key });
        }
      } catch (error) {
        // The in-memory dataset remains usable, but a restart cannot restore its source.
        if (current()) {
          set({ sourceSaveError: `데이터는 불러왔지만 프로젝트에 경로를 저장하지 못했습니다: ${importErrorMessage(error)}` });
        }
      }
      if (!current()) return;
      set({
        sourceAuthority: readAuthority(),
        totalImages: res.total_images,
        sourceImages: res.source_images ?? res.total_images,
        unlabeledImages: res.unlabeled_images ?? 0,
        splitSupported: res.split_supported ?? null,
        splitUnavailableReason: res.split_unavailable_reason ?? null,
        classes: res.classes || {},
        classCountUnit: task === 'detection' ? 'objects' : 'images',
        split: {
          train: res.split.train,
          val: res.split.val,
          test: res.split.test || 0,
        },
        corruptedImages: res.corrupted_images || [],
        excludedUndecodable: res.excluded_undecodable ?? null,
        splitState: res.split_state ?? null,
        labelsMissing: Boolean(res.labels_missing),
        expectedLayout: res.expected_layout ?? '',
        quickValidation: res.validation ?? null,
        lastImportedKey: key,
        page: 1,
      });
      if (!current()) return;
      set({ isLoading: false });
      await get().loadImages(1);
    } catch (err) {
      if (!current()) return;
      set({ isLoading: false, importError: importErrorMessage(err) });
      throw err;
    } finally {
      if (ownsRequest() && get().isLoading) set({ isLoading: false });
    }
  },

  ensureImported: async (task) => {
    const { folderPath, hasSelectedFolder, datasetKey, sourceAuthority, isLoading, importError } = get();
    const authority = readAuthority();
    if (!folderPath || !hasSelectedFolder || isLoading || importError
      || (datasetKey === importKey(folderPath, task) && sourceAuthority === authority)) return;
    const {useProjectStore} = await import('./useProjectStore');
    // Another caller can start the same restore while the module is loading.
    if (get().isLoading || get().importError
      || (get().datasetKey === importKey(folderPath, task) && get().sourceAuthority === authority)) return;
    const accepted = () => {
      const state = useProjectStore.getState(), project = state.project, context = getProjectContext();
      return Boolean(project && context?.project_id === project.id && state.projectDir === project.project_dir
        && state.task === task && project.task === task && project.source_dataset_dir === folderPath
        && get().folderPath === folderPath && readAuthority() === authority);
    };
    if (!accepted()) return;
    // Reopening a saved source is a read. Choosing/replacing a source uses
    // importFolder directly and still requires project write permission.
    await get().importFolder(folderPath, task, undefined, true, accepted);
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
    const authority = readAuthority();
    if (get().sourceAuthority !== authority) return;
    const requestId = ++latestSplitRequest;
    const { folderPath, datasetKey } = get();
    const current = () => requestId === latestSplitRequest && get().datasetKey === datasetKey
      && get().sourceAuthority === authority && readAuthority() === authority;
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
      if (!current()) return;
      if (datasetKey) {
        set((state) => ({ staleDatasetKeys: [...new Set([...state.staleDatasetKeys, datasetKey])] }));
      }
      invalidateDownstream();
      set({
        isSplitting: false,
        trainRatio: ratio,
        split: res.split,
        splitState: 'saved',
        classSplitCounts: null,
      });
      // The folder may have changed since it was imported (images added or removed): read the counts again so the
      // screen shows the images the saved split actually covers.
      if (folderPath && task) {
        try {
          const fresh = await api.dataset.import({ folder_path: folderPath, task, validate_images: false });
          if (current()) set({ totalImages: fresh.total_images, sourceImages: fresh.source_images ?? fresh.total_images,
            classes: fresh.classes || {}, split: { train: fresh.split.train, val: fresh.split.val, test: fresh.split.test || 0 },
            splitState: fresh.split_state ?? 'saved' });
        } catch { /* the split itself is saved; the next import refreshes the counts */ }
      }
      if (!current()) return;
      await get().loadImages(1);
    } catch (err) {
      if (!current()) return;
      set({ isSplitting: false, splitError: importErrorMessage(err, 'Dataset split failed') });
      throw err;
    }
  },

  annotationsChanged: async () => {
    const authority = readAuthority();
    if (get().sourceAuthority !== authority) return;
    const currentKey = get().datasetKey;
    if (currentKey) {
      set((state) => ({ staleDatasetKeys: [...new Set([...state.staleDatasetKeys, currentKey])] }));
    }
    invalidateDownstream();
    const { folderPath, datasetKey } = get();
    if (!datasetKey) return;
    latestImageRequest += 1;
    // Until the refreshed manifest is read, never show the old split as trainable.
    set({ split: { train: 0, val: 0, test: 0 }, classSplitCounts: null, splitError: null });
    const task = datasetKey.slice(datasetKey.lastIndexOf('\0') + 1) as VisionTask;
    const requestId = ++latestImportRequest;
    const current = () => requestId === latestImportRequest && get().datasetKey === datasetKey
      && get().sourceAuthority === authority && readAuthority() === authority;
    try {
      const res = await api.dataset.import({ folder_path: folderPath, task, validate_images: false });
      if (!current()) return;
      set({
        totalImages: res.total_images,
        sourceImages: res.source_images ?? res.total_images,
        unlabeledImages: res.unlabeled_images ?? 0,
        splitSupported: res.split_supported ?? null,
        splitUnavailableReason: res.split_unavailable_reason ?? null,
        classes: res.classes || {},
        classCountUnit: task === 'detection' ? 'objects' : 'images',
        split: { train: res.split.train, val: res.split.val, test: res.split.test || 0 },
        splitState: res.split_state ?? get().splitState,
        labelsMissing: Boolean(res.labels_missing),
        importError: null,
        page: 1,
      });
      await get().loadImages(1);
    } catch (error) {
      if (current()) {
        set({ importError: importErrorMessage(error) });
      }
    }
  },

  loadImages: async (pageArg) => {
    const authority = readAuthority();
    if (get().isLoading || get().importError || get().sourceAuthority !== authority) return;
    const requestId = ++latestImageRequest;
    const { folderPath, datasetKey, pageSize, activeSplitFilter, activeClassFilter, activeLabelFilter } = get();
    const task = datasetKey?.slice(datasetKey.lastIndexOf('\0') + 1) as VisionTask | undefined;
    const p = pageArg || get().page;
    const offset = (p - 1) * pageSize;
    const current = () => requestId === latestImageRequest && get().folderPath === folderPath && get().datasetKey === datasetKey
      && get().sourceAuthority === authority && readAuthority() === authority;
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
      if (!current()) return;
      set({
        images: res.items || [],
        totalImagesCount: res.total || 0,
        ...(res.class_split_counts != null ? { classSplitCounts: res.class_split_counts } : {}),
        page: p,
      });
      // A split/label refresh may occur while the gallery is filtered. Obtain
      // its full summary independently so a page can never stand in for a class.
      if (get().classSplitCounts === null && (task === 'classification' || task === 'anomaly')
        && (activeSplitFilter !== 'all' || activeClassFilter || activeLabelFilter !== 'all')) {
        const summary = await api.dataset.getImages({ folder_path: folderPath, task, limit: 1, offset: 0 });
        if (!current()) return;
        if (summary.class_split_counts != null) set({ classSplitCounts: summary.class_split_counts });
      }
    } catch {
      // non-blocking fallback
    }
  },
}));
