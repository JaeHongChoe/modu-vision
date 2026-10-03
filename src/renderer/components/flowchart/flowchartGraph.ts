import type { FlowEdge, FlowNode, FlowNodeData, FlowchartPipeline } from '../../types';

type FlowNodeType = FlowNode['data']['node_type'];
type Branch = NonNullable<FlowEdge['isBranch']>;

const operatorTypes: FlowNodeType[] = ['patch_split', 'preprocess'];
const modelTypes: FlowNodeType[] = ['detection_crop', 'inspection'];
const resultTypes: FlowNodeType[] = [...modelTypes, 'blob_measure', 'measurement', 'aggregate'];

function allowedPayloads(from: FlowNodeType, to: FlowNodeType): NonNullable<FlowEdge['payload_type']>[] {
  if (from === 'input' && (modelTypes.includes(to) || operatorTypes.includes(to) || to === 'fixed_roi')) return ['image'];
  if ((from === 'fixed_roi' || operatorTypes.includes(from)) && (modelTypes.includes(to) || operatorTypes.includes(to))) return ['roi'];
  if (modelTypes.includes(from) && (modelTypes.includes(to) || operatorTypes.includes(to))) return ['image', 'roi'];
  if (modelTypes.includes(from) && ['blob_measure', 'measurement', 'aggregate', 'decision'].includes(to)) return ['result'];
  if ((from === 'blob_measure' || from === 'measurement') && (to === 'aggregate' || to === 'decision')) return ['result'];
  if (from === 'aggregate' && to === 'decision') return ['result'];
  if (from === 'decision' && to === 'output') return ['result'];
  return [];
}

/** What a port carries: the edge payloads, plus `verdict` for the decision's routed result (S2-05). */
export type FlowPortPayload = 'image' | 'roi' | 'result' | 'verdict';
export interface FlowPort { id: string; payloads: FlowPortPayload[]; label: string }
const PORT_NAMES: Record<FlowPortPayload, string> = { image: 'IMAGE', roi: 'ROI', result: 'RESULT', verdict: 'VERDICT' };
const PORT_KOREAN: Record<FlowPortPayload, string> = { image: '이미지', roi: 'ROI', result: '결과', verdict: '판정' };
const flowPort = (direction: 'IN' | 'OUT', ...payloads: FlowPortPayload[]): FlowPort =>
  ({ id: `${direction.toLowerCase()}_${payloads.join('_')}`, payloads, label: `${payloads.map((payload) => PORT_NAMES[payload]).join('/')} ${direction}` });
const edgePayloads = (payloads: FlowPortPayload[]) => payloads.map((payload) => payload === 'verdict' ? 'result' : payload);

/** The typed ports of a node, from the same payload rules every connection is checked with. A model passes its image or
 *  regions on to the next model or operator, and its result to Blob, measurement, aggregate or decision. */
export function flowNodePorts(node: FlowNode): { inputs: FlowPort[]; outputs: FlowPort[] } {
  switch (node.data.node_type) {
    case 'input': return { inputs: [], outputs: [flowPort('OUT', 'image')] };
    case 'fixed_roi': return { inputs: [flowPort('IN', 'image')], outputs: [flowPort('OUT', 'roi')] };
    case 'patch_split': case 'preprocess': return { inputs: [flowPort('IN', 'image', 'roi')], outputs: [flowPort('OUT', 'roi')] };
    case 'detection_crop': case 'inspection':
      return { inputs: [flowPort('IN', 'image', 'roi')], outputs: [flowPort('OUT', 'image', 'roi'), flowPort('OUT', 'result')] };
    case 'blob_measure': case 'measurement': case 'aggregate': return { inputs: [flowPort('IN', 'result')], outputs: [flowPort('OUT', 'result')] };
    case 'decision': return { inputs: [flowPort('IN', 'result')], outputs: [flowPort('OUT', 'verdict')] };
    case 'output': return { inputs: [flowPort('IN', 'verdict')], outputs: [] };
    default: return { inputs: [], outputs: [] };
  }
}

/** An edge's payload; older saved edges without one carry what the backend resolves for them (_edge_payload_type). */
export function flowEdgePayload(edge: FlowEdge, target?: FlowNode, source?: FlowNode): NonNullable<FlowEdge['payload_type']> {
  if (edge.payload_type) return edge.payload_type;
  if (source?.data.node_type === 'input') return 'image';
  return target && ['blob_measure', 'measurement', 'aggregate', 'decision', 'output'].includes(target.data.node_type) ? 'result' : 'roi';
}

/** The output port an edge leaves from (its payload's port), for drawing it. */
export function flowEdgeSourcePort(node: FlowNode, edge: FlowEdge, target?: FlowNode): number {
  const payload = flowEdgePayload(edge, target, node);
  const index = flowNodePorts(node).outputs.findIndex((port) => edgePayloads(port.payloads).includes(payload));
  return Math.max(0, index);
}

function scoreSpecIssue(data: FlowNodeData): string | null {
  const spec=data.score_spec, threshold=data.threshold;
  if(!spec)return typeof threshold==='number'&&Number.isFinite(threshold)&&threshold>=0&&threshold<=1?null:'점수 임계치는 0~1이어야 합니다.';
  if(!['distance','probability'].includes(spec.domain)||!['mahalanobis_distance','euclidean_distance','probability'].includes(spec.unit)
    ||(spec.domain==='probability')!==(spec.unit==='probability')||spec.direction!=='higher_is_defect'||!spec.calibration_id?.trim())return '점수 단위 또는 보정 식별자를 확인하세요.';
  if(!Number.isFinite(threshold)||threshold!<0||spec.threshold!==threshold||(spec.domain==='probability'&&threshold!>1))return '점수 명세와 임계값의 단위·범위가 일치해야 합니다.';
  return null;
}

export function decisionRulePatch(data: FlowNodeData, rule: string, sourceModels: FlowNode[] = []): Partial<FlowNodeData> {
  if (rule !== 'score_gt_threshold') return { rule };
  const current = data.threshold;
  const sourceSpec=sourceModels[0]?.data.score_spec;
  if(!data.score_spec&&sourceSpec&&sourceModels.every(node=>['domain','unit','direction','calibration_id'].every(key=>node.data.score_spec?.[key as keyof typeof sourceSpec]===sourceSpec[key as keyof typeof sourceSpec])))return {rule,threshold:sourceSpec.threshold,score_spec:{...sourceSpec}};
  if(data.score_spec)return {rule,threshold:data.score_spec.threshold,score_spec:data.score_spec};
  return { rule, threshold: typeof current === 'number' && Number.isFinite(current) && current >= 0 && current <= 1 ? current : 0.5 };
}

