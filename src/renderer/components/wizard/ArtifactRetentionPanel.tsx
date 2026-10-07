import React, { useEffect, useRef, useState } from 'react';
import { artifactRetention, type RetentionSummary, type TrashResult } from '../../services/artifactRetention';

const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const pathsFrom = (text: string) => [...new Set(text.split('\n').map((path) => path.trim()).filter(Boolean))];
const button = 'rounded border border-slate-600 px-3 py-2 text-xs text-slate-200 disabled:opacity-40';
const input = 'mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2';

export const ArtifactRetentionPanel: React.FC<{ projectId: string }> = ({ projectId }) => {
  const [summary, setSummary] = useState<RetentionSummary | null>(null);
  const [days, setDays] = useState(30);
  const [trashDays, setTrashDays] = useState(30);
  const [quotaMB, setQuotaMB] = useState('');
  const [paths, setPaths] = useState('');
  const [preview, setPreview] = useState<{ input: string; result: TrashResult } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const generation = useRef(0);

  useEffect(() => {
    const current = ++generation.current;
    setSummary(null); setPreview(null); setPaths(''); setMessage(''); setError(''); setBusy(true);
    void artifactRetention.summary().then((result) => {
      if (current !== generation.current) return;
      setSummary(result); setDays(result.policy.retention_days); setTrashDays(result.policy.trash_days);
      setQuotaMB(result.policy.quota_bytes === null ? '' : String(result.policy.quota_bytes / 1024 / 1024));
    }).catch((failure: unknown) => {
      if (current === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
    }).finally(() => { if (current === generation.current) setBusy(false); });
    return () => { ++generation.current; };
  }, [projectId]);

  const run = async (action: () => Promise<void>) => {
    const current = generation.current;
    setBusy(true); setError(''); setMessage('');
    try { await action(); }
    catch (failure) { if (current === generation.current) setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { if (current === generation.current) setBusy(false); }
  };
  const reload = async (current: number) => {
    const result = await artifactRetention.summary();
    if (current === generation.current) setSummary(result);
  };
  const policyValid = Number.isInteger(days) && days >= 0 && days <= 3650
    && Number.isInteger(trashDays) && trashDays >= 0 && trashDays <= 3650
    && (quotaMB === '' || (Number.isFinite(Number(quotaMB)) && Number(quotaMB) > 0));
  const canMove = preview?.input === paths && preview.result.eligible.length > 0
    && /^[0-9a-f]{64}$/.test(preview.result.preview_sha256);

  return <section className="space-y-4" aria-label="프로젝트 보존기한과 복구 보관함">
    <h3 className="text-sm font-semibold text-slate-100">보존기한과 복구 보관함</h3>
    <p className="text-xs leading-5 text-slate-400">보호된 배포·드리프트 기준과 연결된 원본은 유지합니다. 경로를 검토한 뒤 복구 가능한 보관함으로 이동할 수 있습니다. 영구 삭제는 지원하지 않습니다.</p>
    {summary && <>
      <p className="text-xs text-slate-300">관리 대상 파일 {size(summary.active_bytes)} · 복구 보관함 {size(summary.trash_bytes)} · 합계 {size(summary.total_bytes)}{summary.over_quota ? ' · 용량 기준 초과' : ''}</p>
      <div className="grid grid-cols-2 gap-3 text-xs text-slate-300">
        <label>미보호 파일 보존일<input aria-label="파일 보존일" type="number" min="0" max="3650" value={days} onChange={(event) => { setDays(Number(event.target.value)); setPreview(null); }} className={input} /></label>
        <label>복구 보관함 기준일<input aria-label="복구 보관함 기준일" type="number" min="0" max="3650" value={trashDays} onChange={(event) => { setTrashDays(Number(event.target.value)); setPreview(null); }} className={input} /></label>
        <label className="col-span-2">용량 기준 MB · 비우면 제한 없음<input aria-label="프로젝트 용량 기준 MB" type="number" min="1" value={quotaMB} onChange={(event) => { setQuotaMB(event.target.value); setPreview(null); }} className={input} /></label>
      </div>
      <button disabled={busy || !policyValid} onClick={() => void run(async () => {
        const current = generation.current;
        await artifactRetention.policy({ retention_days: days, trash_days: trashDays, quota_bytes: quotaMB === '' ? null : Math.round(Number(quotaMB) * 1024 * 1024) });
        await reload(current);
        if (current === generation.current) { setPreview(null); setMessage('보존 정책을 저장했습니다. 자동 이동·영구 삭제는 실행하지 않았습니다.'); }
      })} className={button}>보존 정책 저장</button>
      <details className="text-xs text-slate-400"><summary>보호된 경로 {summary.pins.length}개</summary>{summary.pins.map((pin) => <p className="mt-2 break-all" key={`${pin.owner}:${pin.relative_path}`}>{pin.relative_path} · {pin.reason}</p>)}</details>
      <label className="block text-xs text-slate-300">이동할 프로젝트 내부 경로 · 한 줄에 하나<textarea aria-label="복구 보관함 이동 경로" value={paths} onChange={(event) => { setPaths(event.target.value); setPreview(null); }} rows={3} placeholder="reports/old-report" className={`${input} font-mono`} /></label>
      <button disabled={busy || pathsFrom(paths).length === 0} onClick={() => void run(async () => {
        const current = generation.current; const value = paths;
        const result = await artifactRetention.preview(pathsFrom(value));
        if (current === generation.current) setPreview({ input: value, result });
      })} className={button}>이동 가능 여부 미리 확인</button>
      {preview && <div className="space-y-2 rounded border border-amber-700/40 p-3 text-xs text-amber-100">
        {preview.result.eligible.map((item) => <p className="break-all" key={item.relative_path}>{item.relative_path} · {size(item.size_bytes)}</p>)}
        <button disabled={busy || !canMove} onClick={() => void run(async () => {
          const current = generation.current;
          const result = await artifactRetention.move(preview.result.eligible.map((item) => item.relative_path), preview.result.preview_sha256);
          await reload(current);
          if (current === generation.current) { setPreview(null); setMessage(`${result.trashed.length}개 경로를 복구 보관함으로 이동했습니다.`); }
        })} className={button}>확인한 경로를 복구 보관함으로 이동</button>
      </div>}
      <div className="space-y-2 text-xs text-slate-300"><h4>복구 보관함</h4>{summary.trash.filter((item) => item.state !== 'restored').map((item) => <div key={item.trash_id} className="rounded border border-slate-700 p-2"><p className="break-all">{item.relative_path} · {item.state} · {size(item.size_bytes)}</p><button disabled={busy || item.state !== 'trashed'} onClick={() => void run(async () => {
        const current = generation.current; await artifactRetention.restore(item.trash_id); await reload(current);
        if (current === generation.current) setMessage('원래 경로로 복원했습니다.');
      })} className={button}>원래 경로로 복원</button></div>)}</div>
      <p className="text-xs text-slate-400">백업 파일 검증 {summary.backups.filter((item) => item.status === 'archive_verified').length}건 · 새 폴더 복원 검증 {summary.restores.filter((item) => item.status === 'restore_verified').length}건</p>
    </>}
    {busy && <p role="status" className="text-xs text-slate-400">보존 상태 확인 중…</p>}
    {message && <p role="status" className="text-xs text-emerald-300">{message}</p>}
    {error && <p role="alert" className="break-all text-xs text-red-300">{error}</p>}
  </section>;
};
