/** The assistant's tools wired to the app's real stores and services. Every write goes through the same store action or
 * service call the screen uses (and so the same backend role checks); the context is the footer's published picture
 * reduced by assistantContext.ts. */
import { currentAssistContext } from '../stores/assistContext';
import { useProjectStore } from '../stores/useProjectStore';
import { useDatasetStore } from '../stores/useDatasetStore';
import { useTrainingStore } from '../stores/useTrainingStore';
import { useAnnotationStore } from '../stores/useAnnotationStore';
import { useComputeStore } from '../stores/useComputeStore';
import { computeSampleVerdict, useEvaluationStore } from '../stores/useEvaluationStore';
import { bumpWorkflowEvidence } from '../stores/workflowEvidence';
import { focusWorkflowTarget, type WorkflowStep } from '../components/wizard/workflowReadiness';
import { api, getApiPersistenceIdentity, request } from './api';
import { fileName, minimalAssistContext, type AssistantFacts } from './assistantContext';
import { createAssistantTools, type ImageChoice, type ToolDeps } from './assistantTools';

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

export const runtimeToolDeps: ToolDeps = {
  snapshot: currentAssistContext,
  facts: assistantFacts,
  setStep: step => useProjectStore.getState().setStep(step as WorkflowStep),
  focusTarget: target => focusWorkflowTarget(target),
  setTrainRatio: ratio => useDatasetStore.getState().setTrainRatio(ratio),
  splitSupport: () => {
    const data = useDatasetStore.getState();
    return { supported: data.splitSupported, reason: data.splitUnavailableReason };
  },
  applySplit: async (train, val, test) => {
    await useDatasetStore.getState().applySplit(train, val, test);
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
  setImageClass: async (image, className) => {
    // The same calls as step 2's class picker (ClassificationClassPicker): save, update the open image, refresh counts.
    const result = await api.dataset.setImageClass({ image_path: image.path, class_name: className, actor: 'operator' });
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
  startDraftBatch: async (jobId, threshold) => {
    const batch = await api.labelSuggestions.startBatch({ job_id: jobId, threshold });
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
  startTraining: async () => {
    const project = useProjectStore.getState(), data = useDatasetStore.getState();
    // The same call as step 3's start button with its defaults (no warm start, the chosen preset and compute target).
    await useTrainingStore.getState().startTraining(data.folderPath, project.task);
    return { jobId: useTrainingStore.getState().jobId ?? null };
  },
};

export const createRuntimeTools = () => createAssistantTools(runtimeToolDeps);

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
