/**
 * src/renderer/stores/useAnnotationStore.ts
 * Unified Zustand Store for Canvas Labeling Tool with Undo/Redo & API Persistence.
 */

import { create } from 'zustand';
import type { AnnotationItem, Category, ImageMeta, TaskType, ToolType, ViewTransform } from '../types';
import { api, getApiBaseUrl } from '../services/api';
import { datasetWorkflow, type ImageReviewMetadata } from '../services/datasetWorkflow';
import { useDatasetStore } from './useDatasetStore';
import { brushEditTarget, labelCategoryPalette } from '../components/labeling/foundationRequest';
import { applyConvertedShape } from '../components/labeling/convertedAnnotation';

export const DEFAULT_CATEGORIES: Category[] = [
  { id: 0, name: 'OK', color: '#10b981' },
  { id: 1, name: 'Solder Bridge', color: '#3b82f6' },
  { id: 2, name: 'Scratch', color: '#f59e0b' },
  { id: 3, name: 'Crack', color: '#ef4444' },
  { id: 4, name: 'Void', color: '#a855f7' },
  { id: 5, name: 'Contamination', color: '#06b6d4' },
];

const MAX_HISTORY = 40;
let annotationLoadSequence = 0;
let pendingSave: Promise<boolean> | null = null;

function annotationMaskUrl(image: ImageMeta): string {
  const imageId = encodeURIComponent(image.image_id);
  const filePath = encodeURIComponent(image.file_path);
  return `/api/annotations/${imageId}/mask?file_path=${filePath}&v=${Date.now()}`;
}

function annotationReadErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object') {
    for (const key of ['details', 'message_ko', 'detail', 'message']) {
      const value = (error as Record<string, unknown>)[key];
      if (typeof value === 'string' && value) return value;
    }
  }
  return '알 수 없는 오류';
}

interface AnnotationState {
  // Current Task & Images
  task: TaskType;
  images: ImageMeta[];
  currentImageIndex: number;
  currentImage: ImageMeta | null;
  activeImage: ImageMeta | null;

  // Annotations & Tools
  annotations: AnnotationItem[];
  selectedAnnotationId: string | null;
  activeTool: ToolType;
  activeCategory: Category;
  currentLabel: string;
  categories: Category[];
  brushRadius: number;

  // Viewport & Overlays
  viewTransform: ViewTransform;
  imageDimensions: { width: number; height: number };
  zoom: number;
  pan: { x: number; y: number };
  maskOpacity: number;
  maskVisible: boolean;
  maskUrl: string | null;
  heatmapUrl: string | null;

  // Actions
  setImageDimensions: (dim: { width: number; height: number }) => void;

  externalSelectionPath: string | null;
  metadata: ImageReviewMetadata | null;
  reviewerName: string;
  setReviewerName: (name: string) => void;
  setMetadata: (metadata: ImageReviewMetadata | null) => void;

  // Persistence & History
  isDirty: boolean;
  isSaving: boolean;
  saveMessage: string | null;
  annotationLoadStatus: 'ready' | 'loading' | 'error';
  annotationLoadError: string | null;
  autoSelectError: string | null;
  history: AnnotationItem[][];
  future: AnnotationItem[][];

  // Actions
  setTask: (task: TaskType) => void;
  setImages: (images: ImageMeta[], initialIndex?: number) => Promise<boolean>;
  syncDatasetImages: (images: ImageMeta[]) => Promise<boolean>;
  setActiveImage: (image: ImageMeta | null) => Promise<void>;
  selectImageByIndex: (index: number) => Promise<void>;
  nextImage: () => Promise<void>;
  prevImage: () => Promise<void>;

  setAnnotations: (annotations: AnnotationItem[]) => void;
  addAnnotation: (item: AnnotationItem) => void;
  updateAnnotation: (id: string, updates: Partial<AnnotationItem>) => void;
  deleteAnnotation: (idOrIndex: string | number) => void;
  deleteSelected: () => void;
  setSelectedAnnotationId: (id: string | null) => void;

  setActiveTool: (tool: ToolType) => void;
  setActiveCategory: (cat: Category) => void;
  setCurrentLabel: (label: string) => void;
  addCategory: (name: string, color?: string) => void;
  setBrushRadius: (radius: number) => void;
  commitBrushMask: (dataUrl: string) => void;

