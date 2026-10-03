// S1-05: this computer as a worker. Each stage on each device is one of four states from the backend's support
// decision; only a real preflight on this computer's current runtime makes a stage "검증됨".
export type SupportState = 'verified' | 'unverified' | 'not_installed' | 'unsupported';
export type Decision = { state: SupportState; reason: string };
export type PreflightRow = { passed: boolean; reason: string; seconds: number; at: number; evidence?: Record<string, unknown> };
export type LocalWorker = {
  worker_id: string; devices: Array<{ kind: string; name: string }>;
  support: Record<string, Record<string, Record<string, Decision>>>;
  preflight: Record<string, PreflightRow>; preflight_tasks: string[]; preflight_record_error: string | null;
  /** The small architecture each family's preflight trains (not necessarily the family's default model). */
  preflight_architectures?: Record<string, string>;
  /** Why this computer's compute is reserved now (a training or another preflight in any app process), or null. */
  local_compute_busy?: string | null;
};
export type RunningPreflight = { task: string; device: string; stages: string[] } | null;
export type LastPreflight = { task: string; device: string; results: Record<string, PreflightRow>; error: string | null };
export type WorkersState = { workers: LocalWorker[]; running_preflight: RunningPreflight; last_preflight: LastPreflight | null };

export const SUPPORT_LABEL: Record<SupportState, string> = { verified: '검증됨', unverified: '미검증', not_installed: '미설치', unsupported: '미지원' };
export const SUPPORT_STAGES: Array<[string, string]> = [['train', '학습'], ['evaluate', '평가'], ['infer', '추론'], ['export', '내보내기'], ['search', '자동 탐색']];
const PREFLIGHT_STAGES = ['train', 'evaluate', 'infer', 'export'];

/** Why a preflight of ``task`` on ``device`` cannot start now, or null; the backend refuses the same cases. */
export function preflightBlocker(worker: LocalWorker, task: string, device: string, running: RunningPreflight): string | null {
  if (running) return `다른 사전 점검(${running.task} · ${running.device.toUpperCase()})이 실행 중입니다.`;
  if (!worker.preflight_tasks.includes(task)) return '이 모델군의 사전 점검은 아직 제공되지 않습니다.';
  if (worker.local_compute_busy) return worker.local_compute_busy;
  for (const stage of PREFLIGHT_STAGES) {
    const decision = worker.support[task]?.[stage]?.[device];
    if (!decision) return '이 장치의 지원 상태를 확인하지 못했습니다.';
    if (decision.state === 'unsupported' || decision.state === 'not_installed') return decision.reason;
  }
  return null;
}

/** What a family's preflight checks: the architecture it trains, which a verified state is about. */
export function preflightScope(worker: LocalWorker, task: string): string {
  const architecture = worker.preflight_architectures?.[task];
  return architecture
    ? `${architecture} 구조를 작은 합성 데이터로 학습·평가·추론하고 플로우 패키지로 내보내 실행한 결과입니다. 모델군 기본 구조의 가중치와 의존성, ONNX 등 다른 내보내기 형식은 이 점검에 포함되지 않습니다.`
    : '작은 합성 데이터로 학습·평가·추론하고 플로우 패키지로 내보내 실행한 결과입니다.';
}

/** The last preflight's outcome per stage; a failed stage keeps its reason. */
export function lastPreflightText(last: LastPreflight): { text: string; ok: boolean } {
  const device = last.device.toUpperCase();
  if (last.error) return { text: `마지막 ${device} 사전 점검: 기록 실패 · ${last.error}`, ok: false };
  const rows = Object.entries(last.results);
  const parts = rows.map(([stage, row]) => `${SUPPORT_STAGES.find(([key]) => key === stage)?.[1] ?? stage} ${row.passed ? '통과' : `실패(${row.reason})`}`);
  return { text: `마지막 ${device} 사전 점검: ${parts.join(' · ')}`, ok: rows.length > 0 && rows.every(([, row]) => row.passed) };
}
