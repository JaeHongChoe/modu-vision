/** S2-09: one reading of a training job for every model family, so each workbench and the task center show its state,
 *  progress, location, uncertainty, failure and next action the same way. The four record shapes are the core store
 *  (current_epoch/total_epochs), the program workbenches (epoch/epochs), rotated detection (epochs_completed) and the
 *  specialized families (epoch/epochs with batches). */
export const JOB_STATUS_LABELS: Record<string, string> = {
  queued: '대기', preparing: '준비 중', transferring: '전송 중', running: '실행 중',
  stopping: '취소 요청 · 종료 확인 중', cancelling: '취소 요청 · 종료 확인 중', unverified: '저장 기록 · 검증 필요',
  syncing: '결과 동기화 중', reconnecting: '재연결 중', disconnected: '연결 끊김 · 상태 미확인', completed: '완료 후보', aborted: '중단 확인',
  cancelled: '취소 확인', failed: '실패', stopped: '중단 확인', interrupted: '실행 주체 없음 · 재개 확인 필요',
};
const ACTIVE = ['queued', 'preparing', 'transferring', 'running', 'stopping', 'cancelling', 'syncing'];
const UNCERTAIN = ['disconnected', 'unverified', 'interrupted'];
const STOPPED = ['aborted', 'cancelled', 'stopped'];
const PHASES = ['preparing', 'transferring', 'syncing', 'reconnecting'];
const TERMINAL = ['completed', 'failed', ...STOPPED];
// What a family job without the backend's own observation should say; the disconnected text is the backend's
// network_lost next action, so the two never disagree.
const DEFAULT_NEXT: Record<string, string> = {
  disconnected: '작업 장비와 연결이 끊겼습니다. 연결을 복구하면 같은 작업을 다시 관찰합니다. 결과가 확인될 때까지 장비 예약은 유지됩니다.',
  unverified: '저장된 기록만 있고 실행 상태는 아직 확인되지 않았습니다. 같은 작업의 상태를 다시 확인하세요.',
  interrupted: '앱이 다시 시작될 때 이 작업의 실행 주체를 찾지 못했습니다. 후보 모델은 등록되지 않았습니다. 같은 설정으로 다시 학습하세요.',
  failed: '작업이 실패했습니다. 오류 내용을 확인하고 설정을 고친 뒤 다시 실행하세요.',
};

export type JobRecord = Record<string, any> & {status: string};
export type JobProgress = {
  status: string; label: string; tone: 'active' | 'uncertain' | 'completed' | 'stopped' | 'failed';
  /** Keep reading the job: it is active, or its state may still change (a dropped connection, an unverified record). */
  watch: boolean; stopping: boolean;
  epoch: number; totalEpochs: number; batch: number; batches: number; loss: number | null;
  /** The compute profile the job runs on; null for this computer. */
  server: string | null;
  failure: string | null; nextAction: string | null; canCancel: boolean; canReconnect: boolean;
};

/** Still working: a new start waits for it. */
export const activeJob = (status: string) => ACTIVE.includes(status);
export const watchJob = (status: string) => ACTIVE.includes(status) || status === 'disconnected' || status === 'unverified';
/** The task center and every workbench offer cancel by this one rule. */
export const cancellable = (job: {status: string; cancel_supported?: boolean}) =>
  job.cancel_supported !== false && !TERMINAL.includes(job.status) && !['interrupted', 'stopping', 'cancelling'].includes(job.status);
const number = (...values: unknown[]) => {const found = values.find((value) => typeof value === 'number' && Number.isFinite(value)); return typeof found === 'number' ? found : null;};
const message = (error: unknown): string | null => {
  if (!error) return null;
  if (typeof error === 'string') return error;
  if (typeof error === 'object' && typeof (error as {message?: unknown}).message === 'string') return (error as {message: string}).message;
  return JSON.stringify(error);
};

export function jobProgress(job: Record<string, any>): JobProgress {
  const status = String(job.status || '');
  const tone = status === 'completed' ? 'completed' : status === 'failed' ? 'failed' : STOPPED.includes(status) ? 'stopped'
    : UNCERTAIN.includes(status) ? 'uncertain' : 'active';
  const server = typeof job.compute_profile_id === 'string' && job.compute_profile_id ? job.compute_profile_id : null;
  // an error kept from before a successful reconnect is not this active job's failure
  const failure = tone === 'active' || tone === 'completed' ? null : message(job.error);
  const observed = typeof job.observation?.next_action === 'string' && job.observation.next_action ? job.observation.next_action : null;
  return {
    // a server job reports its transfer, result sync and reconnection as a phase while its status stays running
    status, label: (status === 'running' && PHASES.includes(job.phase) ? JOB_STATUS_LABELS[job.phase] : JOB_STATUS_LABELS[status]) || status, tone, watch: watchJob(status), stopping: ['stopping', 'cancelling'].includes(status),
    epoch: number(job.current_epoch, job.epoch, job.epochs_completed) ?? 0,
    totalEpochs: number(job.total_epochs, job.epochs) ?? 0,
    batch: number(job.current_step, job.batch) ?? 0, batches: number(job.total_steps, job.batches) ?? 0,
    loss: number(job.current_train_loss, job.loss),
    server, failure,
    nextAction: status === 'completed' ? null : observed ?? DEFAULT_NEXT[status] ?? null,
    canCancel: cancellable({status, cancel_supported: job.cancel_supported}), canReconnect: status === 'disconnected' && server !== null,
  };
}
