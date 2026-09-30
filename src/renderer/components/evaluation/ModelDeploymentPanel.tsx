import { RuntimeServicePanel } from '../runtime/RuntimeServicePanel';
import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, RotateCcw, ShieldCheck } from 'lucide-react';
import { api, type ModelComparisonRecord, type ModelDeploymentAssessment,
  type ModelDeploymentRevision } from '../../services/api';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import type { FlowModelTask } from '../../types';

export const ModelDeploymentPanel: React.FC<{ taskOverride?: FlowModelTask }> = ({ taskOverride }) => {
  const projectTask = useProjectStore((state) => state.task);
  const task = taskOverride || projectTask;
  const projectDir = useProjectStore((state) => state.projectDir);
  const folderPath = useDatasetStore((state) => state.folderPath);
  const datasetKey = useDatasetStore((state) => state.datasetKey);
  const source = datasetKey === `${folderPath}\0${projectTask}` ? folderPath : '';
  const scope = `${projectDir || ''}\0${source}\0${task}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const [comparisons, setComparisons] = useState<ModelComparisonRecord[]>([]);
  const [comparisonId, setComparisonId] = useState('');
  const [assessment, setAssessment] = useState<ModelDeploymentAssessment | null>(null);
  const [active, setActive] = useState<ModelDeploymentRevision | null>(null);
  const [history, setHistory] = useState<ModelDeploymentRevision[]>([]);
  const [reviewer, setReviewer] = useState('');
  const [reason, setReason] = useState('');
  const [attested, setAttested] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let valid = true;
    setComparisons([]); setComparisonId(''); setAssessment(null); setActive(null);
    setHistory([]); setAttested(false); setError(null); setNotice(null);
    if (!projectDir || !source) return () => { valid = false; };
    setLoading(true);
    Promise.all([
      api.evaluation.listComparisons(source, task),
      api.modelDeployments.active(source, task),
      api.modelDeployments.history(source, task),
    ]).then(([reports, approval, revisions]) => {
      if (!valid || currentScope.current !== scope) return;
      setComparisons(reports.comparisons);
      setComparisonId((previous) => reports.comparisons.some((item) => item.comparison_id === previous)
        ? previous : reports.comparisons[0]?.comparison_id || '');
      setActive(approval.active);
      setHistory(revisions.revisions);
      setRollbackTarget(revisions.revisions.find((item) => !item.is_active)?.revision_id || '');
    }).catch((cause) => {
      if (valid && currentScope.current === scope) setError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => { if (valid && currentScope.current === scope) setLoading(false); });
    return () => { valid = false; };
  }, [scope, projectDir, source, task, revision]);

  useEffect(() => {
    let valid = true;
    setAssessment(null);
    if (!comparisonId || !source) return () => { valid = false; };
    api.modelDeployments.assess(comparisonId, source, task).then((result) => {
      if (valid && currentScope.current === scope) setAssessment(result);
    }).catch((cause) => {
      if (valid && currentScope.current === scope) setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => { valid = false; };
  }, [scope, source, task, comparisonId, revision]);

  const apply = async (kind: 'approve' | 'rollback') => {
    if (!source || !reviewer.trim() || reason.trim().length < 8) return;
    const started = scope;
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = kind === 'approve'
        ? await api.modelDeployments.approve({ source_dataset_path: source, task, comparison_id: comparisonId,
          reviewer: reviewer.trim(), reason: reason.trim(), holdout_reviewed: true })
        : await api.modelDeployments.rollback({ source_dataset_path: source, task,
          target_revision_id: rollbackTarget, reviewer: reviewer.trim(), reason: reason.trim() });
      if (currentScope.current !== started) return;
      setReason(''); setAttested(false);
      setNotice(`${response.revision.job_id} · ${kind === 'approve' ? '승인' : '롤백'} revision ${response.revision.revision_id.slice(0, 8)} 저장됨`);
      setRevision((current) => current + 1);
    } catch (cause) {
      if (currentScope.current === started) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (currentScope.current === started) setBusy(false);
    }
  };

  return <section className="rounded-lg border border-[#3B5269] bg-[#111C2A] p-4 text-xs text-slate-200" aria-label="모델 승인과 롤백">
    <div className="flex items-start gap-3">
      <div className="rounded-md border border-cyan-500/30 bg-cyan-500/10 p-2 text-cyan-300"><ShieldCheck className="h-4 w-4" /></div>
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-white">후보 모델 승인 · 롤백</h3>
        <p className="mt-1 leading-relaxed text-slate-400">동일 test 이미지의 비교 결과를 검토해 승인 revision을 남깁니다. 5단계 저장 플로우와 현장 실행 서비스에는 자동 적용되지 않습니다.</p>
      </div>
      <button type="button" onClick={() => setRevision((value) => value + 1)} disabled={loading || busy || !source}
        className="inline-flex shrink-0 items-center gap-1 rounded border border-[#526177] px-2 py-1 text-[11px] text-slate-200 hover:bg-[#283247] disabled:opacity-40">
        <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 새로고침
      </button>
    </div>
    {!projectDir || !source ? <p className="mt-3 text-amber-300">1단계에서 현재 프로젝트의 데이터 출처를 선택하세요.</p> : <>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="rounded border border-[#30475C] bg-[#0B1520] p-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-cyan-300">승인 모델</div>
          {active ? <>
            <div className="mt-1 truncate font-mono text-sm font-semibold text-white" title={active.job_id}>{active.job_id}</div>
            <div className="mt-1 break-all font-mono text-[10px] text-slate-400">revision {active.revision_id} · SHA-256 {active.checkpoint_sha256}</div>
            {!active.valid && <p className="mt-2 text-rose-300">체크포인트를 재검증할 수 없습니다. 새 비교가 필요합니다.</p>}
          </> : <p className="mt-2 text-slate-400">아직 승인된 모델이 없습니다.</p>}
        </div>
        <div className="rounded border border-[#30475C] bg-[#0B1520] p-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-cyan-300">승인 기준</div>
          <p className="mt-1 leading-relaxed text-slate-300">OK·NG 정답 test 이미지 각 8장 이상, 두 모델의 모든 판정 완료, 신규 미검·과검 0건, 변경 없는 데이터·모델 해시가 필요합니다. 표본 대표성은 검토자가 확인합니다.</p>
        </div>
      </div>
      <label className="mt-3 block space-y-1 text-slate-300">저장된 후보 비교
        <select value={comparisonId} onChange={(event) => { setComparisonId(event.target.value); setAttested(false); setError(null); }}
          disabled={busy || loading} className="w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2 text-slate-100">
          <option value="">비교 보고서 선택</option>
          {comparisons.map((item) => <option key={item.comparison_id} value={item.comparison_id}>{item.incumbent_job_id} → {item.candidate_job_id} · {item.created_at.slice(0, 16)}</option>)}
        </select>
      </label>
      {loading && <p className="mt-2 text-cyan-300">승인 기록을 불러오는 중입니다.</p>}
      {assessment && <div role="status" className={`mt-3 rounded border p-3 ${assessment.status === 'ready' ? 'border-emerald-600/50 bg-emerald-950/20' : 'border-amber-600/50 bg-amber-950/20'}`}>
        <div className="flex items-center gap-1.5 font-semibold">{assessment.status === 'ready'
          ? <><CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" /> 정량 사전 검사 통과</>
          : <><AlertTriangle className="h-3.5 w-3.5 text-amber-300" /> 검토 필요 · 승격 차단</>}</div>
        <p className="mt-1 text-[11px] text-slate-300">정답 표본 OK {assessment.known_ok_images}장 · NG {assessment.known_ng_images}장 · 후보 {assessment.candidate_job_id}</p>
        {assessment.reasons.length > 0 && <ul className="mt-1 list-disc space-y-0.5 pl-4 text-amber-200">{assessment.reasons.map((item) => <li key={item}>{item}</li>)}</ul>}
      </div>}
      <div className="mt-3 grid gap-2 sm:grid-cols-[160px_1fr]">
        <label>승인자<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} maxLength={100} placeholder="이름 또는 ID"
          className="mt-1 w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2 text-white" /></label>
        <label>승인·롤백 사유<input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} placeholder="비교 결과와 판단 근거를 적어 주세요"
          className="mt-1 w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2 text-white" /></label>
      </div>
      <label className="mt-3 flex items-start gap-2 leading-relaxed text-slate-300">
        <input type="checkbox" checked={attested} onChange={(event) => setAttested(event.target.checked)} className="mt-0.5" />
        OK·NG 표본의 제품·Lot·촬영 조건이 적용 범위를 대표하는지 직접 검토했습니다.
      </label>
      <button type="button" onClick={() => void apply('approve')}
        disabled={busy || assessment?.status !== 'ready' || !attested || !reviewer.trim() || reason.trim().length < 8}
        className="mt-3 rounded border border-emerald-500/60 bg-emerald-700/30 px-3 py-2 font-semibold text-emerald-100 hover:bg-emerald-700/50 disabled:cursor-not-allowed disabled:opacity-40">
        후보 승인 revision 저장
      </button>
      {history.length > 1 && <div className="mt-4 border-t border-[#30475C] pt-3">
        <div className="flex items-center gap-1.5 font-semibold text-slate-100"><RotateCcw className="h-3.5 w-3.5 text-sky-300" /> 이전 승인 모델로 롤백</div>
        <div className="mt-2 flex flex-wrap gap-2">
          <select value={rollbackTarget} onChange={(event) => setRollbackTarget(event.target.value)} disabled={busy}
            className="min-w-[220px] flex-1 rounded border border-[#415970] bg-[#0B1520] px-2 py-2 text-slate-100">
            {history.filter((item) => !item.is_active).map((item) => <option key={item.revision_id} value={item.revision_id}>{item.job_id} · {item.action} · {item.revision_id.slice(0, 8)}</option>)}
          </select>
          <button type="button" onClick={() => void apply('rollback')}
            disabled={busy || !rollbackTarget || !reviewer.trim() || reason.trim().length < 8}
            className="rounded border border-sky-500/60 bg-sky-700/20 px-3 py-2 font-semibold text-sky-100 hover:bg-sky-700/40 disabled:opacity-40">롤백 revision 저장</button>
        </div>
      </div>}
      {notice && <p role="status" className="mt-3 rounded border border-emerald-700/40 bg-emerald-950/20 p-2 text-emerald-200">{notice}</p>}
      {error && <p role="alert" className="mt-3 rounded border border-rose-700/40 bg-rose-950/20 p-2 text-rose-200">{error}</p>}
    </>}
    <RuntimeServicePanel projectDir={projectDir} />
  </section>;
};
