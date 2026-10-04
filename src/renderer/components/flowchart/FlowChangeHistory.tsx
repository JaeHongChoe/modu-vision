import React, { useEffect, useState } from 'react';
import { api, type FlowConfigurationChange, type FlowRuntimeStatus } from '../../services/api';
import { changeTitle, describeChange, runtimeLabel, summarizeDelta } from './flowChangeText';

/** E04: the project's inspection-rule change record (newest first), whether it is intact, and the release the
 * inspection runtime runs now. */
export const FlowChangeHistory: React.FC<{ refreshKey: unknown }> = ({ refreshKey }) => {
  const [changes, setChanges] = useState<FlowConfigurationChange[] | null>(null);
  const [integrity, setIntegrity] = useState<{ intact: boolean; rows: number; broken_at?: string } | null>(null);
  const [runtime, setRuntime] = useState<FlowRuntimeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    api.flowchart.listChanges(50).then(result => {
      if (!active) return;
      setChanges(result.changes);
      setIntegrity(result.integrity);
      setRuntime(result.runtime);
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { active = false; };
  }, [refreshKey]);

  return (
    <section aria-label="검사 규칙 변경 기록" className="space-y-2 rounded border border-slate-700 bg-slate-900/60 p-3 text-xs text-slate-300">
      <h3 className="text-sm font-bold text-slate-100">검사 규칙 변경 기록</h3>
      <p aria-label="운영 런타임 상태">{runtimeLabel(runtime)}</p>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {integrity && !integrity.intact && <p role="alert" className="text-red-300">
        변경 기록이 앱 밖에서 바뀌었습니다 (기록 {integrity.broken_at?.slice(0, 8)}부터 확인할 수 없습니다).
      </p>}
      {changes && changes.length === 0 && <p className="text-slate-500">아직 기록된 변경이 없습니다.</p>}
      {changes && changes.length > 0 && <ol className="max-h-72 space-y-2 overflow-y-auto">
        {changes.map(change => <li key={change.change_id} className="rounded border border-slate-800 p-2">
          <p className="font-semibold text-slate-100">{changeTitle(change)}</p>
          <p className="font-mono text-[11px] text-slate-400">
            {change.parent_revision ? change.parent_revision.slice(0, 8) : '없음'} → {change.next_revision.slice(0, 8)}
            {' · '}{summarizeDelta(change.semantic_delta, !change.parent_revision)}
          </p>
          <p>사유: {change.reason || '기록 없음'}</p>
          {change.observed_runtime_release && <p className="text-slate-400">
            당시 운영 릴리스: {(change.observed_runtime_release.manifest_sha256 || change.observed_runtime_release.deployment_id || '읽을 수 없음').slice(0, 12)}
          </p>}
          {change.semantic_delta.changes.length > 0 && <details>
            <summary className="cursor-pointer text-slate-400">규칙 차이 {change.semantic_delta.changes.length}건</summary>
            <ul className="list-disc pl-5">
              {change.semantic_delta.changes.slice(0, 50).map((item, index) => <li key={index}>{describeChange(item)}</li>)}
            </ul>
          </details>}
        </li>)}
      </ol>}
    </section>
  );
};

export default FlowChangeHistory;
