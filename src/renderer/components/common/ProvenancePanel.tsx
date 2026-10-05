import { useEffect, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { History, Loader2, RefreshCw, X } from 'lucide-react';
import { request, getProjectContextGeneration, subscribeProjectContext } from '../../services/api';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import {WorkflowImpactPanel} from './WorkflowImpactPanel';
import {useDeliveryScope} from '../runtime/useDeliveryScope';
import {ImageSearchSelect} from './ImageSearchSelect';

type TraceRecord = Record<string, unknown>;
interface Trace {
  project: { name: string; id: string; active_labelset_id: string };
  image: { image_uuid: string; content_hash: string; content_version: number; file_path: string; product: string; lot: string };
  label: { sha256: string | null; revision: number; workflow_state: string; reviewer: string | null; history: TraceRecord[] };
  split: { sha256: string | null; assignment: string | null };
  dataset_versions: TraceRecord[];
  models: TraceRecord[];
  flows: TraceRecord[];
  inspections: TraceRecord[];
  review_binding?: {book_version:number;book_sha256:string|null;policy_revision:number;policy_sha256:string;review_revision:number;review_state:string;image_eligible:boolean;eligible_count:number;eligibility_sha256:string;approved_only_training:boolean;ready:boolean;blockers:string[]};
}
const states: Record<string, string> = { unworked: '미작업', needs_review: '검수 필요', approved: '승인' };
const hash = (value: unknown) => typeof value === 'string' ? value : '기록 없음';

function EvidenceList({ title, rows }: { title: string; rows: TraceRecord[] }) {
  return <section className="rounded border border-[#344255] p-3">
    <h3 className="mb-2 font-semibold text-slate-100">{title} <span className="text-slate-300">{rows.length}</span></h3>
    {!rows.length && <p className="text-slate-300">저장된 기록이 없습니다.</p>}
    <div className="max-h-56 space-y-2 overflow-y-auto">{rows.map((row, index) => <details key={String(row.id || row.job_id || row.version_id || row.run_id || index)} className="rounded border border-[#344255] bg-[#111B28] px-3 py-2">
      <summary className="cursor-pointer text-slate-200">{String(row.name || row.task || row.state || row.action || '기록')} · {String(row.id || row.job_id || row.version_id || row.run_id || row.revision || index + 1)}
        {row.image_matches === false && <span className="ml-2 text-amber-300">원본 변경됨</span>}
        {row.data_state === 'changed' && <span className="ml-2 text-amber-300">학습 입력 변경됨 · 재검토 필요</span>}
        {row.data_state === 'unverified' && <span className="ml-2 text-amber-300">학습 버전 확인 필요</span>}
      </summary><dl className="mt-2 space-y-1">{Object.entries(row).map(([key, value]) => <div key={key} className="break-all text-xs"><dt className="inline text-slate-300">{key}: </dt><dd className="inline whitespace-pre-wrap font-mono text-slate-100">{typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? '기록 없음')}</dd></div>)}</dl>
    </details>)}</div>
  </section>;
}

