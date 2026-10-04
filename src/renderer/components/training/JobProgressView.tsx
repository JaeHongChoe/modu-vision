import {Loader2, RefreshCw, Square} from 'lucide-react';
import {useComputeStore} from '../../stores/useComputeStore';
import {jobProgress, type JobRecord} from './jobProgress';
import {normalizeTask, observationSummary} from './taskCenterModel';

const button = 'rounded border border-[#344255] px-3 py-2 text-slate-200 hover:bg-[#25344A] disabled:opacity-40';
const toneColor = {active: 'text-cyan-200', uncertain: 'text-amber-200', completed: 'text-emerald-300', stopped: 'text-slate-300', failed: 'text-rose-300'};
const barColor = {active: 'bg-cyan-500', uncertain: 'bg-amber-500', completed: 'bg-emerald-500', stopped: 'bg-slate-500', failed: 'bg-rose-500'};

/** S2-09: the progress of one training job, shown the same way in every model family's workbench: its state, where it
 *  runs, epochs and loss, a failure with its recorded cause, the next action, and cancel or reconnect when they apply. */
export function JobProgressView({job, busy, onCancel, onReconnect, showActions = true}: {job: JobRecord | null; busy: boolean; onCancel: () => void; onReconnect?: () => void; showActions?: boolean}) {
  const profiles = useComputeStore((state) => state.profiles);
  if (!job) return null;
  const view = jobProgress(job);
  const observation = observationSummary(normalizeTask('training', job));
  const waitReason: Record<string, string> = {
    device_reserved: '장치 예약이 반환되면 다시 확인합니다.', uncertain_reservation: '이전 작업의 종료와 장치 예약을 확인해야 합니다.',
    priority: '앞선 우선순위 작업을 기다립니다.', project_quota: '이 프로젝트의 실행 한도에 빈자리가 필요합니다.',
    legacy_app_active: '다른 앱의 장치 사용이 끝나기를 기다립니다.', external_reservation: '다른 실행 주체의 장치 예약을 기다립니다.',
    capability_mismatch: '이 작업을 실행할 수 있는 장치를 기다립니다.',
  };
  const stopping = view.stopping && job.cancel_supported !== false;  // a pending stop, shown only for a job that can be cancelled
  const where = view.server ? profiles.find((profile) => profile.id === view.server)?.name || '저장 서버' : '이 컴퓨터';
  return <div role="status" aria-label="학습 작업 상태" className="space-y-2 rounded border border-[#344255] bg-[#0B1520] p-3">
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <strong className={toneColor[view.tone]}>{view.tone === 'active' && <Loader2 className="mr-1 inline h-4 w-4 animate-spin" />}{view.label}</strong>
      <span className="text-slate-300">{where}</span>
      {(view.totalEpochs > 0 || view.epoch > 0) && <span>epoch {view.epoch}/{view.totalEpochs || '?'}</span>}
      {view.batches > 0 && <span>batch {view.batch}/{view.batches}</span>}
      {view.loss !== null && <span className="text-slate-400">손실 {view.loss.toFixed(4)}</span>}
    </p>
    {view.totalEpochs > 0 && <div role="progressbar" aria-label="학습 진행률" aria-valuemin={0} aria-valuemax={view.totalEpochs} aria-valuenow={view.epoch}
      className="h-1.5 overflow-hidden rounded bg-slate-800"><div className={`h-full ${barColor[view.tone]}`} style={{width: `${Math.min(100, (view.epoch / view.totalEpochs) * 100)}%`}} /></div>}
    {view.failure && <p role="alert" className="break-words text-rose-300">{view.failure}</p>}
    {job.status === 'queued' && <p className="text-slate-300">{Number.isInteger(job.queue_position) && job.queue_position > 0 ? `이 프로젝트 대기 순서: ${job.queue_position}` : '대기 순서 미확인'} · {waitReason[job.wait_reason] || '대기 사유를 다시 확인하세요.'}</p>}
    {observation && <div className="space-y-1 text-xs">
      {observation.steps.length > 0 && <ol aria-label="취소 확인 단계" className="flex flex-wrap gap-2">{observation.steps.map(step => <li key={step.label} className={step.done ? 'text-emerald-300' : 'text-slate-400'}>{step.done ? '✓' : '○'} {step.label}</li>)}</ol>}
      {observation.facts.length > 0 && <p>{observation.facts.join(' · ')}</p>}
    </div>}
    {view.nextAction && <p className={view.tone === 'failed' ? 'text-rose-200' : 'text-amber-200'}>다음 행동: {view.nextAction}</p>}
    {showActions && (view.canCancel || stopping || (view.canReconnect && onReconnect)) && <div className="flex flex-wrap gap-2">
      {(view.canCancel || stopping) && <button type="button" className={button} disabled={busy || view.stopping} onClick={onCancel}>
        <Square className="mr-1 inline h-3 w-3" />{view.stopping ? '종료 확인 중' : '취소 요청'}</button>}
      {view.canReconnect && onReconnect && <button type="button" className={button} disabled={busy} onClick={onReconnect}>
        <RefreshCw className="mr-1 inline h-3 w-3" />{view.server ? '같은 서버 작업 재연결' : '같은 로컬 작업 재연결'}</button>}
    </div>}
  </div>;
}
