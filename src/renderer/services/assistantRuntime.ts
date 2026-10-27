/** The assistant's tools wired to the app's real stores and services. Every write goes through the same store action or
 * service call the screen uses (and so the same backend role checks); the context is the footer's published picture
 * reduced by assistantContext.ts. */
import { currentAssistContext } from '../stores/assistContext';
import { useProjectStore } from '../stores/useProjectStore';
import { useDatasetStore } from '../stores/useDatasetStore';
import { useTrainingStore } from '../stores/useTrainingStore';
import { useAnnotationStore } from '../stores/useAnnotationStore';
import { useComputeStore } from '../stores/useComputeStore';
import { computeSampleVerdict, currentEvaluationGuidanceSource, isDefectLabel, isNormalLabel, useEvaluationStore } from '../stores/useEvaluationStore';
import { draftReviewScope, useDraftReviewStore } from '../stores/useDraftReviewStore';
import { bumpWorkflowEvidence } from '../stores/workflowEvidence';
import { focusWorkflowTarget, type WorkflowStep } from '../components/wizard/workflowReadiness';
import { api, getApiPersistenceIdentity, getProjectContextGeneration, getProjectContext, request, type LabelSuggestion } from './api';
import { fileName, minimalAssistContext, type AssistantFacts } from './assistantContext';
import { createAssistantTools, requireToolCurrent, ToolRefusal, type DraftQueueOrder, type ImageChoice, type ToolCurrent, type ToolDeps, type AssistantTaskToolDeps } from './assistantTools';
import { orderDrafts, topConfidence } from '../components/labeling/draftReview';
import { evaluationGuidance, preparationEvidence } from './assistantGuidance';

export function assistantFacts(): AssistantFacts {
  const project = useProjectStore.getState(), data = useDatasetStore.getState();
  let shared = false;
  try { shared = getApiPersistenceIdentity() !== 'local'; } catch { shared = true; }
  return {
    task: project.task ?? null, view: project.viewMode === 'full' ? 'full' : 'simple', shared,
    images: data.hasSelectedFolder ? data.totalImages : 0, unlabeled: data.hasSelectedFolder ? data.unlabeledImages : 0,
    classes: data.hasSelectedFolder ? { ...(data.classes || {}) } : {}, split: { ...data.split }, splitState: data.splitState ?? null,
    trainingStatus: useTrainingStore.getState().status ?? null, currentImage: useAnnotationStore.getState().currentImage?.file_path ?? null,
  };
}

export const assistantContext = () => minimalAssistContext(currentAssistContext(), assistantFacts());

const imageChoice = (image: { file_path: string; label?: string | null; folder_label?: string | null; class_source?: string | null }): ImageChoice => ({
  path: image.file_path, name: fileName(image.file_path), currentClass: image.label ?? null,
  folderClass: image.folder_label ?? (image.class_source === 'override' ? null : image.label ?? null),
});

