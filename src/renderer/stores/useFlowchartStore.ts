/**
 * src/renderer/stores/useFlowchartStore.ts
 * Multi-Model Chaining & Flowchart Pipeline State Management (Neuro-T Flowchart).
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

interface FlowchartState {
  pipeline: FlowchartPipeline | null;
  executionResult: FlowchartExecutionResult | null;
  isLoading: boolean;
  isRunning: boolean;
  activeRunningNodeId: string | null;
  selectedNodeId: string | null;
  saveMessage: string | null;
  errorMessage: string | null;

  // Real Image Selection
  selectedImage: SelectedInspectionImage | null;
  isImagePickerOpen: boolean;

  // Intermediate Crop Inspection Modal
  inspectedCrop: FlowchartCrop | null;

  // Actions
  loadPipeline: () => Promise<void>;
  savePipeline: (customPipeline?: FlowchartPipeline) => Promise<void>;
  runPipeline: (customImagePath?: string, customImageId?: string) => Promise<void>;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, patch: Partial<FlowNodeData>) => void;
  addNode: (node: FlowNode) => void;
  setSelectedImage: (image: SelectedInspectionImage | null) => void;
  setImagePickerOpen: (open: boolean) => void;
  setInspectedCrop: (crop: FlowchartCrop | null) => void;
  clearError: () => void;
  resetExecution: () => void;
}

export const useFlowchartStore = create<FlowchartState>((set, get) => ({
  pipeline: null,
  executionResult: null,
  isLoading: false,
  isRunning: false,
  activeRunningNodeId: null,
  selectedNodeId: null,
  saveMessage: null,
  errorMessage: null,

  selectedImage: null,
  isImagePickerOpen: false,
  inspectedCrop: null,

  loadPipeline: async () => {
    set({ isLoading: true, errorMessage: null });
    try {
      const data = await api.flowchart.getPipeline();
      set({ pipeline: data, isLoading: false });
    } catch (e: any) {
      console.error('Failed to load flowchart pipeline:', e);
      set({ isLoading: false, errorMessage: e?.message || '파이프라인 로드 실패' });
    }
  },

  savePipeline: async (customPipeline) => {
    const target = customPipeline || get().pipeline;
    if (!target) return;
    try {
      await api.flowchart.savePipeline(target);
      set({ saveMessage: '파이프라인이 저장되었습니다.' });
      setTimeout(() => set({ saveMessage: null }), 3000);
    } catch (e: any) {
      console.error('Failed to save flowchart pipeline:', e);
      set({ errorMessage: e?.message || '파이프라인 저장 실패' });
    }
  },

  runPipeline: async (customImagePath, customImageId) => {
    const { pipeline, selectedImage } = get();
    const imagePath = customImagePath || selectedImage?.imagePath;
    const imageId = customImageId || selectedImage?.imageId;

    set({ isRunning: true, errorMessage: null, activeRunningNodeId: 'node_input' });

    // Step-by-step progress animation timers
    const timer1 = setTimeout(() => set({ activeRunningNodeId: 'node_crop' }), 200);
    const timer2 = setTimeout(() => set({ activeRunningNodeId: 'node_inspect' }), 450);
    const timer3 = setTimeout(() => set({ activeRunningNodeId: 'node_decision' }), 700);

    try {
      const res = await api.flowchart.run({
        image_path: imagePath,
        image_id: imageId,
        pipeline: pipeline || undefined,
      });

      clearTimeout(timer1);
      clearTimeout(timer2);
      clearTimeout(timer3);

      set({
        executionResult: res,
        isRunning: false,
        activeRunningNodeId: null,
      });
    } catch (e: any) {
      clearTimeout(timer1);
      clearTimeout(timer2);
      clearTimeout(timer3);
      console.error('Failed to run flowchart pipeline:', e);
      set({
        isRunning: false,
        activeRunningNodeId: null,
        errorMessage: e?.message || '파이프라인 실행 중 오류가 발생했습니다.',
      });
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
    });
  },

  setSelectedImage: (image) => set({ selectedImage: image }),
  setImagePickerOpen: (isImagePickerOpen) => set({ isImagePickerOpen }),
  setInspectedCrop: (inspectedCrop) => set({ inspectedCrop }),
  clearError: () => set({ errorMessage: null }),
  resetExecution: () => set({ executionResult: null, activeRunningNodeId: null }),
}));