export function shouldShowThreshold(node: FlowNode): boolean {
  return node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop' ||
    (node.data.node_type === 'decision' && node.data.rule === 'score_gt_threshold');
}

function graphParts(pipeline: FlowchartPipeline) {
  const nodes = new Map(pipeline.nodes.map((node) => [node.id, node]));
  const incoming = new Map(pipeline.nodes.map((node) => [node.id, [] as FlowEdge[]]));
  const outgoing = new Map(pipeline.nodes.map((node) => [node.id, [] as FlowEdge[]]));
  for (const edge of pipeline.edges) {
    incoming.get(edge.target)?.push(edge);
    outgoing.get(edge.source)?.push(edge);
  }
  return { nodes, incoming, outgoing };
}

/** Optional layout for editable DAGs, including unconnected draft components. */
export function layoutFlowchart(pipeline: FlowchartPipeline): FlowchartPipeline {
  const { nodes, incoming, outgoing } = graphParts(pipeline);
  if (nodes.size !== pipeline.nodes.length) throw new Error('노드 ID가 중복되어 흐름을 정렬할 수 없습니다.');
  for (const edge of pipeline.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target))
      throw new Error('연결선의 시작 또는 끝 노드를 확인한 뒤 흐름을 정렬하세요.');
  }
  const ordinal = new Map(pipeline.nodes.map((node, index) => [node.id, index]));
  const components: string[][] = [];
  const seen = new Set<string>();
  // Keep the image-input component above unfinished, unconnected additions.
  const seeds = [...pipeline.nodes].sort((a, b) =>
    Number(b.data.node_type === 'input') - Number(a.data.node_type === 'input'));
  for (const seed of seeds) {
    if (seen.has(seed.id)) continue;
    const component: string[] = [];
    const pending = [seed.id];
    seen.add(seed.id);
    while (pending.length) {
      const id = pending.shift() as string;
      component.push(id);
      const neighbors = [...(incoming.get(id) || []).map((edge) => edge.source),
        ...(outgoing.get(id) || []).map((edge) => edge.target)];
      for (const neighbor of neighbors) if (!seen.has(neighbor)) { seen.add(neighbor); pending.push(neighbor); }
    }
    components.push(component);
  }
  const positions = new Map<string, { x: number; y: number }>();
  let top = 80;
  for (const component of components) {
    const remaining = new Map(component.map((id) => [id, incoming.get(id)?.length || 0]));
    const rank = new Map(component.map((id) => [id, 0]));
    const ready = component.filter((id) => !remaining.get(id));
    let visited = 0;
    while (ready.length) {
      const id = ready.shift() as string;
      visited++;
      for (const edge of outgoing.get(id) || []) {
        rank.set(edge.target, Math.max(rank.get(edge.target) || 0, (rank.get(id) || 0) + 1));
        remaining.set(edge.target, (remaining.get(edge.target) || 0) - 1);
        if (!remaining.get(edge.target)) ready.push(edge.target);
      }
    }
    if (visited !== component.length) throw new Error('순환 연결을 제거한 뒤 흐름을 정렬하세요.');
    const layers = new Map<number, string[]>();
    for (const id of component) {
      const column = rank.get(id) || 0;
      layers.set(column, [...(layers.get(column) || []), id]);
    }
    const rowCount = Math.max(...[...layers.values()].map((layer) => layer.length));
    const rows = new Map<string, number>();
    const parentRow = (id: string) => {
      const parents = incoming.get(id) || [];
      return parents.length ? parents.reduce((sum, edge) => sum + (rows.get(edge.source) || 0), 0) / parents.length : 0;
    };
    const branchRank = (id: string) => Math.min(...(incoming.get(id) || []).map((edge) =>
      edge.isBranch === 'pass' || edge.predicate?.operator === 'present' ? 0
        : edge.isBranch === 'fail' || edge.predicate?.operator === 'absent' ? 2 : edge.isBranch === 'review' ? 3 : 1), 4);
    for (const [column, layer] of [...layers.entries()].sort(([a], [b]) => a - b)) {
      layer.sort((a, b) => parentRow(a) - parentRow(b) || branchRank(a) - branchRank(b)
        || (nodes.get(a)?.position.y || 0) - (nodes.get(b)?.position.y || 0)
        || (ordinal.get(a) || 0) - (ordinal.get(b) || 0));
      layer.forEach((id, index) => {
        const row = (rowCount - layer.length) / 2 + index;
        rows.set(id, row);
        positions.set(id, { x: 64 + column * 360, y: top + row * 300 });
      });
    }
    top += rowCount * 300 + 120;
  }
  const changed = pipeline.nodes.some((node) => {
    const position = positions.get(node.id);
    return position && (position.x !== node.position.x || position.y !== node.position.y);
  });
  return changed ? { ...pipeline, nodes: pipeline.nodes.map((node) => ({ ...node, position: positions.get(node.id) as { x: number; y: number } })) } : pipeline;
}

function firstUnvisitedNode(
  pipeline: FlowchartPipeline,
  incoming: Map<string, FlowEdge[]>,
  outgoing: Map<string, FlowEdge[]>,
  inputId: string,
): string | null {
  const pending = new Map(pipeline.nodes.map((node) => [node.id, incoming.get(node.id)?.length || 0]));
  const ready = [inputId];
  const visited = new Set<string>();
  while (ready.length) {
    const nodeId = ready.shift() as string;
    if (visited.has(nodeId)) continue;
    visited.add(nodeId);
    for (const edge of outgoing.get(nodeId) || []) {
      const remaining = (pending.get(edge.target) || 0) - 1;
      pending.set(edge.target, remaining);
      if (remaining === 0) ready.push(edge.target);
    }
  }
  const unresolved = pipeline.nodes.filter((node) => !visited.has(node.id));
  if (!unresolved.length) return null;
  const unresolvedIds = new Set(unresolved.map((node) => node.id));
  const visiting = new Set<string>();
  const checked = new Set<string>();
  const findCycle = (nodeId: string): string | null => {
    if (visiting.has(nodeId)) return nodeId;
    if (checked.has(nodeId)) return null;
    visiting.add(nodeId);
    for (const edge of outgoing.get(nodeId) || []) {
      if (!unresolvedIds.has(edge.target)) continue;
      const cycle = findCycle(edge.target);
      if (cycle) return cycle;
    }
    visiting.delete(nodeId);
    checked.add(nodeId);
    return null;
  };
  for (const node of unresolved) {
    const cycle = findCycle(node.id);
    if (cycle) return cycle;
  }
  return unresolved.find((node) =>
    !(incoming.get(node.id) || []).some((edge) => unresolvedIds.has(edge.source))
  )?.id || unresolved[0].id;
}

