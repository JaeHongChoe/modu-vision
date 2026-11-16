import React from 'react';
import type { SavedFlowIdentity } from './flowHandoff';
import { useProjectStore } from '../../stores/useProjectStore';

/** A saved selection describes the next use; historical executions keep their own snapshot. */
export const SavedFlowIdentityCard: React.FC<{
  identity: SavedFlowIdentity;
  isActive: boolean;
}> = ({ identity, isActive }) => {
  const technicalDetails = useProjectStore(state => state.technicalDetails);
  const viewMode = useProjectStore(state => state.viewMode);
  return (
    <div className="min-w-0 rounded border border-sky-800/70 bg-sky-950/20 px-3 py-3 text-xs text-slate-300" aria-label="검사 플로우 저장 버전 정보">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <strong className="block break-words text-sm text-slate-100">{identity.pipelineName}</strong>
          <p className="mt-1 text-slate-400">{isActive ? '활성 저장 버전' : '선택한 저장 버전'} · 모델 {identity.modelJobIds.length ? `${identity.modelJobIds.length}개 연결` : '없음'}</p>
        </div>
        <span className="rounded border border-sky-800/70 px-2 py-1 text-sky-200" title={identity.versionId}>v{identity.versionId.slice(0, 8)}</span>
      </div>
      <details className="mt-3 border-t border-sky-800/50 pt-2" open={viewMode === 'full' && technicalDetails}>
        <summary className="cursor-pointer text-sky-200">저장 버전·모델 근거</summary>
        <dl className="mt-2 space-y-2 text-slate-400">
          <div><dt>저장 버전 ID</dt><dd className="break-all font-mono text-slate-200">{identity.versionId}</dd></div>
          <div><dt>플로우 ID</dt><dd className="break-all font-mono text-slate-200">{identity.pipelineId}</dd></div>
          <div><dt>그래프 SHA-256</dt><dd className="break-all font-mono text-slate-200">{identity.pipelineHash}</dd></div>
          <div><dt>연결한 모델 작업 ID</dt><dd className="break-all font-mono text-slate-200">{identity.modelJobIds.length ? identity.modelJobIds.join(' · ') : '연결된 모델 없음'}</dd></div>
        </dl>
      </details>
    </div>
  );
};

export default SavedFlowIdentityCard;