  setViewTransform: (transform: ViewTransform | ((prev: ViewTransform) => ViewTransform)) => void;
  setZoom: (zoom: number) => void;
  setPan: (pan: { x: number; y: number }) => void;
  resetView: () => void;
  setMaskOpacity: (opacity: number) => void;
  toggleMaskVisible: () => void;
  setHeatmapUrl: (url: string | null) => void;

  markNormal: (isNormal: boolean) => void;
  loadAnnotationsForCurrent: () => Promise<boolean>;
  saveAnnotations: () => Promise<boolean>;

  autoSelectTolerance: number;
  setAutoSelectTolerance: (tolerance: number) => void;
  triggerAutoSelect: (seedX: number, seedY: number) => Promise<boolean>;
  clearAutoSelectError: () => void;
  triggerShapeConverter: () => Promise<boolean>;
  convertShape: (annId: string, targetType: 'bbox' | 'polygon' | 'mask' | 'rotated_bbox') => Promise<boolean>;

  undo: () => void;
  redo: () => void;
}

export const useAnnotationStore = create<AnnotationState>((set, get) => ({
  externalSelectionPath: null,
  metadata: null,
  reviewerName: typeof localStorage !== 'undefined' ? localStorage.getItem('modu-reviewer-name') || '' : '',
  setReviewerName: (name) => { if (typeof localStorage !== 'undefined') localStorage.setItem('modu-reviewer-name', name); set({ reviewerName: name }); },
  setMetadata: (metadata) => set({ metadata }),
  task: 'detection',
  images: [],
  currentImageIndex: -1,
  currentImage: null,
  activeImage: null,

  annotations: [],
  selectedAnnotationId: null,
  activeTool: 'bbox',
  activeCategory: DEFAULT_CATEGORIES[1],
  currentLabel: DEFAULT_CATEGORIES[1].name,
  categories: DEFAULT_CATEGORIES,
  brushRadius: 12,

  viewTransform: { scale: 1, offsetX: 0, offsetY: 0 },
  imageDimensions: { width: 8192, height: 5464 },
  zoom: 1,
  pan: { x: 0, y: 0 },
  maskOpacity: 0.5,
  maskVisible: true,
  maskUrl: null,
  heatmapUrl: null,

  setImageDimensions: (imageDimensions) => set({ imageDimensions }),

  isDirty: false,
  isSaving: false,
  saveMessage: null,
  annotationLoadStatus: 'ready',
  annotationLoadError: null,
  autoSelectError: null,
  history: [],
  future: [],

  setTask: (task) => set({ task }),

  setImages: async (images, initialIndex = 0) => {
    if (get().isDirty) {
      const saved = await get().saveAnnotations();
      if (!saved || get().isDirty) return false;
    }
    const idx = images.length > 0 ? Math.max(0, Math.min(images.length - 1, initialIndex)) : -1;
    const current = idx >= 0 ? images[idx] : null;
    set({
      images,
      externalSelectionPath: null,
      metadata: null,
      currentImageIndex: idx,
      currentImage: current,
      activeImage: current,
      annotations: [],
      selectedAnnotationId: null,
      history: [],
      future: [],
      isDirty: false,
      annotationLoadStatus: current ? 'loading' : 'ready',
      annotationLoadError: null,
      autoSelectError: null,
    });
    if (current) {
      await get().loadAnnotationsForCurrent();
    }
    return true;
  },

  syncDatasetImages: async (images) => {
    const { currentImage } = get();
    const preservedIndex = currentImage
      ? images.findIndex((image) =>
          image.image_id === currentImage.image_id && image.file_path === currentImage.file_path)
      : -1;
    return get().setImages(images, preservedIndex >= 0 ? preservedIndex : 0);
  },

  setActiveImage: async (activeImage) => {
    if (get().isDirty) {
      const saved = await get().saveAnnotations();
      if (!saved || get().isDirty) return;
    }
    if (!activeImage) {
      set({
        currentImage: null,
        activeImage: null,
        currentImageIndex: -1,
        annotations: [],
        isDirty: false,
        annotationLoadStatus: 'ready',
        annotationLoadError: null,
        autoSelectError: null,
      });
      return;
    }
    const idx = get().images.findIndex((img) =>
      img.image_id === activeImage.image_id && img.file_path === activeImage.file_path);
    set({
      currentImage: activeImage,
      metadata: null,
      externalSelectionPath: null,
      activeImage,
      currentImageIndex: idx >= 0 ? idx : get().currentImageIndex,
      annotations: [],
      maskUrl: null,
      selectedAnnotationId: null,
      history: [],
      future: [],
      isDirty: false,
      annotationLoadStatus: 'loading',
      annotationLoadError: null,
      autoSelectError: null,
    });
    await get().loadAnnotationsForCurrent();
  },

  selectImageByIndex: async (index) => {
    const { images, isDirty, saveAnnotations } = get();
    if (index < 0 || index >= images.length) return;
    if (index === get().currentImageIndex && get().currentImage) return;
    if (isDirty) {
      const saved = await saveAnnotations();
      if (!saved || get().isDirty) return;
    }
    const current = images[index];
    set({
      currentImageIndex: index,
      metadata: null,
      externalSelectionPath: null,
      currentImage: current,
      activeImage: current,
      annotations: [],
      maskUrl: null,
      selectedAnnotationId: null,
      history: [],
      future: [],
      isDirty: false,
      annotationLoadStatus: 'loading',
      annotationLoadError: null,
      autoSelectError: null,
    });
    await get().loadAnnotationsForCurrent();
  },

  nextImage: async () => {
    const { currentImageIndex, images, selectImageByIndex } = get();
    if (currentImageIndex < images.length - 1) {
      await selectImageByIndex(currentImageIndex + 1);
    }
  },

  prevImage: async () => {
    const { currentImageIndex, selectImageByIndex } = get();
    if (currentImageIndex > 0) {
      await selectImageByIndex(currentImageIndex - 1);
    }
  },

  setAnnotations: (annotations) => {
    if (get().annotationLoadStatus !== 'ready') return;
    set({ annotations, isDirty: true });
  },

  addAnnotation: (item) => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { annotations, history } = get();
    const itemWithId = {
      ...item,
      id: item.id || `ann_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
    };
    set({
      history: [...history, annotations].slice(-MAX_HISTORY),
      future: [],
      annotations: [...annotations, itemWithId],
      selectedAnnotationId: itemWithId.id || null,
      isDirty: true,
    });
  },

  updateAnnotation: (id, updates) => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { annotations, history } = get();
    set({
      history: [...history, annotations].slice(-MAX_HISTORY),
      future: [],
      annotations: annotations.map((ann) => (ann.id === id ? { ...ann, ...updates } : ann)),
      isDirty: true,
    });
  },

  deleteAnnotation: (idOrIndex) => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { annotations, history, selectedAnnotationId } = get();
    let newAnnotations: AnnotationItem[];
    let deletedId: string | null = null;

    if (typeof idOrIndex === 'number') {
      deletedId = annotations[idOrIndex]?.id || null;
      newAnnotations = annotations.filter((_, idx) => idx !== idOrIndex);
    } else {
      deletedId = idOrIndex;
      newAnnotations = annotations.filter((ann) => ann.id !== idOrIndex);
    }

    set({
      history: [...history, annotations].slice(-MAX_HISTORY),
      future: [],
      annotations: newAnnotations,
      selectedAnnotationId: selectedAnnotationId === deletedId ? null : selectedAnnotationId,
      isDirty: true,
    });
  },

  deleteSelected: () => {
    const { selectedAnnotationId, deleteAnnotation } = get();
    if (selectedAnnotationId) {
      deleteAnnotation(selectedAnnotationId);
    }
  },

  setSelectedAnnotationId: (id) => set({ selectedAnnotationId: id }),

  setActiveTool: (tool) => set({ activeTool: tool, autoSelectError: null }),

  setActiveCategory: (cat) => set({ activeCategory: cat, currentLabel: cat.name }),

  setCurrentLabel: (label) => {
    const cat = get().categories.find((c) => c.name === label) || {
      id: 99,
      name: label,
      color: '#3b82f6',
    };
    set({ currentLabel: label, activeCategory: cat });
  },

  addCategory: (name, color) => {
    const { categories } = get();
    const newId = categories.length > 0 ? Math.max(...categories.map((c) => c.id)) + 1 : 1;
    const newCat: Category = {
      id: newId,
      name,
      color: color || '#8b5cf6',
    };
    set({
      categories: [...categories, newCat],
      activeCategory: newCat,
      currentLabel: name,
    });
  },

  setBrushRadius: (radius) => set({ brushRadius: Math.max(2, Math.min(64, radius)) }),
  commitBrushMask: (dataUrl) => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { annotations, history, activeCategory, currentImage } = get();
    if (!currentImage) return;
    const existing = brushEditTarget(annotations, get().selectedAnnotationId, activeCategory.id);
    const updated: AnnotationItem = existing
      ? { ...existing, mask_rle: dataUrl }
      : { id: `brush_${currentImage.image_id}_${activeCategory.id}`, type: 'brush_mask', label: activeCategory.name,
          category_id: activeCategory.id, color: activeCategory.color, mask_rle: dataUrl };
    set({
      annotations: existing
        ? annotations.map((item) => item.id === existing.id ? updated : item)
        : [...annotations, updated],
      history: [...history, annotations].slice(-MAX_HISTORY),
      future: [],
      isDirty: true,
    });
  },

  setViewTransform: (transform) => {
    if (typeof transform === 'function') {
      set((state) => {
        const next = transform(state.viewTransform);
        return {
          viewTransform: next,
          zoom: next.scale,
          pan: { x: next.offsetX, y: next.offsetY },
        };
      });
    } else {
      set({
        viewTransform: transform,
        zoom: transform.scale,
        pan: { x: transform.offsetX, y: transform.offsetY },
      });
    }
  },

  setZoom: (zoomVal) => {
    const scale = Math.max(0.05, Math.min(zoomVal, 32));
    set((state) => ({
      zoom: scale,
      viewTransform: { ...state.viewTransform, scale },
    }));
  },

  setPan: (pan) => {
    set((state) => ({
      pan,
      viewTransform: { ...state.viewTransform, offsetX: pan.x, offsetY: pan.y },
    }));
  },

  resetView: () => {
    set({
      zoom: 1,
      pan: { x: 0, y: 0 },
      viewTransform: { scale: 1, offsetX: 0, offsetY: 0 },
    });
  },

  setMaskOpacity: (opacity) => set({ maskOpacity: Math.max(0, Math.min(1, opacity)) }),

  toggleMaskVisible: () => set((state) => ({ maskVisible: !state.maskVisible })),

  setHeatmapUrl: (url) => set({ heatmapUrl: url }),

  markNormal: (isNormal) => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { annotations, history } = get();
    set({ history: [...history, annotations].slice(-MAX_HISTORY), future: [] });

    if (isNormal) {
      const okItem: AnnotationItem = {
        id: `norm_${Date.now()}`,
        type: 'tag',
        label: 'OK',
        category_id: 0,
        is_normal: true,
        color: '#10b981',
      };
      set({ annotations: [okItem], selectedAnnotationId: null, isDirty: true });
    } else {
      set({ annotations: annotations.filter((a) => !a.is_normal), isDirty: true });
    }
  },

  loadAnnotationsForCurrent: async () => {
    const { currentImage } = get();
    if (!currentImage || get().isDirty) return false;
    const requestSequence = ++annotationLoadSequence;
    set({ annotationLoadStatus: 'loading', annotationLoadError: null });

    try {
      const data = await datasetWorkflow.annotations(currentImage.image_id, currentImage.file_path);
      if (requestSequence !== annotationLoadSequence ||
          get().currentImage !== currentImage || get().isDirty) return false;
      if (!data || !Array.isArray(data.annotations)
          || (data.image_id && data.image_id !== currentImage.image_id)) {
        throw new Error('라벨 조회 응답이 올바르지 않습니다.');
      }
      const items: AnnotationItem[] = data.annotations.map((item: any, idx: number) => ({
        ...item,
        id: item.id || `ann_${idx}_${Date.now()}`,
        color: item.color || DEFAULT_CATEGORIES.find((c) => c.name === item.label)?.color || '#3b82f6',
      }));

      const currentCats=labelCategoryPalette(get().categories,items,data.mask_classes);

      const nextDimensions = data.image_width && data.image_height
        ? { width: data.image_width, height: data.image_height }
        : get().imageDimensions;

      set({
        annotations: items,
        metadata: data.metadata || null,
        categories: currentCats,
        activeCategory: currentCats.find(c=>c.name===get().activeCategory.name)||currentCats[0]||get().activeCategory,
        imageDimensions: nextDimensions,
        maskUrl: data.mask_file ? annotationMaskUrl(currentImage) : null,
        isDirty: false,
        annotationLoadStatus: 'ready',
        annotationLoadError: null,
        saveMessage: null,
      });
      return true;
    } catch (error) {
      if (requestSequence === annotationLoadSequence && get().currentImage === currentImage) {
        set({ annotationLoadStatus: 'error', annotationLoadError: annotationReadErrorMessage(error) });
      }
      return false;
    }
  },

  saveAnnotations: () => {
    if (get().annotationLoadStatus !== 'ready') {
      set({ saveMessage: '기존 라벨을 불러온 뒤 저장할 수 있습니다.' });
      return Promise.resolve(false);
    }
    if (pendingSave) return pendingSave;
    const { currentImage, annotations, isDirty } = get();
    if (!currentImage) return Promise.resolve(false);

    set({ isSaving: true, saveMessage: 'Saving...' });

    const save = async (): Promise<boolean> => {
      try {
        const res = await datasetWorkflow.saveAnnotations({
          expected_revision: get().metadata?.revision,
          actor: get().reviewerName.trim() || "operator",
          image_id: currentImage.image_id,
          image_path: currentImage.file_path,
          annotations: annotations.map((a) => ({
            id: a.id,
            type: a.type,
            label: a.label,
            category_id: a.category_id,
            bbox: a.bbox,
            polygon: a.polygon || a.points,
            points: a.points || a.polygon,
            is_normal: a.is_normal,
            color: a.color,
            rotated_bbox: a.rotated_bbox,
            direction_deg: a.direction_deg,
            mask_rle: a.mask_rle,
          })),
          image_width: get().imageDimensions?.width || currentImage.width || 8192,
          image_height: get().imageDimensions?.height || currentImage.height || 5464,
        });

        if (res.status === 'saved' || res.status === 'ok') {
          const sameImage = get().currentImage === currentImage;
          set({
            isDirty: get().currentImage !== currentImage || get().annotations !== annotations,
            isSaving: false,
            saveMessage: 'Saved',
            metadata: sameImage ? (res.metadata || null) : get().metadata,
            maskUrl: sameImage ? (res.mask_generated ? annotationMaskUrl(currentImage) : null) : get().maskUrl,
          });
          setTimeout(() => {
            if (get().saveMessage === 'Saved') set({ saveMessage: null });
          }, 2000);
          if (isDirty) void useDatasetStore.getState().annotationsChanged();
          return true;
        }
        set({ isSaving: false, saveMessage: `Failed: unexpected save status ${res.status}` });
        return false;
      } catch (e: any) {
        set({ isSaving: false, saveMessage: `Failed: ${annotationReadErrorMessage(e)}` });
        return false;
      }
    };
    pendingSave = save().finally(() => { pendingSave = null; });
    return pendingSave;
  },

  undo: () => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { history, annotations, future } = get();
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    set({
      history: history.slice(0, -1),
      future: [annotations, ...future],
      annotations: prev,
      isDirty: true,
    });
  },

  redo: () => {
    if (get().annotationLoadStatus !== 'ready') return;
    const { future, annotations, history } = get();
    if (future.length === 0) return;
    const next = future[0];
    set({
      history: [...history, annotations].slice(-MAX_HISTORY),
      future: future.slice(1),
      annotations: next,
      isDirty: true,
    });
  },

  autoSelectTolerance: 25,
  setAutoSelectTolerance: (autoSelectTolerance) => set({ autoSelectTolerance }),
  clearAutoSelectError: () => set({ autoSelectError: null }),

  triggerAutoSelect: async (seedX, seedY) => {
    const { currentImage, activeCategory, autoSelectTolerance } = get();
    if (get().annotationLoadStatus !== 'ready') return false;
    if (!currentImage) {
      set({ autoSelectError: '외곽선을 추출할 이미지를 먼저 선택하세요.' });
      return false;
    }
    set({ autoSelectError: null });
    try {
      const res = await api.annotations.autoSelect({
        image_path: currentImage.file_path,
        image_id: currentImage.image_id,
        seed_x: seedX,
        seed_y: seedY,
        tolerance: autoSelectTolerance,
      });
      if (get().currentImage !== currentImage || get().annotationLoadStatus !== 'ready') return false;
      if (res?.result?.polygon && res.result.polygon.length >= 3) {
        const newAnn: AnnotationItem = {
          id: `auto_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
          type: 'polygon',
          label: activeCategory.name,
          category_id: activeCategory.id,
          color: activeCategory.color,
          polygon: res.result.polygon,
          points: res.result.polygon,
          bbox: res.result.bbox,
        };
        get().addAnnotation(newAnn);
        return true;
      }
      set({ autoSelectError: '선택한 지점에서 외곽선을 찾지 못했습니다. 민감도를 조정하거나 수동 도구를 사용하세요.' });
    } catch (e) {
      if (get().currentImage === currentImage) {
        const details = e && typeof e === 'object' && 'details' in e ? e.details : null;
        const message = e && typeof e === 'object' && 'message_ko' in e ? e.message_ko : null;
        const detail = e instanceof Error ? e.message
          : typeof details === 'string' && details ? details
          : typeof message === 'string' && message ? message
          : String(e);
        set({ autoSelectError: `자동 외곽선 추출 실패: ${detail}` });
      }
    }
    return false;
  },

  convertShape: async (annId: string, targetType: 'bbox' | 'polygon' | 'mask' | 'rotated_bbox') => {
    if (get().annotationLoadStatus !== 'ready') return false;
    const { currentImage, annotations, history } = get();
    if (!currentImage) return false;
    const target = annotations.find((a) => a.id === annId);
    if (!target) return false;

    let sourceType: 'bbox' | 'polygon' | 'mask' | 'rotated_bbox' = 'bbox';
    let sourceData: any = null;

    if (target.type === 'rotated_bbox' && target.rotated_bbox) {
      sourceType = 'rotated_bbox';
      sourceData = target.rotated_bbox;
    } else if (target.type === 'polygon' && (target.polygon || target.points)) {
      sourceType = 'polygon';
      sourceData = target.polygon || target.points;
    } else if (target.type === 'brush_mask') {
      sourceType = 'mask';
      sourceData = target.mask_rle || [];
    } else if (target.bbox) {
      sourceType = 'bbox';
      sourceData = target.bbox;
    } else if (target.polygon || target.points) {
      sourceType = 'polygon';
      sourceData = target.polygon || target.points;
    } else if (target.rotated_bbox) {
      sourceType = 'rotated_bbox';
      sourceData = target.rotated_bbox;
    }

    if (!sourceData) return false;

    try {
      const base = await getApiBaseUrl();
      const res = await fetch(`${base}/api/annotations/shape-converter`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_type: sourceType,
          target_type: targetType,
          data: sourceData,
          image_dimensions: {
            width: currentImage.width || 512,
            height: currentImage.height || 512,
          },
          image_path: currentImage.file_path,
          image_id: currentImage.image_id,
          mask_color: target.color || '#3b82f6',
        }),
      });

      if (!res.ok) {
        console.error('Shape converter HTTP error:', res.status, res.statusText);
        return false;
      }

      const json = await res.json();
      if (get().currentImage !== currentImage || get().annotationLoadStatus !== 'ready') return false;
      const converted = json.converted_data || json.result;
      if (!converted) return false;
      const replacement = applyConvertedShape(target, targetType, converted);
      if (!replacement) return false;
      const updated = annotations.map((a) => a.id === annId ? replacement : a);

      set({
        annotations: updated,
        history: [...history, annotations].slice(-MAX_HISTORY),
        future: [],
        isDirty: true,
      });
      return true;
    } catch (e) {
      console.error('Shape conversion failed:', e);
      return false;
    }
  },

  triggerShapeConverter: async () => {
    const { selectedAnnotationId, convertShape, annotations } = get();
    if (!selectedAnnotationId) return false;
    const target = annotations.find((a) => a.id === selectedAnnotationId);
    if (!target) return false;
    // If target is bbox, convert to polygon; if polygon, convert to bbox
    const targetType = target.type === 'polygon' ? 'bbox' : 'polygon';
    return convertShape(selectedAnnotationId, targetType);
  },
}));
