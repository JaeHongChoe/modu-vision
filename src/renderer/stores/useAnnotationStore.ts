/**
 * src/renderer/stores/useAnnotationStore.ts
 * Unified Zustand Store for Canvas Labeling Tool with Undo/Redo & API Persistence.
 */

import { create } from 'zustand';
import type { AnnotationItem, Category, ImageMeta, TaskType, ToolType, ViewTransform } from '../types';
import { api, getApiBaseUrl, getApiPersistenceIdentity, getProjectContextGeneration } from '../services/api';
import { datasetWorkflow, type ImageReviewMetadata } from '../services/datasetWorkflow';
import { useDatasetStore } from './useDatasetStore';
import { brushEditTarget, labelCategoryPalette } from '../components/labeling/foundationRequest';
import { applyConvertedShape } from '../components/labeling/convertedAnnotation';
import {bookPalette,imageLeaseToken} from '../components/labeling/teamDataWorkflow';
import type {BookCategory,EditLease} from '../services/teamDataApi';

// Example defect names: the class bar offers them as one-click additions, never as the palette or the drawing class by
// themselves. OK (id 0) is the normal tag.
export const DEFAULT_CATEGORIES: Category[] = [
  { id: 0, name: 'OK', color: '#10b981' },
  { id: 1, name: 'Solder Bridge', color: '#3b82f6' },
  { id: 2, name: 'Scratch', color: '#f59e0b' },
  { id: 3, name: 'Crack', color: '#ef4444' },
  { id: 4, name: 'Void', color: '#a855f7' },
  { id: 5, name: 'Contamination', color: '#06b6d4' },
];
// The drawing class of a project that has no class yet: neutral, the backend's own default label, ASCII because result
// overlays print class names with OpenCV's font. The class bar says so, and 클래스 이름 변경 renames it with a backup.
// It is also the drawing class before the first load of a project (whether a class was picked is `classPicked`).
export const FIRST_CLASS: Category = { id: 1, name: 'defect', color: '#3b82f6' };
const BASE_CATEGORIES: Category[] = [DEFAULT_CATEGORIES[0]];
// The copy of FIRST_CLASS the store puts in a palette is marked (a symbol key survives the palette's object spreads and is
// never serialised), so a class a person names 'defect' is never mistaken for it.
const STAND_IN = Symbol('stand-in class');
const isStandIn = (c: Category) => Boolean((c as Category & { [STAND_IN]?: true })[STAND_IN]);
/** Whether `c` is the store's stand-in (the class bar says which name the first labels get). */
export const isStandInCategory = isStandIn;
// The stand-in becomes a real class once a person picks it or draws with it: drop the mark there and in the palette.
const unmarked = (c: Category): Category => ({ id: c.id, name: c.name, color: c.color });
// Normal-class names keep their chips, but a region is drawn to mark a defect, so none of them is the default drawing
// class. This is the normal test of utils/classSemantics.ts (the renderer port of backend class_semantics), repeated here
// because the store takes no new imports (its tests load it with fixed module mocks); drawingClassDefault.test.cjs checks
// that both agree on every shared case in classSemanticsCases.json.
const NORMAL_WHOLE = new Set(['0', 'bg', 'background', 'nodefect', 'nondefect', 'nondefective', 'notdefective', 'defectfree']);
const NORMAL_TOKENS = new Set(['ok', 'good', 'normal', 'pass', 'passed', '정상', '정상품', '양품', '합격']);
const DEFECT_TOKENS = new Set(['ng', 'nok', 'defect', 'defective', 'fail', 'failed', '불량', '불량품', '불합격', '비정상', '결함']);
export function isNormalClassName(name: string): boolean {
  const tokens = String(name).normalize('NFKC').toLowerCase().trim().split(/[\s_\-./]+/u).filter(Boolean);
  if (NORMAL_WHOLE.has(tokens.join(''))) return true;
  const found = new Set<string>();
  let negate = false;
  for (const token of tokens) {
    if (token === 'no' || token === 'non' || token === 'not') { negate = true; continue; }
    const role = NORMAL_TOKENS.has(token) ? 'normal' : DEFECT_TOKENS.has(token) ? 'defect' : null;
    if (role) found.add(negate ? (role === 'normal' ? 'defect' : 'normal') : role);
    negate = false;
  }
  return found.size === 1 && found.has('normal');
}
const pickable = (c: Category) => c.id > 0 && !isNormalClassName(c.name);
/** A shape drawn under the drawing class makes it the person's class (kept on the next images), and a stand-in drawn
 *  with becomes a real class. */
