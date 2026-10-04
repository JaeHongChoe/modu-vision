import type { FlowChangePreview, FlowConfigurationChange, FlowRuntimeStatus, FlowSemanticChange, FlowSemanticDelta } from '../../services/api';

/** E04: how an inspection-rule change is shown before it is saved and in the change record. */

const NODE_TYPES: Record<string, string> = {
  input: '입력', inspection: '검사 모델', detection_crop: '검출 ROI', fixed_roi: '고정 ROI', decision: '최종 판정',
  output: '출력', preprocess: '전처리', patch_split: '패치 분할', blob_measure: 'Blob 측정', measurement: '기하 측정',
  aggregate: '결과 집계',
};
const BRANCHES: Record<string, string> = { pass: '통과', fail: '불합격', review: '검토', default: '기본' };

function shown(value: unknown): string {
  if (value === undefined || value === null) return '없음';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function edge(change: FlowSemanticChange): string {
  const predicate = change.predicate as { operator?: string; class_name?: string; min_confidence?: number } | null | undefined;
  const condition = predicate?.class_name
    ? ` (${predicate.class_name} ${predicate.operator === 'absent' ? '없음' : '있음'}${predicate.min_confidence != null ? ` ≥ ${predicate.min_confidence}` : ''})`
    : change.isBranch ? ` (${BRANCHES[String(change.isBranch)] ?? String(change.isBranch)})` : '';
  return `${change.source} → ${change.target}${condition}`;
}

export function describeChange(change: FlowSemanticChange): string {
  const type = change.node_type ? NODE_TYPES[change.node_type] ?? change.node_type : '';
  switch (change.kind) {
    case 'node_added': return `노드 추가: ${change.node_id}${type ? ` (${type})` : ''}`;
    case 'node_removed': return `노드 삭제: ${change.node_id}${type ? ` (${type})` : ''}`;
    case 'node_changed': return `${change.node_id} · ${change.field}: ${shown(change.before)} → ${shown(change.after)}`;
    case 'edge_added': return `연결 추가: ${edge(change)}`;
    case 'edge_removed': return `연결 삭제: ${edge(change)}`;
    case 'execution_changed': return `실행 설정: ${shown(change.before)} → ${shown(change.after)}`;
    default: return JSON.stringify(change);
  }
}

/** One line for a recorded or previewed difference. */
export function summarizeDelta(delta: FlowSemanticDelta, first = false): string {
  if (delta.layout_only) return '배치·이름만 변경 (검사 규칙과 평가 근거 유지)';
  if (!delta.changes.length) return '검사 규칙 변경 없음';
  return first ? `새 검사 플로우 (규칙 ${delta.changes.length}개)` : `검사 규칙 변경 ${delta.changes.length}건`;
}

/** A reason is required when the change replaces the rules of an active version (not for a first flow, a layout move
 * or a save that changes nothing). */
export function reasonRequired(preview: FlowChangePreview | null): boolean {
  return Boolean(preview && preview.parent_revision && !preview.layout_only && preview.semantic_delta.changes.length > 0);
}

export function runtimeLabel(runtime: FlowRuntimeStatus | null | undefined): string {
  if (!runtime) return '운영 런타임 정보를 읽는 중입니다.';
  if (runtime.unreadable) return '운영 런타임 배포 기록을 읽을 수 없습니다.';
  const active = runtime.active
    ? `운영 런타임: 릴리스 ${(runtime.active.manifest_sha256 || '').slice(0, 12) || runtime.active.deployment_id.slice(0, 8)}${runtime.active.acknowledged ? ' · 확인(ACK) 완료' : ' · 확인 전'}`
    : '운영 런타임: 적용된 릴리스 없음';
  const pending = runtime.pending
    ? ` · 적용 중 ${(runtime.pending.manifest_sha256 || '').slice(0, 12) || runtime.pending.operation_id.slice(0, 8)} (${runtime.pending.status}, 확인 전에는 위 릴리스가 계속 실행)`
    : '';
  return active + pending;
}

export function changeTitle(change: FlowConfigurationChange): string {
  const when = new Date(change.time_ns / 1_000_000).toLocaleString('ko-KR');
  return `${when} · ${change.actor.name} · ${change.action === 'activate' ? '활성화' : '저장'}`;
}
