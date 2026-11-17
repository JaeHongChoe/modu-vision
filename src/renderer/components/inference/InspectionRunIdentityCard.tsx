import React from 'react';
import { useProjectStore } from '../../stores/useProjectStore';
import type { BatchInspectionReport } from './batchInspection';

type RecordedIdentity = Pick<BatchInspectionReport, 'run_id' | 'pipeline_id' | 'pipeline_name'
  | 'saved_version_id' | 'pipeline_hash' | 'model_sha256' | 'execution_config'>;

/** Render only the selected run's recorded identity, never the current flow/model selection. */
export const InspectionRunIdentityCard: React.FC<{ report: RecordedIdentity }> = ({ report }) => {
  const viewMode = useProjectStore(state => state.viewMode);
  const technicalDetails = useProjectStore(state => state.technicalDetails);
  const models = Object.entries(report.model_sha256 || {});
  const missingEvidence = !report.saved_version_id || !report.pipeline_hash || models.length === 0;
  return (
    <section className="min-w-0 rounded-lg border border-sky-700/50 bg-[#111D2D] px-3 py-3 text-xs text-slate-300" aria-label="선택한 검사 실행 식별자">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[11px] text-sky-200">선택한 검사 실행의 기록 · 검사 당시 저장본</p>
          <strong className="mt-1 block break-words text-sm text-slate-100">{report.pipeline_name}</strong>
        </div>
        {report.saved_version_id && <span className="rounded border border-sky-800/70 px-2 py-1 text-sky-200" title={report.saved_version_id}>v{report.saved_version_id.slice(0, 8)}</span>}
      </div>
      <p className="mt-2 text-slate-400">이 기록은 다음 검사에 사용할 현재 선택과 별도로 보관됩니다.</p>
      {report.execution_config && <p className="mt-2 text-sky-100">
        실행 위치: {report.execution_config.execution_target === 'model_compute' ? '모델 학습 위치 · 기존 방식'
          : report.execution_config.execution_target === 'local' ? '이 컴퓨터'
          : report.execution_config.profile?.name || report.execution_config.compute_profile_name
            || report.execution_config.compute_profile_id || '선택 서버'}
        {report.execution_config.execution_target !== 'model_compute' && ` · ${report.execution_config.device.toUpperCase()}`}
        {report.execution_config.execution_target === 'model_compute' && ' · 실제 장치는 이미지별 기록에서 확인'}
      </p>}
      {missingEvidence && <p role="status" className="mt-2 text-amber-300">이전 기록 · 저장 버전, 그래프 또는 모델 해시 일부가 미기록입니다.</p>}
      <details className="mt-3 border-t border-sky-800/50 pt-2" open={viewMode === 'full' && technicalDetails}>
        <summary className="cursor-pointer text-sky-200">검사 당시 실행 근거</summary>
        <dl className="mt-2 space-y-2 text-slate-400">
          <div><dt>실행 ID</dt><dd className="break-all font-mono text-slate-200">{report.run_id || '미기록'}</dd></div>
          <div><dt>플로우 ID</dt><dd className="break-all font-mono text-slate-200">{report.pipeline_id}</dd></div>
          <div><dt>저장 플로우 버전</dt><dd className="break-all font-mono text-slate-200">{report.saved_version_id || '이전 기록 · 미기록'}</dd></div>
          <div><dt>그래프 SHA-256</dt><dd className="break-all font-mono text-slate-200">{report.pipeline_hash || '이전 기록 · 미기록'}</dd></div>
        </dl>
        <div className="mt-3 space-y-2 border-t border-sky-800/50 pt-2">
          <p className="font-semibold text-sky-200">검사 시작 시 검증한 체크포인트</p>
          {models.length > 0 ? models.map(([jobId, digest]) => <div key={jobId} className="space-y-1">
            <p className="break-all font-mono text-slate-300">{jobId}</p>
            <p className="break-all font-mono text-slate-200">SHA-256 {digest}</p>
          </div>) : <p className="text-slate-400">이전 형식의 기록에는 체크포인트 해시가 없습니다.</p>}
        </div>
      </details>
    </section>
  );
};
