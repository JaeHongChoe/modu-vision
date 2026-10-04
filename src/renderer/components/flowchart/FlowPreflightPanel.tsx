import { useEffect, useRef, useState } from 'react';
import { flowPreflight, type PreflightReport } from '../../services/flowPreflight';
import type { FlowchartPipeline } from '../../types';
import { byNode, KIND_NAMES, PREFLIGHT_TARGETS, remedyText, sameTarget, staleLine, STATE_NAMES, statusLine } from './flowPreflightText';

const STATE_STYLE: Record<string, string> = {
  ready: 'text-emerald-300', unverified: 'text-sky-300', missing: 'text-rose-300', mismatch: 'text-rose-300', unavailable: 'text-rose-300',
};

/** E07: the saved version's dependencies on a deployment target, node by node, with what to do; the last report for the
 * version and target reopens with whether it is still current. Nothing is installed or substituted. */
export function FlowPreflightPanel({ versionId, recipeTask, sourceDatasetPath, pipeline, onSelectNode }: {
  versionId: string | null; recipeTask: string | null; sourceDatasetPath: string; pipeline: FlowchartPipeline | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const [targetKey, setTargetKey] = useState(PREFLIGHT_TARGETS[0].key);
  const [report, setReport] = useState<PreflightReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const target = PREFLIGHT_TARGETS.find(row => row.key === targetKey)!.target;
  const generation = useRef(0);
  const scope = JSON.stringify([versionId, recipeTask, sourceDatasetPath, targetKey]);
  const currentScope = useRef(scope);
  currentScope.current = scope;

  useEffect(() => {
    const request = ++generation.current;
    const isCurrent = () => request === generation.current && scope === currentScope.current;
    setReport(null); setError(''); setBusy(false);
    if (versionId) {
      flowPreflight.list(versionId).then(async ({ reports }) => {
        const latest = reports.find(row => sameTarget(row.target_identity, target));
        if (!latest || !isCurrent()) return;
        const reopened = await flowPreflight.read(latest.report_id, sourceDatasetPath, target);
        if (isCurrent()) setReport(reopened);
      }).catch(cause => { if (isCurrent()) setError(cause instanceof Error ? cause.message : String(cause)); });
    }
    return () => { generation.current += 1; };
  }, [versionId, recipeTask, targetKey, sourceDatasetPath]);

  const run = async () => {
    if (!versionId || !recipeTask || busy) return;
    const request = ++generation.current;
    const isCurrent = () => request === generation.current && scope === currentScope.current;
    setBusy(true); setError('');
    try {
      const checked = await flowPreflight.run({ source_dataset_path: sourceDatasetPath, recipe_task: recipeTask, version_id: versionId, target });
      if (isCurrent()) setReport(checked);
    }
    catch (cause) { if (isCurrent()) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (isCurrent()) setBusy(false); }
  };
  const label = (id: string | null) => id === null ? '플로우 전체' : pipeline?.nodes.find(node => node.id === id)?.data.label || id;
  const order = pipeline?.nodes.map(node => node.id) ?? [];

  return (
    <section aria-label="배포 전 의존성 점검" className="space-y-3 rounded border border-slate-700 p-3">
      <h4 className="font-semibold">배포 전 의존성 점검</h4>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">배포 대상<select aria-label="점검 대상" value={targetKey} onChange={event => setTargetKey(event.target.value)}
          className="ml-2 rounded border border-slate-600 bg-slate-950 p-2 text-sm">
          {PREFLIGHT_TARGETS.map(row => <option key={row.key} value={row.key}>{row.label}</option>)}</select></label>
        <button type="button" className="workspace-button" disabled={!versionId || !recipeTask || busy} onClick={() => void run()}>
          {busy ? '점검 중…' : '점검 실행'}</button>
      </div>
      {!versionId && <p className="text-amber-200">저장된 버전을 열면 점검할 수 있습니다.</p>}
      {error && <p role="alert" className="text-amber-200">{error}</p>}
      {report && <>
        {report.stale && <p role="alert" className="rounded border border-amber-600 bg-amber-950/40 p-2 text-amber-200">{staleLine(report.stale_reasons)}</p>}
        <p role="status" className={report.stale ? 'text-amber-200' : report.status === 'ready' ? 'text-emerald-300' : report.status === 'blocked' ? 'text-rose-300' : 'text-sky-300'}>{statusLine(report)}</p>
        <p className="text-xs text-slate-400">{new Date(report.checked_at).toLocaleString('ko-KR')} 점검 · 보고서 {report.report_id.slice(0, 8)}</p>
        <ol aria-label="노드별 의존성" className="space-y-2">{byNode(report.requirements, order).map(([nodeId, rows]) => <li key={nodeId ?? 'flow'} className="rounded border border-slate-800 p-2">
          <div className="flex items-center gap-2">
            {nodeId ? <button type="button" className="font-semibold text-cyan-200 underline" onClick={() => onSelectNode(nodeId)}>{label(nodeId)}</button>
              : <strong>{label(null)}</strong>}
            {nodeId && report.blocked_nodes[nodeId] && <span className="text-xs text-rose-300">막힘</span>}
          </div>
          <ul className="mt-1 space-y-1 text-xs">{rows.map((row, index) => <li key={index}>
            <span className="text-slate-400">{KIND_NAMES[row.kind]}</span> {row.artifact_ref}{row.version_range ? ` ${row.version_range}` : ''}
            {' · '}<span className={STATE_STYLE[row.state]}>{STATE_NAMES[row.state]}</span>
            {row.license_ref && <span className="text-slate-500"> · 가중치 조건 {row.license_ref.replace('model-license-matrix:', '')}</span>}
            {remedyText(row) && <p className="text-slate-300" title={row.remedy ?? undefined}>→ {remedyText(row)}</p>}
          </li>)}</ul>
        </li>)}</ol>
      </>}
    </section>
  );
}
