/**
 * src/renderer/stores/useFlowchartStore.ts
 * Multi-Model Chaining & Flowchart Pipeline State Management (Workflow Flowchart).
 */

import { create } from 'zustand';
import type {
  FlowchartCrop,
  FlowchartExecutionResult,
  FlowchartPipeline,
  FlowNode,
  FlowNodeData,
  SelectedInspectionImage,
} from '../types';
import { api } from '../services/api';
import { useEvaluationStore } from './useEvaluationStore';

let flowchartGeneration = 0;

function onlyVerifiedModelIds(
  pipeline: FlowchartPipeline,
  verifiedJobId: string | null,
): { pipeline: FlowchartPipeline; removedModelId: boolean } {
  let removedModelId = false;
  const nodes = pipeline.nodes.map((node) => {
    if (!node.data.model_job_id || node.data.model_job_id === verifiedJobId) return node;
    removedModelId = true;
    return { ...node, data: { ...node.data, model_job_id: undefined } };
  });
  return { pipeline: { ...pipeline, nodes }, removedModelId };
}

interface FlowchartState {
  pipeline: FlowchartPipeline | null;
  executionResult: FlowchartExecutionResult | null;
  isLoading: boolean;
  isRunning: boolean;
  activeRunningNodeId: string | null;
  selectedNodeId: string | null;
  pipelineDirty: boolean;
  saveMessage: string | null;
  errorMessage: string | null;
  modelContextInvalidated: boolean;
  contextRevision: number;

  // Real Image Selection
  selectedImage: SelectedInspectionImage | null;
  isImagePickerOpen: boolean;

  // Intermediate Crop Inspection Modal
  inspectedCrop: FlowchartCrop | null;

  // Actions
  loadPipeline: (force?: boolean) => Promise<FlowchartPipeline | null>;
  loadSingleSegmentationTemplate: (jobId?: string) => Promise<void>;
  savePipeline: (customPipeline?: FlowchartPipeline) => Promise<void>;
  runPipeline: (customImagePath?: string, customImageId?: string) => Promise<boolean>;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, patch: Partial<FlowNodeData>) => void;
  addNode: (node: FlowNode) => void;
  setSelectedImage: (image: SelectedInspectionImage | null) => void;
  setImagePickerOpen: (open: boolean) => void;
  setInspectedCrop: (crop: FlowchartCrop | null) => void;
  clearError: () => void;
  resetExecution: () => void;
  invalidateForDataChange: () => void;
}

export const useFlowchartStore = create<FlowchartState>((set, get) => ({
  pipeline: null,
  executionResult: null,
  isLoading: false,
  isRunning: false,
  activeRunningNodeId: null,
  selectedNodeId: null,
  pipelineDirty: false,
  saveMessage: null,
  errorMessage: null,
  modelContextInvalidated: false,
  contextRevision: 0,

  selectedImage: null,
  isImagePickerOpen: false,
  inspectedCrop: null,

  loadPipeline: async (force = false) => {
    if (!force && get().pipeline) return get().pipeline;
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      const data = await api.flowchart.getPipeline();
      if (generation !== flowchartGeneration) return null;
      // Step 4 clears its job ID when the dataset, labels, split, or task change.
      // A saved model reference is reusable only after that exact job is verified again.
      const verifiedJobId = useEvaluationStore.getState().jobId;
      const { pipeline, removedModelId } = onlyVerifiedModelIds(data, verifiedJobId);
      set({
        pipeline,
        pipelineDirty: removedModelId,
        selectedNodeId: null,
        executionResult: null,
        inspectedCrop: null,
        isLoading: false,
      });
      return pipeline;
    } catch (e: any) {
      if (generation !== flowchartGeneration) return null;
      console.error('Failed to load flowchart pipeline:', e);
      set({ isLoading: false, errorMessage: e?.message || '파이프라인 로드 실패' });
      return null;
    }
  },

  loadSingleSegmentationTemplate: async (jobId) => {
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      const pipeline = await api.flowchart.getSingleSegmentationTemplate(jobId);
      if (generation !== flowchartGeneration) return;
      set({
        pipeline,
        pipelineDirty: Boolean(jobId),
        selectedNodeId: 'node_inspect',
        executionResult: null,
        inspectedCrop: null,
        isLoading: false,
      });
    } catch (e: any) {
      if (generation !== flowchartGeneration) return;
      set({ isLoading: false, errorMessage: e?.message || '단일 분할 플로우를 불러오지 못했습니다.' });
    }
  },

  savePipeline: async (customPipeline) => {
    const target = customPipeline || get().pipeline;
    if (!target) return;
    const generation = flowchartGeneration;
    try {
      await api.flowchart.savePipeline(target);
      if (generation !== flowchartGeneration) return;
      set({ pipelineDirty: false, saveMessage: '파이프라인이 저장되었습니다.' });
      setTimeout(() => set({ saveMessage: null }), 3000);
    } catch (e: any) {
      if (generation !== flowchartGeneration) return;
      console.error('Failed to save flowchart pipeline:', e);
      set({ errorMessage: e?.message || '파이프라인 저장 실패' });
    }
  },

  runPipeline: async (customImagePath, customImageId) => {
    const { pipeline, selectedImage } = get();
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
      (node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop') && !node.data.model_job_id
    )) {
      set({ errorMessage: '검사를 실행하려면 검사 노드에 학습 모델 작업 ID를 지정해야 합니다.' });
      return false;
    }

    const generation = ++flowchartGeneration;
    set({ isRunning: true, errorMessage: null, executionResult: null, inspectedCrop: null, activeRunningNodeId: null });

    try {
      const res = await api.flowchart.run({
        image_path: imagePath,
        image_id: imageId,
        pipeline: pipeline || undefined,
      });
      if (generation !== flowchartGeneration) return false;

      set({
        executionResult: res,
        isRunning: false,
        activeRunningNodeId: null,
      });
      return true;
    } catch (e: any) {
      if (generation !== flowchartGeneration) return false;
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
    const current = get().pipeline;
    if (!current) return;
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
    set({
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
    const current = get().pipeline;
    if (!current) return;
    set({
      pipeline: {
        ...current,
        nodes: [...current.nodes, newNode],
      },
      pipelineDirty: true,
      executionResult: null,
      inspectedCrop: null,
    });
  },

  setSelectedImage: (image) => set({ selectedImage: image, executionResult: null, inspectedCrop: null }),
  setImagePickerOpen: (isImagePickerOpen) => set({ isImagePickerOpen }),
  setInspectedCrop: (inspectedCrop) => set({ inspectedCrop }),
  clearError: () => set({ errorMessage: null }),
  resetExecution: () => set({ executionResult: null, inspectedCrop: null, activeRunningNodeId: null }),

  invalidateForDataChange: () => {
    flowchartGeneration += 1;
    set({
      pipeline: null,
      pipelineDirty: false,
      modelContextInvalidated: true,
      contextRevision: get().contextRevision + 1,
      executionResult: null, isLoading: false, isRunning: false,
      activeRunningNodeId: null, selectedNodeId: null,
      selectedImage: null, isImagePickerOpen: false, inspectedCrop: null,
      saveMessage: null, errorMessage: null,
    });
  },
}));
