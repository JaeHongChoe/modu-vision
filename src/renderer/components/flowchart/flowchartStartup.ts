import type { FlowchartPipeline, FlowModelTask, FlowNode, VisionTask } from '../../types';

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
  verifyModels: (sourceFolder: string, models: Array<{ job_id: string; task: FlowModelTask }>) => Promise<void>;
  isCurrent: () => boolean;
}

/** Identify model-bearing nodes, including nodes whose model is not bound yet. */
export function getFlowchartModelTask(node: FlowNode): FlowModelTask | null {
  if (node.data.node_type === 'detection_crop') return node.data.task === 'rotated_detection' ? 'rotated_detection' : 'detection';
  if (node.data.node_type === 'preprocess' && node.data.params?.operation === 'enhancement') return 'enhancement';
  if (node.data.node_type === 'preprocess' && node.data.params?.operation === 'learned_rotation') return 'rotation';
  if (node.data.node_type !== 'inspection') return null;
  const task = node.data.task;
  if (task !== 'anomaly' && task !== 'segmentation' && task !== 'classification' && task !== 'patch_classification' && task !== 'ocr' && task !== 'rotated_detection') {
    throw new Error('Unsupported inspection model task');
  }
  return task;
}

/** Collect the model references used by an executable graph. */
export function getFlowchartModelReferences(pipeline: FlowchartPipeline): Array<{ job_id: string; task: FlowModelTask }> {
  const models: Array<{ job_id: string; task: FlowModelTask }> = [];
  for (const node of pipeline.nodes) {
    if (!node.data.model_job_id) continue;
    const task = getFlowchartModelTask(node);
    if (task) models.push({ job_id: node.data.model_job_id, task });
  }
  return models;
}

/** Attach the source-verified model only to an empty, matching single inspection flow. */
export function singleModelAutoBinding(
  pipeline: FlowchartPipeline,
  task: VisionTask,
  verifiedJobId: string,
): { nodeId: string; modelJobId: string } | null {
  if (task === 'detection') {
    const detector = pipeline.nodes.filter((node) => node.data.node_type === 'detection_crop');
    if (detector.length !== 1 || getFlowchartModelTask(detector[0]) !== 'detection' || pipeline.nodes.some((node) => node.data.node_type === 'inspection') || detector[0].data.model_job_id) return null;
    return { nodeId: detector[0].id, modelJobId: verifiedJobId };
  }
  if (pipeline.nodes.some((node) => node.data.node_type === 'detection_crop')) return null;
  const inspectionNodes = pipeline.nodes.filter((node) => node.data.node_type === 'inspection');
  if (inspectionNodes.length !== 1) return null;
  const inspection = inspectionNodes[0];
  if (inspection.data.task !== task || inspection.data.model_job_id) return null;
  return { nodeId: inspection.id, modelJobId: verifiedJobId };
}

/** A draft from another recipe must not appear under the newly selected recipe. */
export function pipelineMatchesTask(pipeline: FlowchartPipeline | null, task: VisionTask): boolean {
  if (!pipeline) return false;
  if (pipeline.nodes.some((node) => node.data.task === 'ocr' || node.data.task === 'rotated_detection' || ['enhancement','learned_rotation'].includes(node.data.params?.operation))) return true;
  if (task === 'detection') return pipeline.nodes.some((node) => node.data.node_type === 'detection_crop');
  const inspections = pipeline.nodes.filter((node) => node.data.node_type === 'inspection');
  return inspections.some((node) => node.data.task === task);
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
    if (options.allowLatestRecovery || options.completedCurrentJobId) {
      try {
        await options.loadEvaluation(undefined, { folderPath: options.folderPath, task: options.task });
      } catch {
        // A saved multi-model graph can be valid even if no single model is
        // selected in the evaluation tab. Its references are verified below.
      }
      if (!options.isCurrent()) return { status: 'cancelled' };
      verifiedJobId = options.getVerifiedJobId();
    }
  }

  const pipeline = await options.loadSavedPipeline();
  if (!options.isCurrent()) return { status: 'cancelled' };
  if (!pipeline) return { status: 'blocked', reason: 'saved_flow_unavailable' };
  try {
    const models = getFlowchartModelReferences(pipeline);
    if (models.length > 0) {
      await options.verifyModels(options.folderPath, models);
    } else if (!verifiedJobId) {
      return { status: 'blocked', reason: options.allowLatestRecovery ? 'model_unavailable' : 'model_recovery_disabled' };
    }
  } catch {
    if (!options.isCurrent()) return { status: 'cancelled' };
    return { status: 'blocked', reason: 'saved_model_mismatch' };
  }
  if (!options.isCurrent()) return { status: 'cancelled' };
  return { status: 'ready', pipeline, verifiedJobId: verifiedJobId || '' };
}
