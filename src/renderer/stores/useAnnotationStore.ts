/**
 * src/renderer/stores/useAnnotationStore.ts
 * Unified Zustand Store for Canvas Labeling Tool with Undo/Redo & API Persistence.
 */

import { create } from 'zustand';
import type { AnnotationItem, Category, ImageMeta, TaskType, ToolType, ViewTransform } from '../types';
import { api, getApiBaseUrl } from '../services/api';

export const DEFAULT_CATEGORIES: Category[] = [
  { id: 0, name: 'OK', color: '#10b981' },
  { id: 1, name: 'Solder Bridge', color: '#3b82f6' },
  { id: 2, name: 'Scratch', color: '#f59e0b' },
  { id: 3, name: 'Crack', color: '#ef4444' },
  { id: 4, name: 'Void', color: '#a855f7' },
  { id: 5, name: 'Contamination', color: '#06b6d4' },
];

const MAX_HISTORY = 40;

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

  // Persistence & History
  isDirty: boolean;
  isSaving: boolean;
  saveMessage: string | null;
  history: AnnotationItem[][];
  future: AnnotationItem[][];

  // Actions
  setTask: (task: TaskType) => void;
  setImages: (images: ImageMeta[], initialIndex?: number) => void;
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

  setViewTransform: (transform: ViewTransform | ((prev: ViewTransform) => ViewTransform)) => void;
  setZoom: (zoom: number) => void;
  setPan: (pan: { x: number; y: number }) => void;
  resetView: () => void;
  setMaskOpacity: (opacity: number) => void;
  toggleMaskVisible: () => void;
  setHeatmapUrl: (url: string | null) => void;

  markNormal: (isNormal: boolean) => void;
  loadAnnotationsForCurrent: () => Promise<void>;
  saveAnnotations: () => Promise<boolean>;

  autoSelectTolerance: number;
  setAutoSelectTolerance: (tolerance: number) => void;
  triggerAutoSelect: (seedX: number, seedY: number) => Promise<boolean>;
  triggerShapeConverter: () => Promise<boolean>;
  convertShape: (annId: string, targetType: 'bbox' | 'polygon' | 'mask' | 'rotated_bbox') => Promise<boolean>;

  undo: () => void;
  redo: () => void;
}

