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
  FlowchartPartialRunSnapshot,
  FlowNode,
  FlowNodeData,
  SelectedInspectionImage,
  VisionTask,
  FlowModelTask,
} from '../types';
import { api, getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration } from '../services/api';
import { flowDraft, type FlowDraft, type FlowDraftContext } from '../services/flowDraft';
import { useProjectStore } from './useProjectStore';
import { getFlowchartModelTask } from '../components/flowchart/flowchartStartup';

let flowchartGeneration = 0;
let flowchartSaveGeneration = 0;
let flowchartRunInputRevision = 0;
let flowchartRunSequence = 0;
let activePartialOwner: { stop: () => Promise<boolean>; refresh: () => Promise<boolean> } | null = null;
const HISTORY_LIMIT = 50;

/** Versions of the edited flow: semantic edits change what it computes, layout
 * edits only move nodes. Results are bound to the semantic key they ran with. */
export interface FlowIdentity { semantic_revision: number; layout_revision: number }
export interface ExecutionIdentity { semantic_key: string; semantic_revision: number }

export interface FlowPartialRunView {
  runId: string | null;
  projectId: string;
  stopNodeId: string;
  stopNodeLabel: string;
  phase: 'preparing' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed' | 'unknown';
  cancelRequested: boolean;
  checking: boolean;
}

const semanticKeys = new WeakMap<FlowchartPipeline, string>();

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value as Record<string, unknown>).sort()
      .map(key => [key, stable((value as Record<string, unknown>)[key])]));
  }
  return value;
}

/** Everything that changes what a flow computes; node positions and the flow's
 * display name are layout. */
export function flowSemanticKey(pipeline: FlowchartPipeline | null): string {
  if (!pipeline) return '';
  const cached = semanticKeys.get(pipeline);
  if (cached !== undefined) return cached;
  const { nodes, edges, name: _name, description: _description, ...rest } = pipeline;
  void _name; void _description;
  const key = JSON.stringify(stable({
    ...rest,
    nodes: [...nodes].map(({ position: _position, ...node }) => node).sort((a, b) => a.id.localeCompare(b.id)),
    edges: [...edges].sort((a, b) => a.id.localeCompare(b.id)),
  }));
  semanticKeys.set(pipeline, key);
  return key;
}

/** A result is current only when it ran with the flow's present semantics. */
export function isExecutionResultCurrent(state: {
  executionResult: FlowchartExecutionResult | null; executionIdentity: ExecutionIdentity | null; pipeline: FlowchartPipeline | null;
}): boolean {
  return Boolean(state.executionResult && state.executionIdentity
    && state.executionIdentity.semantic_key === flowSemanticKey(state.pipeline));
}

function editedIdentity(state: { flowIdentity: FlowIdentity; pipeline: FlowchartPipeline | null }, next: FlowchartPipeline): FlowIdentity {
  return flowSemanticKey(next) === flowSemanticKey(state.pipeline)
    ? { ...state.flowIdentity, layout_revision: state.flowIdentity.layout_revision + 1 }
    : { ...state.flowIdentity, semantic_revision: state.flowIdentity.semantic_revision + 1 };
}

/** A read of the graph keeps the user's selection when that node is still in it: a click must not lose to a
 * draft or version read that lands a moment later. A node that is gone clears the selection as before. */
function keptSelection(state: { selectedNodeId: string | null }, pipeline: { nodes: Array<{ id: string }> }): string | null {
  const id = state.selectedNodeId;
  return id && pipeline.nodes.some(node => node.id === id) ? id : null;
}

function loadedIdentity(state: { flowIdentity: FlowIdentity }): Pick<FlowchartState, 'flowIdentity' | 'executionIdentity'> {
  return { flowIdentity: { semantic_revision: state.flowIdentity.semantic_revision + 1, layout_revision: 0 }, executionIdentity: null };
}

