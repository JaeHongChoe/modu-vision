import type { FlowchartPipeline, FlowModelTask } from '../types';

/** Match the recipe selected by the flow editor when saving a draft on project switch. */
export function projectFlowRecipe(pipeline: FlowchartPipeline): FlowModelTask | 'mixed' {
  const inspectionTasks = new Set(
    pipeline.nodes.filter((node) => node.data.node_type === 'inspection').map((node) => node.data.task),
  );
  if (inspectionTasks.size > 1) return 'mixed';
  if (inspectionTasks.size === 1) {
    const task = [...inspectionTasks][0];
    if (task === 'anomaly' || task === 'segmentation' || task === 'classification' || task === 'patch_classification' || task === 'ocr' || task === 'rotated_detection') return task;
    throw new Error('검사 노드의 작업 유형을 확인할 수 없습니다.');
  }
  if (pipeline.nodes.some((node) => node.data.node_type === 'detection_crop')) return 'detection';
  throw new Error('플로우에 검사 모델 노드가 없습니다.');
}