/** A problem of an editable graph, attached to the node or connection where the user can fix it. */
export interface FlowIssue { kind: 'node' | 'edge' | 'graph'; id: string | null; message: string }
/** The class vocabulary of the completed models (the flow model catalog); without it the class checks are skipped. */
export type FlowModelVocabulary = { job_id: string; class_names?: string[]; class_ids?: number[] };

/** The classes a class rule of `nodeId` may name: the vocabulary of its own model or of the nearest upstream models,
 *  without background 0; empty when unknown or when two upstream models disagree. */
export function nodeClassChoices(pipeline:FlowchartPipeline,nodeId:string,models:FlowModelVocabulary[]):{id:number;name:string}[]{
  const own=pipeline.nodes.find(node=>node.id===nodeId)?.data.model_job_id;
  const jobs=new Set<string>();const visited=new Set<string>();let unbound=false;
  const visit=(id:string)=>{
    if(visited.has(id))return;visited.add(id);
    const node=pipeline.nodes.find(row=>row.id===id);
    if(node?.data.model_job_id){jobs.add(node.data.model_job_id);return;}
    // The nearest model node decides the classes: one without its model yet has no vocabulary (never a model above it).
    if(node&&modelTypes.includes(node.data.node_type as FlowNodeType)){unbound=true;return;}
    pipeline.edges.filter(edge=>edge.target===id).forEach(edge=>visit(edge.source));
  };
  if(own)jobs.add(own);else visit(nodeId);
  if(unbound)return [];
  const vocabulary=new Map<number,string>();
  for(const job of jobs){
    const model=models.find(row=>row.job_id===job);
    if(!model?.class_names?.length||model.class_ids?.length!==model.class_names.length)return [];
    for(let index=0;index<model.class_names.length;index++){
      const id=model.class_ids[index],name=model.class_names[index];
      if(id===0)continue;
      if(vocabulary.has(id)&&vocabulary.get(id)!==name)return [];
      vocabulary.set(id,name);
    }
  }
  return [...vocabulary].sort(([a],[b])=>a-b).map(([id,name])=>({id,name}));
}

function modelClassNames(models: FlowModelVocabulary[] | undefined, jobId: string | undefined): string[] | null {
  const model = jobId ? models?.find((row) => row.job_id === jobId) : undefined;
  return model?.class_names?.length ? model.class_names : null;
}

/** Class IDs a rule names that the connected model does not record (S2-05); an unknown or conflicting vocabulary is
 *  not judged here (nodeClassChoices answers nothing), so a missing catalog never blocks the editor. */
function classIdIssue(pipeline: FlowchartPipeline, node: FlowNode, models: FlowModelVocabulary[] | undefined): string | null {
  // A model node is judged only against its own model: an unbound one is already refused for its missing model, and an
  // upstream model's classes would suggest the wrong choices.
  if (!models || (node.data.node_type === 'inspection' && !node.data.model_job_id)) return null;
  const choices = nodeClassChoices(pipeline, node.id, models);
  if (!choices.length) return null;
  const params = node.data.params || {};
  const named = [...(Array.isArray(params.class_ids) ? params.class_ids : []),
    ...(Array.isArray(params.class_rules) ? params.class_rules.map((row: { class_id?: unknown }) => row?.class_id) : [])];
  const missing = [...new Set(named.filter((id) => !choices.some((choice) => choice.id === id)))];
  return missing.length
    ? `연결된 모델에 없는 클래스 ID: ${missing.join(', ')}. 선택 가능한 클래스: ${choices.map((choice) => `${choice.id} ${choice.name}`).join(', ')}`
    : null;
}

/** The key a connection's problems are filed under: its id, or its position for saved data without one. */
export const flowEdgeKey = (edge: FlowEdge, index: number): string => edge.id || `\u0000${index}`;

/** 을 after a final consonant, 를 after a vowel or a Latin abbreviation (ROI). */
const objectParticle = (word: string): string => {
  const code = word.charCodeAt(word.length - 1) - 0xac00;
  return code >= 0 && code < 11172 && code % 28 ? '을' : '를';
};

/** A check over saved data the editor never writes (a predicate without a class name, a non-text calibration id) reports
 *  that data's format instead of throwing during render. */
function guarded(message: string, check: () => string | null): string | null {
  try {
    return check();
  } catch {
    return message;
  }
}

/** Every problem of the graph in the order the backend reports them (S2-05). With `first`, stops at the first one,
 *  which is the validator's answer; otherwise each node and connection reports its own first problem, so the editor can
 *  mark all of them at once. */
