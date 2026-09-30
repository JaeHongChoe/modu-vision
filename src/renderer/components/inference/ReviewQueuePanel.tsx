import React, { useEffect, useState } from 'react';
import { AlertTriangle, ClipboardCheck, RefreshCw } from 'lucide-react';
import { api } from '../../services/api';
import type { VisionTask } from '../../types';

type QueueResponse = Awaited<ReturnType<typeof api.inspections.reviewQueue>>;

export const ReviewQueuePanel: React.FC<{
  sourceFolder: string;
  task: VisionTask;
  projectDir: string | null;
  refreshKey: number;
  disabled?: boolean;
  onOpen: (runId: string, imagePath: string) => void;
}> = ({ sourceFolder, task, projectDir, refreshKey, disabled = false, onOpen }) => {
  const [queue, setQueue] = useState<QueueResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setQueue(null); setError(null);
    if (!sourceFolder || !projectDir) return () => { active = false; };
    setLoading(true);
    api.inspections.reviewQueue(sourceFolder, task).then((result) => {
      if (active) setQueue(result);
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sourceFolder, task, projectDir, refreshKey, reload]);

  return <div className="rounded-lg border border-amber-600/30 bg-[#1B2230] p-3"
    aria-label="검토 작업함">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="flex items-center gap-2">
        <div className="rounded border border-amber-500/30 bg-amber-500/10 p-1.5 text-amber-300"><ClipboardCheck className="h-4 w-4" /></div>
        <div><h4 className="text-xs font-semibold text-slate-100">검토 작업함</h4>
          <p className="text-[11px] text-slate-400">미확정 REVIEW와 검사 오류를 검사 실행별로 모았습니다.</p></div>
      </div>
      <button type="button" onClick={() => setReload((value) => value + 1)} disabled={loading || !sourceFolder || !projectDir}
        className="flex items-center gap-1 rounded border border-[#526177] px-2 py-1 text-[11px] text-slate-200 hover:bg-[#283247] disabled:opacity-40">
        <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 새로고침
      </button>
    </div>
    {!sourceFolder || !projectDir ? <p className="mt-2 text-[11px] text-slate-500">프로젝트 데이터를 열면 검토 목록이 표시됩니다.</p>
      : <>
        {loading && <p role="status" className="mt-2 text-[11px] text-amber-200">검토 목록을 불러오는 중입니다.</p>}
        {error && <p role="alert" className="mt-2 text-[11px] text-rose-300">{error}</p>}
        {queue && <>
          <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
            <span className="rounded border border-amber-600/30 bg-amber-950/20 px-2 py-1 text-amber-200">검토 대기 {queue.review_required}</span>
            <span className="rounded border border-rose-600/30 bg-rose-950/20 px-2 py-1 text-rose-200">진단 오류 {queue.diagnostic_errors}</span>
            <span className="px-1 py-1 text-slate-400">전체 {queue.total}건</span>
          </div>
          {queue.total === 0 && <p className="mt-2 text-[11px] text-slate-400">현재 미확정 판정이나 검사 오류가 없습니다.</p>}
          {queue.items.length > 0 && <div className="mt-2 max-h-48 space-y-1 overflow-y-auto" aria-label="검토 대기 이미지">
            {queue.items.map((item) => <button type="button" key={`${item.run_id}:${item.image.file_path}`}
              onClick={() => onOpen(item.run_id, item.image.file_path)} disabled={disabled}
              className="flex w-full items-center gap-2 rounded border border-[#344254] bg-[#0D1723] px-2.5 py-2 text-left hover:border-amber-500/50 disabled:opacity-50">
              {item.status === 'error' ? <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-300" />
                : <ClipboardCheck className="h-3.5 w-3.5 shrink-0 text-amber-300" />}
              <span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-medium text-slate-100" title={item.image.file_path}>{item.image.file_name}</span>
                <span className="block truncate text-[10px] text-slate-400">{item.pipeline_name} · {item.run_id.slice(0, 8)} · {item.status === 'error' ? '오류 원인 확인' : item.status === 'still_review' ? '추가 검토 필요' : '작업자 미확인'}</span></span>
              <span className={`shrink-0 text-[10px] ${item.status === 'error' ? 'text-rose-300' : 'text-amber-200'}`}>열기</span>
            </button>)}
          </div>}
          {queue.total > queue.items.length && <p className="mt-1 text-[10px] text-slate-500">최근 {queue.items.length}건을 표시합니다.</p>}
        </>}
      </>}
  </div>;
};
