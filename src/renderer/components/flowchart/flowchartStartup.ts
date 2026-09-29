import type { FlowchartPipeline, VisionTask } from '../../types';

export type FlowchartStartupResult =
  | { status: 'ready'; pipeline: FlowchartPipeline; verifiedJobId: string }
  | { status: 'waiting'; reason: string }
  | { status: 'blocked'; reason: string }
  | { status: 'cancelled' };

interface FlowchartStartupOptions {
  folderPath: string;
  task: VisionTask;
  datasetKey: string | null;
  hasSelectedFolder: boolean;
  datasetIsLoading: boolean;
  importError: string | null;
  allowLatestRecovery: boolean;
  completedCurrentJobId: string | null;
  getVerifiedJobId: () => string | null;
  loadEvaluation: (jobId?: string, source?: { folderPath: string; task: VisionTask }) => Promise<void>;
  loadSavedPipeline: () => Promise<FlowchartPipeline | null>;
  verifyModels: (sourceFolder: string, models: Array<{ job_id: string; task: VisionTask }>) => Promise<void>;
  isCurrent: () => boolean;
}

/** The runtime supports one optional detector followed by one inspection model. */
export function getFlowchartModelReferences(pipeline: FlowchartPipeline): Array<{ job_id: string; task: VisionTask }> {
  const models: Array<{ job_id: string; task: VisionTask }> = [];
  for (const node of pipeline.nodes) {
    if (!node.data.model_job_id) continue;
    if (node.data.node_type === 'detection_crop') {
      models.push({ job_id: node.data.model_job_id, task: 'detection' });
    } else if (node.data.node_type === 'inspection') {
      const task = node.data.task;
      if (task !== 'anomaly' && task !== 'segmentation' && task !== 'classification') {
        throw new Error('Unsupported inspection model task');
      }
      models.push({ job_id: node.data.model_job_id, task });
    }
  }
  return models;
}

/** Confirm a model belongs to the selected source before reading a saved model reference. */
export async function recoverThenLoadFlowchart(options: FlowchartStartupOptions): Promise<FlowchartStartupResult> {
  if (!options.isCurrent()) return { status: 'cancelled' };
  if (options.datasetIsLoading) return { status: 'waiting', reason: 'dataset_loading' };
  if (options.importError || !options.hasSelectedFolder ||
      options.datasetKey !== `${options.folderPath}\0${options.task}`) {
    return { status: 'blocked', reason: 'dataset_unavailable' };
  }

  let verifiedJobId = options.getVerifiedJobId();
  if (!verifiedJobId) {
    if (!options.allowLatestRecovery && !options.completedCurrentJobId) {
      return { status: 'blocked', reason: 'model_recovery_disabled' };
    }
    try {
      await options.loadEvaluation(undefined, { folderPath: options.folderPath, task: options.task });
    } catch {
      if (!options.isCurrent()) return { status: 'cancelled' };
      return { status: 'blocked', reason: 'model_unavailable' };
    }
    if (!options.isCurrent()) return { status: 'cancelled' };
    verifiedJobId = options.getVerifiedJobId();
    if (!verifiedJobId) return { status: 'blocked', reason: 'model_unavailable' };
  }

  const pipeline = await options.loadSavedPipeline();
  if (!options.isCurrent()) return { status: 'cancelled' };
  if (!pipeline) return { status: 'blocked', reason: 'saved_flow_unavailable' };
  try {
    const models = getFlowchartModelReferences(pipeline);
    if (models.length > 0) {
      await options.verifyModels(options.folderPath, models);
    }
  } catch {
    if (!options.isCurrent()) return { status: 'cancelled' };
    return { status: 'blocked', reason: 'saved_model_mismatch' };
  }
  if (!options.isCurrent()) return { status: 'cancelled' };
  return { status: 'ready', pipeline, verifiedJobId };
}