export function flowGraphIssues(pipeline: FlowchartPipeline, { first = false, models: catalog }: { first?: boolean; models?: FlowModelVocabulary[] } = {}): FlowIssue[] {
  const issues: FlowIssue[] = [];
  const report = (message: string, kind: FlowIssue['kind'] = 'graph', id: string | null = null) => {
    issues.push({ kind, id, message });
    return first;
  };
  if (pipeline.execution_config && Object.values(pipeline.execution_config).some((v)=>!Number.isInteger(v)||v<1||v>8)
    && report('병렬 실행 작업 수와 장치 슬롯은 1~8의 정수여야 합니다.')) return issues;
  const { nodes, incoming, outgoing } = graphParts(pipeline);
  if (nodes.size !== pipeline.nodes.length) { report('노드 ID가 중복되었습니다.'); return issues; }
  const inputs = pipeline.nodes.filter((node) => node.data.node_type === 'input');
  const decisions = pipeline.nodes.filter((node) => node.data.node_type === 'decision');
  const outputs = pipeline.nodes.filter((node) => node.data.node_type === 'output');
  const models = pipeline.nodes.filter((node) => modelTypes.includes(node.data.node_type));
  const fixedRois = pipeline.nodes.filter((node) => node.data.node_type === 'fixed_roi');
  const blobs = pipeline.nodes.filter((node) => node.data.node_type === 'blob_measure');
  const measurements = pipeline.nodes.filter((node) => node.data.node_type === 'measurement');
  const aggregates = pipeline.nodes.filter((node) => node.data.node_type === 'aggregate');
  if (inputs.length !== 1 && report('입력 노드는 하나여야 합니다.')) return issues;
  if (decisions.length !== 1 && report('판정 노드는 하나여야 합니다.')) return issues;
  if ((outputs.length < 1 || outputs.length > 3) && report('출력 노드는 1~3개가 필요합니다.')) return issues;
  if ((models.length < 1 || models.length > 8) && report('모델 노드는 1~8개가 필요합니다.')) return issues;
  if (fixedRois.length > 8 && report('고정 ROI 노드는 최대 8개입니다.')) return issues;
  if (blobs.length + measurements.length > 8 && report('Blob·기하 측정 노드는 합계 최대 8개입니다.')) return issues;
  if (aggregates.length > 4 && report('결과 집계 노드는 최대 4개입니다.')) return issues;
  if (inputs.length !== 1 || decisions.length !== 1) return issues;  // the checks below need the one input and decision
  const edgeIds = new Set<string>();
  const connections = new Set<string>();
  for (const [index, edge] of pipeline.edges.entries()) {
    const issue = guarded('연결선의 저장된 조건·데이터 형식을 확인하세요.', () => {
      if (!edge.id || edgeIds.has(edge.id)) return '연결선 ID가 중복되었거나 비었습니다.';
      edgeIds.add(edge.id);
      if (!nodes.has(edge.source) || !nodes.has(edge.target) || edge.source === edge.target) return '연결선의 시작 또는 끝 노드가 올바르지 않습니다.';
      const pair = `${edge.source}\0${edge.target}`;
      if (connections.has(pair)) return '같은 노드 사이의 연결선이 중복되었습니다.';
      connections.add(pair);
      if (edge.predicate && (!modelTypes.includes(nodes.get(edge.source)?.data.node_type as FlowNodeType) || !edge.predicate.class_name.trim() || !['present', 'absent'].includes(edge.predicate.operator) ||
        !Number.isFinite(edge.predicate.min_confidence ?? 0) || (edge.predicate.min_confidence ?? 0) < 0 || (edge.predicate.min_confidence ?? 0) > 1 ||
        (edge.isBranch && edge.isBranch !== 'default'))) return '클래스 조건과 신뢰도 범위를 확인하세요.';
      if (edge.predicate) {
        // A class the source model never predicts would make the condition silently never (present) or always (absent) hold.
        // The engine compares the name exactly (no trimming, case kept), so the editor does too.
        const source = nodes.get(edge.source);
        const names = modelClassNames(catalog, source?.data.model_job_id);
        const wanted = edge.predicate.class_name;
        if (names && !names.includes(wanted)) {
          return names.includes(wanted.trim())
            ? `연결 조건 클래스 '${wanted}'의 앞뒤 공백을 지우세요. 실행 시 클래스 이름은 공백까지 그대로 비교됩니다.`
            : `연결 조건 클래스 '${wanted}': ${source?.data.label?.trim() || edge.source} 모델에 없는 이름입니다. 모델 클래스: ${names.join(', ')}`;
        }
      }
      const from = nodes.get(edge.source)?.data.node_type;
      const to = nodes.get(edge.target)?.data.node_type;
      const payloads = allowedPayloads(from as FlowNodeType, to as FlowNodeType);
      if (payloads.length === 0) return '노드 사이의 연결 형식이 올바르지 않습니다.';
      if (edge.payload_type && !payloads.includes(edge.payload_type)) return '연결선의 데이터 형식(payload)이 노드와 맞지 않습니다.';
      if ((from === 'input' || from === 'fixed_roi') && edge.isBranch && edge.isBranch !== 'default') {
        return '입력과 고정 ROI 연결에는 조건 분기를 지정할 수 없습니다.';
      }
      return null;
    });
    if (issue && report(issue, 'edge', flowEdgeKey(edge, index))) return issues;
  }
  const inputId = inputs[0].id;
  const decisionId = decisions[0].id;
  if ((incoming.get(inputId)?.length || !outgoing.get(inputId)?.length) && report('입력 노드에서 모델 노드로 연결하세요.', 'node', inputId)) return issues;
  const nodeChecks: Array<[FlowNode[], (node: FlowNode) => string | null]> = [
    [fixedRois, (fixedRoi) => {
      const rectangle = fixedRoi.data.params?.roi_bbox;
      if (!Array.isArray(rectangle) || rectangle.length !== 4 ||
        rectangle.some((value) => !Number.isInteger(value)) ||
        rectangle[0] < 0 || rectangle[1] < 0 ||
        rectangle[2] - rectangle[0] < 16 || rectangle[3] - rectangle[1] < 16) {
        return `${fixedRoi.data.label}: 고정 ROI 원본 픽셀 좌표는 16×16 이상인 [x1, y1, x2, y2] 정수여야 합니다.`;
      }
      const parents = incoming.get(fixedRoi.id) || [];
      if (parents.length !== 1 || parents[0].source !== inputId) return `${fixedRoi.data.label}: 원본 이미지 입력 연결선 하나가 필요합니다.`;
      if (!(outgoing.get(fixedRoi.id) || []).length) return `${fixedRoi.data.label}: 검사 모델로 연결하세요.`;
      return null;
    }],
    [pipeline.nodes.filter((item) => operatorTypes.includes(item.data.node_type)), (node) => {
      const params = node.data.params || {};
      if ((incoming.get(node.id) || []).length !== 1 || !(outgoing.get(node.id) || []).length) return `${node.data.label}: 입력 하나와 다음 모델 연결이 필요합니다.`;
      if (node.data.node_type === 'patch_split') {
        const width=params.patch_width ?? 224, height=params.patch_height ?? 224, overlap=params.overlap ?? 0;
        if (![width,height].every((v) => Number.isInteger(v) && v>=16 && v<=8192) || !Number.isInteger(overlap) || overlap<0 || overlap>=Math.min(width,height)) return `${node.data.label}: 패치 크기와 겹침 범위를 확인하세요.`;
      } else if (!['rotate','align','improve','enhancement','learned_rotation','fitted_roi'].includes(params.operation || 'rotate')) return `${node.data.label}: 전처리 종류를 확인하세요.`;
      if (params.operation === 'enhancement' && !node.data.model_job_id) return `${node.data.label}: 영상 개선 모델을 선택하세요.`;
      if (params.operation === 'learned_rotation' && !node.data.model_job_id) return `${node.data.label}: 회전 학습 모델을 선택하세요.`;
      return null;
    }],
    [models, (node) => {
      const modelThreshold = node.data.threshold === undefined ? 0.5 : node.data.threshold;
      const scoreIssue=scoreSpecIssue({...node.data,threshold:modelThreshold});
      if(scoreIssue)return `${node.data.label}: ${scoreIssue}`;
      if(node.data.score_spec?.domain==='distance'&&(node.data.node_type!=='inspection'||node.data.task!=='anomaly'))return '거리 점수는 이상 탐지 모델에만 적용할 수 있습니다.';
      if (node.data.crop_padding !== undefined && (!Number.isInteger(node.data.crop_padding) || node.data.crop_padding < 0)) {
        return `${node.data.label}: ROI 패딩은 0 이상의 정수여야 합니다.`;
      }
      const parent = incoming.get(node.id) || [];
      if (parent.length !== 1) return `${node.data.label}: 모델 입력 연결선이 정확히 하나 필요합니다.`;
      const parentType = nodes.get(parent[0].source)?.data.node_type;
      if (parentType !== 'input' && parentType !== 'fixed_roi' && !operatorTypes.includes(parentType as FlowNodeType) && !modelTypes.includes(parentType as FlowNodeType)) {
        return `${node.data.label}: 지원하지 않는 상류 연결입니다.`;
      }
      if (node.data.node_type === 'inspection' && !['segmentation', 'classification', 'anomaly', 'patch_classification', 'ocr', 'rotated_detection'].includes(node.data.task || '')) {
        return `${node.data.label}: 지원하지 않는 검사 작업입니다.`;
      }
      if (node.data.task === 'ocr') {
        const issue=ocrRuleIssue(node.data.params || {});if(issue)return `${node.data.label}: ${issue}`;
      }
      if(node.data.task==='segmentation'){
        const issue=classRuleIssue(node.data.params || {},false)??classIdIssue(pipeline,node,catalog);if(issue)return `${node.data.label}: ${issue}`;
        const requested=node.data.params?.class_names,recorded=modelClassNames(catalog,node.data.model_job_id);
        // The engine refuses a channel order that differs from the checkpoint; say so before a run does.
        if(Array.isArray(requested)&&requested.length&&recorded&&JSON.stringify(requested)!==JSON.stringify(recorded))return `${node.data.label}: 분할 클래스 순서(${requested.join(', ')})가 모델(${recorded.join(', ')})과 다릅니다.`;
      }
      const targets = outgoing.get(node.id) || [];
      if (!targets.length || targets.some((edge) => ![...operatorTypes, ...modelTypes, 'blob_measure', 'measurement', 'aggregate', 'decision'].includes(nodes.get(edge.target)?.data.node_type as FlowNodeType))) {
        return `${node.data.label}: 다음 모델, Blob, 집계 또는 판정 노드로 연결하세요.`;
      }
      return null;
    }],
    [blobs, (node) => {
      const issue=classRuleIssue(node.data.params || {},true)??classIdIssue(pipeline,node,catalog);if(issue)return `${node.data.label}: ${issue}`;
      const parents = incoming.get(node.id) || [];
      const source = nodes.get(parents[0]?.source);
      if (parents.length !== 1 || source?.data.node_type !== 'inspection' || !['segmentation','anomaly'].includes(source.data.task || '')) {
        return `${node.data.label}: Blob 측정에는 분할 모델 결과 연결선 하나가 필요합니다.`;
      }
      const targets = outgoing.get(node.id) || [];
      if (!targets.length || targets.some((edge) => !['aggregate', 'decision'].includes(nodes.get(edge.target)?.data.node_type || ''))) {
        return `${node.data.label}: Blob 결과를 집계 또는 판정 노드로 연결하세요.`;
      }
      for (const key of ['min_blob_area_px', 'min_blob_count_for_ng'] as const) {
        const value = node.data.params?.[key] ?? 1;
        if (!Number.isInteger(value) || value < 1) return `${node.data.label}: Blob 면적과 개수 기준은 1 이상의 정수여야 합니다.`;
      }
      return null;
    }],
    [measurements, (node) => {
      const parents=incoming.get(node.id)||[];
      if(parents.length!==1||!modelTypes.includes(nodes.get(parents[0]?.source)?.data.node_type as FlowNodeType))return `${node.data.label}: 기하 측정에는 모델 결과 하나가 필요합니다.`;
      const targets=outgoing.get(node.id)||[];
      if(!targets.length||targets.some(edge=>!['aggregate','decision'].includes(nodes.get(edge.target)?.data.node_type || '')))return `${node.data.label}: 측정 결과를 집계 또는 판정 노드로 연결하세요.`;
      const issue=measurementIssue(node.data.params||{});if(issue)return `${node.data.label}: ${issue}`;
      return null;
    }],
    [aggregates, (node) => {
      const parents = incoming.get(node.id) || [];
      if (parents.length < 1 || parents.length > 8 || parents.some((edge) =>
        ![...modelTypes, 'blob_measure', 'measurement'].includes(nodes.get(edge.source)?.data.node_type as FlowNodeType))) {
        return `${node.data.label}: 집계 노드에는 모델 또는 Blob 결과 연결선 1~8개가 필요합니다.`;
      }
      const targets = outgoing.get(node.id) || [];
      if (targets.length !== 1 || targets[0].target !== decisionId) return `${node.data.label}: 집계 결과를 판정 노드에 직접 연결하세요.`;
      if (node.data.rule !== 'any_ng' && node.data.rule !== 'all_ng') return `${node.data.label}: 집계 룰은 any_ng 또는 all_ng여야 합니다.`;
      return null;
    }],
  ];
  for (const [group, check] of nodeChecks) {
    for (const node of group) {
      const issue = guarded(`${node.data.label}: 저장된 설정 형식을 확인하세요.`, () => check(node));
      if (issue && report(issue, 'node', node.id)) return issues;
    }
  }
  const evidence = incoming.get(decisionId) || [];
  const decision = decisions[0];
  const decisionIssue = guarded('판정 노드의 저장된 룰 형식을 확인하세요.', () => {
    if (!evidence.length || evidence.some((edge) => !resultTypes.includes(nodes.get(edge.source)?.data.node_type as FlowNodeType))) {
      return '판정 노드에 모델, Blob 또는 집계 결과 연결선이 필요합니다.';
    }
    const rule = decision.data.rule || 'any_defect_is_ng';
    if (!['any_defect_is_ng', 'score_gt_threshold', 'max_flaws_allowed', 'aggregate_verdict'].includes(rule)) {
      return '지원하지 않는 판정 룰입니다.';
    }
    if (rule === 'aggregate_verdict' && (evidence.length !== 1 || nodes.get(evidence[0].source)?.data.node_type !== 'aggregate')) {
      return '집계 판정 룰에는 집계 결과 연결선 하나가 필요합니다.';
    }
    if (evidence.some((edge) => nodes.get(edge.source)?.data.node_type === 'aggregate') && rule !== 'aggregate_verdict') {
      return '집계 결과에는 집계 판정 룰을 선택하세요.';
    }
    if (rule === 'score_gt_threshold') {
      const issue=scoreSpecIssue(decision.data);
      if(issue)return issue;
      const spec=decision.data.score_spec;
      if(models.some(node=>Boolean(node.data.score_spec)!==Boolean(spec)||
        (spec&&['domain','unit','direction','calibration_id'].some(key=>node.data.score_spec?.[key as keyof typeof spec]!==spec[key as keyof typeof spec]))))return '전역 점수 룰에는 동일한 단위와 보정 식별자가 필요합니다.';
    }
    if (rule === 'max_flaws_allowed') {
      if (models.some((node) => node.data.node_type === 'inspection' && node.data.task === 'segmentation')) {
        return '분할 검사에서는 결함 덩어리 수를 세지 않으므로 허용 결함 개수 룰을 사용할 수 없습니다. 다른 판정 룰을 선택하세요.';
      }
      const allowed = decision.data.params?.max_flaws_allowed ?? 0;
      if (!Number.isInteger(allowed) || allowed < 0) return '허용 결함 개수는 0 이상의 정수여야 합니다.';
    }
    return null;
  });
  if (decisionIssue && report(decisionIssue, 'node', decisionId)) return issues;
  const branchEdges = outgoing.get(decisionId) || [];
  if ((branchEdges.length !== outputs.length || branchEdges.some((edge) => nodes.get(edge.target)?.data.node_type !== 'output'))
    && report('판정 노드를 모든 출력 노드에 연결하세요.', 'node', decisionId)) return issues;
  for (const node of outputs) {
    const parents = incoming.get(node.id) || [];
    if ((parents.length !== 1 || parents[0].source !== decisionId || outgoing.get(node.id)?.length)
      && report(`${node.data.label}: 출력 노드는 판정 분기 한 개를 받아야 합니다.`, 'node', node.id)) return issues;
  }
  if (outputs.length > 1) {
    const expected = outputs.length === 2 ? ['pass', 'fail'] : ['pass', 'fail', 'review'];
    if ((expected.some((branch) => !branchEdges.some((edge) => edge.isBranch === branch)) ||
      branchEdges.some((edge) => !expected.includes(edge.isBranch || '')))
      && report('출력 분기는 OK(pass), NG(fail), 필요하면 REVIEW(review)를 각각 지정하세요.', 'node', decisionId)) return issues;
  }
  const unresolved = firstUnvisitedNode(pipeline, incoming, outgoing, inputId);
  // An added node with no input that already shows its own problem does not also get the general message; a node in a
  // cycle, or reached only through one, always does.
  const ownProblem = issues.some((issue) => issue.kind === 'node' && issue.id === unresolved);
  if (unresolved && !(ownProblem && !(incoming.get(unresolved) || []).length)) {
    report('모든 노드를 입력부터 출력까지 순환 없이 연결하세요.', 'node', unresolved);
  }
  return issues;
}

