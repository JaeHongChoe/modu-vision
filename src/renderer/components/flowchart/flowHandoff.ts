import type { SavedFlowVersion } from '../../services/api';
import type { FlowchartPipeline } from '../../types';

export interface SavedFlowIdentity {
  versionId: string;
  pipelineId: string;
  pipelineName: string;
  pipelineHash: string;
  modelJobIds: string[];
  recipeTask: SavedFlowVersion['recipe_task'];
}

/** The batch inspector must not silently choose a newer, inactive revision. */
export function activeSavedVersion(versions: SavedFlowVersion[]): SavedFlowVersion | null {
  return versions.find((version) => version.is_active) || null;
}

/** Describe the models in the graph instead of assuming the recipe task is the whole flow. */
export function flowRecipeLabel(
  pipeline: FlowchartPipeline,
  fallbackTask: SavedFlowVersion['recipe_task'],
): string {
  const names: Record<string, string> = {
    classification: '분류', detection: '검출', segmentation: '분할', anomaly: '이상 탐지',
  };
  const tasks = [...new Set(pipeline.nodes.flatMap((node) => {
    if (node.data.node_type === 'detection_crop') return ['detection'];
    if (node.data.node_type === 'inspection' && node.data.task) return [node.data.task];
    return [];
  }))];
  if (tasks.length) return tasks.map((task) => names[task] || task).join('+');
  return fallbackTask === 'mixed' ? '복합 검사' : names[fallbackTask] || fallbackTask;
}

export function flowRunSourceLabel(
  source: { kind: 'draft' | 'saved'; versionId: string | null },
): string {
  return source.kind === 'saved' && source.versionId
    ? `활성 저장 버전 ${source.versionId} 검사 결과`
    : '미저장 초안 검사 결과 · 6단계에서 사용하려면 플로우를 저장하세요';
}

// Older backends do not publish graph hashes. Keep their browser-side identity
// available while newer backends supply the authoritative canonical graph hash.
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).sort()
      .filter((key) => object[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

/** The hash identifies graph content; the immutable version ID identifies the saved revision. */
export async function savedFlowIdentity(
  version: SavedFlowVersion,
  pipeline: FlowchartPipeline,
): Promise<SavedFlowIdentity> {
  let pipelineHash = version.pipeline_hash;
  if (typeof pipelineHash !== 'string' || !/^[a-f0-9]{64}$/.test(pipelineHash)) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(pipeline)));
    pipelineHash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  const modelJobIds = [...new Set(pipeline.nodes
    .filter((node) => node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop')
    .map((node) => node.data.model_job_id)
    .filter((jobId): jobId is string => typeof jobId === 'string' && jobId.length > 0))];
  return {
    versionId: version.version_id,
    pipelineId: pipeline.id,
    pipelineName: pipeline.name,
    pipelineHash,
    modelJobIds,
    recipeTask: version.recipe_task,
  };
}
