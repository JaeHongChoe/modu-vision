/**
 * src/renderer/stores/useFlowchartStore.ts
 * Multi-Model Chaining & Flowchart Pipeline State Management.
 */

import { create } from 'zustand';
import type { FlowExecutionChoice } from '../components/flowchart/flowExecution';
import type {
  FlowchartCrop,
  FlowchartExecutionResult,
  FlowchartPipeline,
  FlowNode,
  FlowNodeData,
  SelectedInspectionImage,
  VisionTask,
  FlowModelTask,
} from '../types';
import { api } from '../services/api';
import { flowDraft, type FlowDraftContext } from '../services/flowDraft';
import { useProjectStore } from './useProjectStore';
import { getFlowchartModelTask } from '../components/flowchart/flowchartStartup';

let flowchartGeneration = 0;
let flowchartSaveGeneration = 0;
let flowchartRunInputRevision = 0;
let flowchartRunSequence = 0;
const HISTORY_LIMIT = 50;

interface FlowchartState {
  executionChoiceOverride: FlowExecutionChoice | null;
  setExecutionChoice: (choice: FlowExecutionChoice | null) => void;
  pipeline: FlowchartPipeline | null;
  executionResult: FlowchartExecutionResult | null;
  lastRunSource: { kind: 'draft' | 'saved'; versionId: string | null } | null;
  isLoading: boolean;
  isSaving: boolean;
  isRunning: boolean;
  activeRunningNodeId: string | null;
  selectedNodeId: string | null;
  pipelineDirty: boolean;
  pipelineIsDraft: boolean;
  saveMessage: string | null;
  errorMessage: string | null;
  modelContextInvalidated: boolean;
  contextRevision: number;
  cleanPipeline: FlowchartPipeline | null;
  historyPast: FlowchartPipeline[];
  historyFuture: FlowchartPipeline[];
  historyGroupStart: FlowchartPipeline | null;
  canUndo: boolean;
  canRedo: boolean;

  // Real Image Selection
  selectedImage: SelectedInspectionImage | null;
  isImagePickerOpen: boolean;

  // Intermediate Crop Inspection Modal
  inspectedCrop: FlowchartCrop | null;

  // Actions
  loadPipeline: (force?: boolean, inspectionTask?: VisionTask, sourceDatasetPath?: string) => Promise<FlowchartPipeline | null>;
  loadPipelineVersion: (versionId: string, sourceDatasetPath: string) => Promise<FlowchartPipeline | null>;
  loadSingleSegmentationTemplate: (jobId?: string, inspectionTask?: VisionTask) => Promise<void>;
  loadDetectorRoiTemplate: (inspectionTask: Exclude<VisionTask, 'detection'>) => Promise<void>;
  savePipeline: (customPipeline?: FlowchartPipeline, recipeTask?: FlowModelTask | 'mixed', sourceDatasetPath?: string) => Promise<void>;
  saveDraft: () => Promise<boolean>;
  loadDraft: () => Promise<FlowchartPipeline | null>;
  runPipeline: (customImagePath?: string, customImageId?: string,
    source?: { savedVersionId: string | null; executionTarget?: 'local' | 'selected_compute' | 'model_compute';
      device?: 'cpu' | 'mps' | 'cuda'; computeProfileId?: string }) => Promise<boolean>;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, patch: Partial<FlowNodeData>) => void;
  addNode: (node: FlowNode) => void;
  replacePipeline: (pipeline: FlowchartPipeline) => void;
  moveNode: (id: string, position: { x: number; y: number }) => void;
  beginHistoryGroup: () => void;
  endHistoryGroup: () => void;
  undo: () => void;
  redo: () => void;
  setSelectedImage: (image: SelectedInspectionImage | null) => void;
  setImagePickerOpen: (open: boolean) => void;
  setInspectedCrop: (crop: FlowchartCrop | null) => void;
  clearError: () => void;
  resetExecution: () => void;
  invalidateForDataChange: () => void;
}

function editedHistory(state: FlowchartState): Pick<FlowchartState, 'historyPast' | 'historyFuture' | 'canUndo' | 'canRedo'> {
  const historyPast = state.historyGroupStart || !state.pipeline
    ? state.historyPast : [...state.historyPast, state.pipeline].slice(-HISTORY_LIMIT);
  return { historyPast, historyFuture: [], canUndo: historyPast.length > 0, canRedo: false };
}

function draftContext(): FlowDraftContext | null {
  const project = useProjectStore.getState().project;
  return project ? { project_id: project.id, source_dataset_path: project.source_dataset_dir || null,
    labelset_id: project.active_labelset_id || 'default' } : null;
}

