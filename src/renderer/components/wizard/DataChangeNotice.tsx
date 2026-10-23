/** What changed in the data since a model was trained, and the two ways back: train again (step 3), or, when only
 *  labels, split or classes changed, restore the version the model was trained on (step 1's data versions, which asks
 *  for confirmation and backs up the current state first). Read-only; it never changes data itself (audit F-09/A3). */
import { useEffect, useState } from 'react';
import { request } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { requestDataVersionPanel } from '../../stores/dataVersionRequest';

export type DataChanges = {
  job_id: string; state: 'current' | 'changed' | 'unverified' | string; reason: string;
  dataset_version_id: string | null; dataset_version_name: string | null;
  counts: { added: number; removed: number; modified: number; label_files: number };
  split_changed: boolean; classes_changed: boolean; labelset_changed: boolean;
  examples: { added: string[]; removed: string[]; modified: string[] }; restorable: boolean;
};

/** "추가 1장 · 삭제 1장 · 라벨 파일 2개 · 분할 · 클래스 지정" — only the parts that changed. */
export function changeSummary(changes: DataChanges): string {
  const parts: string[] = [];
  if (changes.counts.added) parts.push(`이미지 추가 ${changes.counts.added}장`);
  if (changes.counts.removed) parts.push(`이미지 삭제 ${changes.counts.removed}장`);
  if (changes.counts.modified) parts.push(`이미지 내용 바뀜 ${changes.counts.modified}장`);
  if (changes.counts.label_files) parts.push(`라벨 파일 ${changes.counts.label_files}개 바뀜`);
  if (changes.classes_changed) parts.push('이미지 클래스 지정 바뀜');
  if (changes.split_changed) parts.push('분할 바뀜');
  if (changes.labelset_changed) parts.push('라벨 세트 바뀜');
  return parts.join(' · ');
}

export function DataChangeNotice({ jobId }: { jobId: string }) {
  const [changes, setChanges] = useState<DataChanges | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setChanges(null); setError('');
    request<DataChanges>(`/api/provenance/data-changes?job_id=${encodeURIComponent(jobId)}`)
      .then(value => { if (live) setChanges(value); })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { live = false; };
  }, [jobId]);
  const go = (step: 1 | 3) => void useProjectStore.getState().setStep(step);
  if (error) return <p role="alert" className="text-amber-200">바뀐 내용을 읽지 못했습니다: {error}</p>;
  if (!changes) return <p className="text-slate-400">바뀐 내용을 확인하는 중…</p>;
  const summary = changeSummary(changes);
  const examples = [...changes.examples.added.map(name => `${name} (추가)`), ...changes.examples.removed.map(name => `${name} (삭제)`),
    ...changes.examples.modified.map(name => `${name} (내용 바뀜)`)].slice(0, 6);
  return <section aria-label="데이터 변경 내용" className="rounded border border-amber-500/50 bg-amber-950/20 p-2">
    <p className="font-semibold text-amber-100">모델 {changes.job_id}을(를) 학습한 뒤 데이터가 바뀌었습니다{summary ? `: ${summary}` : ''}</p>
    {examples.length > 0 && <p className="mt-1 break-all text-slate-300">예: {examples.join(', ')}</p>}
    <p className="mt-1 text-slate-300">{changes.reason}</p>
    <div className="mt-2 flex flex-wrap gap-2">
      <button type="button" className="workspace-button" onClick={() => go(3)}>지금 데이터로 다시 학습 (3단계)</button>
      {changes.restorable && <button type="button" className="workspace-button" onClick={() => { requestDataVersionPanel(changes.dataset_version_id); go(1); }}>
        학습 때 버전으로 되돌리기 (1단계 · 확인 후 복원)</button>}
    </div>
  </section>;
}
