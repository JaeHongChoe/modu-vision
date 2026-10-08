import React, { useEffect, useRef, useState } from 'react';
import {request, getApiPersistenceIdentity, getProjectContextGeneration, subscribeProjectContext} from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
type Family = 'ocr' | 'rotated_detection' | 'enhancement' | 'rotation';
type Evidence = { evaluation_id: string; created_at: number; result: { job_id: string; task?: Family; sample_count?: number; split?: string; [key: string]: unknown }; binding: { checkpoint_sha256: string; dataset_fingerprint: string; source_dataset_path?: string; family_dataset_sha256?: string } };
type ActiveApproval = {job_id: string; checkpoint_sha256: string; valid?: boolean};
const defaults = { ocr: { minimum: { exact_match_accuracy: 1 }, maximum: { character_error_rate: 0 } }, rotated_detection: { minimum: { precision: 1, recall: 1, mean_oriented_iou: .5 }, maximum: { mean_angle_error_deg: 10 } }, enhancement: { minimum: { output_psnr: 20 }, maximum: { output_mse: .01 } }, rotation: {minimum:{within_10_deg:.95},maximum:{angular_mae_deg:5}} };
function readySource(projectState: ReturnType<typeof useProjectStore.getState>, dataset: ReturnType<typeof useDatasetStore.getState>): string {
  const project = projectState.project, source = dataset.folderPath;
  return project && project.id && !projectState.isProjectBusy && projectState.projectDir === project.project_dir
    && projectState.task === project.task && source && source === project.source_dataset_dir
    && dataset.datasetKey === `${source}\0${project.task}` && !dataset.isLoading && !dataset.importError ? source : '';
}
export const SpecializedApprovalPanel: React.FC = () => {
  const projectState = useProjectStore(state => state), dataset = useDatasetStore(state => state);
  const projectDir = projectState.projectDir, source = readySource(projectState, dataset);
  const [,setContextRevision] = useState(getProjectContextGeneration);
  useEffect(() => subscribeProjectContext(() => setContextRevision(getProjectContextGeneration())), []);
  const [family, setFamily] = useState<Family>('ocr');
  const [records, setRecords] = useState<Evidence[]>([]); const [selected, setSelected] = useState('');
  const [incumbent, setIncumbent] = useState(''); const [reviewer, setReviewer] = useState(''); const [reason, setReason] = useState('');
  const [bounds, setBounds] = useState(JSON.stringify(defaults.ocr, null, 2)); const [minimumCount, setMinimumCount] = useState(8);
  const [reviewed, setReviewed] = useState(false); const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  const [active, setActive] = useState<ActiveApproval | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [readRevision, setReadRevision] = useState(0);
  const captureScope = () => {
    const current = useProjectStore.getState();
    return JSON.stringify([current.project?.id, current.projectDir, current.task, readySource(current, useDatasetStore.getState()),
      family, getProjectContextGeneration(), getApiPersistenceIdentity(), readRevision]);
  };
  const scope = captureScope();
  const scopeRef = useRef(scope); scopeRef.current = scope;
  useEffect(() => {
    let valid = true;
    const requestScope = scope;
    const current = () => valid && scopeRef.current === requestScope && captureScope() === requestScope;
    setSelected(''); setRecords([]); setReviewed(false); setMessage(''); setBusy(false); setIncumbent('');
    setActive(null); setLoaded(false); setReviewer(''); setReason(''); setMinimumCount(8);
    setBounds(JSON.stringify(defaults[family], null, 2));
    if (projectDir && source) Promise.all([
      request<{items: Evidence[]}>(`/api/evaluation/history?source_dataset_path=${encodeURIComponent(source)}&task=${family}`),
      request<{active: ActiveApproval | null}>(`/api/model-deployments/active?source_dataset_path=${encodeURIComponent(source)}&task=${family}`),
    ]).then(([history, approval]) => {
      if (!current()) return;
      setRecords(history.items.filter(row => row.result.task === family && row.result.split === 'test'));
      setActive(approval.active); setLoaded(true);
    }).catch(cause => { if (current()) setMessage(cause instanceof Error ? cause.message : String(cause)); });
    return () => { valid = false; };
  }, [scope]);
  const evidence = records.find(row => row.evaluation_id === selected);
  const replacing = !!active && !!evidence && active.job_id !== evidence.result.job_id;
  const compatible = evidence && replacing ? records.filter(row => row.evaluation_id !== evidence.evaluation_id
    && row.result.job_id === active!.job_id && row.result.task === family && row.result.split === 'test'
    && row.binding.checkpoint_sha256 === active!.checkpoint_sha256
    && !!evidence.binding.family_dataset_sha256 && row.binding.family_dataset_sha256 === evidence.binding.family_dataset_sha256
    && row.binding.source_dataset_path === evidence.binding.source_dataset_path
    && row.binding.dataset_fingerprint === evidence.binding.dataset_fingerprint
    && row.result.sample_count === evidence.result.sample_count) : [];
  const canApprove = loaded && (!active || active.valid === true) && !busy && !!evidence && reviewed
    && !!reviewer.trim() && reason.trim().length >= 8 && (!replacing || compatible.some(row => row.evaluation_id === incumbent));
  const approve = async () => {
    const requestScope = scope;
    if (!canApprove || captureScope() !== requestScope) return;
    setBusy(true); setMessage('');
    try {
      const criteria = JSON.parse(bounds);
      const response = await request<{ revision: { revision_id: string } }>('/api/model-deployments/specialized-approve', { method: 'POST', body: JSON.stringify({ source_dataset_path: source, task: family, evaluation_id: selected, incumbent_evaluation_id: replacing ? incumbent : null, reviewer, reason, holdout_reviewed: reviewed, minimum_sample_count: minimumCount, minimum_metrics: criteria.minimum || {}, maximum_metrics: criteria.maximum || {} }) });
      if (scopeRef.current === requestScope && captureScope() === requestScope) setMessage(`승인 revision: ${response.revision.revision_id}. 이 revision을 포함한 flow 패키지로 서비스를 명시적으로 적용하세요.`);
    } catch (cause) { if (scopeRef.current === requestScope && captureScope() === requestScope) setMessage(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (scopeRef.current === requestScope && captureScope() === requestScope) setBusy(false); }
  };
  if (!projectDir || !source) return null;
  return <details className="mt-3 rounded border border-slate-700 p-3 text-xs"><summary className="cursor-pointer font-semibold">OCR · 회전 검출 · 이미지 개선 승인</summary>
    <label className="mt-2 block">모델 유형<select aria-label="특수 모델 승인 유형" className="ml-2 rounded bg-slate-800 p-1" value={family} onChange={event => setFamily(event.target.value as Family)}><option value="ocr">OCR</option><option value="rotated_detection">회전 검출</option><option value="enhancement">이미지 개선</option><option value="rotation">회전·정렬</option></select></label>
    <button type="button" aria-label="특수 모델 평가 기록 새로 고침" disabled={busy} onClick={() => setReadRevision(value => value + 1)} className="mt-2 rounded border border-slate-600 px-2 py-1 disabled:opacity-40">평가 기록 새로 고침</button>
    <label className="mt-2 block">변경 불가 test 평가<select aria-label="특수 모델 평가 승인" disabled={!loaded || busy} className="mt-1 w-full rounded bg-slate-800 p-2 disabled:opacity-40" value={selected} onChange={event => { setSelected(event.target.value); setIncumbent(''); setReviewed(false); }}><option value="">평가 기록 선택</option>{records.map(row => <option key={row.evaluation_id} value={row.evaluation_id}>{row.result.job_id} · {row.result.split} · {row.result.sample_count ?? 0}장 · {new Date(row.created_at * 1000).toLocaleString()}</option>)}</select></label>
    {evidence && <p className="mt-2 break-all font-mono text-[10px]">{JSON.stringify(Object.fromEntries(Object.entries(evidence.result).filter(([key, value]) => typeof value === 'number' || ['split', 'improved'].includes(key))))} · SHA {evidence.binding.checkpoint_sha256}</p>}
    <div className="mt-2 grid grid-cols-2 gap-2"><label>검토자<input aria-label="특수 모델 검토자" value={reviewer} onChange={event => setReviewer(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2" /></label><label>최소 test 표본<input aria-label="특수 모델 최소 표본" type="number" min={1} value={minimumCount} onChange={event => setMinimumCount(Number(event.target.value))} className="mt-1 w-full rounded bg-slate-800 p-2" /></label></div>
    <label className="mt-2 block">검토 근거<input aria-label="특수 모델 승인 근거" value={reason} onChange={event => setReason(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2" /></label>
    <label className="mt-2 block">현재 승인 모델의 같은 test 평가 (교체 시 필수)<select aria-label="특수 모델 기준 평가" value={incumbent} disabled={!loaded || !replacing || busy || active?.valid !== true} onChange={event => {setIncumbent(event.target.value); setReviewed(false);}} className="mt-1 w-full rounded bg-slate-800 p-2 disabled:opacity-40">
      <option value="">{!loaded ? '현재 승인 모델 확인 중' : !active ? '첫 승인 · 비교 기준 없음' : !replacing ? '다른 후보를 선택하면 기준 평가를 고릅니다' : '같은 test 기준 평가 선택'}</option>
      {compatible.map(row => <option key={row.evaluation_id} value={row.evaluation_id}>{row.result.job_id} · {row.result.sample_count ?? 0}장 · {new Date(row.created_at * 1000).toLocaleString()} · {row.evaluation_id}</option>)}
    </select></label>
    {loaded && active?.valid === false && <p role="alert" className="mt-2 text-amber-200">현재 승인 모델의 근거를 다시 확인하세요. 확인 전에는 교체를 승인하지 않습니다.</p>}
    {loaded && replacing && compatible.length === 0 && <p role="alert" className="mt-2 text-amber-200">현재 승인 모델을 후보와 같은 test 데이터로 다시 평가한 뒤 평가 기록을 새로 고치세요.</p>}
    <label className="mt-2 block">품질 기준 (minimum / maximum)<textarea aria-label="특수 모델 품질 기준" rows={6} value={bounds} onChange={event => setBounds(event.target.value)} className="mt-1 w-full rounded bg-slate-800 p-2 font-mono" /></label>
    <label className="mt-2 flex gap-2"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} />독립 test 정답과 평가 결과를 검토했습니다.</label>
    <button type="button" disabled={!canApprove} onClick={() => void approve()} className="mt-2 rounded border border-blue-600 px-3 py-2 disabled:opacity-40">품질 기준 검증 후 승인</button>
    {message && <p role="status" className="mt-2 break-all text-amber-200">{message}</p>}
  </details>;
};