interface FlowchartState {
  executionChoiceOverride: FlowExecutionChoice | null;
  setExecutionChoice: (choice: FlowExecutionChoice | null) => void;
  pipeline: FlowchartPipeline | null;
  executionResult: FlowchartExecutionResult | null;
  executionIdentity: ExecutionIdentity | null;
  flowIdentity: FlowIdentity;
  lastRunSource: { kind: 'draft' | 'saved'; versionId: string | null } | null;
  isLoading: boolean;
  isSaving: boolean;
  isRunning: boolean;
  partialRun: FlowPartialRunView | null;
  activeRunningNodeId: string | null;
  selectedNodeId: string | null;
  pipelineDirty: boolean;
  pipelineIsDraft: boolean;
  persistedDraftHash: string | null;
  /** The active version this editor started from (null: there was none; undefined: unknown, as in a draft of an older
   * editor). A save or activation is checked against it, so a change made elsewhere meanwhile is never overwritten. */
  baseVersionId: string | null | undefined;
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
  loadPipeline: (force?: boolean, inspectionTask?: VisionTask, sourceDatasetPath?: string, canPublish?: () => boolean) => Promise<FlowchartPipeline | null>;
  loadPipelineVersion: (versionId: string, sourceDatasetPath: string) => Promise<FlowchartPipeline | null>;
  loadSingleSegmentationTemplate: (jobId?: string, inspectionTask?: VisionTask) => Promise<void>;
  loadDetectorRoiTemplate: (inspectionTask: Exclude<VisionTask, 'detection'>) => Promise<void>;
  savePipeline: (customPipeline?: FlowchartPipeline, recipeTask?: FlowModelTask | 'mixed', sourceDatasetPath?: string,
    change?: { changeReason?: string }) => Promise<boolean>;
  setBaseVersionId: (versionId: string | null | undefined) => void;
  saveDraft: (isCurrent?: () => boolean) => Promise<boolean>;
  loadDraft: () => Promise<FlowchartPipeline | null>;
  runPipeline: (customImagePath?: string, customImageId?: string,
    source?: { savedVersionId: string | null; executionTarget?: 'local' | 'selected_compute' | 'model_compute';
      device?: 'cpu' | 'mps' | 'cuda'; computeProfileId?: string; stopNodeId?: string }) => Promise<boolean>;
  cancelPartialRun: () => Promise<boolean>;
  refreshPartialRun: () => Promise<boolean>;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, patch: Partial<FlowNodeData>) => void;
  addNode: (node: FlowNode) => void;
  replacePipeline: (pipeline: FlowchartPipeline, newBasis?: { baseVersionId: string | null }) => void;
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

function draftBase(draft: FlowDraft): string | null | undefined {
  // The same response proves this draft equals the active saved graph.
  if (typeof draft.active_version_id === 'string' && /^[a-f0-9]{32}$/.test(draft.active_version_id)) return draft.active_version_id;
  return draft.base_version_id === 'none' ? null : draft.base_version_id || undefined;
}

/** The project's active version now (undefined when it cannot be read: the save is then not checked). */
async function activeBase(): Promise<string | null | undefined> {
  try { return (await api.flowchart.activeVersionId()).version_id ?? null; } catch { return undefined; }
}

// A startup shortcut needs the actual saved-record read, not an unrelated base ID.
let savedStartupRead: { pipeline: FlowchartPipeline; version: string; scope: string; graph: string; identity: FlowIdentity } | null = null;
function savedStartupScope(task?: VisionTask, source?: string): string | null {
  try {
    const state = useProjectStore.getState(), project = state.project;
    if (!project || !source || !task || state.isProjectBusy || state.task !== task || project.task !== task
      || state.projectDir !== project.project_dir || project.source_dataset_dir !== source) return null;
    return JSON.stringify([project.id, project.project_dir, project.active_labelset_id || 'default', task, source,
      getProjectContextGeneration(), getApiPersistenceIdentity(), getProjectContext()]);
  } catch { return null; }
}
function rememberSavedStartupRead(pipeline: FlowchartPipeline, version: unknown, scope: string | null) {
  savedStartupRead = null;
  if (scope && typeof version === 'string' && /^[a-f0-9]{32}$/.test(version)) {
    try { savedStartupRead = {pipeline, version, scope, graph: JSON.stringify(pipeline), identity: useFlowchartStore.getState().flowIdentity}; } catch { /* No shortcut proof. */ }
  }
}
export function isCurrentSavedPipeline(pipeline: FlowchartPipeline, task: VisionTask, source: string): boolean {
  const state = useFlowchartStore.getState(), read = savedStartupRead;
  try {
    return Boolean(read && read.pipeline === pipeline && state.pipeline === pipeline && state.cleanPipeline === pipeline
      && !state.pipelineDirty && !state.pipelineIsDraft && !state.isLoading && !state.isSaving && !state.isRunning
      && !state.historyGroupStart && state.flowIdentity === read.identity && state.baseVersionId === read.version && savedStartupScope(task, source) === read.scope
      && JSON.stringify(pipeline) === read.graph);
  } catch { return false; }
}