export function ProvenancePanel({ onClose }: { onClose: () => void }) {
  const images = useDatasetStore((s) => s.images);
  const currentImage = useAnnotationStore((s) => s.currentImage);
  const {scope,key,project}=useDeliveryScope();
  const language=useProjectStore((s)=>s.language);
  const epoch=useSyncExternalStore(subscribeProjectContext,getProjectContextGeneration,getProjectContextGeneration);
  const [imagePath, setImagePath] = useState(currentImage?.file_path || images[0]?.file_path || '');
  const snapshotKey=JSON.stringify([key,epoch,imagePath]);
  const [saved, setResult] = useState<{key:string;data:Trace} | null>(null);
  const result=saved?.key===snapshotKey?saved.data:null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let live = true;const started=scope.current;
    const current=()=>live&&scope.current===started&&getProjectContextGeneration()===epoch;
    if (!imagePath) { setResult(null); return; }
    setBusy(true); setError(''); setResult(null);
    void request<Trace>(`/api/provenance?image_path=${encodeURIComponent(imagePath)}`).then((data) => {
      if (current()) setResult({key:snapshotKey,data});
    }).catch((e) => { if (current()) setError(e instanceof Error ? e.message : '이력을 불러오지 못했습니다.'); })
      .finally(() => { if (current()) setBusy(false); });
    return () => { live = false; };
  }, [imagePath, snapshotKey, revision]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [onClose]);
  return createPortal(<div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/70 p-5">
    <section role="dialog" aria-modal="true" aria-labelledby="provenance-title" className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded border border-[#344255] bg-[#182332] text-sm text-slate-200">
      <header className="flex items-center justify-between border-b border-[#344255] p-4"><h2 id="provenance-title" className="flex items-center gap-2 font-semibold"><History className="h-4 w-4 text-cyan-300" />데이터·모델·판정 이력</h2><button type="button" onClick={onClose} aria-label="이력 닫기" className="rounded p-2 hover:bg-slate-700"><X className="h-5 w-5" /></button></header>
      <div className="space-y-4 overflow-y-auto p-4">
        <WorkflowImpactPanel />
        <p className="text-slate-300">이미지부터 라벨, 분할, 모델, 플로우와 검사 결과의 저장된 출처를 확인합니다.</p>
        <p role="note" className="text-slate-300">이 화면은 읽기 전용입니다. 현재 선택과 과거 실행 입력은 별도이며, 외부 변경 후 새로고침하여 저장된 근거와 비교하세요.</p>
        <div className="flex items-end gap-2"><div className="flex-1"><ImageSearchSelect label="검사 이미지" value={imagePath} onChange={setImagePath} language={language} folder={project?.source_dataset_dir} task={project?.task} /></div><button type="button" disabled={busy || !imagePath} onClick={() => setRevision((v) => v + 1)} aria-label="이력 새로고침" className="rounded border border-slate-600 p-2 disabled:opacity-40"><RefreshCw className="h-4 w-4" /></button></div>
        {busy && <p role="status"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />저장된 출처를 확인하고 있습니다…</p>}
        {error && <p role="alert" className="rounded border border-rose-700 p-3 text-rose-200">{error}</p>}
        {result && <>
          <dl className="grid gap-3 rounded border border-[#344255] p-3 md:grid-cols-2">
            <div><dt className="text-slate-300">프로젝트</dt><dd>{result.project.name} · {result.project.id}</dd></div>
            <div><dt className="text-slate-300">이미지 ID · 내용 버전</dt><dd className="break-all font-mono">{result.image.image_uuid} · v{result.image.content_version}</dd></div>
            <div><dt className="text-slate-300">이미지 해시</dt><dd className="break-all font-mono text-xs">{hash(result.image.content_hash)}</dd></div>
            <div><dt className="text-slate-300">라벨 해시 · 수정 버전</dt><dd className="break-all font-mono text-xs">{hash(result.label.sha256)} · r{result.label.revision}</dd></div>
            <div><dt className="text-slate-300">검수 상태 · 검토자</dt><dd>{states[result.label.workflow_state] || result.label.workflow_state} · {result.label.reviewer || '미지정'}</dd></div>
            <div><dt className="text-slate-300">제품 · Lot · 분할</dt><dd>{result.image.product || '미지정'} · {result.image.lot || '미지정'} · {result.split.assignment || '미분할'}</dd></div>
            <div><dt className="text-slate-300">분할 해시</dt><dd className="break-all font-mono text-xs">{hash(result.split.sha256)}</dd></div>
          </dl>
          {result.review_binding&&<section aria-label="현재 검수·학습 적격성" className="rounded border border-[#344255] p-3"><h3 className="font-semibold">현재 검수·학습 적격성</h3><dl className="mt-2 grid gap-2 md:grid-cols-2"><div><dt>가이드·정책·검수 revision</dt><dd>가이드 v{result.review_binding.book_version} · 정책 r{result.review_binding.policy_revision} · 검수 r{result.review_binding.review_revision}</dd></div><div><dt>선택 이미지 학습 적격성</dt><dd>{result.review_binding.image_eligible?'적격':'제외'} · 전체 적격 {result.review_binding.eligible_count}장 · {result.review_binding.approved_only_training?'승인된 데이터만':'승인 전 데이터 포함 정책'}</dd></div><div><dt>가이드 해시</dt><dd className="break-all font-mono text-xs">{hash(result.review_binding.book_sha256)}</dd></div><div><dt>정책 해시</dt><dd className="break-all font-mono text-xs">{hash(result.review_binding.policy_sha256)}</dd></div><div><dt>적격 cohort 해시</dt><dd className="break-all font-mono text-xs">{hash(result.review_binding.eligibility_sha256)}</dd></div></dl>{result.review_binding.blockers?.map(reason=><p key={reason} className="mt-2 text-amber-300">{reason}</p>)}<p className="mt-2 text-slate-300">학습 모델에 고정된 검수 receipt는 아래 과거 데이터 버전·모델 기록에서 확인합니다. 현재 적격성은 모델 품질 승인이 아닙니다.</p></section>}
          <div className="grid gap-3 md:grid-cols-2"><EvidenceList title="데이터 버전" rows={result.dataset_versions} /><EvidenceList title="학습 모델" rows={result.models} /><EvidenceList title="저장 플로우" rows={result.flows} /><EvidenceList title="검사 결과·판정 근거" rows={result.inspections} /></div>
          <EvidenceList title="라벨·검수 변경 이력" rows={result.label.history} />
          <p className="text-xs leading-5 text-slate-300">현재 라벨과 과거 데이터 버전은 별도 기록입니다. 이전 모델에 사용한 라벨 버전이 저장되지 않았다면 현재 라벨을 그 모델의 학습 정답으로 표시하지 않습니다.</p>
        </>}
      </div>
    </section>
  </div>, document.body);
}
