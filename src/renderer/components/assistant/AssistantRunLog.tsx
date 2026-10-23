/** The open project's assistant run log (§4.6): what was read, opened, confirmed or refused, newest first. "기록 비우기"
 * moves the log file into the project's assistant/archive folder; nothing is deleted. */
import React from 'react';
import { useAssistantStore } from '../../stores/useAssistantStore';

const TIER: Record<string, string> = { read: '읽기', navigate: '화면 이동', write: '쓰기', forbidden: '직접 하세요', conversation: '대화' };
const RESULT: Record<string, string> = { ok: '완료', refused: '거절', error: '오류', cancelled: '취소' };

export const AssistantRunLog: React.FC = () => {
  const runs = useAssistantStore(state => state.runs);
  const runsError = useAssistantStore(state => state.runsError);
  const { loadRuns, archiveRuns } = useAssistantStore.getState();
  return (
    <details className="rounded border border-slate-700 p-2" onToggle={event => { if ((event.currentTarget as HTMLDetailsElement).open) void loadRuns(); }}>
      <summary className="cursor-pointer text-slate-200">실행 기록(이 프로젝트)</summary>
      <div className="mt-2 space-y-2">
        {runsError && <p role="alert" className="text-amber-200">기록을 읽지 못했습니다: {runsError}</p>}
        {runs && !runs.length && <p className="text-slate-400">아직 기록이 없습니다.</p>}
        {runs && runs.length > 0 && (
          <ol aria-label="도우미 실행 기록" className="max-h-56 space-y-1 overflow-y-auto">
            {runs.map(run => (
              <li key={`${run.run_id}-${run.at}`} className="text-xs text-slate-300">
                <span className="text-slate-400">{run.at.replace('T', ' ').replace('Z', '')}</span> · {TIER[run.tier] ?? run.tier} · {run.tool}
                {run.summary ? ` · ${run.summary}` : ''} · {run.confirmed ? '사람이 확인 · ' : ''}{RESULT[run.result] ?? run.result}{run.error_code ? `(${run.error_code})` : ''}
              </li>
            ))}
          </ol>
        )}
        <div className="flex flex-wrap gap-2">
          <button type="button" className="workspace-button" onClick={() => void loadRuns()}>새로 읽기</button>
          <button type="button" className="workspace-button" disabled={!runs?.length} onClick={() => void archiveRuns()}>기록 비우기(보관 폴더로 이동)</button>
        </div>
      </div>
    </details>
  );
};