/** Local authority stays local: it is neither tool output nor provider context. A-B-A changes also invalidate it. */
function currentRuntimeScope() {
  const project = useProjectStore.getState(), data = useDatasetStore.getState(), compute = useComputeStore.getState(), training = useTrainingStore.getState();
  return JSON.stringify([project.projectDir, project.project?.id, project.project?.source_dataset_dir, project.project?.active_labelset_id || 'default', project.task,
    project.isProjectBusy, data.folderPath, data.datasetKey, data.hasSelectedFolder, data.isLoading, data.isSplitting, data.importError,
    compute.selectedProfileId, compute.profiles.find(row => row.id === compute.selectedProfileId) || null, compute.transportRevision, training.preset, training.nextDevice, training.nextBatchSize]);
}
function runtimeGuard(isCurrent?: ToolCurrent) {
  requireToolCurrent(isCurrent);
  const project = useProjectStore.getState(), data = useDatasetStore.getState(), context = getProjectContext();
  if (!project.projectDir || !project.project || project.isProjectBusy || !data.hasSelectedFolder || data.isLoading || data.isSplitting || data.importError
    || project.project.source_dataset_dir !== data.folderPath || data.datasetKey !== `${data.folderPath}\0${project.task}`
    || project.project.task !== project.task || (context && context.project_id !== project.project.id))
    throw new ToolRefusal('context_changed', '현재 프로젝트의 원본 가져오기가 끝나지 않았습니다. 1단계에서 현재 자료를 확인하세요.');
  const scope = currentRuntimeScope(), epoch = getProjectContextGeneration(), transport = getApiPersistenceIdentity(), authority = JSON.stringify(context);
  return () => {
    requireToolCurrent(isCurrent);
    if (currentRuntimeScope() !== scope || getProjectContextGeneration() !== epoch || getApiPersistenceIdentity() !== transport || JSON.stringify(getProjectContext()) !== authority)
      throw new ToolRefusal('context_changed', '점검하는 동안 프로젝트·원본·라벨 세트·연결 또는 설정이 바뀌었습니다. 다시 요청하세요.');
  };
}
/** A completed write changes counts, metadata and consumed settings itself. Keep its authority, not that old content. */
function runtimeMutationGuard(isCurrent?: ToolCurrent) {
  runtimeGuard(isCurrent); // the same full preflight and coherent imported-source requirement
  const identity = () => {
    const project = useProjectStore.getState(), data = useDatasetStore.getState(), compute = useComputeStore.getState();
    return JSON.stringify([project.projectDir, project.project?.id, project.project?.source_dataset_dir, project.project?.active_labelset_id || 'default', project.task,
      data.folderPath, data.datasetKey, data.hasSelectedFolder, compute.selectedProfileId, compute.profiles.find(row => row.id === compute.selectedProfileId) || null, compute.transportRevision,
      getProjectContextGeneration(), getApiPersistenceIdentity(), getProjectContext()]);
  };
  const authority = identity();
  return () => {
    requireToolCurrent(isCurrent?.afterMutation || isCurrent);
    if (identity() !== authority) throw new ToolRefusal('context_changed', '실행하는 동안 프로젝트·원본·라벨 세트·연결 또는 실행 위치가 바뀌었습니다. 현재 화면에서 다시 확인하세요.');
  };
}
const draftRow = (draft: LabelSuggestion) => ({id: draft.id, image: fileName(draft.image_path), source: draft.backend || 'trained_model', candidate_count: draft.candidates.length, confidence: topConfidence(draft)});
async function pendingRows(order: DraftQueueOrder, isCurrent?: ToolCurrent) {
  const check = runtimeGuard(isCurrent);
  const listed = await api.labelSuggestions.list();check();
  return {check, drafts: orderDrafts(listed.suggestions.filter(row => row.candidates.length > 0), order)};
}

