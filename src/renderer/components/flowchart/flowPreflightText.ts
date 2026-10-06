import type { PreflightReport, PreflightRequirement, PreflightTarget } from '../../services/flowPreflight';

/** E07: how a deployment preflight reads. */

export const PREFLIGHT_TARGETS: { key: string; label: string; target: PreflightTarget }[] = [
  { key: 'local-cpu', label: '이 컴퓨터 · CPU', target: { kind: 'this_computer', device: 'cpu' } },
  { key: 'local-cuda', label: '이 컴퓨터 · CUDA', target: { kind: 'this_computer', device: 'cuda:0' } },
  { key: 'local-mps', label: '이 컴퓨터 · Apple MPS', target: { kind: 'this_computer', device: 'mps' } },
  { key: 'edge-windows-cpu', label: 'Edge · Windows x64 · CPU', target: { kind: 'edge', profile: 'edge_cpu', os: 'windows', architecture: 'x86_64', device: 'cpu' } },
  { key: 'edge-linux-cpu', label: 'Edge · Linux x64 · CPU', target: { kind: 'edge', profile: 'edge_cpu', os: 'linux', architecture: 'x86_64', device: 'cpu' } },
  { key: 'edge-linux-arm-cpu', label: 'Edge · Linux arm64 · CPU', target: { kind: 'edge', profile: 'edge_cpu', os: 'linux', architecture: 'arm64', device: 'cpu' } },
  { key: 'edge-linux-cuda', label: 'Edge · Linux x64 · CUDA', target: { kind: 'edge', profile: 'edge_cuda', os: 'linux', architecture: 'x86_64', device: 'cuda:0' } },
];

export function sameTarget(a: PreflightTarget, b: PreflightTarget): boolean {
  if(a.kind==='selected_compute'||b.kind==='selected_compute')return a.kind==='selected_compute'&&b.kind==='selected_compute'&&a.compute_profile_id===b.compute_profile_id&&a.device===b.device;
  const keys = (value: PreflightTarget) => JSON.stringify(Object.keys(value).sort().map(key => [key, (value as unknown as Record<string, unknown>)[key]]));
  return keys(a) === keys(b);
}

export const KIND_NAMES: Record<PreflightRequirement['kind'], string> = { model: '모델', calibration: '교정', runtime: '런타임', device: '장치' };
export const STATE_NAMES: Record<PreflightRequirement['state'], string> = {
  ready: '준비됨', missing: '없음', mismatch: '불일치', unavailable: '사용 불가', unverified: '대상에서 확인 필요',
};

export function statusLine(report: Pick<PreflightReport, 'status' | 'blocked_nodes' | 'decision_blocked' | 'counts'> & { stale?: boolean }): string {
  if (report.stale) return '이전 점검 결과입니다. 현재 준비 상태를 확인하려면 다시 점검하세요.';
  if (report.status === 'ready') return '모든 의존성이 이 대상에서 준비됐습니다.';
  if (report.status === 'unverified') return `대상 장비에서 확인할 항목 ${report.counts.unverified}개: 패키지를 설치한 뒤 run_flow.py --preflight로 확인하세요.`;
  const nodes = Object.keys(report.blocked_nodes).length;
  return `차단됨: 노드 ${nodes}개가 막혔습니다${report.decision_blocked ? ' (최종 판정 포함). 대체 실행 없이 이 대상에는 배포할 수 없습니다.' : '.'}`;
}

export function staleLine(reasons: string[]): string {
  const names: Record<string, string> = { release_changed: '플로우 버전이 바뀌었습니다', target_changed: '선택한 대상과 다릅니다',
    selected_environment_not_rechecked:'선택 서버의 현재 환경은 다시 확인하지 않았습니다',
    environment_changed: '이 컴퓨터의 런타임 구성(패키지·버전·장치)이 바뀌었습니다', artifacts_changed: '모델 또는 교정 파일이 바뀌었습니다' };
  return `이전 점검 결과입니다: ${reasons.map(reason => names[reason] ?? reason).join(', ')}. 다시 점검하세요.`;
}

/** The requirements grouped by node in flow order; the whole flow's own requirements first. */
export function byNode(requirements: PreflightRequirement[], order: string[]): [string | null, PreflightRequirement[]][] {
  const groups = new Map<string | null, PreflightRequirement[]>();
  for (const requirement of requirements) groups.set(requirement.node_id, [...(groups.get(requirement.node_id) ?? []), requirement]);
  const rank = (id: string | null) => (id === null ? -1 : order.indexOf(id) === -1 ? order.length : order.indexOf(id));
  return [...groups.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
}

/** What to do, in the app's words (the report's own remedy text, written for the package CLI too, stays as detail). */
export function remedyText(row: PreflightRequirement): string | null {
  if (row.state === 'ready') return null;
  if (row.state === 'unverified') return '대상 장비에 패키지를 설치한 뒤 run_flow.py --preflight로 확인하세요.';
  switch (row.kind) {
    case 'model': return row.state === 'missing'
      ? '3단계에서 모델을 학습하거나 이 노드에 완료 모델을 연결하세요.'
      : '이 모델을 쓸 수 없습니다(현재 데이터·저장 플로우와 맞지 않음). 다시 학습하거나 다른 완료 모델을 연결하세요.';
    case 'calibration': return `이 프로젝트에 교정 ${row.artifact_ref.slice(0, 32)}…이 없습니다. 측정 노드에서 교정을 다시 만들거나 프로젝트에 있는 교정을 고르세요.`;
    case 'runtime': return `${row.artifact_ref} ${row.version_range ?? ''}을(를) 실행 환경에 설치하세요. 없으면 이 노드는 실행되지 않습니다.`;
    case 'device': return `이 컴퓨터에 ${row.artifact_ref} 장치가 없습니다. 장치가 있는 대상을 고르세요(다른 장치로 대신 실행하지 않습니다).`;
    default: return row.remedy;
  }
}