/** The refusal of a save or activation based on a version that is no longer active (not another 409, such as a
 * changed dataset). */
export function isStaleBaseError(error: unknown): boolean {
  const value = error as { status?: number; message?: string } | null;
  return value?.status === 409 && /active flow changed since|적용 중인 플로우가 바뀌었습니다/.test(String(value?.message ?? ''));
}

export const STALE_SAVE_MESSAGE = '이 편집을 시작한 뒤 다른 곳에서 활성 플로우가 바뀌었습니다. 편집 내용은 그대로 있습니다. 활성 버전을 다시 열어 확인한 뒤 저장하세요.';

/** Own one explicit CPU partial request. A cancel acknowledgement never settles
 * the original Run, and execution_finished is not native/launch-lease closure. */
async function runOwnedCPUPartial(
  request: Parameters<typeof api.flowchart.preparePartialRun>[0],
  sequence: number,
  ranWith: ExecutionIdentity,
  savedVersionId: string | null,
  inputCurrent: () => boolean,
  discardStaleRun: () => void,
  set: (next: Partial<FlowchartState>) => void,
): Promise<boolean> {
  const context = getProjectContext();
  const epoch = getProjectContextGeneration();
  const transport = getApiPersistenceIdentity();
  let runId: string | null = null;
  let stopRequested = false, runSettled = false, finished = false;
  let runResult: FlowchartExecutionResult | null = null;
  let runError: unknown = null;
  let cancelFlight: Promise<void> | null = null;
  let stopCompletion: Promise<boolean> | null = null;
  let refreshFlight: Promise<boolean> | null = null;
  let preparedDone: () => void = () => {};
  let originalDone: () => void = () => {};
  const preparation = new Promise<void>(resolve => { preparedDone = resolve; });
  const original = new Promise<void>(resolve => { originalDone = resolve; });
  const owner = { stop: () => stop(), refresh: () => refresh() };
  activePartialOwner = owner;
  const current = () => activePartialOwner === owner && sequence === flowchartRunSequence
    && useProjectStore.getState().project?.id === request.project_id
    && getProjectContextGeneration() === epoch && getApiPersistenceIdentity() === transport;
  const commit = (next: Partial<FlowchartState>) => { if (current()) set(next); };
  const view = (phase: FlowPartialRunView['phase'], checking = false) => {
    commit({ partialRun: { runId, projectId: request.project_id, stopNodeId: request.stop_node_id,
      stopNodeLabel: request.pipeline.nodes.find(node => node.id === request.stop_node_id)?.data.label || '',
      phase, cancelRequested: stopRequested, checking } });
  };
  const matches = (value: FlowchartPartialRunSnapshot, preparing = false) => Boolean(value
    && typeof value.run_id === 'string' && /^[0-9a-f]{32}$/.test(value.run_id)
    && (preparing || value.run_id === runId) && value.project_id === request.project_id
    && value.stop_node_id === request.stop_node_id && value.execution_target === 'local' && value.device === 'cpu'
    && typeof value.execution_finished === 'boolean');
  const noResult = { executionResult: null, executionIdentity: null, lastRunSource: null, inspectedCrop: null,
    activeRunningNodeId: null };
  const uncertain = () => {
    view('unknown');
    commit({ ...noResult, isRunning: true,
      errorMessage: '부분 실행의 종료 상태를 확인하지 못했습니다. 중단 상태를 확인한 뒤 다시 실행하세요.' });
    return false;
  };

  async function refresh(): Promise<boolean> {
    if (!current() || !runId || !runSettled) return false;
    if (refreshFlight) return refreshFlight;
    if (finished) return false;
    const checking = (async () => {
      view(stopRequested ? 'cancelling' : 'running', true);
      try {
        if (!current()) return false;
        const terminal = await api.flowchart.getPartialRun(runId!, context);
        if (!current()) return false;
        if (!matches(terminal) || terminal.execution_finished !== true
            || !['cancelled', 'completed', 'failed'].includes(terminal.state)) return uncertain();
        if (terminal.state === 'cancelled') {
          finished = true; view('cancelled');
          commit({ ...noResult, isRunning: false, errorMessage: null });
          return false;
        }
        if (terminal.state === 'failed') {
          finished = true; view('failed');
          commit({ ...noResult, isRunning: false,
            errorMessage: runError instanceof Error ? runError.message : '부분 실행 중 오류가 발생했습니다.' });
          return false;
        }
        if (!runResult) {
          // The server finished, but a lost HTTP body is not a result to adopt.
          finished = true; view('completed');
          commit({ ...noResult, isRunning: false,
            errorMessage: '부분 실행은 종료됐지만 결과를 받지 못했습니다. 입력을 확인한 뒤 다시 실행하세요.' });
          return false;
        }
        if (runResult.status !== 'partial' || runResult.partial_run_id !== runId
            || runResult.stop_node_id !== request.stop_node_id || runResult.execution_target !== 'local'
            || runResult.execution_device !== 'cpu' || runResult.compute_profile_id != null
            || runResult.final_verdict !== 'REVIEW' || runResult.is_ok !== false) return uncertain();
        finished = true; view('completed');
        if (!current()) return false;
        if (!inputCurrent()) { discardStaleRun(); return false; }
        commit({ executionResult: runResult, executionIdentity: ranWith,
          lastRunSource: savedVersionId ? { kind: 'saved', versionId: savedVersionId } : { kind: 'draft', versionId: null },
          isRunning: false, activeRunningNodeId: null, errorMessage: null });
        return true;
      } catch {
        return current() ? uncertain() : false;
      }
    })();
    refreshFlight = checking;
    try { return await checking; }
    finally { if (refreshFlight === checking) refreshFlight = null; }
  }

  function stop(): Promise<boolean> {
    if (stopCompletion) return stopCompletion;
    if (!current() || finished) return Promise.resolve(false);
    stopRequested = true; view('cancelling');
    cancelFlight = (async () => {
      await preparation;
      if (!current() || !runId || finished) return;
      try {
        const ack = await api.flowchart.cancelPartialRun(runId, context);
        if (current() && (!matches(ack) || !['cancelling', 'cancelled'].includes(ack.state))) uncertain();
      } catch {
        // A 409 may mean completion won. Any other transport error is also
        // reconciled through the same terminal GET, never treated as stopped.
      }
    })();
    stopCompletion = (async () => { await cancelFlight; await original; return refresh(); })();
    return stopCompletion;
  }

  view('preparing');
  commit({ executionIdentity: null });
  try {
    if (!current()) return false;
    const prepared = await api.flowchart.preparePartialRun(request, context);
    if (!current()) return false;
    if (!matches(prepared, true) || prepared.state !== 'prepared' || prepared.execution_finished !== false)
      throw new Error('부분 실행의 프로젝트·노드·실행 위치를 확인할 수 없습니다. 다시 확인하세요.');
    runId = prepared.run_id;
  } catch (cause) {
    if (current()) {
      finished = true; view('failed');
      commit({ ...noResult, isRunning: false, errorMessage: cause instanceof Error ? cause.message : '부분 실행을 준비하지 못했습니다.' });
    }
    return false;
  } finally {
    preparedDone();
    if (!runId) { runSettled = true; originalDone(); }
  }
  if (!current()) { runSettled = true; originalDone(); return false; }
  if (!inputCurrent() && !stopRequested) {
    // No admitted Run exists. Do not start one with an obsolete image/graph.
    runSettled = true; originalDone();
    view('preparing');
    commit({ ...noResult, isRunning: true,
      errorMessage: '입력이 변경되어 부분 실행을 시작하지 않았습니다. 준비된 부분 실행을 중단한 뒤 다시 실행하세요.' });
    return false;
  }
  if (!stopRequested) {
    view('running');
    try {
      if (!current()) return false;
      runResult = await api.flowchart.run({ ...request, partial_run_id: runId! }, context);
    }
    catch (cause) { runError = cause; }
    finally { runSettled = true; originalDone(); }
  } else {
    // Stop intent arrived before prepare returned; never consume that ID in Run.
    runSettled = true; originalDone();
  }
  if (cancelFlight) await cancelFlight;
  return refresh();
}