export const runtimeToolDeps: ToolDeps = {
  snapshot: currentAssistContext,
  facts: assistantFacts,
  setStep: step => useProjectStore.getState().setStep(step as WorkflowStep),
  focusTarget: target => focusWorkflowTarget(target),
  setTrainRatio: ratio => useDatasetStore.getState().prefillSplit(ratio),
  splitSupport: () => {
    const data = useDatasetStore.getState();
    return { supported: data.splitSupported, reason: data.splitUnavailableReason };
  },
  applySplit: async (train, val, test, isCurrent) => {
    const check = runtimeMutationGuard(isCurrent);
    await useDatasetStore.getState().applySplit(train, val, test);
    check();
    const error = useDatasetStore.getState().splitError;
    return { ok: !error, error };
  },
  findImage: name => {
    const annotation = useAnnotationStore.getState();
    if (name === 'current') return annotation.currentImage ? [imageChoice(annotation.currentImage)] : [];
    const seen = new Map<string, ImageChoice>();
    for (const image of [...annotation.images, ...useDatasetStore.getState().images]) {
      if (fileName(image.file_path) === name) seen.set(image.file_path, imageChoice(image));
    }
    return [...seen.values()];
  },
  setImageClass: async (image, className, isCurrent) => {
    const check = runtimeMutationGuard(isCurrent);
    // The same calls as step 2's class picker (ClassificationClassPicker): save, update the open image, refresh counts.
    const result = await api.dataset.setImageClass({ image_path: image.path, class_name: className, actor: 'operator' });
    check();
    useAnnotationStore.getState().applyImageClass(image.path, result.class, result.class_source, result.metadata);
    void useDatasetStore.getState().annotationsChanged();
    bumpWorkflowEvidence();
    return { className: result.class, changed: result.changed };
  },
  draftModels: async () => {
    const task = useProjectStore.getState().task;
    const { models } = await api.labelSuggestions.models();
    return models.filter(model => model.task === task).map(model => ({ jobId: model.job_id, task: model.task,
      dataState: model.data_state === 'changed' ? 'changed' : 'current', createdAt: model.created_at }));
  },
  startDraftBatch: async (jobId, threshold, isCurrent) => {
    const check = runtimeMutationGuard(isCurrent);
    const batch = await api.labelSuggestions.startBatch({ job_id: jobId, threshold });
    check();
    return { id: batch.id, total: batch.total };
  },
  draftStats: () => api.labelSuggestions.stats(),
  impact: () => request('/api/provenance/impact'),
  evaluation: () => {
    const state = useEvaluationStore.getState();
    if (!state.testPredictions?.length) return null;
    let escapes = 0, overkills = 0, right = 0;
    for (const item of state.testPredictions) {
      const verdict = computeSampleVerdict(item, state.confidenceThreshold, state.overkillAnalysis, state.classSemantics?.roles);
      if (verdict === 'ESCAPE') escapes++; else if (verdict === 'OVERKILL') overkills++; else if (verdict === 'CORRECT_OK' || verdict === 'CORRECT_NG') right++;
    }
    const samples = state.testPredictions.length;
    return { samples, escapes, overkills, accuracy: right + escapes + overkills ? Math.round(right / samples * 100) : null, threshold: state.confidenceThreshold };
  },
  training: () => {
    const training = useTrainingStore.getState(), project = useProjectStore.getState(), data = useDatasetStore.getState();
    return { isTraining: training.isTraining, status: training.status ?? null, preset: String(training.preset),
      computeOnServer: Boolean(useComputeStore.getState().selectedProfileId),
      folderReady: Boolean(project.project?.source_dataset_dir && project.project.source_dataset_dir === data.folderPath && data.totalImages > 0) };
  },
  startTraining: async isCurrent => {
    const check = runtimeMutationGuard(isCurrent);
    const project = useProjectStore.getState(), data = useDatasetStore.getState();
    // The same call as step 3's start button with its defaults (no warm start, the chosen preset and compute target).
    await useTrainingStore.getState().startTraining(data.folderPath, project.task);
    check();
    return { jobId: useTrainingStore.getState().jobId ?? null };
  },
  trainingPreparation: async isCurrent => {
    const check = runtimeGuard(isCurrent), project = useProjectStore.getState(), training = useTrainingStore.getState(), compute = useComputeStore.getState();
    if (!compute.isLoaded || compute.isLoading || compute.loadError) throw new ToolRefusal('compute_not_ready', '실행 위치를 아직 확인하지 못했습니다. 3단계에서 연결 상태를 확인하세요.');
    // This is the existing assistant start path (no model overrides), explicitly separate from the controller's local custom model form.
    const catalog = await request<{families: {task: string; default_architecture: string}[]}>('/api/models/capabilities');check();
    const model = catalog.families.find(row => row.task === project.task)?.default_architecture;
    if (!model) throw new ToolRefusal('unknown_model', '현재 작업의 기본 모델 구조를 확인하지 못했습니다. 3단계 모델 목록을 확인하세요.');
    const settings = {scope: 'assistant_default_start', task: project.task, model, preset: training.preset, batch_size: training.nextBatchSize,
      device: training.nextDevice || 'auto', target: compute.selectedProfileId ? 'selected_server' : 'local'};
    if (!['auto', 'cpu', 'mps', 'cuda'].includes(settings.device)) throw new ToolRefusal('invalid_device', '3단계의 로컬 장치 설정을 확인하세요.');
    const raw = compute.selectedProfileId
      ? await request('/api/training-workspace/readiness', {method: 'POST', body: JSON.stringify({task: settings.task, model, preset: settings.preset, device: settings.device, compute_profile_id: compute.selectedProfileId})})
      : await request(`/api/training-workspace/readiness?${new URLSearchParams({task: settings.task, model, preset: settings.preset, device: settings.device})}`);
    check();return {...preparationEvidence(raw), settings, custom_settings: '3단계에서 따로 고른 모델·체크포인트·이어 학습 설정은 이 도우미 기본 시작 경로에 포함하지 않습니다.'};
  },
  prefillTraining: async (settings, isCurrent) => {
    const check = runtimeGuard(isCurrent), training = useTrainingStore.getState(), compute = useComputeStore.getState();
    if (training.isTraining) throw new ToolRefusal('already_training', '학습 중에는 설정을 바꾸지 않습니다.');
    if (compute.selectedProfileId && settings.device !== undefined) throw new ToolRefusal('server_device', '선택 서버는 서버 프로필의 장치를 씁니다. 로컬 장치를 바꾸려면 3단계에서 이 컴퓨터를 직접 선택하세요.');
    check();if (settings.preset) training.setPreset(settings.preset);
    training.setNextSettings({...(settings.device ? {device: settings.device} : {}), ...(settings.batch_size !== undefined ? {batchSize: settings.batch_size} : {})});
    const next = useTrainingStore.getState();
    return {preset: next.preset, device: next.nextDevice || 'auto', batch_size: next.nextBatchSize, target: compute.selectedProfileId ? 'selected_server' : 'local'};
  },
  pendingDrafts: async (order, limit, isCurrent) => {
    const {drafts} = await pendingRows(order, isCurrent);
    return {order, total: drafts.length, drafts: drafts.slice(0, limit).map(draftRow), meaning: '후보 중 가장 높은 신뢰도 순서이며 정답 대비 품질 점수가 아닙니다. 빈 후보·닫힌 초안은 제외합니다.'};
  },
  openPendingDraft: async (id, order, isCurrent) => {
    const {drafts, check} = await pendingRows(order, isCurrent);
    const selected = id ? drafts.find(row => row.id === id) : drafts[0];
    if (!selected) throw new ToolRefusal('no_pending_draft', '현재 대기 목록에 그 초안이 없습니다. 대기 목록을 다시 읽으세요.');
    check();
    const project = useProjectStore.getState();
    if (useAnnotationStore.getState().currentImage?.file_path !== selected.image_path) {
      const opened = await project.openImageForLabeling(fileName(selected.image_path).replace(/\.[^.]+$/, ''), selected.image_path);
      check();if (!opened) throw new ToolRefusal('image_not_opened', '현재 편집을 저장하지 못해 초안 이미지로 이동하지 않았습니다. 2단계 저장 오류를 확인하세요.');
    } else {await project.setStep(2);check();}
    useDraftReviewStore.getState().requestOpen(selected.id, undefined, draftReviewScope(useProjectStore.getState()), order);
    return {...draftRow(selected), order, adopted: false};
  },
  evaluationGuidance: () => {
    const state = useEvaluationStore.getState(), source = currentEvaluationGuidanceSource();
    if (!source || state.isLoading || !state.testPredictions.length) return {loaded: false, recommendation: null, recommendation_reason: '현재 프로젝트·원본·라벨 세트에 묶인 평가를 4단계에서 먼저 여세요.'};
    let knownNormals = 0, knownDefects = 0, unresolved = 0, escapes = 0, overkills = 0, right = 0;
    for (const row of state.testPredictions) {
      const explicitTruth = (row as typeof row & {is_defect?: boolean}).is_defect;
      if (typeof explicitTruth !== 'boolean' && !isDefectLabel(row.ground_truth, state.classSemantics?.roles) && !isNormalLabel(row.ground_truth, state.classSemantics?.roles)) {unresolved++;continue;}
      const verdict = computeSampleVerdict(row, state.confidenceThreshold, state.overkillAnalysis, state.classSemantics?.roles);
      if (verdict === 'ESCAPE') {knownDefects++;escapes++;} else if (verdict === 'CORRECT_NG') {knownDefects++;right++;}
      else if (verdict === 'OVERKILL') {knownNormals++;overkills++;} else if (verdict === 'CORRECT_OK') {knownNormals++;right++;} else unresolved++;
    }
    const samples = state.testPredictions.length;
    return evaluationGuidance({task: source.source.task, jobId: state.jobId, threshold: state.confidenceThreshold, samples, knownNormals, knownDefects, unresolved,
      summary: {samples, escapes, overkills, accuracy: right + escapes + overkills ? Math.round(right / samples * 100) : null, threshold: state.confidenceThreshold},
      metrics: state.metrics, analysis: state.overkillAnalysis, sourceVerified: true, cohort: state.executionEvidence?.common_cohort, evaluatedAt: state.executionEvidence?.evaluated_at});
  },
};

export const createRuntimeTools = (tasks?:AssistantTaskToolDeps) => createAssistantTools({...runtimeToolDeps,tasks});

/** The open step-2 image as a ≤1024 px copy for an image question, read only after the person confirmed sending it. */
export async function currentImageForQuestion(): Promise<{ mediaType: 'image/png' | 'image/jpeg' | 'image/webp'; base64: string } | null> {
  const image = useAnnotationStore.getState().currentImage;
  if (!image) return null;
  const blob = await request<Blob>(`/api/dataset/thumbnail/${encodeURIComponent(image.file_name)}?file_path=${encodeURIComponent(image.file_path)}&size=1024`, { responseType: 'blob' });
  const type = blob.type === 'image/jpg' ? 'image/jpeg' : blob.type;
  if (type !== 'image/png' && type !== 'image/jpeg' && type !== 'image/webp') throw new Error('이미지 형식을 보낼 수 없습니다(PNG·JPEG·WebP만).');
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return { mediaType: type, base64: btoa(binary) };
}
