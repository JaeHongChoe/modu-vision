import React, { useEffect, useState } from 'react';
import { api, type FlowChangePreview } from '../../services/api';
import type { FlowchartPipeline } from '../../types';
import { describeChange, reasonRequired, summarizeDelta } from './flowChangeText';

/** E04: before a flow is saved or activated, the inspection-rule difference it records (from the active version) and
 * the reason. Nothing is saved until confirmed; a change based on a version that is no longer active is refused here
 * and again by the server. */
export const FlowChangeDialog: React.FC<{
  mode: 'save' | 'activate';
  pipeline: FlowchartPipeline;
  baseVersionId: string | null | undefined;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
}> = ({ mode, pipeline, baseVersionId, onConfirm, onCancel }) => {
  const [preview, setPreview] = useState<FlowChangePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api.flowchart.previewChange(pipeline, baseVersionId)
      .then(result => { if (active) setPreview(result); })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { active = false; };
  }, [pipeline, baseVersionId]);

  const required = reasonRequired(preview);
  const ready = Boolean(preview) && !preview?.stale && (!required || reason.trim().length > 0) && !busy;
  const title = mode === 'save' ? '검사 규칙 변경 저장' : '저장 버전 활성화';
  const confirm = async () => {
    if (!ready) return;
    setBusy(true);
    try { await onConfirm(reason.trim()); } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div role="dialog" aria-modal="true" aria-label={title}
        className="w-full max-w-xl space-y-3 rounded-lg border border-slate-700 bg-slate-900 p-4 text-sm text-slate-200">
        <h2 className="text-base font-bold">{title}</h2>
        {!preview && !error && <p className="text-slate-400">활성 버전과 비교하는 중입니다.</p>}
        {error && <p role="alert" className="text-red-300">변경 내용을 비교하지 못했습니다: {error}</p>}
        {preview?.stale && <p role="alert" className="rounded border border-amber-600 bg-amber-950/40 p-2 text-amber-200">
          이 편집을 시작한 뒤 다른 곳에서 활성 플로우가 바뀌었습니다. 편집 내용은 그대로 있습니다. 활성 버전을 다시 열어 확인한 뒤 진행하세요.
        </p>}
        {preview && <section aria-label="검사 규칙 차이" className="space-y-1">
          <p className="font-semibold">{summarizeDelta(preview.semantic_delta, !preview.parent_revision)}
            <span className="ml-2 font-mono text-[11px] font-normal text-slate-400">
              {preview.parent_revision ? `활성 ${preview.parent_revision.slice(0, 8)} 기준` : '활성 버전 없음'}
            </span>
          </p>
          {preview.semantic_delta.changes.length > 0 && <ul className="max-h-48 list-disc overflow-y-auto pl-5 text-xs text-slate-300">
            {preview.semantic_delta.changes.slice(0, 50).map((change, index) => <li key={index}>{describeChange(change)}</li>)}
            {preview.semantic_delta.changes.length > 50 && <li>외 {preview.semantic_delta.changes.length - 50}건</li>}
          </ul>}
        </section>}
        <label className="block space-y-1">
          <span>변경 사유{required ? ' (필수)' : ' (선택)'}</span>
          <textarea aria-label="변경 사유" value={reason} maxLength={2000} rows={3} onChange={event => setReason(event.target.value)}
            className="w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm" />
        </label>
        <p className="text-[11px] text-slate-400">
          저장한 사람, 이전·새 버전, 규칙 차이와 사유가 변경 기록에 남습니다. 운영 런타임은 새 릴리스를 적용하고 런타임이 확인(ACK)하기 전까지 지금 릴리스로 계속 검사합니다.
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" className="workspace-button" onClick={onCancel} disabled={busy}>취소</button>
          <button type="button" className="workspace-button" onClick={() => void confirm()} disabled={!ready}>
            {mode === 'save' ? '변경 저장' : '활성화'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default FlowChangeDialog;
