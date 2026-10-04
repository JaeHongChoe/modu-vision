import type {TrainingSchedulingOptions} from '../../stores/useTrainingStore';

export type TrainingSchedulingForm = {runtimeMinutes: string; queue: boolean; priority: string};
export const defaultTrainingScheduling: TrainingSchedulingForm = {runtimeMinutes: '', queue: true, priority: '0'};

export function trainingSchedulingOptions(value: TrainingSchedulingForm, remote: boolean): TrainingSchedulingOptions {
  const minutes = value.runtimeMinutes.trim() ? Number(value.runtimeMinutes) : null;
  if (minutes !== null && (!Number.isFinite(minutes) || minutes <= 0 || minutes > 10080)) throw new Error('학습 시간 제한은 0분 초과, 10080분(7일) 이하여야 합니다. 비워 두면 제한 없이 실행합니다.');
  const priority = Number(value.priority);
  if (!remote && (!value.priority.trim() || !Number.isInteger(priority) || priority < -10 || priority > 10)) throw new Error('학습 대기열 우선순위는 -10~10 정수여야 합니다.');
  return {queue: remote ? true : value.queue, priority: remote ? 0 : priority, ...(minutes !== null ? {max_runtime_s: minutes * 60} : {})};
}

export function TrainingSchedulingSettings({value, onChange, remote, disabled, error}: {
  value: TrainingSchedulingForm; onChange: (value: TrainingSchedulingForm) => void; remote: boolean; disabled: boolean; error: string | null;
}) {
  const input = 'mt-1 w-full rounded border border-slate-600 bg-[#0B1520] p-2 disabled:opacity-40';
  return <details className="rounded border border-slate-600 bg-[#111C2A] p-3 text-sm text-slate-200">
    <summary className="cursor-pointer">학습 실행 예산·대기열</summary>
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <label>학습 시간 제한 (분)<input aria-label="학습 시간 제한 (분)" type="number" min="0.001" max="10080" step="any"
        value={value.runtimeMinutes} disabled={disabled} placeholder="비워 두면 제한 없음" className={input}
        onChange={event => onChange({...value, runtimeMinutes: event.target.value})} /></label>
      <label>학습 대기열 우선순위<input aria-label="학습 대기열 우선순위" type="number" min="-10" max="10" step="1"
        value={remote ? '0' : value.priority} disabled={disabled || remote} className={input}
        onChange={event => onChange({...value, priority: event.target.value})} /></label>
      <label className="flex items-center gap-2 sm:col-span-2"><input type="checkbox" aria-label="장치가 사용 중이면 대기열에 넣기"
        checked={remote || value.queue} disabled={disabled || remote} onChange={event => onChange({...value, queue: event.target.checked})} />장치가 사용 중이면 대기열에 넣기</label>
    </div>
    <p className="mt-2 text-xs text-slate-400">{remote ? '서버 대기열은 등록 순서로 실행합니다. 대기 거절·우선순위 변경은 지원하지 않습니다.' : '높은 우선순위를 먼저 실행하되 프로젝트 간 순서를 조정합니다. 대기열을 끄면 장치 사용 중인 요청은 거절합니다.'}</p>
    <p className="mt-1 text-xs text-slate-400">시간 제한은 실행 시도 시작부터 계산합니다. 초과하면 현재 작업의 취소를 요청하고 종료·예약 반환을 확인합니다. 대기 시간은 제외하며 즉시 종료를 보장하지 않습니다. 앱의 백엔드가 종료되거나 서버 연결이 끊기면 제한·종료 확인이 지연될 수 있습니다.</p>
    {error && <p role="alert" className="mt-2 text-amber-300">{error}</p>}
  </details>;
}
