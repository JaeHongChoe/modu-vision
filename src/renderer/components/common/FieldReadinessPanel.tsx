/** Steps 1 and 4: the conditions a field package needs, all at once, with where to do each (audit F-11 / A6). */
import { useEffect, useState } from 'react';
import { request } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { subscribeWorkflowEvidence, workflowEvidenceRevision } from '../../stores/workflowEvidence';
import type { WorkflowImpact } from '../wizard/workflowReadiness';
import { fieldConditions, singleModelExportNote } from './fieldReadiness';

function useImpact(): WorkflowImpact | null {
  const project = useProjectStore(state => state.project);
  const [revision, setRevision] = useState(workflowEvidenceRevision);
  const [impact, setImpact] = useState<WorkflowImpact | null>(null);
  useEffect(() => subscribeWorkflowEvidence(() => setRevision(workflowEvidenceRevision())), []);
  useEffect(() => {
    let live = true;
    setImpact(null);
    if (project?.id && project.source_dataset_dir) request<WorkflowImpact>('/api/provenance/impact')
      .then(value => { if (live && value.project_id === project.id && Array.isArray(value.models)) setImpact(value); })
      .catch(() => { /* the conditions stay "확인 필요" */ });
    return () => { live = false; };
  }, [project?.id, project?.source_dataset_dir, project?.active_labelset_id, revision]);
  return impact;
}

export function FieldReadinessPanel() {
  const task = useProjectStore(state => state.task);
  const setStep = useProjectStore(state => state.setStep);
  const classSplitCounts = useDatasetStore(state => state.classSplitCounts);
  const impact = useImpact();
  // Approval and release refuse an example project (synthetic data) whatever its counts; say so up front.
  const example = useProjectStore(state => Boolean((state.project as { example?: unknown } | null)?.example));
  const conditions = fieldConditions({ task, classSplitCounts: classSplitCounts ?? null, impact });
  const done = conditions.filter(row => row.state === 'done').length;
  return <details aria-label="현장 적용까지 남은 조건" className="rounded border border-slate-700 bg-[#101A28] p-3 text-xs text-slate-200">
    <summary className="cursor-pointer font-semibold">현장 적용까지 남은 조건 · {done}/{conditions.length} 완료</summary>
    {example && <p role="note" className="mt-2 text-amber-100">이 프로젝트는 예제(합성 데이터)라 승인·현장 적용 대상이 아닙니다. 아래 조건은 실제 공정 이미지로 만든 프로젝트에서 채우세요.</p>}
    <p className="mt-2 text-slate-400">단일 모델 파일은 승인 없이도 내보낼 수 있지만, 현장 검사 흐름 패키지는 아래 조건이 모두 필요합니다. 미리 데이터를 계획하세요.</p>
    <ul className="mt-2 space-y-1">{conditions.map(row => <li key={row.id} className="flex flex-wrap items-center gap-2">
      <span className={row.state === 'done' ? 'text-emerald-300' : row.state === 'todo' ? 'text-amber-200' : 'text-slate-400'}>{row.state === 'done' ? '✓ 완료' : row.state === 'todo' ? '○ 남음' : '? 확인 필요'}</span>
      <span>{row.text}</span>
      {row.state !== 'done' && <button type="button" className="workspace-button min-h-0 py-0.5" onClick={() => void setStep(row.step)}>{row.where}로</button>}
    </li>)}</ul>
  </details>;
}

export function SingleModelExportNotice({ jobId }: { jobId: string | null }) {
  const note = singleModelExportNote(jobId, useImpact());
  return note ? <p role="note" className="mt-2 rounded border border-amber-700/60 bg-amber-950/20 p-2 text-xs text-amber-100">{note}</p> : null;
}
