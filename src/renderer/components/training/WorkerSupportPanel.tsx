import { useCallback, useEffect, useState } from 'react';
import { request } from '../../services/api';
import { SUPPORT_LABEL, SUPPORT_STAGES as STAGES, lastPreflightText, preflightBlocker, preflightScope, type SupportState, type WorkersState } from './workerSupport';

const TONE: Record<SupportState, string> = {
  verified: 'text-emerald-300', unverified: 'text-slate-300', not_installed: 'text-amber-300', unsupported: 'text-slate-500',
};

export function WorkerSupportPanel({ task }: { task: string }) {
  const [state, setState] = useState<WorkersState | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const load = useCallback(() => request<WorkersState>('/api/workers').then(value => { setState(value); setError(''); })
    .catch(cause => setError(String(cause?.message || cause))), []);
  useEffect(() => { void load(); }, [load]);
  const running = state?.running_preflight ?? null;
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => { void load(); }, 1000);
    return () => clearInterval(timer);
  }, [running, load]);
  const worker = state?.workers[0];
  const start = async (device: string) => {
    setStarting(true); setError('');
    try {
      await request('/api/workers/local/preflight', { method: 'POST', body: JSON.stringify({ task, device }) });
      await load();
    } catch (cause) { setError(String((cause as Error)?.message || cause)); }
    finally { setStarting(false); }
  };
  const last = state?.last_preflight && state.last_preflight.task === task ? state.last_preflight : null;
  return <section aria-label="이 컴퓨터 지원 상태" className="mt-3 border-t border-[#314155] pt-2">
    <h4 className="font-semibold text-slate-200">이 컴퓨터 지원 상태</h4>
    <p className="mt-1 text-slate-400">이 컴퓨터에서 실제 사전 점검을 통과한 단계만 검증됨으로 표시합니다. 런타임(패키지·드라이버·앱 코드)이 바뀌면 다시 점검합니다. 모델 품질은 점검하지 않습니다.</p>
    {worker && <p className="mt-1 text-slate-400">점검 범위: {preflightScope(worker, task)}</p>}
    {worker?.preflight_record_error && <p role="alert" className="mt-1 text-amber-300">사전 점검 기록을 읽지 못했습니다: {worker.preflight_record_error}</p>}
    {worker && <table className="mt-2 w-full text-left">
      <thead><tr><th scope="col" className="pr-2 font-normal text-slate-400">장치</th>{STAGES.map(([, label]) => <th key={label} scope="col" className="pr-2 font-normal text-slate-400">{label}</th>)}</tr></thead>
      <tbody>{worker.devices.map(device => <tr key={device.kind}>
        <th scope="row" className="pr-2 font-normal text-slate-300" title={device.name}>{device.kind.toUpperCase()}</th>
        {STAGES.map(([stage, label]) => {
          const decision = worker.support[task]?.[stage]?.[device.kind];
          const row = worker.preflight[`${task}:${stage}:${device.kind}`];
          const failed = row && !row.passed ? ` · 마지막 점검 실패: ${row.reason}` : '';
          return <td key={stage} className={`pr-2 ${decision ? TONE[decision.state] : 'text-slate-500'}`}
            aria-label={`${device.kind.toUpperCase()} ${label}: ${decision ? SUPPORT_LABEL[decision.state] : '알 수 없음'}`}
            title={decision ? `${decision.reason}${failed}` : undefined}>
            {decision ? SUPPORT_LABEL[decision.state] : '—'}{failed && <span className="text-amber-300"> ⚠</span>}
          </td>;
        })}
      </tr>)}</tbody>
    </table>}
    {worker && <div className="mt-2 flex flex-wrap gap-2">{worker.devices.map(device => {
      const blocker = preflightBlocker(worker, task, device.kind, running);
      return <button key={device.kind} type="button" className="workspace-button" disabled={starting || Boolean(blocker)}
        title={blocker ?? undefined} onClick={() => void start(device.kind)}>{device.kind.toUpperCase()} 사전 점검</button>;
    })}</div>}
    {running && running.task === task && <p role="status" className="mt-2 text-cyan-200">{running.device.toUpperCase()} 사전 점검 중: {running.stages.join(' · ')}</p>}
    {last && !running && <p role="status" className={`mt-2 ${lastPreflightText(last).ok ? 'text-emerald-300' : 'text-amber-300'}`}>{lastPreflightText(last).text}</p>}
    {error && <p role="alert" className="mt-2 text-amber-300">사전 점검: {error}</p>}
  </section>;
}