export const useFlowchartStore = create<FlowchartState>((set, get) => ({
  executionChoiceOverride: null,
  setExecutionChoice: (choice) => { get().resetExecution(); set({ executionChoiceOverride: choice }); },
  pipeline: null,
  executionResult: null,
  executionIdentity: null,
  flowIdentity: { semantic_revision: 0, layout_revision: 0 },
  lastRunSource: null,
  isLoading: false,
  isSaving: false,
  isRunning: false,
  partialRun: null,
  activeRunningNodeId: null,
  selectedNodeId: null,
  pipelineDirty: false,
  pipelineIsDraft: false,
  persistedDraftHash: null,
  baseVersionId: undefined,
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

  loadPipeline: async (force = false, inspectionTask, sourceDatasetPath, canPublish) => {
    if (!force && get().pipeline) return get().pipeline;
    if (canPublish && !canPublish()) return null;
    savedStartupRead = null;
    const savedScope = savedStartupScope(inspectionTask, sourceDatasetPath);
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      let data: FlowchartPipeline;
      let savedRecordVersion: string | null = null;
      const context = draftContext();
      if (context && (!sourceDatasetPath || context.source_dataset_path === sourceDatasetPath)) {
        try {
          const draft = await flowDraft.get();
          if (generation !== flowchartGeneration || !sameContext(context, draftContext()) || (canPublish && !canPublish())) return null;
          if (!sameContext(context, draft.context)) throw new Error('저장 초안의 프로젝트 또는 라벨셋이 다릅니다.');
          if (!/^[a-f0-9]{64}$/.test(draft.draft_sha256)) throw new Error('저장 초안의 검증 해시를 확인하세요.');
          data = draft.pipeline;
          set({ ...loadedIdentity(get()), pipeline: data, cleanPipeline: data, pipelineIsDraft: !draft.active_version_id,
            persistedDraftHash: draft.draft_sha256, baseVersionId: draftBase(draft),
            historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
            pipelineDirty: false, selectedNodeId: keptSelection(get(), data), executionResult: null, lastRunSource: null,
            inspectedCrop: null, isLoading: false });
          rememberSavedStartupRead(data, draft.active_version_id, savedScope);
          return data;
        } catch (error) {
          if ((error as { status?: number }).status !== 404) throw error;
        }
      }
      let base: string | null | undefined;
      if (sourceDatasetPath) {
        try {
          const record = await api.flowchart.getActivePipelineRecord(sourceDatasetPath);
          data = record.pipeline;
          base = record.version_id;
          savedRecordVersion = record.version_id;
        } catch (error) {
          if ((error as { status?: number }).status !== 404) throw error;
          data = await api.flowchart.getPipeline(inspectionTask, sourceDatasetPath);
          base = await activeBase();
        }
      } else {
        data = await api.flowchart.getPipeline(inspectionTask, sourceDatasetPath);
      }
      if (generation !== flowchartGeneration || (canPublish && !canPublish())) return null;
      set({
        ...loadedIdentity(get()),
        pipeline: data,
        cleanPipeline: data,
        baseVersionId: base,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        pipelineDirty: false,
        pipelineIsDraft: false,
        persistedDraftHash: null,
        selectedNodeId: keptSelection(get(), data),
        executionResult: null,
        inspectedCrop: null,
        isLoading: false,
      });
      rememberSavedStartupRead(data, savedRecordVersion, savedScope);
      return data;
    } catch (e: any) {
      if (generation !== flowchartGeneration || (canPublish && !canPublish())) return null;
      console.error('Failed to load flowchart pipeline:', e);
      set({ isLoading: false, errorMessage: e?.message || '파이프라인 로드 실패' });
      return null;
    } finally {
      if (canPublish && generation === flowchartGeneration && get().isLoading) set({ isLoading: false });
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
      if (!/^[a-f0-9]{64}$/.test(draft.draft_sha256)) throw new Error('저장 초안의 검증 해시를 확인하세요.');
      set({ ...loadedIdentity(get()), pipeline: draft.pipeline, cleanPipeline: draft.pipeline, pipelineIsDraft: !draft.active_version_id,
        persistedDraftHash: draft.draft_sha256, baseVersionId: draftBase(draft),
        pipelineDirty: false, historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        executionResult: null, lastRunSource: null, inspectedCrop: null, selectedNodeId: keptSelection(get(), draft.pipeline), errorMessage: null });
      return draft.pipeline;
    } catch (error) {
      if (generation === flowchartGeneration && (error as { status?: number }).status !== 404)
        set({ errorMessage: error instanceof Error ? error.message : '초안을 불러오지 못했습니다.' });
      return null;
    }
  },

  saveDraft: async (isCurrent) => {
    const target = get().pipeline;
    const context = draftContext();
    if (!target || !context || get().isSaving || get().isRunning || get().isLoading || get().historyGroupStart || (isCurrent && !isCurrent())) return false;
    const generation = flowchartGeneration;
    const saveGeneration = ++flowchartSaveGeneration;
    set({ isSaving: true, errorMessage: null, saveMessage: null });
    try {
      const base = get().baseVersionId;
      const saved = await flowDraft.save(target, context, base === undefined ? undefined : base ?? 'none');
      if (isCurrent && !isCurrent()) return false;
      const readback = await flowDraft.get();
      if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration
          || get().pipeline !== target || !sameContext(context, draftContext()) || (isCurrent && !isCurrent())) return false;
      if (!sameContext(context, saved.context) || !sameContext(context, readback.context)
          || !/^[a-f0-9]{64}$/.test(saved.draft_sha256) || saved.draft_sha256 !== readback.draft_sha256)
        throw new Error('초안 저장 내용을 다시 확인하지 못했습니다.');
      set({ cleanPipeline: target, pipelineDirty: false, pipelineIsDraft: !saved.active_version_id,
        persistedDraftHash: saved.draft_sha256,
        saveMessage: '편집 초안이 저장되었습니다.' });
      return true;
    } catch (error) {
      if (generation === flowchartGeneration && saveGeneration === flowchartSaveGeneration && (!isCurrent || isCurrent()))
        set({ errorMessage: error instanceof Error ? error.message : '초안 저장 실패' });
      return false;
    } finally {
      if (saveGeneration === flowchartSaveGeneration) set({ isSaving: false });
    }
  },

  loadPipelineVersion: async (versionId, _sourceDatasetPath) => {
    const generation = ++flowchartGeneration;
    set({ isLoading: true, errorMessage: null });
    try {
      const data = await api.flowchart.getPipelineVersion(versionId);
      const base = await activeBase();
      if (generation !== flowchartGeneration) return null;
      flowchartRunInputRevision += 1;
      set({
        ...loadedIdentity(get()),
        pipeline: data, cleanPipeline: data, pipelineIsDraft: false, baseVersionId: base,
        persistedDraftHash: null,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
        pipelineDirty: false, selectedNodeId: keptSelection(get(), data),
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
        ...loadedIdentity(get()),
        pipeline,
        cleanPipeline: jobId ? null : pipeline, pipelineIsDraft: true,
        persistedDraftHash: null,
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
        ...loadedIdentity(get()),
        pipeline,
        cleanPipeline: pipeline, pipelineIsDraft: true,
        persistedDraftHash: null,
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

  savePipeline: async (customPipeline, recipeTask, sourceDatasetPath, change) => {
    if (get().isSaving) return false;
    const target = customPipeline || get().pipeline;
    if (!target) return false;
    const generation = flowchartGeneration;
    const saveGeneration = ++flowchartSaveGeneration;
    set({ isSaving: true, errorMessage: null, saveMessage: null });
    try {
      const saved = await api.flowchart.savePipeline(target, recipeTask, sourceDatasetPath,
        { changeReason: change?.changeReason, expectedVersionId: get().baseVersionId });
      if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration) return false;
      // The server stores the flow with its defaults filled in (null branch and predicate fields, node type, threshold,
      // padding, execution limits). A graph built here (a recipe, a palette node) then never equals its saved version,
      // so the editor adopts the stored version when nothing was edited since: the same rules, now equal to what was saved.
      let stored: FlowchartPipeline | null = null;
      if (get().pipeline === target && saved?.version_id) {
        try { stored = await api.flowchart.getPipelineVersion(saved.version_id); } catch { stored = null; }
        if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration) return false;
      }
      const currentVersionSaved = get().pipeline === target;
      const adopted = currentVersionSaved && stored ? stored : null;
      // A run made with the saved rules stays current: only the server's defaults were added.
      const keepRun = adopted && isExecutionResultCurrent(get()) ? get().executionIdentity : null;
      set({
        ...(adopted ? { pipeline: adopted, executionIdentity: keepRun ? { ...keepRun, semantic_key: flowSemanticKey(adopted) } : get().executionIdentity } : {}),
        cleanPipeline: currentVersionSaved ? adopted || target : get().cleanPipeline,
        pipelineDirty: currentVersionSaved ? false : get().pipelineDirty,
        pipelineIsDraft: currentVersionSaved ? false : get().pipelineIsDraft,
        persistedDraftHash: currentVersionSaved ? null : get().persistedDraftHash,
        // The saved version is active now: the next save is based on it.
        baseVersionId: saved?.version_id ?? get().baseVersionId,
        saveMessage: currentVersionSaved
          ? '파이프라인이 저장되었습니다.'
          : '이전 버전이 저장되었습니다. 새 변경 사항은 미저장입니다.',
      });
      setTimeout(() => set({ saveMessage: null }), 3000);
      return currentVersionSaved;
    } catch (e: any) {
      if (generation !== flowchartGeneration || saveGeneration !== flowchartSaveGeneration) return false;
      console.error('Failed to save flowchart pipeline:', e);
      set({ errorMessage: isStaleBaseError(e) ? STALE_SAVE_MESSAGE : e?.recovery_incomplete
        ? '저장 복구가 완료되지 않았습니다. 현재 활성 플로우와 저장 버전을 다시 확인하세요.'
        : e?.message || '파이프라인 저장 실패' });
      return false;
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
    const scope = new Set<string>(source?.stopNodeId ? [source.stopNodeId] : pipeline.nodes.map(node=>node.id));
    if (source?.stopNodeId) {
      if (!pipeline.nodes.some(node=>node.id===source.stopNodeId)) {set({errorMessage:'실행할 노드를 다시 선택하세요.'});return false;}
      const pending=[source.stopNodeId];
      while(pending.length) {
        const id=pending.pop();
        for(const edge of pipeline.edges) if(edge.target===id&&!scope.has(edge.source)){scope.add(edge.source);pending.push(edge.source);}
      }
    }
    if (pipeline.nodes.some((node) => scope.has(node.id) &&
      getFlowchartModelTask(node) !== null && !node.data.model_job_id
    )) {
      set({ errorMessage: '검사를 실행하려면 검사 노드에 학습 모델 작업 ID를 지정해야 합니다.' });
      return false;
    }

    const savedVersionId = !get().pipelineDirty && !get().pipelineIsDraft && source?.savedVersionId ? source.savedVersionId : null;
    const generation = ++flowchartGeneration;
    const inputRevision = flowchartRunInputRevision;
    const runSequence = ++flowchartRunSequence;
    // The result is bound to the semantics it ran with; later edits mark it earlier.
    const ranWith: ExecutionIdentity = { semantic_key: flowSemanticKey(pipeline), semantic_revision: get().flowIdentity.semantic_revision };
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
    set({ isRunning: true, partialRun: null, errorMessage: null, executionResult: null, lastRunSource: null,
      inspectedCrop: null, activeRunningNodeId: null });

    if (source?.stopNodeId && source.executionTarget === 'local' && source.device === 'cpu' && !source.computeProfileId) {
      if (!requestedProjectId) { set({ isRunning: false, errorMessage: '현재 프로젝트를 다시 확인하세요.' }); return false; }
      // Freeze once: prepare and Run must serialize the same original input.
      const request = JSON.parse(JSON.stringify({ image_path: imagePath, image_id: imageId ?? null, pipeline,
        project_id: requestedProjectId, stop_node_id: source.stopNodeId, execution_target: 'local', device: 'cpu' })) as Parameters<typeof api.flowchart.preparePartialRun>[0];
      return runOwnedCPUPartial(request, runSequence, ranWith, savedVersionId,
        () => generation === flowchartGeneration && inputRevision === flowchartRunInputRevision
          && flowSemanticKey(get().pipeline) === ranWith.semantic_key,
        discardStaleRun, set);
    }

    try {
      const res = await api.flowchart.run({
        image_path: imagePath,
        image_id: imageId ?? null,
        ...(source?.stopNodeId ? {stop_node_id:source.stopNodeId} : {}),
        pipeline: pipeline || undefined,
        ...(requestedProjectId ? { project_id: requestedProjectId } : {}),
        ...(source?.executionTarget ? { execution_target: source.executionTarget, device: source.device || 'cpu',
          ...(source.computeProfileId ? { compute_profile_id: source.computeProfileId } : {}) } : {}),
      });
      if (source?.stopNodeId && (res.stop_node_id !== source.stopNodeId || res.status !== 'partial')) throw new Error('선택 노드와 다른 실행 결과가 반환되었습니다. 다시 확인하세요.');
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
        executionIdentity: ranWith,
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

  cancelPartialRun: () => activePartialOwner?.stop() ?? Promise.resolve(false),
  refreshPartialRun: () => activePartialOwner?.refresh() ?? Promise.resolve(false),

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
    const next = { ...current, nodes: nextNodes };
    // The earlier result stays visible as an earlier version (isExecutionResultCurrent).
    set({
      ...editedHistory(state),
      flowIdentity: editedIdentity(state, next),
      pipeline: next,
      pipelineDirty: true,
      inspectedCrop: null,
    });
  },

  addNode: (newNode) => {
    const state = get();
    const current = state.pipeline;
    if (!current) return;
    const next = { ...current, nodes: [...current.nodes, newNode] };
    set({
      ...editedHistory(state),
      flowIdentity: editedIdentity(state, next),
      pipeline: next,
      pipelineDirty: true,
      inspectedCrop: null,
    });
  },

  replacePipeline: (pipeline, newBasis) => {
    const state = get();
    if (!state.pipeline || (state.pipeline === pipeline && !newBasis)) return;
    const selectedNodeId = state.selectedNodeId;
    set({
      // An explicit recipe starts a new edit basis. Its previous stale graph must
      // not be restored by undo while retaining the newly observed active base.
      ...(newBasis ? {
        baseVersionId: newBasis.baseVersionId,
        historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
      } : editedHistory(state)),
      flowIdentity: editedIdentity(state, pipeline),
      pipeline,
      pipelineDirty: true,
      persistedDraftHash: null,
      selectedNodeId: selectedNodeId && pipeline.nodes.some((node) => node.id === selectedNodeId) ? selectedNodeId : null,
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
    const next = { ...current, nodes: nextNodes };
    // Moving a node is a layout edit: results and running inspections stay valid.
    set({
      ...editedHistory(state),
      flowIdentity: editedIdentity(state, next),
      pipeline: next,
      pipelineDirty: true,
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
    set({
      flowIdentity: editedIdentity(state, previous),
      pipeline: previous, pipelineDirty: previous !== state.cleanPipeline,
      historyPast, historyFuture, canUndo: historyPast.length > 0, canRedo: true,
      selectedNodeId: state.selectedNodeId && previous.nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId : null,
      inspectedCrop: null, errorMessage: null, saveMessage: null,
    });
  },
  redo: () => {
    get().endHistoryGroup();
    const state = get();
    if (!state.pipeline || !state.historyFuture.length) return;
    const next = state.historyFuture[state.historyFuture.length - 1];
    const historyFuture = state.historyFuture.slice(0, -1);
    const historyPast = [...state.historyPast, state.pipeline].slice(-HISTORY_LIMIT);
    set({
      flowIdentity: editedIdentity(state, next),
      pipeline: next, pipelineDirty: next !== state.cleanPipeline,
      historyPast, historyFuture, canUndo: true, canRedo: historyFuture.length > 0,
      selectedNodeId: state.selectedNodeId && next.nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId : null,
      inspectedCrop: null, errorMessage: null, saveMessage: null,
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

  setBaseVersionId: (versionId) => set({ baseVersionId: versionId }),

  invalidateForDataChange: () => {
    flowchartGeneration += 1;
    flowchartSaveGeneration += 1;
    flowchartRunInputRevision += 1;
    flowchartRunSequence += 1;
    set({
      ...loadedIdentity(get()),
      pipeline: null,
      cleanPipeline: null,
      historyPast: [], historyFuture: [], historyGroupStart: null, canUndo: false, canRedo: false,
      pipelineDirty: false,
      pipelineIsDraft: false,
      persistedDraftHash: null,
      baseVersionId: undefined,
      modelContextInvalidated: true,
      contextRevision: get().contextRevision + 1,
      executionResult: null, lastRunSource: null, isLoading: false, isSaving: false, isRunning: false, partialRun: null,
      activeRunningNodeId: null, selectedNodeId: null,
      selectedImage: null, isImagePickerOpen: false, inspectedCrop: null,
      saveMessage: null, errorMessage: null,
    });
  },
}));