function claimDrawingClass(state: { activeCategory: Category; categories: Category[] }, item: { label?: string; category_id?: number }) {
  const active = state.activeCategory;
  if (item.label !== active.name || item.category_id !== active.id) return {};
  if (!isStandIn(active)) return { classPicked: true };
  const real = unmarked(active);
  return { classPicked: true, activeCategory: real, categories: state.categories.map((c) => isStandIn(c) && c.id === active.id ? real : c) };
}

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

function annotationContext(task: TaskType): string {
  return JSON.stringify([
    getApiPersistenceIdentity(), getProjectContextGeneration(),
    useDatasetStore.getState().folderPath, task,
  ]);
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
  /** 한 장에 하나씩: a finished box saves the image and moves to the next one (one drag per image instead of drag + next).
   *  Off by default (an image can hold several defects); remembered on this device. */
  advanceOnBox: boolean;
  setAdvanceOnBox: (on: boolean) => void;
  /** Counts the canvas's requests to move on; the filmstrip moves forward once per new count. */
  advanceRequest: number;
  requestAdvance: () => void;
  setMetadata: (metadata: ImageReviewMetadata | null) => void;
  teamEditingEnabled: boolean;
  teamReviewEnabled: boolean;
  editLease: EditLease | null;
  /** The image whose 편집 시작 request is still waiting for its reply. Its save button stays off until the lease lands,
   *  and a save tried in between says the lease is on its way instead of asking for 편집 시작 again. */
  editLeaseAcquiring: { image_uuid: string } | null;
  labelbookVersion: number | null;
  /** The source folder and task the class palette was built for; another one starts a fresh palette. */
  paletteContext: string | null;
  /** The person picked the drawing class (a chip, a new class) or drew with it; until then each image's load picks the default. */
  classPicked: boolean;
  setTeamEditingEnabled: (enabled:boolean) => void;
  setTeamReviewEnabled: (enabled:boolean) => void;
  setEditLease: (lease:EditLease|null) => void;
  setEditLeaseAcquiring: (value:{ image_uuid: string }|null) => void;
  setLabelbook: (version:number|null,categories?:BookCategory[]) => void;

  // Persistence & History
  isDirty: boolean;
  isSaving: boolean;
  saveMessage: string | null;
  annotationLoadStatus: 'ready' | 'loading' | 'error';
  annotationLoadError: string | null;
  loadedAnnotationContext: string | null;
  autoSelectError: string | null;
  history: AnnotationItem[][];
  future: AnnotationItem[][];

  // Actions
  setTask: (task: TaskType) => void;
  setImages: (images: ImageMeta[], initialIndex?: number, externalSelectionPath?: string | null) => Promise<boolean>;
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
  addCategory: (name: string, color?: string, preferredId?: number) => void;
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
  /** Classification: the class chosen for an image (or its folder class again), applied to the open image list. */
  /** `metadata`: the image's row after the class change (a new revision), taken for the image being edited so its next
   *  label save is checked against that revision. */
  applyImageClass: (filePath: string, label: string | null, classSource: 'folder' | 'override', metadata?: ImageReviewMetadata | null) => void;
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

const LEASE_PENDING_MESSAGE = '편집 권한을 받는 중입니다. 받은 뒤 다시 저장하세요.';

export const useAnnotationStore = create<AnnotationState>((set, get) => ({
  externalSelectionPath: null,
  metadata: null,
  reviewerName: typeof localStorage !== 'undefined' ? localStorage.getItem('modu-reviewer-name') || '' : '',
  setReviewerName: (name) => { if (typeof localStorage !== 'undefined') localStorage.setItem('modu-reviewer-name', name); set({ reviewerName: name }); },
  advanceOnBox: (() => { try { return typeof localStorage !== 'undefined' && localStorage.getItem('modu-advance-on-box') === '1'; } catch { return false; } })(),
  setAdvanceOnBox: (on) => { try { if (typeof localStorage !== 'undefined') localStorage.setItem('modu-advance-on-box', on ? '1' : '0'); } catch { /* the choice still holds for this session */ } set({ advanceOnBox: on }); },
  advanceRequest: 0,
  requestAdvance: () => set((state) => ({ advanceRequest: state.advanceRequest + 1 })),
  setMetadata: (metadata) => set({ metadata }),
  teamEditingEnabled:false,
  teamReviewEnabled:false,
  editLease:null,
  editLeaseAcquiring:null,
  labelbookVersion:null,
  paletteContext:null,
  classPicked:false,
  setTeamEditingEnabled:(enabled)=>set({teamEditingEnabled:enabled}),
  setTeamReviewEnabled:(enabled)=>set({teamReviewEnabled:enabled}),
  setEditLease:(editLease)=>set({editLease}),
  // The 'on its way' notice of a save refused meanwhile goes with the request, so a refused 편집 시작 does not leave it behind.
  setEditLeaseAcquiring:(editLeaseAcquiring)=>set(state=>({editLeaseAcquiring,...(!editLeaseAcquiring&&state.saveMessage===LEASE_PENDING_MESSAGE?{saveMessage:null}:{})})),
  // With no book there is nothing to enforce (the panel clears the book on every scope change, often before the first
  // labels load), so the drawing class stays. With one, a save under a class outside it is refused (team_data.py): the
  // drawing class is a book class, the one in use or else the first that is not the OK tag.
  setLabelbook:(version,categories)=>{if(!categories?.length){set({labelbookVersion:version});return;}const next=bookPalette(categories,get().categories);const book=next.slice(0,categories.length);const kept=book.find(row=>row.name===get().activeCategory.name);const active=kept||book.find(pickable)||book.find(row=>row.id>0)||book[0];set({labelbookVersion:version,categories:next,activeCategory:active,currentLabel:active.name,classPicked:get().classPicked&&Boolean(kept)});},
  task: 'detection',
  images: [],
  currentImageIndex: -1,
  currentImage: null,
  activeImage: null,

  annotations: [],
  selectedAnnotationId: null,
  activeTool: 'bbox',
  activeCategory: FIRST_CLASS,
  currentLabel: FIRST_CLASS.name,
  categories: BASE_CATEGORIES,
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
  loadedAnnotationContext: null,
  autoSelectError: null,
  history: [],
  future: [],

  setTask: (task) => set({ task }),

  setImages: async (images, initialIndex = 0, externalSelectionPath = null) => {
    if (get().isDirty) {
      const saved = await get().saveAnnotations();
      if (!saved || get().isDirty) return false;
    }
    const idx = images.length > 0 ? Math.max(0, Math.min(images.length - 1, initialIndex)) : -1;
    const current = idx >= 0 ? images[idx] : null;
    set({
      images,
      ...(!images.length?{teamEditingEnabled:false,teamReviewEnabled:false,editLease:null,editLeaseAcquiring:null,labelbookVersion:null,paletteContext:null,classPicked:false,categories:BASE_CATEGORIES,activeCategory:FIRST_CLASS,currentLabel:FIRST_CLASS.name}:{}),
      externalSelectionPath: current?.file_path === externalSelectionPath ? externalSelectionPath : null,
      metadata: null,
      loadedAnnotationContext: null,
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
    const { currentImage, task, annotationLoadStatus, loadedAnnotationContext } = get();
    const preservedIndex = currentImage
      ? images.findIndex((image) =>
          image.image_id === currentImage.image_id && image.file_path === currentImage.file_path)
      : -1;
    const refreshed = images[preservedIndex];
    if (currentImage && refreshed && annotationLoadStatus === 'ready'
        && loadedAnnotationContext === annotationContext(task)
        && refreshed.width === currentImage.width && refreshed.height === currentImage.height) {
      // A post-save gallery refresh updates counts; it must not replace the editing
      // image, discard a newer draft, or erase the user's undo/redo history.
      set({ images, currentImageIndex: preservedIndex });
      return true;
    }
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
        loadedAnnotationContext: null,
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
      loadedAnnotationContext: null,
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
      loadedAnnotationContext: null,
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
    if (!get().currentImage || get().annotationLoadStatus !== 'ready') return;
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
      ...claimDrawingClass(get(), itemWithId),
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

  setActiveCategory: (cat) => {
    const picked = isStandIn(cat) ? unmarked(cat) : cat;
    set((state) => ({ activeCategory: picked, currentLabel: picked.name, classPicked: true,
      ...(picked !== cat ? { categories: state.categories.map((c) => isStandIn(c) && c.id === cat.id ? picked : c) } : {}) }));
  },

  setCurrentLabel: (label) => {
    const cat = get().categories.find((c) => c.name === label) || {
      id: 99,
      name: label,
      color: '#3b82f6',
    };
    set({ currentLabel: label, activeCategory: cat, classPicked: true });
  },

  addCategory: (name, color, preferredId) => {
    const { categories } = get();
    // The palette merges names ignoring letter case, so a name it already has picks that class (a stand-in becomes real)
    // instead of a second class under another id that the next image would drop.
    const existing = categories.find((c) => c.name.toLowerCase() === name.toLowerCase());
    if (existing) { get().setActiveCategory(existing); return; }
    // An example name keeps its own id while that id is free, so the same example gets the same id in every project.
    const newId = preferredId && preferredId > 0 && !categories.some((c) => c.id === preferredId) ? preferredId
      : categories.length > 0 ? Math.max(...categories.map((c) => c.id)) + 1 : 1;
    const newCat: Category = {
      id: newId,
      name,
      color: color || '#8b5cf6',
    };
    set({
      categories: [...categories, newCat],
      activeCategory: newCat,
      currentLabel: name,
      classPicked: true,
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
      ...claimDrawingClass(get(), updated),
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

  applyImageClass: (filePath, label, classSource, metadata) => {
    const update = (image: ImageMeta) => image.file_path === filePath
      ? { ...image, label, labels: label ? [label] : [], class_source: classSource } : image;
    const { images, currentImage } = get();
    const editing = currentImage?.file_path === filePath && metadata && get().metadata?.image_uuid === metadata.image_uuid;
    set({ images: images.map(update), currentImage: currentImage ? update(currentImage) : currentImage, ...(editing ? { metadata } : {}) });
  },

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
    const authority = annotationContext(get().task);
    const sameAuthority = () => authority === annotationContext(get().task);
    set({ annotationLoadStatus: 'loading', annotationLoadError: null, loadedAnnotationContext: null });

    try {
      const data = await datasetWorkflow.annotations(currentImage.image_id, currentImage.file_path);
      if (!sameAuthority() || requestSequence !== annotationLoadSequence ||
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

      // The project's own classes, most used first, join the palette and the most used one is the default drawing class.
      // The import summary also counts 'defect_mask' (segmentation: mask images) and 'good' (anomaly: normal images);
      // neither is a class to draw. A label book sets the classes itself.
      const counts=useDatasetStore.getState().classes||{};const task=get().task;
      const projectClasses=get().labelbookVersion?[]:Object.keys(counts)
        .filter(name=>!(task==='segmentation'&&name==='defect_mask')&&!(task==='anomaly'&&name==='good')).sort((a,b)=>counts[b]-counts[a]);
      // A palette built for another source folder or task starts fresh, as on a project switch (a label book keeps its own).
      const paletteContext=JSON.stringify([useDatasetStore.getState().folderPath,task]);
      const fresh=!get().labelbookVersion&&get().paletteContext!==null&&get().paletteContext!==paletteContext;
      // The stand-in class of a project without classes leaves once the project has its own, unless this image uses it.
      const standIn=(c:Category)=>isStandIn(c)&&projectClasses.length>0
        &&!projectClasses.some(name=>name.toLowerCase()===c.name.toLowerCase())&&!items.some(a=>a.label===c.name);
      // A stand-in whose name the project or this image now has is a real class: it keeps its id and loses the mark.
      const owned=(c:Category)=>projectClasses.some(name=>name.toLowerCase()===c.name.toLowerCase())||items.some(a=>a.label.toLowerCase()===c.name.toLowerCase());
      const merged=labelCategoryPalette((fresh?BASE_CATEGORIES:get().categories).filter(c=>!standIn(c)).map(c=>isStandIn(c)&&owned(c)?unmarked(c):c),items,data.mask_classes,projectClasses);
      let freeId=1;while(merged.some(c=>c.id===freeId))freeId++;
      const currentCats=merged.some(pickable)?merged:[...merged,{...FIRST_CLASS,id:freeId,[STAND_IN]:true} as Category];
      const named=(name:string)=>currentCats.find(c=>c.id>0&&c.name.toLowerCase()===name.toLowerCase());
      const firstPickable=(names:string[])=>names.map(named).find(c=>c&&pickable(c));
      // A class the person picked (or drew with) stays picked while it exists. Otherwise each image's load picks a defect
      // class: this image's own first (the dataset counts can name a class the masks do not), then the project's most
      // used, never a normal name.
      const current=get().activeCategory;
      // The palette merges names ignoring letter case, so the pick follows its entry the same way.
      const kept=!fresh&&get().classPicked?(currentCats.find(c=>c.name===current.name)||currentCats.find(c=>c.name.toLowerCase()===current.name.toLowerCase())):undefined;
      const own=[...(data.mask_classes||[]).filter((c:{id:number})=>c.id>0).map((c:{name:string})=>c.name),
        ...items.filter(a=>a.type!=='tag'&&a.category_id).map(a=>a.label)];
      const active=kept||firstPickable(own)||firstPickable(projectClasses)||currentCats.find(pickable)||currentCats[0]||current;

      const nextDimensions = data.image_width && data.image_height
        ? { width: data.image_width, height: data.image_height }
        : get().imageDimensions;

      set({
        annotations: items,
        metadata: data.metadata || null,
        categories: currentCats,
        activeCategory: active,
        currentLabel: active.name,
        paletteContext,
        classPicked: Boolean(kept),
        imageDimensions: nextDimensions,
        maskUrl: data.mask_file ? annotationMaskUrl(currentImage) : null,
        isDirty: false,
        annotationLoadStatus: 'ready',
        loadedAnnotationContext: authority,
        annotationLoadError: null,
        saveMessage: null,
      });
      return true;
    } catch (error) {
      if (sameAuthority() && requestSequence === annotationLoadSequence && get().currentImage === currentImage) {
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
    const leaseToken=imageLeaseToken(get().editLease,get().metadata?.image_uuid);
    if(get().teamEditingEnabled&&!leaseToken){
      // 편집 시작 was pressed and its reply is on the way: say so, rather than asking for a press that already happened.
      const acquiring=!!get().editLeaseAcquiring&&get().editLeaseAcquiring?.image_uuid===get().metadata?.image_uuid;
      set({saveMessage:acquiring?LEASE_PENDING_MESSAGE:'팀 작업의 편집 시작을 누른 뒤 저장하세요. 편집 권한이 만료됐으면 다시 시작하세요.'});return Promise.resolve(false);
    }

    set({ isSaving: true, saveMessage: 'Saving...' });

    const save = async (): Promise<boolean> => {
      try {
        const res = await datasetWorkflow.saveAnnotations({
          expected_revision: get().metadata?.revision,
          ...(leaseToken?{lease_token:leaseToken}:{}),
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
            // An adopted draft keeps where it came from; without it the adoption could not be taken back later.
            ...(a.origin ? { origin: a.origin } : {}),
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
        // The route also names the backend it used and says whether it fell back to a small box around the click.
        const detail = res.result as { source?: unknown; fallback?: unknown };
        const newAnn: AnnotationItem = {
          id: `auto_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`,
          type: 'polygon',
          label: activeCategory.name,
          category_id: activeCategory.id,
          color: activeCategory.color,
          polygon: res.result.polygon,
          points: res.result.polygon,
          bbox: res.result.bbox,
          // The auto selector is a drawing aid, not a draft (docs §3.3): the shape joins the canvas and is saved by the
          // person's own save like a drawn one, and its origin says the machine chose the outline. A fallback box is
          // marked so the list and the toolbar can say the selector found nothing instead of passing it off as a shape.
          origin: {
            kind: 'auto_selector',
            backend: typeof detail.source === 'string' && detail.source ? detail.source : 'opencv_flood_fill',
            seed_point: [seedX, seedY],
            tolerance: autoSelectTolerance,
            fallback: detail.fallback === true,
            created_at: new Date().toISOString(),
          },
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