function sameContext(expected: FlowDraftContext | null, actual: FlowDraftContext | null): boolean {
  return JSON.stringify(expected) === JSON.stringify(actual);
}

export const useFlowchartStore = create<FlowchartState>((set, get) => ({
  executionChoiceOverride: null,
  setExecutionChoice: (choice) => { get().resetExecution(); set({ executionChoiceOverride: choice }); },
  pipeline: null,
  executionResult: null,
  lastRunSource: null,
  isLoading: false,
  isSaving: false,
  isRunning: false,
  activeRunningNodeId: null,
  selectedNodeId: null,
  pipelineDirty: false,
  pipelineIsDraft: false,
  saveMessage: null,
  errorMessage: null,
  modelContextInvalidated: false,
  contextRevision: 0,
  cleanPipeline: null,
  historyPast: [],
  historyFuture: [],
  historyGroupStart: null,
  canUndo: false,
  canRedo: false,

  selectedImage: null,
  isImagePickerOpen: false,
  inspectedCrop: null,

  loadPipeline: async (force = false, inspectionTask, sourceDatasetPath) => {
    if (!force && get().pipeline) return get().pipeline;
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      let data: FlowchartPipeline;
      const context = draftContext();
      if (context && (!sourceDatasetPath || context.source_dataset_path === sourceDatasetPath)) {
        try {
          const draft = await flowDraft.get();
          if (generation !== flowchartGeneration || !sameContext(context, draftContext())) return null;
          if (!sameContext(context, draft.context)) throw new Error('저장 초안의 프로젝트 또는 라벨셋이 다릅니다.');
          data = draft.pipeline;
          set({ pipeline: data, cleanPipeline: data, pipelineIsDraft: !draft.active_version_id,
            historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
            pipelineDirty: false, selectedNodeId: null, executionResult: null, lastRunSource: null,
            inspectedCrop: null, isLoading: false });
          return data;
        } catch (error) {
          if ((error as { status?: number }).status !== 404) throw error;
        }
      }
      if (sourceDatasetPath) {
        try {
          data = await api.flowchart.getActivePipeline(sourceDatasetPath);
        } catch (error) {
          if ((error as { status?: number }).status !== 404) throw error;
          data = await api.flowchart.getPipeline(inspectionTask, sourceDatasetPath);
        }
      } else {
        data = await api.flowchart.getPipeline(inspectionTask, sourceDatasetPath);
      }
      if (generation !== flowchartGeneration) return null;
      set({
        pipeline: data,
        cleanPipeline: data,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        pipelineDirty: false,
        pipelineIsDraft: false,
        selectedNodeId: null,
        executionResult: null,
        inspectedCrop: null,
        isLoading: false,
      });
      return data;
    } catch (e: any) {
      if (generation !== flowchartGeneration) return null;
      console.error('Failed to load flowchart pipeline:', e);
      set({ isLoading: false, errorMessage: e?.message || '파이프라인 로드 실패' });
      return null;
    }
  },

  loadDraft: async () => {
    const context = draftContext();
    if (!context) return null;
    const generation = ++flowchartGeneration;
    try {
      const draft = await flowDraft.get();
      if (generation !== flowchartGeneration || !sameContext(context, draftContext())) return null;
      if (!sameContext(context, draft.context)) throw new Error('저장 초안의 프로젝트 또는 라벨셋이 다릅니다.');
      set({ pipeline: draft.pipeline, cleanPipeline: draft.pipeline, pipelineIsDraft: !draft.active_version_id,
        pipelineDirty: false, historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        executionResult: null, lastRunSource: null, inspectedCrop: null, selectedNodeId: null, errorMessage: null });
      return draft.pipeline;
    } catch (error) {
      if (generation === flowchartGeneration && (error as { status?: number }).status !== 404)
        set({ errorMessage: error instanceof Error ? error.message : '초안을 불러오지 못했습니다.' });
      return null;
    }
  },

  saveDraft: async () => {
    const target = get().pipeline;
    const context = draftContext();
    if (!target || !context || get().isSaving) return false;
    const generation = flowchartGeneration;
    const saveGeneration = ++flowchartSaveGeneration;
    set({ isSaving: true, errorMessage: null, saveMessage: null });
    try {
      const saved = await flowDraft.save(target, context);
      const readback = await flowDraft.get();
      if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration
          || get().pipeline !== target || !sameContext(context, draftContext())) return false;
      if (!sameContext(context, saved.context) || !sameContext(context, readback.context)
          || !/^[a-f0-9]{64}$/.test(saved.draft_sha256) || saved.draft_sha256 !== readback.draft_sha256)
        throw new Error('초안 저장 내용을 다시 확인하지 못했습니다.');
      set({ cleanPipeline: target, pipelineDirty: false, pipelineIsDraft: !saved.active_version_id,
        saveMessage: '편집 초안이 저장되었습니다.' });
      return true;
    } catch (error) {
      if (generation === flowchartGeneration && saveGeneration === flowchartSaveGeneration)
        set({ errorMessage: error instanceof Error ? error.message : '초안 저장 실패' });
      return false;
    } finally {
      if (saveGeneration === flowchartSaveGeneration) set({ isSaving: false });
    }
  },

  loadPipelineVersion: async (versionId, sourceDatasetPath) => {
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      const { pipeline: data } = await api.flowchart.activatePipelineVersion(versionId, sourceDatasetPath);
      if (generation !== flowchartGeneration) return null;
      flowchartRunInputRevision += 1;
      set({
        pipeline: data, cleanPipeline: data, pipelineIsDraft: false,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        pipelineDirty: false, selectedNodeId: null,
        executionResult: null, inspectedCrop: null, isLoading: false,
      });
      return data;
    } catch (error) {
      if (generation !== flowchartGeneration) return null;
      set({ isLoading: false, errorMessage: error instanceof Error ? error.message : '플로우 버전을 열 수 없습니다.' });
      return null;
    }
  },

  loadSingleSegmentationTemplate: async (jobId, inspectionTask) => {
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      const pipeline = inspectionTask === 'detection'
        ? await api.flowchart.getSingleDetectionTemplate(jobId)
        : await api.flowchart.getSingleSegmentationTemplate(jobId, inspectionTask);
      if (generation !== flowchartGeneration) return;
      set({
        pipeline,
        cleanPipeline: jobId ? null : pipeline, pipelineIsDraft: true,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        pipelineDirty: Boolean(jobId),
        selectedNodeId: inspectionTask === 'detection' ? 'node_crop' : 'node_inspect',
        executionResult: null,
        inspectedCrop: null,
        isLoading: false,
      });
    } catch (e: any) {
      if (generation !== flowchartGeneration) return;
      set({ isLoading: false, errorMessage: e?.message || '단일 분할 플로우를 불러오지 못했습니다.' });
    }
  },

  loadDetectorRoiTemplate: async (inspectionTask) => {
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      const pipeline = await api.flowchart.getDetectorRoiTemplate(inspectionTask);
      if (generation !== flowchartGeneration) return;
      set({
        pipeline,
        cleanPipeline: pipeline, pipelineIsDraft: true,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        pipelineDirty: false,
        selectedNodeId: 'node_crop',
        executionResult: null,
        inspectedCrop: null,
        isLoading: false,
      });
    } catch (e: any) {
      if (generation !== flowchartGeneration) return;
      set({ isLoading: false, errorMessage: e?.message || '검출 ROI 플로우를 불러오지 못했습니다.' });
    }
  },

  savePipeline: async (customPipeline, recipeTask, sourceDatasetPath) => {
    if (get().isSaving) return;
    const target = customPipeline || get().pipeline;
    if (!target) return;
    const generation = flowchartGeneration;
    const saveGeneration = ++flowchartSaveGeneration;
    set({ isSaving: true, errorMessage: null, saveMessage: null });
    try {
      await api.flowchart.savePipeline(target, recipeTask, sourceDatasetPath);
      if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration) return;
      const currentVersionSaved = get().pipeline === target;
      set({
        cleanPipeline: currentVersionSaved ? target : get().cleanPipeline,
        pipelineDirty: currentVersionSaved ? false : get().pipelineDirty,
        pipelineIsDraft: currentVersionSaved ? false : get().pipelineIsDraft,
        saveMessage: currentVersionSaved
          ? '파이프라인이 저장되었습니다.'
          : '이전 버전이 저장되었습니다. 새 변경 사항은 미저장입니다.',
      });
      setTimeout(() => set({ saveMessage: null }), 3000);
    } catch (e: any) {
      if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration) return;
      console.error('Failed to save flowchart pipeline:', e);
      set({ errorMessage: e?.recovery_incomplete
        ? '저장 복구가 완료되지 않았습니다. 현재 활성 플로우와 저장 버전을 다시 확인하세요.'
        : e?.message || '파이프라인 저장 실패' });
    } finally {
      if (saveGeneration === flowchartSaveGeneration) set({ isSaving: false });
    }
  },

  runPipeline: async (customImagePath, customImageId, source) => {
    const { pipeline, selectedImage } = get();
    const requestedProjectId = useProjectStore.getState().project?.id;
    const imagePath = customImagePath || selectedImage?.imagePath;
    const imageId = customImageId || selectedImage?.imageId;
    if (get().isRunning) return false;
    if (!pipeline) {
      set({ errorMessage: '검사 플로우를 먼저 불러오세요.' });
      return false;
    }
    if (!imagePath) {
      set({ errorMessage: '검사 이미지를 먼저 선택하세요.' });
      return false;
    }
    if (pipeline.nodes.some((node) =>
      getFlowchartModelTask(node) !== null && !node.data.model_job_id
    )) {
      set({ errorMessage: '검사를 실행하려면 검사 노드에 학습 모델 작업 ID를 지정해야 합니다.' });
      return false;
    }

    const savedVersionId = !get().pipelineDirty && !get().pipelineIsDraft && source?.savedVersionId ? source.savedVersionId : null;
    const generation = ++flowchartGeneration;
    const inputRevision = flowchartRunInputRevision;
    const runSequence = ++flowchartRunSequence;
    const discardStaleRun = () => {
      // Data invalidation can start a newer run. Never clear that newer run's state.
      if (runSequence === flowchartRunSequence) {
        set({
          isRunning: false,
          activeRunningNodeId: null,
          errorMessage: '검사 중 이미지나 플로우가 변경되어 이전 결과를 표시하지 않았습니다. 다시 실행하세요.',
        });
      }
    };
    set({ isRunning: true, errorMessage: null, executionResult: null, lastRunSource: null,
      inspectedCrop: null, activeRunningNodeId: null });

    try {
      const res = await api.flowchart.run({
        image_path: imagePath,
        image_id: imageId,
        pipeline: pipeline || undefined,
        ...(requestedProjectId ? { project_id: requestedProjectId } : {}),
        ...(source?.executionTarget ? { execution_target: source.executionTarget, device: source.device || 'cpu',
          ...(source.computeProfileId ? { compute_profile_id: source.computeProfileId } : {}) } : {}),
      });
      if (source?.executionTarget && source.executionTarget !== 'model_compute') {
        const requestedDevice = source.device || 'cpu';
        const actualDevice = res.execution_device || '';
        if (res.execution_target !== source.executionTarget
            || (source.executionTarget === 'selected_compute' && res.compute_profile_id !== source.computeProfileId)
            || (requestedDevice === 'cuda' ? !/^cuda(?::[0-9]+)?$/.test(actualDevice) : actualDevice !== requestedDevice)) {
          throw new Error('검사 결과의 실행 서버 또는 장치가 요청한 위치와 다릅니다.');
        }
      }
      if (generation !== flowchartGeneration || inputRevision !== flowchartRunInputRevision) {
        discardStaleRun();
        return false;
      }

      set({
        executionResult: res,
        lastRunSource: savedVersionId
          ? { kind: 'saved', versionId: savedVersionId }
          : { kind: 'draft', versionId: null },
        isRunning: false,
        activeRunningNodeId: null,
      });
      return true;
    } catch (e: any) {
      if (generation !== flowchartGeneration || inputRevision !== flowchartRunInputRevision) {
        discardStaleRun();
        return false;
      }
      console.error('Failed to run flowchart pipeline:', e);
      set({
        isRunning: false,
        activeRunningNodeId: null,
        errorMessage: e?.message || '파이프라인 실행 중 오류가 발생했습니다.',
      });
      return false;
    }
  },

  selectNode: (id) => set({ selectedNodeId: id }),

  updateNodeData: (id, patch) => {
    const state = get();
    const current = state.pipeline;
    if (!current || !current.nodes.some((node) => node.id === id)) return;
    const nextNodes = current.nodes.map((node) => {
      if (node.id === id) {
        return {
          ...node,
          data: {
            ...node.data,
            ...patch,
          },
        };
      }
      return node;
    });
    flowchartRunInputRevision += 1;
    set({
      ...editedHistory(state),
      pipeline: {
        ...current,
        nodes: nextNodes,
      },
      pipelineDirty: true,
      executionResult: null,
      inspectedCrop: null,
    });
  },

  addNode: (newNode) => {
    const state = get();
    const current = state.pipeline;
    if (!current) return;
    flowchartRunInputRevision += 1;
    set({
      ...editedHistory(state),
      pipeline: {
        ...current,
        nodes: [...current.nodes, newNode],
      },
      pipelineDirty: true,
      executionResult: null,
      inspectedCrop: null,
    });
  },

  replacePipeline: (pipeline) => {
    const state = get();
    if (!state.pipeline || state.pipeline === pipeline) return;
    flowchartRunInputRevision += 1;
    const selectedNodeId = state.selectedNodeId;
    set({
      ...editedHistory(state),
      pipeline,
      pipelineDirty: true,
      selectedNodeId: selectedNodeId && pipeline.nodes.some((node) => node.id === selectedNodeId) ? selectedNodeId : null,
      executionResult: null,
      inspectedCrop: null,
      errorMessage: null,
      saveMessage: null,
    });
  },

  moveNode: (id, position) => {
    const state = get();
    const current = state.pipeline;
    if (!current || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
    const clamped = { x: Math.max(0, position.x), y: Math.max(0, position.y) };
    if (!current.nodes.some((node) => node.id === id &&
      (node.position.x !== clamped.x || node.position.y !== clamped.y))) return;
    const nextNodes = current.nodes.map((node) => node.id === id
      ? { ...node, position: clamped }
      : node);
    flowchartRunInputRevision += 1;
    set({
      ...editedHistory(state),
      pipeline: { ...current, nodes: nextNodes },
      pipelineDirty: true,
      executionResult: null,
      inspectedCrop: null,
      saveMessage: null,
    });
  },

  beginHistoryGroup: () => {
    const state = get();
    if (state.pipeline && !state.historyGroupStart) set({ historyGroupStart: state.pipeline });
  },
  endHistoryGroup: () => {
    const state = get();
    const start = state.historyGroupStart;
    if (!start) return;
    if (state.pipeline === start) { set({ historyGroupStart: null }); return; }
    const historyPast = [...state.historyPast, start].slice(-HISTORY_LIMIT);
    set({ historyPast, historyGroupStart: null, canUndo: true });
  },
  undo: () => {
    get().endHistoryGroup();
    const state = get();
    if (!state.pipeline || !state.historyPast.length) return;
    const previous = state.historyPast[state.historyPast.length - 1];
    const historyPast = state.historyPast.slice(0, -1);
    const historyFuture = [...state.historyFuture, state.pipeline].slice(-HISTORY_LIMIT);
    flowchartRunInputRevision += 1;
    set({
      pipeline: previous, pipelineDirty: previous !== state.cleanPipeline,
      historyPast, historyFuture, canUndo: historyPast.length > 0, canRedo: true,
      selectedNodeId: state.selectedNodeId && previous.nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId : null,
      executionResult: null, lastRunSource: null, inspectedCrop: null,
      errorMessage: null, saveMessage: null,
    });
  },
  redo: () => {
    get().endHistoryGroup();
    const state = get();
    if (!state.pipeline || !state.historyFuture.length) return;
    const next = state.historyFuture[state.historyFuture.length - 1];
    const historyFuture = state.historyFuture.slice(0, -1);
    const historyPast = [...state.historyPast, state.pipeline].slice(-HISTORY_LIMIT);
    flowchartRunInputRevision += 1;
    set({
      pipeline: next, pipelineDirty: next !== state.cleanPipeline,
      historyPast, historyFuture, canUndo: true, canRedo: historyFuture.length > 0,
      selectedNodeId: state.selectedNodeId && next.nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId : null,
      executionResult: null, lastRunSource: null, inspectedCrop: null,
      errorMessage: null, saveMessage: null,
    });
  },

  setSelectedImage: (image) => {
    flowchartRunInputRevision += 1;
    set({ selectedImage: image, executionResult: null, inspectedCrop: null });
  },
  setImagePickerOpen: (isImagePickerOpen) => set({ isImagePickerOpen }),
  setInspectedCrop: (inspectedCrop) => set({ inspectedCrop }),
  clearError: () => set({ errorMessage: null }),
  resetExecution: () => {
    flowchartRunInputRevision += 1;
    set({ executionResult: null, inspectedCrop: null, activeRunningNodeId: null });
  },

  invalidateForDataChange: () => {
    flowchartGeneration += 1;
    flowchartSaveGeneration += 1;
    flowchartRunInputRevision += 1;
    flowchartRunSequence += 1;
    set({
      pipeline: null,
      cleanPipeline: null,
      historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
      pipelineDirty: false,
      pipelineIsDraft: false,
      modelContextInvalidated: true,
      contextRevision: get().contextRevision + 1,
      executionResult: null, lastRunSource: null, isLoading: false, isSaving: false, isRunning: false,
      activeRunningNodeId: null, selectedNodeId: null,
      selectedImage: null, isImagePickerOpen: false, inspectedCrop: null,
      saveMessage: null, errorMessage: null,
    });
  },
}));