/** Mirrors the backend's supported executable graph, including saved linear flows. */
export function validateFlowchartGraph(pipeline: FlowchartPipeline, models?: FlowModelVocabulary[]): string | null {
  return flowGraphIssues(pipeline, { first: true, models })[0]?.message ?? null;
}

/** The issues of each node and connection, for marking them on the canvas. */
export function flowIssuesByTarget(pipeline: FlowchartPipeline, models?: FlowModelVocabulary[]): { nodes: Map<string, string[]>; edges: Map<string, string[]> } {
  const nodes = new Map<string, string[]>();
  const edges = new Map<string, string[]>();
  for (const issue of flowGraphIssues(pipeline, { models })) {
    if (!issue.id || issue.kind === 'graph') continue;
    const target = issue.kind === 'node' ? nodes : edges;
    target.set(issue.id, [...(target.get(issue.id) || []), issue.message]);
  }
  return { nodes, edges };
}

/** Point an editor validation message at the node or connection the user can fix. */
export function locateFlowIssue(
  pipeline: FlowchartPipeline,
  message: string | null,
  models?: FlowModelVocabulary[],
): { kind: 'node' | 'edge'; id: string } | null {
  if (!message) return null;
  const first = flowGraphIssues(pipeline, { first: true, models })[0];
  // A connection saved without an id has only a positional key on the canvas; there is nothing to select.
  const selectable = first?.kind === 'node' || pipeline.edges.some((edge) => edge.id && edge.id === first?.id);
  if (first && first.message === message && first.id && first.kind !== 'graph' && selectable) return { kind: first.kind, id: first.id };
  const named = pipeline.nodes.find((node) => message.startsWith(`${node.data.label}:`));
  if (named) return { kind: 'node', id: named.id };
  if (message === '모든 노드를 입력부터 출력까지 순환 없이 연결하세요.') {
    const input = pipeline.nodes.find((node) => node.data.node_type === 'input');
    if (input) {
      const { incoming, outgoing } = graphParts(pipeline);
      const unresolved = firstUnvisitedNode(pipeline, incoming, outgoing, input.id);
      if (unresolved) return { kind: 'node', id: unresolved };
    }
  }
  if (message.includes('데이터 형식(payload)') || message.includes('노드 사이의 연결 형식')) {
    const nodes = new Map(pipeline.nodes.map((node) => [node.id, node]));
    const edge = pipeline.edges.find((item) => {
      const from = nodes.get(item.source)?.data.node_type;
      const to = nodes.get(item.target)?.data.node_type;
      if (!from || !to) return true;
      const allowed = allowedPayloads(from, to);
      return !allowed.length || Boolean(item.payload_type && !allowed.includes(item.payload_type));
    });
    if (edge) return { kind: 'edge', id: edge.id };
  }
  const type = message.includes('출력 분기') || message.includes('판정') ? 'decision'
    : message.includes('입력') ? 'input' : null;
  const node = type && pipeline.nodes.find((item) => item.data.node_type === type);
  return node ? { kind: 'node', id: node.id } : null;
}

