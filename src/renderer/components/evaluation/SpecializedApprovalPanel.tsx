import React, { useEffect, useRef, useState } from 'react';
import { request } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
type Family = 'ocr' | 'rotated_detection' | 'enhancement';
type Evidence = { evaluation_id: string; created_at: number; result: { job_id: string; sample_count?: number; split?: string; [key: string]: unknown }; binding: { checkpoint_sha256: string; dataset_fingerprint: string } };
const defaults = { ocr: { minimum: { exact_match_accuracy: 1 }, maximum: { character_error_rate: 0 } }, rotated_detection: { minimum: { precision: 1, recall: 1, mean_oriented_iou: .5 }, maximum: { mean_angle_error_deg: 10 } }, enhancement: { minimum: { output_psnr: 20 }, maximum: { output_mse: .01 } } };
export const SpecializedApprovalPanel: React.FC = () => {
  const projectDir = useProjectStore(state => state.projectDir);
  const source = useDatasetStore(state => state.folderPath);
  const [family, setFamily] = useState<Family>('ocr');
  const [records, setRecords] = useState<Evidence[]>([]); const [selected, setSelected] = useState('');
  const [incumbent, setIncumbent] = useState(''); const [reviewer, setReviewer] = useState(''); const [reason, setReason] = useState('');
  const [bounds, setBounds] = useState(JSON.stringify(defaults.ocr, null, 2)); const [minimumCount, setMinimumCount] = useState(8);
  const [reviewed, setReviewed] = useState(false); const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  const scope = `${projectDir}\u0000${source}\u0000${family}`;
  const scopeRef = useRef(scope); scopeRef.current = scope;
  useEffect(() => {
    let valid = true; setSelected(''); setRecords([]); setReviewed(false); setMessage(''); setBusy(false); setIncumbent(''); setBounds(JSON.stringify(defaults[family], null, 2));
    if (projectDir && source) request<{ items: Evidence[] }>(`/api/evaluation/history?source_dataset_path=${encodeURIComponent(source)}&task=${family}`).then(result => { if (valid) setRecords(result.items); }).catch(cause => { if (valid) setMessage(cause.message || String(cause)); });
    return () => { valid = false; };
  }, [projectDir, source, family]);
  const approve = async () => {
    const requestScope = scope;
    setBusy(true); setMessage('');
    try {
      const criteria = JSON.parse(bounds);
      const response = await request<{ revision: { revision_id: string } }>('/api/model-deployments/specialized-approve', { method: 'POST', body: JSON.stringify({ source_dataset_path: source, task: family, evaluation_id: selected, incumbent_evaluation_id: incumbent || null, reviewer, reason, holdout_reviewed: reviewed, minimum_sample_count: minimumCount, minimum_metrics: criteria.minimum || {}, maximum_metrics: criteria.maximum || {} }) });
      if (scopeRef.current === requestScope) setMessage(`승인 revision: ${response.revision.revision_id}. 이 revision을 포함한 flow 패키지로 서비스를 명시적으로 적용하세요.`);
    } catch (cause) { if (scopeRef.current === requestScope) setMessage(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (scopeRef.current === requestScope) setBusy(false); }
  };
  const evidence = records.find(row => row.evaluation_id === selected);
  if (!projectDir || !source) return null;
  return <details className="mt-3 rounded border border-slate-700 p-3 text-xs"><summary className="cursor-pointer font-semibold">OCR · 회전 검출 · 이미지 개선 승인</summary>
    <label className="mt-2 block">모델 유형<select aria-label="특수 모델 승인 유형" className="ml-2 rounded bg-slate-800 p-1" value={family} onChange={event => setFamily(event.target.value as Family)}><option value="ocr">OCR</option><option value="rotated_detection">회전 검출</option><option value="enhancement">이미지 개선</option></select></label>
    <label className="mt-2 block">변경 불가 test 평가<select aria-label="특수 모델 평가 승인" className="mt-1 w-full rounded bg-slate-800 p-2" value={selected} onChange={event => { setSelected(event.target.value); setReviewed(false); }}><option value="">평가 기록 선택</option>{records.map(row => <option key={row.evaluation_id} value={row.evaluation_id}>{row.result.job_id} · {row.result.split} · {row.result.sample_count ?? 0}장 · {new Date(row.created_at * 1000).toLocaleString()}</option>)}</select></label>
    {evidence && <p className="mt-2 break-all font-mono text-[10px]">{JSON.stringify(Object.fromEntries(Object.entries(evidence.result).filter(([key, value]) => typeof value === 'number' || ['split', 'improved'].includes(key))))} · SHA {evidence.binding.checkpoint_sha256}</p>}
    <div className="mt-2 grid grid-cols-2 gap-2"><label>검토자<input aria-label="특수 모델 검토자" value={reviewer} onChange={event => setReviewer(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2" /></label><label>최소 test 표본<input aria-label="특수 모델 최소 표본" type="number" min={1} value={minimumCount} onChange={event => setMinimumCount(Number(event.target.value))} className="mt-1 w-full rounded bg-slate-800 p-2" /></label></div>
    <label className="mt-2 block">검토 근거<input aria-label="특수 모델 승인 근거" value={reason} onChange={event => setReason(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2" /></label>
    <label className="mt-2 block">현재 운영 모델의 같은 test 평가 ID (교체 시 필수)<input aria-label="특수 모델 기준 평가" value={incumbent} onChange={event => setIncumbent(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2" /></label>
    <label className="mt-2 block">품질 기준 (minimum / maximum)<textarea aria-label="특수 모델 품질 기준" rows={6} value={bounds} onChange={event => setBounds(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2 font-mono" /></label>
    <label className="mt-2 flex gap-2"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} />독립 test 정답과 평가 결과를 검토했습니다.</label>
    <button type="button" disabled={busy || !selected || !reviewed || !reviewer.trim() || reason.trim().length < 8} onClick={() => void approve()} className="mt-2 rounded border border-blue-600 px-3 py-2 disabled:opacity-40">품질 기준 검증 후 승인</button>
    {message && <p role="status" className="mt-2 break-all text-amber-200">{message}</p>}
  </details>;
};
