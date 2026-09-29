import type { FlowEdge, FlowNode, FlowNodeData, FlowchartPipeline } from '../../types';

type FlowNodeType = FlowNode['data']['node_type'];
type Branch = NonNullable<FlowEdge['isBranch']>;

const modelTypes: FlowNodeType[] = ['detection_crop', 'inspection'];

export function decisionRulePatch(data: FlowNodeData, rule: string): Partial<FlowNodeData> {
  if (rule !== 'score_gt_threshold') return { rule };
  const current = data.threshold;
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

/** Mirrors the backend's supported executable graph, including saved linear flows. */
export function validateFlowchartGraph(pipeline: FlowchartPipeline): string | null {
  const { nodes, incoming, outgoing } = graphParts(pipeline);
  if (nodes.size !== pipeline.nodes.length) return '노드 ID가 중복되었습니다.';
  const inputs = pipeline.nodes.filter((node) => node.data.node_type === 'input');
  const decisions = pipeline.nodes.filter((node) => node.data.node_type === 'decision');
  const outputs = pipeline.nodes.filter((node) => node.data.node_type === 'output');
  const models = pipeline.nodes.filter((node) => modelTypes.includes(node.data.node_type));
  const fixedRois = pipeline.nodes.filter((node) => node.data.node_type === 'fixed_roi');
  if (inputs.length !== 1) return '입력 노드는 하나여야 합니다.';
  if (decisions.length !== 1) return '판정 노드는 하나여야 합니다.';
  if (outputs.length < 1 || outputs.length > 3) return '출력 노드는 1~3개가 필요합니다.';
  if (models.length < 1 || models.length > 8) return '모델 노드는 1~8개가 필요합니다.';
  if (fixedRois.length > 8) return '고정 ROI 노드는 최대 8개입니다.';
  const edgeIds = new Set<string>();
  const connections = new Set<string>();
  for (const edge of pipeline.edges) {
    if (!edge.id || edgeIds.has(edge.id)) return '연결선 ID가 중복되었거나 비었습니다.';
    edgeIds.add(edge.id);
    if (!nodes.has(edge.source) || !nodes.has(edge.target) || edge.source === edge.target) return '연결선의 시작 또는 끝 노드가 올바르지 않습니다.';
    const pair = `${edge.source}\0${edge.target}`;
    if (connections.has(pair)) return '같은 노드 사이의 연결선이 중복되었습니다.';
    connections.add(pair);
    const from = nodes.get(edge.source)?.data.node_type;
    const to = nodes.get(edge.target)?.data.node_type;
    const allowedPayloads = from === 'input' && (modelTypes.includes(to as FlowNodeType) || to === 'fixed_roi') ? ['image']
      : from === 'fixed_roi' && modelTypes.includes(to as FlowNodeType) ? ['roi']
      : modelTypes.includes(from as FlowNodeType) && modelTypes.includes(to as FlowNodeType) ? ['image', 'roi']
      : modelTypes.includes(from as FlowNodeType) && to === 'decision' ? ['result']
      : from === 'decision' && to === 'output' ? ['result'] : [];
    if (allowedPayloads.length === 0) return '노드 사이의 연결 형식이 올바르지 않습니다.';
    if (edge.payload_type && !allowedPayloads.includes(edge.payload_type)) return '연결선의 데이터 형식(payload)이 노드와 맞지 않습니다.';
    if ((from === 'input' || from === 'fixed_roi') && edge.isBranch && edge.isBranch !== 'default') {
      return '입력과 고정 ROI 연결에는 조건 분기를 지정할 수 없습니다.';
    }
  }
  const inputId = inputs[0].id;
  const decisionId = decisions[0].id;
  if (incoming.get(inputId)?.length || !outgoing.get(inputId)?.length) return '입력 노드에서 모델 노드로 연결하세요.';
  for (const fixedRoi of fixedRois) {
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
  }
  for (const node of models) {
    const modelThreshold = node.data.threshold === undefined ? 0.5 : node.data.threshold;
    if (!Number.isFinite(modelThreshold) || modelThreshold < 0 || modelThreshold > 1) {
      return `${node.data.label}: 모델 임계치는 0~1이어야 합니다.`;
    }
    if (node.data.crop_padding !== undefined && (!Number.isInteger(node.data.crop_padding) || node.data.crop_padding < 0)) {
      return `${node.data.label}: ROI 패딩은 0 이상의 정수여야 합니다.`;
    }
    const parent = incoming.get(node.id) || [];
    if (parent.length !== 1) return `${node.data.label}: 모델 입력 연결선이 정확히 하나 필요합니다.`;
    const parentType = nodes.get(parent[0].source)?.data.node_type;
    if (parentType !== 'input' && parentType !== 'fixed_roi' && !modelTypes.includes(parentType as FlowNodeType)) {
      return `${node.data.label}: 지원하지 않는 상류 연결입니다.`;
    }
    if (node.data.node_type === 'inspection' && !['segmentation', 'classification', 'anomaly'].includes(node.data.task || '')) {
      return `${node.data.label}: 지원하지 않는 검사 작업입니다.`;
    }
    const targets = outgoing.get(node.id) || [];
    if (!targets.length || targets.some((edge) => ![...modelTypes, 'decision'].includes(nodes.get(edge.target)?.data.node_type as FlowNodeType))) {
      return `${node.data.label}: 다음 모델 또는 판정 노드로 연결하세요.`;
    }
  }
  const evidence = incoming.get(decisionId) || [];
  if (!evidence.length || evidence.some((edge) => !modelTypes.includes(nodes.get(edge.source)?.data.node_type as FlowNodeType))) {
    return '판정 노드에 모델 결과 연결선이 필요합니다.';
  }
  const decision = decisions[0];
  const rule = decision.data.rule || 'any_defect_is_ng';
  if (!['any_defect_is_ng', 'score_gt_threshold', 'max_flaws_allowed'].includes(rule)) {
    return '지원하지 않는 판정 룰입니다.';
  }
  if (rule === 'score_gt_threshold' &&
    (typeof decision.data.threshold !== 'number' || !Number.isFinite(decision.data.threshold) ||
      decision.data.threshold < 0 || decision.data.threshold > 1)) {
    return '판정 점수 임계치는 0~1이어야 합니다.';
  }
  if (rule === 'max_flaws_allowed') {
    if (models.some((node) => node.data.node_type === 'inspection' && node.data.task === 'segmentation')) {
      return '분할 검사에서는 결함 덩어리 수를 세지 않으므로 허용 결함 개수 룰을 사용할 수 없습니다. 다른 판정 룰을 선택하세요.';
    }
    const allowed = decision.data.params?.max_flaws_allowed ?? 0;
    if (!Number.isInteger(allowed) || allowed < 0) return '허용 결함 개수는 0 이상의 정수여야 합니다.';
  }
  const branchEdges = outgoing.get(decisionId) || [];
  if (branchEdges.length !== outputs.length || branchEdges.some((edge) => nodes.get(edge.target)?.data.node_type !== 'output')) {
    return '판정 노드를 모든 출력 노드에 연결하세요.';
  }
  for (const node of outputs) {
    const parents = incoming.get(node.id) || [];
    if (parents.length !== 1 || parents[0].source !== decisionId || outgoing.get(node.id)?.length) {
      return `${node.data.label}: 출력 노드는 판정 분기 한 개를 받아야 합니다.`;
    }
  }
  if (outputs.length > 1) {
    const expected = outputs.length === 2 ? ['pass', 'fail'] : ['pass', 'fail', 'review'];
    if (expected.some((branch) => !branchEdges.some((edge) => edge.isBranch === branch)) ||
      branchEdges.some((edge) => !expected.includes(edge.isBranch || ''))) {
      return '출력 분기는 OK(pass), NG(fail), 필요하면 REVIEW(review)를 각각 지정하세요.';
    }
  }
  const pending = new Map(pipeline.nodes.map((node) => [node.id, incoming.get(node.id)?.length || 0]));
  const ready = [inputId];
  let visited = 0;
  while (ready.length) {
    const nodeId = ready.shift() as string;
    visited += 1;
    for (const edge of outgoing.get(nodeId) || []) {
      const remaining = (pending.get(edge.target) || 0) - 1;
      pending.set(edge.target, remaining);
      if (remaining === 0) ready.push(edge.target);
    }
  }
  return visited === nodes.size ? null : '모든 노드를 입력부터 출력까지 순환 없이 연결하세요.';
}

/** Add a typed connection. Draft graphs may stay incomplete until all nodes connect. */
export function connectFlowNodes(pipeline: FlowchartPipeline, sourceId: string, targetId: string): FlowchartPipeline {
  const { nodes, incoming, outgoing } = graphParts(pipeline);
  const source = nodes.get(sourceId);
  const target = nodes.get(targetId);
  if (!source || !target || sourceId === targetId) throw new Error('연결할 노드를 선택하세요.');
  if (pipeline.edges.some((edge) => edge.source === sourceId && edge.target === targetId)) throw new Error('이미 연결된 노드입니다.');
  const from = source.data.node_type;
  const to = target.data.node_type;
  const allowed = (from === 'input' && (to === 'fixed_roi' || to === 'detection_crop' || to === 'inspection')) ||
    (from === 'fixed_roi' && modelTypes.includes(to)) ||
    (modelTypes.includes(from) && (modelTypes.includes(to) || to === 'decision')) ||
    (from === 'decision' && to === 'output');
  if (!allowed) throw new Error('이 노드 사이의 연결은 지원하지 않습니다.');
  if (to !== 'decision' && (incoming.get(targetId)?.length || 0) > 0) throw new Error('대상 노드에는 이미 입력 연결이 있습니다.');
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
  const payload_type: FlowEdge['payload_type'] = from === 'input' ? 'image'
    : to === 'decision' || to === 'output' ? 'result' : 'roi';
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
  if (!edge || (!modelTypes.includes(sourceType as FlowNodeType) && sourceType !== 'decision')) throw new Error('모델 또는 판정 연결선만 분기를 바꿀 수 있습니다.');
  if (sourceType !== 'decision') return {
    ...pipeline, edges: pipeline.edges.map((item) => item.id === edgeId ? { ...item, isBranch: branch } : item),
  };
  const previous = edge.isBranch;
  return {
    ...pipeline,
    edges: pipeline.edges.map((item) => {
      if (item.id === edgeId) return { ...item, isBranch: branch };
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
  const allowed = sourceType === 'input' ? payload === 'image'
    : sourceType === 'fixed_roi' ? payload === 'roi'
    : targetType === 'decision' || targetType === 'output' ? payload === 'result'
    : payload === 'image' || payload === 'roi';
  if (!allowed) throw new Error('이 노드 연결에서 지원하지 않는 데이터 형식입니다.');
  return { ...pipeline, edges: pipeline.edges.map((item) => item.id === edgeId ? { ...item, payload_type: payload } : item) };
}
