/** Local bindings for assistant authority. These values never enter provider context or run logs. */
import type { AssistantConfigState } from '../../types/electron';
import { useProjectStore } from '../stores/useProjectStore';
import { useComputeStore } from '../stores/useComputeStore';
import { useDatasetStore } from '../stores/useDatasetStore';
import { useAnnotationStore } from '../stores/useAnnotationStore';
import { useTrainingStore } from '../stores/useTrainingStore';
import { getApiPersistenceIdentity, getProjectContextGeneration } from './api';
import { workflowEvidenceRevision } from '../stores/workflowEvidence';

/** The project part of the scope: the run log (and a write's outcome) belongs to it whatever the provider settings are. */
function projectAuthority(): unknown[] {
  const project = useProjectStore.getState(), compute = useComputeStore.getState();
  return [getApiPersistenceIdentity(), getProjectContextGeneration(), compute.transportRevision,
    project.projectDir, project.project?.id, project.project?.source_dataset_dir, project.project?.active_labelset_id, project.task];
}
export const assistantProjectScope = (): string => JSON.stringify(projectAuthority());
export function assistantScope(config: AssistantConfigState | null): string {
  const provider = config?.providers.find(item => item.provider === config.active);
  return JSON.stringify([...projectAuthority(),
    config?.active, provider?.baseUrl, provider?.model, provider?.format, provider?.supportsImages,
    provider?.connectionAuthority, config?.chatgpt?.authority]);
}
export function assistantImageBinding(scope: string): string {
  const image = useAnnotationStore.getState();
  return JSON.stringify([scope, image.currentImage?.file_path, image.currentImage?.image_id,
    image.metadata?.image_uuid, image.metadata?.content_hash, image.metadata?.content_version, image.metadata?.revision,
    image.annotationLoadStatus]);
}
export function assistantWriteBinding(scope: string): string {
  const data = useDatasetStore.getState(), image = useAnnotationStore.getState(), training = useTrainingStore.getState(), compute = useComputeStore.getState();
  return JSON.stringify([assistantImageBinding(scope), image.isDirty, image.annotations, image.activeCategory, image.labelbookVersion,
    data.folderPath, data.datasetKey, data.sourceAuthority, data.split, data.splitState, data.totalImages, data.unlabeledImages,
    data.classes, data.trainRatio, training.preset, training.nextBatchSize, training.nextDevice, training.jobId, training.status,
    compute.selectedProfileId, compute.profiles?.find(profile => profile.id === compute.selectedProfileId), workflowEvidenceRevision()]);
}