export const useAnnotationStore = create<AnnotationState>((set, get) => ({
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
  history: [],
  future: [],

  setTask: (task) => set({ task }),

  setImages: (images, initialIndex = 0) => {
    const idx = images.length > 0 ? Math.max(0, Math.min(images.length - 1, initialIndex)) : -1;
    const current = idx >= 0 ? images[idx] : null;
    set({
      images,
      currentImageIndex: idx,
      currentImage: current,
      activeImage: current,
      annotations: [],
      selectedAnnotationId: null,
      history: [],
      future: [],
      isDirty: false,
    });
    if (current) {
      get().loadAnnotationsForCurrent();
    }
  },

  setActiveImage: async (activeImage) => {
    if (!activeImage) {
      set({
        currentImage: null,
        activeImage: null,
        currentImageIndex: -1,
        annotations: [],
        isDirty: false,
      });
      return;
    }
    const idx = get().images.findIndex((img) => img.image_id === activeImage.image_id);
    set({
      currentImage: activeImage,
      activeImage,
      currentImageIndex: idx >= 0 ? idx : get().currentImageIndex,
      selectedAnnotationId: null,
      history: [],
      future: [],
      isDirty: false,
    });
    await get().loadAnnotationsForCurrent();
  },

  selectImageByIndex: async (index) => {
    const { images, isDirty, saveAnnotations } = get();
    if (index < 0 || index >= images.length) return;
    if (isDirty) {
      await saveAnnotations();
    }
    const current = images[index];
    set({
      currentImageIndex: index,
      currentImage: current,
      activeImage: current,
      selectedAnnotationId: null,
      history: [],
      future: [],
      isDirty: false,
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
    set({ annotations, isDirty: true });
  },

  addAnnotation: (item) => {
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
    const { annotations, history } = get();
    set({
      history: [...history, annotations].slice(-MAX_HISTORY),
      future: [],
      annotations: annotations.map((ann) => (ann.id === id ? { ...ann, ...updates } : ann)),
      isDirty: true,
    });
  },

  deleteAnnotation: (idOrIndex) => {
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

  setActiveTool: (tool) => set({ activeTool: tool }),

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
    if (!currentImage) return;

    try {
      const data = await api.annotations.get(currentImage.image_id, undefined, currentImage.file_path);
      const items: AnnotationItem[] = (data.annotations || []).map((item: any, idx: number) => ({
        ...item,
        id: item.id || `ann_${idx}_${Date.now()}`,
        color: item.color || DEFAULT_CATEGORIES.find((c) => c.name === item.label)?.color || '#3b82f6',
      }));

      // Auto-register any new categories found in annotations
      const currentCats = [...get().categories];
      const catNames = new Set(currentCats.map((c) => c.name.toLowerCase()));
      const palette = ['#ef4444', '#f59e0b', '#06b6d4', '#8b5cf6', '#ec4899', '#10b981', '#3b82f6', '#f97316'];
      items.forEach((it) => {
        if (it.label && !catNames.has(it.label.toLowerCase())) {
          catNames.add(it.label.toLowerCase());
          currentCats.push({
            id: currentCats.length + 1,
            name: it.label,
            color: it.color || palette[currentCats.length % palette.length],
          });
        }
      });

      const nextDimensions = data.image_width && data.image_height
        ? { width: data.image_width, height: data.image_height }
        : get().imageDimensions;

      set({
        annotations: items,
        categories: currentCats,
        imageDimensions: nextDimensions,
        maskUrl: data.mask_file ? data.mask_file : null,
        isDirty: false,
      });
    } catch {
      // no existing annotations
    }
  },

  saveAnnotations: async () => {
    const { currentImage, annotations } = get();
    if (!currentImage) return false;

    set({ isSaving: true, saveMessage: 'Saving...' });

    try {
      const res = await api.annotations.save({
        image_id: currentImage.image_id,
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
        })),
        image_width: get().imageDimensions?.width || currentImage.width || 8192,
        image_height: get().imageDimensions?.height || currentImage.height || 5464,
      });

      if (res.status === 'saved' || res.status === 'ok') {
        set({ isDirty: false, isSaving: false, saveMessage: 'Saved' });
        setTimeout(() => set({ saveMessage: null }), 2000);
        return true;
      }
      set({ isSaving: false, saveMessage: 'Saved' });
      setTimeout(() => set({ saveMessage: null }), 2000);
      return true;
    } catch (e: any) {
      set({ isSaving: false, saveMessage: `Failed: ${e.message || e}` });
      return false;
    }
  },

  undo: () => {
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

  triggerAutoSelect: async (seedX, seedY) => {
    const { currentImage, activeCategory, autoSelectTolerance, annotations, history } = get();
    if (!currentImage) return false;
    try {
      const res = await api.annotations.autoSelect({
        image_path: currentImage.file_path,
        image_id: currentImage.image_id,
        seed_x: seedX,
        seed_y: seedY,
        tolerance: autoSelectTolerance,
      });
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
        set({
          annotations: [...annotations, newAnn],
          history: [...history, annotations].slice(-MAX_HISTORY),
          future: [],
          isDirty: true,
        });
        return true;
      }
    } catch (e) {
      console.error('Auto-select failed:', e);
    }
    return false;
  },

  convertShape: async (annId: string, targetType: 'bbox' | 'polygon' | 'mask' | 'rotated_bbox') => {
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
        }),
      });

      if (!res.ok) {
        console.error('Shape converter HTTP error:', res.status, res.statusText);
        return false;
      }

      const json = await res.json();
      const converted = json.converted_data || json.result;
      if (!converted) return false;

      const updated = annotations.map((a) => {
        if (a.id !== annId) return a;

        if (targetType === 'polygon' && converted.polygon) {
          const poly: [number, number][] = converted.polygon;
          const xs = poly.map((p) => p[0]);
          const ys = poly.map((p) => p[1]);
          return {
            ...a,
            type: 'polygon' as const,
            polygon: poly,
            points: poly,
            bbox: (converted.bbox as [number, number, number, number]) || [
              Math.min(...xs),
              Math.min(...ys),
              Math.max(...xs),
              Math.max(...ys),
            ],
            rotated_bbox: undefined,
          };
        } else if (targetType === 'bbox' && converted.bbox) {
          return {
            ...a,
            type: 'bbox' as const,
            bbox: converted.bbox as [number, number, number, number],
            polygon: undefined,
            points: undefined,
            rotated_bbox: undefined,
          };
        } else if (targetType === 'rotated_bbox') {
          const rbox: [number, number, number, number, number] =
            converted.rotated_bbox || [
              converted.center[0],
              converted.center[1],
              converted.size[0],
              converted.size[1],
              converted.angle || 0,
            ];
          return {
            ...a,
            type: 'rotated_bbox' as const,
            rotated_bbox: rbox,
            bbox: (converted.bbox as [number, number, number, number]) || [
              rbox[0] - rbox[2] / 2,
              rbox[1] - rbox[3] / 2,
              rbox[0] + rbox[2] / 2,
              rbox[1] + rbox[3] / 2,
            ],
            polygon: undefined,
            points: undefined,
          };
        } else if (targetType === 'mask') {
          return {
            ...a,
            type: 'brush_mask' as const,
          };
        }
        return a;
      });

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