/** Add a typed connection. Draft graphs may stay incomplete until all nodes connect. */
export function connectFlowNodes(pipeline: FlowchartPipeline, sourceId: string, targetId: string,
  sourcePort?: FlowPortPayload[]): FlowchartPipeline {
  const { nodes, incoming, outgoing } = graphParts(pipeline);
  const source = nodes.get(sourceId);
  const target = nodes.get(targetId);
  if (!source || !target || sourceId === targetId) throw new Error('연결할 노드를 선택하세요.');
  if (pipeline.edges.some((edge) => edge.source === sourceId && edge.target === targetId)) throw new Error('이미 연결된 노드입니다.');
  const from = source.data.node_type;
  const to = target.data.node_type;
  const allowed = allowedPayloads(from, to);
  if (!allowed.length) {
    // The payloads may look compatible (a result into a model input, say) while the node pair is not supported.
    const accepted = (flowNodePorts(target).inputs[0]?.payloads || []).map((payload) => PORT_KOREAN[payload]).join('·');
    throw new Error(`이 노드 사이의 연결은 지원하지 않습니다.${accepted ? ` ${target.data.label} 입력은 ${accepted}${objectParticle(accepted)} 받으며, ${source.data.label}에서 바로 연결할 수 없습니다.` : ''}`);
  }
  // The output port the connection was started from decides the payload; the wrong port is refused at once.
  const payloads = sourcePort ? allowed.filter((payload) => edgePayloads(sourcePort).includes(payload)) : allowed;
  if (!payloads.length) {
    const accepted = (flowNodePorts(target).inputs[0]?.payloads || []).map((payload) => PORT_KOREAN[payload]).join('·');
    throw new Error(`${source.data.label}의 ${(sourcePort || []).map((payload) => PORT_KOREAN[payload]).join('·')} 출력은 ${target.data.label}에 연결할 수 없습니다. 이 입력은 ${accepted}만 받습니다.`);
  }
  if (to === 'blob_measure' && (from !== 'inspection' || !['segmentation','anomaly'].includes(source.data.task || ''))) {
    throw new Error('Blob 측정은 분할 모델 결과에만 연결할 수 있습니다.');
  }
  if (to !== 'decision' && to !== 'aggregate' && (incoming.get(targetId)?.length || 0) > 0) throw new Error('대상 노드에는 이미 입력 연결이 있습니다.');
  if (to === 'aggregate' && (incoming.get(targetId)?.length || 0) >= 8) throw new Error('집계 입력은 최대 여덟 개입니다.');
  if (from === 'aggregate' && (outgoing.get(sourceId)?.length || 0) > 0) throw new Error('집계 노드는 판정 노드 하나로 연결하세요.');
  if (from === 'decision' && (outgoing.get(sourceId)?.length || 0) >= 3) throw new Error('판정 출력은 최대 세 개입니다.');

  const edgeId = `edge_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  let edges = pipeline.edges;
  let isBranch: Branch | undefined;
  if (from === 'decision') {
    const existing = outgoing.get(sourceId) || [];
    if (existing.length === 1 && (!existing[0].isBranch || existing[0].isBranch === 'default')) {
      edges = edges.map((edge) => edge.id === existing[0].id ? { ...edge, isBranch: 'pass' as const } : edge);
    }
    const assigned = new Set(edges.filter((edge) => edge.source === sourceId).map((edge) => edge.isBranch));
    isBranch = (['pass', 'fail', 'review'] as Branch[]).find((branch) => !assigned.has(branch));
    if (existing.length === 0) isBranch = undefined;
  }
  const payload_type: FlowEdge['payload_type'] = payloads.includes('roi') ? 'roi' : payloads[0];
  return { ...pipeline, edges: [...edges, { id: edgeId, source: sourceId, target: targetId, isBranch, payload_type }] };
}

export function removeFlowNode(pipeline: FlowchartPipeline, nodeId: string): FlowchartPipeline {
  const target = pipeline.nodes.find((node) => node.id === nodeId);
  if (!target) return pipeline;
  if (target.data.node_type === 'input' || target.data.node_type === 'decision') throw new Error('입력과 판정 노드는 삭제할 수 없습니다.');
  if (target.data.node_type === 'output' && pipeline.nodes.filter((node) => node.data.node_type === 'output').length === 1) {
    throw new Error('마지막 출력 노드는 삭제할 수 없습니다.');
  }
  return {
    ...pipeline,
    nodes: pipeline.nodes.filter((node) => node.id !== nodeId),
    edges: pipeline.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
  };
}

export function updateFlowEdgeBranch(pipeline: FlowchartPipeline, edgeId: string, branch: Branch): FlowchartPipeline {
  const edge = pipeline.edges.find((item) => item.id === edgeId);
  const sourceType = pipeline.nodes.find((node) => node.id === edge?.source)?.data.node_type;
  if (!edge || (!resultTypes.includes(sourceType as FlowNodeType) && sourceType !== 'decision')) throw new Error('결과 또는 판정 연결선만 분기를 바꿀 수 있습니다.');
  if (sourceType !== 'decision') return {
    ...pipeline, edges: pipeline.edges.map((item) => item.id === edgeId ? { ...item, isBranch: branch, predicate: undefined } : item),
  };
  const previous = edge.isBranch;
  return {
    ...pipeline,
    edges: pipeline.edges.map((item) => {
      if (item.id === edgeId) return { ...item, isBranch: branch, predicate: undefined };
      if (item.source === edge.source && item.isBranch === branch) return { ...item, isBranch: previous };
      return item;
    }),
  };
}

export function updateFlowEdgePayload(pipeline: FlowchartPipeline, edgeId: string, payload: NonNullable<FlowEdge['payload_type']>): FlowchartPipeline {
  const edge = pipeline.edges.find((item) => item.id === edgeId);
  if (!edge) throw new Error('연결선을 찾을 수 없습니다.');
  const sourceType = pipeline.nodes.find((node) => node.id === edge.source)?.data.node_type;
  const targetType = pipeline.nodes.find((node) => node.id === edge.target)?.data.node_type;
  if (!allowedPayloads(sourceType as FlowNodeType, targetType as FlowNodeType).includes(payload)) {
    throw new Error('이 노드 연결에서 지원하지 않는 데이터 형식입니다.');
  }
  return { ...pipeline, edges: pipeline.edges.map((item) => item.id === edgeId ? { ...item, payload_type: payload } : item) };
}

const finite=(value: unknown): value is number => typeof value==='number' && Number.isFinite(value);
const whole=(value:unknown,minimum=0):value is number => finite(value)&&Number.isInteger(value)&&value>=minimum;
const point=(value:unknown):value is [number,number]=>Array.isArray(value)&&value.length===2&&value.every(finite);
export function measurementIssue(params:Record<string,any>):string|null {
  const cal=params.calibration;
  if(cal && (cal.unit!=='mm'||!finite(cal.mm_per_pixel_x)||cal.mm_per_pixel_x<=0||!finite(cal.mm_per_pixel_y)||cal.mm_per_pixel_y<=0||!Array.isArray(cal.source_size)||cal.source_size.length!==2||!cal.source_size.every((n:unknown)=>whole(n,1)))) return '원본 크기와 양수 mm/px 교정값을 입력하세요.';
  const paths=params.paths ?? [];
  if(!Array.isArray(paths)||paths.length>64) return '측정 경로는 최대 64개입니다.';
  const ids=new Set();
  for(const path of paths) {
    if(!path||typeof path.id!=='string'||!path.id.trim()||ids.has(path.id)) return '측정 경로 이름은 중복 없이 입력하세요.';
    ids.add(path.id);
    if(!['polyline','bezier'].includes(path.interpolation ?? 'polyline')||!Array.isArray(path.points)||path.points.length<2||path.points.length>10000||!path.points.every(point)||(path.interpolation==='bezier'&&path.points.length!==4)) return '다각선은 2점 이상, 곡선은 제어점 4개가 필요합니다.';
    if(cal && path.points.some(([x,y]:number[])=>x<0||y<0||x>cal.source_size[0]||y>cal.source_size[1])) return '측정점은 교정된 원본 이미지 범위 안에 있어야 합니다.';
  }
  for(const kind of ['length','area']) {
    for(const bound of ['min','max']) if(params[`${bound}_${kind}`]!==undefined && (!finite(params[`${bound}_${kind}`])||params[`${bound}_${kind}`]<0)) return '측정 기준은 유한한 0 이상 값이어야 합니다.';
    if(params[`min_${kind}`]!==undefined&&params[`max_${kind}`]!==undefined&&params[`min_${kind}`]>params[`max_${kind}`]) return '최대 측정 기준은 최소 기준 이상이어야 합니다.';
  }
  return null;
}
export function classRuleIssue(params:Record<string,any>,blob:boolean):string|null {
  const ids=params.class_ids;
  if(ids!==undefined&&(!Array.isArray(ids)||!ids.length||ids.some((id:unknown)=>!whole(id,1))||new Set(ids).size!==ids.length)) return '클래스 ID는 중복 없는 1 이상의 정수여야 합니다.';
  if(blob&&!['defect_presence','required_structure'].includes(params.rule_mode ?? 'defect_presence')) return 'Blob 판정 방식을 확인하세요.';
  const rows=params.class_rules ?? [];
  if(!Array.isArray(rows)) return '클래스별 기준 목록을 확인하세요.';
  const seen=new Set();
  for(const row of rows) {
    if(!row||!whole(row.class_id,1)||seen.has(row.class_id)) return '클래스별 기준 ID가 중복되었거나 올바르지 않습니다.';
    seen.add(row.class_id);
    for(const key of (blob?['min_count','max_count','min_area_px','max_area_px']:['min_area_px','max_area_px'])) if(row[key]!==undefined&&!whole(row[key],blob?0:1)) return '클래스 개수·면적 기준은 허용 범위의 정수여야 합니다.';
    for(const key of (blob?['min_mean_grayscale','max_mean_grayscale']:['probability_threshold'])) if(row[key]!==undefined&&(!finite(row[key])||row[key]<0||row[key]>(blob?255:1))) return '확률 또는 평균 회색값 기준의 범위를 확인하세요.';
    for(const kind of (blob?['count','area_px','mean_grayscale']:['area_px'])) if(row[`min_${kind}`]!==undefined&&row[`max_${kind}`]!==undefined&&row[`min_${kind}`]>row[`max_${kind}`]) return '최대 클래스 기준은 최소 기준 이상이어야 합니다.';
  }
  return null;
}
export function ocrRuleIssue(params:Record<string,any>):string|null {
  if(('expected_text' in params)===('regex' in params)||typeof(params.expected_text ?? params.regex)!=='string'||!(params.expected_text||params.regex))return '기대 문자열 또는 정규식을 입력하세요.';
  if(params.regex) {try{if(params.regex.length>512)throw new Error();new RegExp(params.regex);}catch{return '정규식이 올바르지 않습니다.';}}
  if(params.correction_map!==undefined && (!params.correction_map||Array.isArray(params.correction_map)||typeof params.correction_map!=='object'||Object.entries(params.correction_map).some(([from,to])=>Array.from(from).length!==1||typeof to!=='string'||Array.from(to).length!==1)))return '문자 교정은 한 글자씩 지정하세요.';
  const seen=new Set();if(params.position_rules!==undefined&&!Array.isArray(params.position_rules))return '문자 위치 기준을 확인하세요.';
  for(const row of params.position_rules ?? []){
    if(!row||!whole(row.index)||seen.has(row.index)||(!row.fixed_char&&!row.allowed_chars)||(row.fixed_char!==undefined&&(typeof row.fixed_char!=='string'||Array.from(row.fixed_char).length!==1))||(row.allowed_chars!==undefined&&(typeof row.allowed_chars!=='string'||!row.allowed_chars)))return '문자 위치는 중복 없는 0 이상 인덱스와 허용·고정 문자가 필요합니다.';
    seen.add(row.index);
  }return null;
}
