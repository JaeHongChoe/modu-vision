import React from 'react';
import type { SavedFlowIdentity } from './flowHandoff';
import { useProjectStore } from '../../stores/useProjectStore';

/** The saved flow a run uses, in plain words; its hashes and model job ids appear only with 기술 상세 on. */
export const SavedFlowIdentityCard: React.FC<{
  identity: SavedFlowIdentity;
  isActive: boolean;
}> = ({ identity, isActive }) => {
  const technicalDetails = useProjectStore(state => state.technicalDetails);
  return (
    <div className="rounded border border-sky-800/70 bg-sky-950/20 px-3 py-2 text-xs text-slate-300" aria-label="검사 플로우 저장 버전 정보">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <strong className="text-sky-200">{isActive ? '활성 저장 버전' : '선택한 저장 버전'}</strong>
        <span>{identity.pipelineName}</span>
        <span className="font-mono text-slate-100" title={identity.versionId}>{technicalDetails ? identity.versionId : `v${identity.versionId.slice(0, 8)}`}</span>
        <span>· 모델 {identity.modelJobIds.length ? `${identity.modelJobIds.length}개 연결` : '없음'}</span>
      </div>
      {technicalDetails && <>
        <div className="mt-1 break-all font-mono text-slate-400">그래프 SHA-256 {identity.pipelineHash}</div>
        <div className="mt-1 break-all text-slate-300">모델 작업 ID {identity.modelJobIds.length ? identity.modelJobIds.join(' · ') : '연결된 모델 없음'}</div>
      </>}
    </div>
  );
};

export default SavedFlowIdentityCard;
