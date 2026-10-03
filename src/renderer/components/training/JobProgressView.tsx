import {Loader2, RefreshCw, Square} from 'lucide-react';
import {useComputeStore} from '../../stores/useComputeStore';
import {jobProgress, type JobRecord} from './jobProgress';

const button = 'rounded border border-[#344255] px-3 py-2 text-slate-200 hover:bg-[#25344A] disabled:opacity-40';
const toneColor = {active: 'text-cyan-200', uncertain: 'text-amber-200', completed: 'text-emerald-300', stopped: 'text-slate-300', failed: 'text-rose-300'};
const barColor = {active: 'bg-cyan-500', uncertain: 'bg-amber-500', completed: 'bg-emerald-500', stopped: 'bg-slate-500', failed: 'bg-rose-500'};

/** S2-09: the progress of one training job, shown the same way in every model family's workbench: its state, where it
 *  runs, epochs and loss, a failure with its recorded cause, the next action, and cancel or reconnect when they apply. */
export function JobProgressView({job, busy, onCancel, onReconnect}: {job: JobRecord | null; busy: boolean; onCancel: () => void; onReconnect?: () => void}) {
  const profiles = useComputeStore((state) => state.profiles);
  if (!job) return null;
  const view = jobProgress(job);
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
    {view.nextAction && <p className={view.tone === 'failed' ? 'text-rose-200' : 'text-amber-200'}>다음 행동: {view.nextAction}</p>}
    {(view.canCancel || stopping || (view.canReconnect && onReconnect)) && <div className="flex flex-wrap gap-2">
      {(view.canCancel || stopping) && <button type="button" className={button} disabled={busy || view.stopping} onClick={onCancel}>
        <Square className="mr-1 inline h-3 w-3" />{view.stopping ? '종료 확인 중' : '취소 요청'}</button>}
      {view.canReconnect && onReconnect && <button type="button" className={button} disabled={busy} onClick={onReconnect}>
        <RefreshCw className="mr-1 inline h-3 w-3" />같은 서버 작업 재연결</button>}
    </div>}
  </div>;
}
