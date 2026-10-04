import React, { useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileArchive, ListChecks, Play, RefreshCw, ShieldCheck, Square, X } from 'lucide-react';
import { api, type DatasetDuplicateGroup, type DatasetImportView, type DatasetRevisionImage, type DatasetRevisionRow } from '../../services/api';
import type { VisionTask } from '../../types';
import { uploadArchive, type ArchiveProgress } from '../../services/archiveUpload';
import { acceptBlocker, annotationSummary, archiveProgressText, clearImportKey, importSourceText, duplicateSummary, importEnded, importHeadline, importKeyFor, importProgress, revisionJobLabel, sourceMismatchNotice } from './datasetImportView';
import { datasetImportError as message } from './datasetImportError';

interface Props {
  projectId: string;
  datasetPath: string;
  /** The project's registered source: the folder the server reads. */
  registeredSource: string | null;
  task: VisionTask;
  onClose: () => void;
  onAccepted: () => void;
}

const storageKey = (projectId: string) => `modu.datasetImport.lastJob.${projectId}`;
const readLastJob = (projectId: string) => { try { return window.localStorage.getItem(storageKey(projectId)); } catch { return null; } };
const writeLastJob = (projectId: string, jobId: string) => { try { window.localStorage.setItem(storageKey(projectId), jobId); } catch { /* per-viewer convenience only */ } };
const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `import-${Date.now()}-${Math.random().toString(16).slice(2)}`);

/** Validated dataset revisions: a durable import reads every image; nothing becomes active until it is accepted. */
export const DatasetImportPanel: React.FC<Props> = ({ projectId, datasetPath, registeredSource, task, onClose, onAccepted }) => {
  const [policy, setPolicy] = useState<'exclude' | 'reject'>('exclude');
  const [verify, setVerify] = useState(false);
  const [followLinks, setFollowLinks] = useState(false);
  const [job, setJob] = useState<DatasetImportView | null>(null);
  const [revisions, setRevisions] = useState<DatasetRevisionRow[]>([]);
  const [activeRevision, setActiveRevision] = useState<string | null>(null);
  const [invalid, setInvalid] = useState<{ items: DatasetRevisionImage[]; next: string | null }>({ items: [], next: null });
  const [gaps, setGaps] = useState<Array<{ relative_path: string; reason: string }>>([]);
  const [annotationErrors, setAnnotationErrors] = useState<{ items: DatasetRevisionImage[]; next: string | null }>({ items: [], next: null });
  const [duplicateKind, setDuplicateKind] = useState<'all' | 'conflicting' | 'cross_split'>('all');
  const [duplicates, setDuplicates] = useState<{ groups: DatasetDuplicateGroup[]; next: string | null }>({ groups: [], next: null });
  const [confirming, setConfirming] = useState(false);
  const [archive, setArchive] = useState<File | null>(null);
  const [archiveProgress, setArchiveProgress] = useState<ArchiveProgress | null>(null);
  const archiveStop = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  // One key per intended import: a retried request (lost response, second click, reopened panel) returns the same job.
  const settingsKey = `${projectId}|${registeredSource || ''}|${task}|${policy}|${verify}|${followLinks}`;

  const loadRevisions = async () => {
    const listed = await api.datasetImports.revisions();
    if (!mounted.current) return;
    setRevisions(listed.revisions);
    setActiveRevision(listed.active_revision);
  };
  const loadInvalid = async (revisionId: string, cursor: string | null) => {
    const page = await api.datasetImports.images(revisionId, { valid: false, limit: 50, cursor });
    if (!mounted.current) return;
    setInvalid((current) => ({ items: cursor ? [...current.items, ...page.items] : page.items, next: page.next_cursor }));
  };
  const loadAnnotationErrors = async (revisionId: string, cursor: string | null) => {
    const page = await api.datasetImports.images(revisionId, { annotationError: true, valid: true, limit: 50, cursor });
    if (!mounted.current) return;
    setAnnotationErrors((current) => ({ items: cursor ? [...current.items, ...page.items] : page.items, next: page.next_cursor }));
  };
  const duplicateRequest = useRef(0);
  const loadDuplicates = async (revisionId: string, kind: 'all' | 'conflicting' | 'cross_split', cursor: string | null) => {
    const request = ++duplicateRequest.current;
    const page = await api.datasetImports.duplicates(revisionId, { limit: 20, cursor, kind: kind === 'all' ? undefined : kind });
    if (!mounted.current || request !== duplicateRequest.current) return;  // a newer filter or revision answered meanwhile
    setDuplicates((current) => ({ groups: cursor ? [...current.groups, ...page.groups] : page.groups, next: page.next_cursor }));
  };

  useEffect(() => {
    mounted.current = true;
    const last = readLastJob(projectId);
    void loadRevisions().catch((caught) => setError(message(caught)));
    if (last) void api.datasetImports.get(last).then((view) => mounted.current && setJob(view)).catch(() => undefined);
    return () => { mounted.current = false; archiveStop.current?.abort(); };  // an upload stops with the panel
  }, [projectId]);

  // Poll a running import; the job keeps running on the server if this panel closes.
  useEffect(() => {
    if (!job || importEnded(job)) return;
    const timer = window.setInterval(() => {
      void api.datasetImports.get(job.job_id).then((view) => {
        if (!mounted.current) return;
        setJob(view);
        if (importEnded(view)) void loadRevisions().catch((caught) => setError(message(caught)));
      }).catch((caught) => mounted.current && setError(message(caught)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.state]);

  const receipt = job?.result?.revision;
  useEffect(() => {
    setInvalid({ items: [], next: null });
    setGaps([]);
    if (receipt && receipt.error_count > 0) void loadInvalid(receipt.revision_id, null).catch((caught) => setError(message(caught)));
    if (receipt && receipt.unreadable_folders > 0) {
      void api.datasetImports.gaps(receipt.revision_id).then((found) => mounted.current && setGaps(found.gaps))
        .catch((caught) => setError(message(caught)));
    }
  }, [receipt?.revision_id]);

  useEffect(() => {
    setAnnotationErrors({ items: [], next: null });
    // Kept images whose annotation files are broken (folder-labelled tasks, or a Studio overlay labels the image).
    if (receipt && (receipt.annotation_errors || 0) > 0) {
      void loadAnnotationErrors(receipt.revision_id, null).catch((caught) => setError(message(caught)));
    }
  }, [receipt?.revision_id]);

  useEffect(() => {
    setDuplicates({ groups: [], next: null });
    if (receipt && (receipt.duplicate_groups || 0) > 0) {
      void loadDuplicates(receipt.revision_id, duplicateKind, null).catch((caught) => setError(message(caught)));
    }
  }, [receipt?.revision_id, duplicateKind]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [busy, onClose]);

  const start = async () => {
    if (busy) return;
    setBusy('start'); setError(null); setConfirming(false);
    try {
      const view = await api.datasetImports.start({ task, invalid_policy: policy, verify, follow_links: followLinks },
        importKeyFor(settingsKey, newKey));
      clearImportKey(settingsKey);
      writeLastJob(projectId, view.job_id);
      setJob(view);
    } catch (caught) { setError(message(caught)); } finally { setBusy(null); }
  };
  const startArchive = async () => {
    if (!archive || busy) return;
    const controller = new AbortController();
    archiveStop.current = controller;
    setBusy('archive'); setError(null); setConfirming(false);
    try {
      const uploaded = await uploadArchive(archive, api.artifacts, { signal: controller.signal, onProgress: (progress) => mounted.current && setArchiveProgress(progress) });
      if (controller.signal.aborted) return;  // stopped (or the panel closed) while the server verified it: no import
      const settings = `${projectId}|zip|${uploaded.sha256}|${task}|${policy}|${verify}`;
      const view = await api.datasetImports.startArchive({ artifact: uploaded.artifact, task, invalid_policy: policy, verify }, importKeyFor(settings, newKey));
      clearImportKey(settings);
      writeLastJob(projectId, view.job_id);
      if (mounted.current) { setJob(view); setArchive(null); }
    } catch (caught) {
      if (mounted.current) setError(message(caught));
    } finally {
      archiveStop.current = null;
      if (mounted.current) { setBusy(null); setArchiveProgress(null); }
    }
  };
  const cancel = async () => {
    if (!job || busy) return;
    setBusy('cancel'); setError(null);
    try { setJob(await api.datasetImports.cancel(job.job_id)); } catch (caught) { setError(message(caught)); } finally { setBusy(null); }
  };
  const blocker = acceptBlocker(job, revisions);
  const accept = async () => {
    if (!job || !receipt || blocker || busy) return;
    if (!confirming) { setConfirming(true); return; }
    setBusy('accept'); setError(null);
    try {
      await api.datasetImports.accept(job.job_id, receipt.revision_id, activeRevision);
      setConfirming(false);
      await loadRevisions();
      onAccepted();
    } catch (caught) {
      setError(`${message(caught)} — 활성 버전이 바뀌었을 수 있습니다. 목록을 새로 고친 뒤 다시 확인하세요.`);
      setConfirming(false);
      void loadRevisions().catch(() => undefined);
    } finally { setBusy(null); }
  };

  const progress = importProgress(job);
  const running = Boolean(job && !importEnded(job));
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[#040912]/80 p-5" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose();
    }}>
      <div role="dialog" aria-modal="true" aria-label="검증된 데이터 버전" className="flex max-h-full w-full max-w-[1000px] flex-col overflow-hidden rounded-xl border border-[#33465C] bg-[#111B29] shadow-[0_30px_90px_rgba(0,0,0,0.55)]">
        <div className="flex items-start justify-between border-b border-[#33465C] bg-[#17263A] px-7 py-6">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-bold tracking-[0.18em] text-sky-300"><ListChecks className="h-4 w-4" /> VALIDATED DATASET REVISIONS</div>
            <h2 className="text-xl font-semibold text-white">검증된 데이터 버전</h2>
            <p className="mt-1.5 max-w-[720px] text-xs leading-5 text-slate-400">모든 이미지를 열어 내용 해시·크기·손상 여부를 기록한 고정 버전을 만듭니다. 원본 파일은 읽기만 하며, 만든 버전은 채택해야 활성 버전이 됩니다.</p>
          </div>
          <button type="button" onClick={onClose} disabled={Boolean(busy)} aria-label="검증된 데이터 버전 닫기" className="rounded-md border border-[#43576F] p-1.5 text-slate-300 hover:bg-[#26394D] disabled:opacity-40"><X className="h-4 w-4" /></button>
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)]">
          <aside className="space-y-4 overflow-y-auto border-r border-[#33465C] bg-[#111A27] p-5">
            <div className="rounded-lg border border-[#344963] bg-[#18283B] p-3">
              <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-sky-300">REGISTERED SOURCE</div>
              <div className="break-all font-mono text-[10px] leading-5 text-slate-300">{registeredSource || '등록되지 않음'}</div>
            </div>
            {sourceMismatchNotice(datasetPath, registeredSource) && <div role="status" className="rounded-md border border-amber-600/50 bg-amber-950/30 p-2 text-[11px] leading-5 text-amber-200">{sourceMismatchNotice(datasetPath, registeredSource)}</div>}
            <fieldset className="space-y-2" disabled={running || Boolean(busy)}>
              <legend className="mb-1 text-sm font-semibold text-white">손상 이미지 처리</legend>
              <label className="flex items-start gap-2 text-xs leading-5 text-slate-300"><input type="radio" name="invalid-policy" checked={policy === 'exclude'} onChange={() => setPolicy('exclude')} className="mt-1" /><span><b className="text-slate-100">제외하고 기록</b> · 손상 이미지는 학습에서 빠지고 목록이 남습니다.</span></label>
              <label className="flex items-start gap-2 text-xs leading-5 text-slate-300"><input type="radio" name="invalid-policy" checked={policy === 'reject'} onChange={() => setPolicy('reject')} className="mt-1" /><span><b className="text-slate-100">하나라도 있으면 거부</b> · 버전은 기록되지만 채택할 수 없습니다.</span></label>
              <label className="flex items-start gap-2 pt-2 text-xs leading-5 text-slate-300"><input type="checkbox" checked={verify} onChange={(event) => setVerify(event.target.checked)} className="mt-1" /><span>바뀌지 않은 파일도 모두 다시 읽기</span></label>
              <label className="flex items-start gap-2 text-xs leading-5 text-slate-300"><input type="checkbox" checked={followLinks} onChange={(event) => setFollowLinks(event.target.checked)} className="mt-1" /><span>폴더 바로가기(링크)도 따라가기 · 이 PC의 원본에서만 허용됩니다</span></label>
            </fieldset>
            <div className="flex gap-2">
              <button type="button" className="workspace-button workspace-button--primary flex-1" onClick={() => void start()} disabled={running || Boolean(busy)}><Play className="h-4 w-4" />{busy === 'start' ? '요청 중...' : '전체 검증 실행'}</button>
              {running && <button type="button" className="workspace-button" onClick={() => void cancel()} disabled={Boolean(busy) || job?.cancel_requested}><Square className="h-4 w-4" />중지</button>}
            </div>
            <section aria-label="ZIP으로 가져오기" className="space-y-2 rounded-lg border border-[#344963] bg-[#18283B] p-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-white"><FileArchive className="h-4 w-4 text-sky-300" />ZIP으로 가져오기</div>
              <p className="text-[11px] leading-5 text-slate-400">ZIP은 이 프로젝트 폴더 안에 풀어서 같은 방식으로 검증합니다. 등록된 원본 폴더는 바뀌지 않습니다.</p>
              <input type="file" accept=".zip,application/zip" aria-label="가져올 ZIP 파일" disabled={running || Boolean(busy)}
                onChange={(event) => setArchive(event.target.files?.[0] || null)} className="block w-full text-[11px] text-slate-300 file:mr-2 file:rounded file:border-0 file:bg-[#26394D] file:px-2 file:py-1 file:text-slate-200" />
              {archive && <div className="break-all text-[11px] text-slate-400">{archive.name} · {(archive.size / (1024 * 1024)).toFixed(1)} MB</div>}
              {archiveProgress && <div role="status" aria-live="polite" className="text-[11px] text-sky-200">{archiveProgressText(archiveProgress)}</div>}
              <div className="flex gap-2">
                <button type="button" className="workspace-button flex-1" onClick={() => void startArchive()} disabled={!archive || running || Boolean(busy)}><FileArchive className="h-4 w-4" />{busy === 'archive' ? '올리는 중...' : 'ZIP 올리고 검증'}</button>
                {busy === 'archive' && <button type="button" className="workspace-button" onClick={() => archiveStop.current?.abort()}><Square className="h-4 w-4" />업로드 중지</button>}
              </div>
            </section>
            <div className="flex items-start gap-2 rounded-md border border-sky-800/50 bg-sky-950/20 p-3 text-[11px] leading-5 text-slate-400"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" /><span>앱을 닫아도 검증 기록은 남습니다. 파일 변경을 믿을 수 있게 확인할 수 있는 저장소에서는 바뀌지 않은 파일을 다시 읽지 않으며(Windows에서는 모두 다시 읽습니다), 버전마다 그 여부를 기록합니다.</span></div>
          </aside>
          <div className="min-h-0 space-y-5 overflow-y-auto p-6">
            {error && <div role="alert" className="rounded-md border border-red-500/50 bg-red-950/30 p-3 text-xs text-red-200">{error}</div>}
            {job && (
              <section aria-label="현재 가져오기" className="rounded-lg border border-[#344963] bg-[#152233] p-4">
                <div className="flex items-center justify-between text-xs"><span className="font-semibold text-slate-100">{importHeadline(job, revisions)}</span><span className="font-mono text-[10px] text-slate-500">{job.job_id}</span></div>
                {job.resumable && <button type="button" className="workspace-button" disabled={Boolean(busy)} onClick={() => {
                  setBusy('resume'); setError(null);
                  void api.datasetImports.resume(job.job_id).then(setJob).catch((caught) => setError(message(caught))).finally(() => setBusy(null));
                }}>중단된 검증 재개</button>}
                {job.operation && <div className="mt-1 text-[11px] text-slate-400">시도 {job.operation.attempt} · 단위 {job.operation.progress_unit} · {job.operation.result_ref ? `검증 ${job.operation.result_ref.count}장 · SHA-256 ${job.operation.result_ref.sha256}` : '결과 검증 대기'}</div>}
                {importSourceText(job) && <div className="mt-1 text-[11px] text-slate-400" title={job.source?.root}>{importSourceText(job)}</div>}
                <div className="mt-3" aria-live="polite">
                  {progress.percent === null
                    ? <div className="text-[11px] text-slate-400">{progress.text}</div>
                    : <><div className="h-2 overflow-hidden rounded bg-[#0C1521]" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}><div className="h-full bg-sky-500" style={{ width: `${progress.percent}%` }} /></div><div className="mt-1 text-[11px] text-slate-400">{progress.text}</div></>}
                </div>
                {receipt && (
                  <dl className="mt-4 grid grid-cols-5 gap-3 text-xs">
                    <div><dt className="text-slate-500">전체</dt><dd className="text-lg font-semibold text-white">{receipt.image_count.toLocaleString()}</dd></div>
                    <div><dt className="text-slate-500">정상</dt><dd className="text-lg font-semibold text-emerald-300">{receipt.valid_count.toLocaleString()}</dd></div>
                    <div><dt className="text-slate-500">손상·읽기 실패</dt><dd className="text-lg font-semibold text-red-300">{receipt.error_count.toLocaleString()}</dd></div>
                    <div><dt className="text-slate-500">읽지 못한 폴더</dt><dd className="text-lg font-semibold text-red-300">{receipt.unreadable_folders.toLocaleString()}</dd></div>
                    <div><dt className="text-slate-500">따라가지 않은 링크</dt><dd className="text-lg font-semibold text-amber-300">{receipt.skipped_links.toLocaleString()}</dd></div>
                  </dl>
                )}
                {receipt && (
                  <ul aria-label="주석과 중복" className="mt-3 space-y-1 text-[11px] leading-5 text-slate-400">
                    {annotationSummary(receipt) && <li>{annotationSummary(receipt)}</li>}
                    {duplicateSummary(receipt) && <li className={receipt.conflicting_duplicates || receipt.cross_split_duplicates ? 'text-amber-200' : undefined}>{duplicateSummary(receipt)}</li>}
                  </ul>
                )}
                {receipt && (
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <button type="button" className="workspace-button workspace-button--primary" onClick={() => void accept()} disabled={Boolean(blocker) || Boolean(busy)} title={blocker || undefined}>
                      <CheckCircle2 className="h-4 w-4" />{confirming ? '채택 확인' : '이 버전 채택'}
                    </button>
                    {confirming && <span className="text-[11px] text-amber-200">현재 활성 버전 {activeRevision ? activeRevision.slice(0, 12) : '없음'}을(를) 이 버전 {receipt.revision_id.slice(0, 12)}로 바꿉니다. 한 번 더 누르면 적용됩니다.</span>}
                    {blocker && <span className="text-[11px] text-slate-400">{blocker}</span>}
                  </div>
                )}
              </section>
            )}
            {gaps.length > 0 && (
              <section aria-label="읽지 못한 폴더" className="rounded-lg border border-red-900/50 bg-red-950/10 p-4">
                <h3 className="mb-2 text-sm font-semibold text-red-200">읽지 못한 폴더 · 이 안의 이미지는 버전에 없습니다</h3>
                <ul className="space-y-1 text-[11px]">
                  {gaps.map((row) => <li key={row.relative_path} className="flex gap-3"><span className="break-all font-mono text-slate-300">{row.relative_path}</span><span className="text-red-300">{row.reason}</span></li>)}
                </ul>
              </section>
            )}
            {invalid.items.length > 0 && (
              <section aria-label="손상·제외 이미지" className="rounded-lg border border-red-900/50 bg-red-950/10 p-4">
                <h3 className="mb-2 text-sm font-semibold text-red-200">손상·제외 이미지</h3>
                <ul className="space-y-1 text-[11px]">
                  {invalid.items.map((row) => (
                    <li key={row.relative_path} className="flex gap-3">
                      <span className="break-all font-mono text-slate-300">{row.relative_path}</span>
                      <span className="shrink-0 text-red-300" title={row.error_detail || undefined}>{row.error_code}</span>
                      {row.annotation_error && <span className="min-w-0 break-words text-slate-400">{row.error_detail}</span>}
                    </li>
                  ))}
                </ul>
                {invalid.next && receipt && <button type="button" className="workspace-button mt-2" onClick={() => void loadInvalid(receipt.revision_id, invalid.next).catch((caught) => setError(message(caught)))}>더 보기</button>}
              </section>
            )}
            {annotationErrors.items.length > 0 && (
              <section aria-label="제외하지 않은 주석 오류" className="rounded-lg border border-amber-900/50 bg-amber-950/10 p-4">
                <h3 className="mb-2 text-sm font-semibold text-amber-100">주석 파일 오류 · 이 이미지들은 폴더 라벨이나 Studio 라벨로 학습되어 제외하지 않았습니다</h3>
                <ul className="space-y-1 text-[11px]">
                  {annotationErrors.items.map((row) => (
                    <li key={row.relative_path} className="flex gap-3"><span className="break-all font-mono text-slate-300">{row.relative_path}</span>
                      <span className="min-w-0 break-words text-amber-200/80">{row.annotation_error}</span></li>
                  ))}
                </ul>
                {annotationErrors.next && receipt && <button type="button" className="workspace-button mt-2" onClick={() => void loadAnnotationErrors(receipt.revision_id, annotationErrors.next).catch((caught) => setError(message(caught)))}>더 보기</button>}
              </section>
            )}
            {receipt && (receipt.duplicate_groups || 0) > 0 && (
              <section aria-label="같은 내용의 이미지" className="rounded-lg border border-amber-900/50 bg-amber-950/10 p-4">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-amber-100">같은 내용의 이미지 · 보고만 하며 지우지 않습니다</h3>
                  <div role="radiogroup" aria-label="중복 그룹 보기" className="flex gap-1 text-[11px]">
                    {([['all', '전체'], ['conflicting', '라벨 다름'], ['cross_split', '분할 섞임']] as const).map(([kind, label]) => (
                      <button key={kind} type="button" role="radio" aria-checked={duplicateKind === kind} onClick={() => setDuplicateKind(kind)}
                        className={`rounded border px-2 py-0.5 ${duplicateKind === kind ? 'border-amber-400 text-amber-100' : 'border-[#43576F] text-slate-400'}`}>{label}</button>
                    ))}
                  </div>
                </div>
                {duplicates.groups.length === 0 && <div className="text-[11px] text-slate-400">이 조건의 그룹이 없습니다.</div>}
                <ul className="space-y-2 text-[11px]">
                  {duplicates.groups.map((group) => (
                    <li key={group.sha256} className="rounded border border-[#2F4157] bg-[#142030] p-2">
                      <div className="mb-1 flex flex-wrap gap-2 text-slate-300">
                        <span className="font-mono">{group.sha256.slice(0, 12)}</span><span>{group.members.toLocaleString()}장</span>
                        {group.conflicting && <span className="text-amber-300">라벨 다름</span>}
                        {group.cross_split && <span className="text-amber-300">train/val/test 섞임</span>}
                      </div>
                      <ul className="space-y-0.5">
                        {group.items.map((item) => (
                          <li key={item.relative_path} className="flex gap-3"><span className="break-all font-mono text-slate-400">{item.relative_path}</span>
                            <span className="shrink-0 text-slate-500">{[item.label, item.split, ...item.annotation_labels].filter(Boolean).join(' · ') || '라벨 없음'}</span></li>
                        ))}
                        {group.members > group.items.length && <li className="text-slate-500">외 {(group.members - group.items.length).toLocaleString()}장</li>}
                      </ul>
                    </li>
                  ))}
                </ul>
                {duplicates.next && receipt && <button type="button" className="workspace-button mt-2" onClick={() => void loadDuplicates(receipt.revision_id, duplicateKind, duplicates.next).catch((caught) => setError(message(caught)))}>더 보기</button>}
              </section>
            )}
            <section aria-label="버전 목록">
              <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-100">기록된 버전</h3><button type="button" className="workspace-button" onClick={() => void loadRevisions().catch((caught) => setError(message(caught)))} disabled={Boolean(busy)}><RefreshCw className="h-3.5 w-3.5" />새로 고침</button></div>
              {revisions.length === 0 && <div className="rounded-lg border border-dashed border-[#3D5066] px-4 py-8 text-center text-xs text-slate-400">아직 검증된 버전이 없습니다.</div>}
              <ul className="space-y-2">
                {revisions.map((row) => (
                  <li key={row.revision_id} className="flex items-center justify-between rounded-md border border-[#2F4157] bg-[#142030] px-3 py-2 text-xs">
                    <span className="font-mono text-slate-300" title={row.publication_key ? `가져오기 작업 ${row.publication_key}` : '작업 기록 없음'}>{row.revision_id.slice(0, 12)} · {revisionJobLabel(row)}</span>
                    <span className="text-slate-400">{row.task} · {row.image_count.toLocaleString()}장 · 손상·주석 오류 {row.error_count} · {row.annotations_scanned ? `중복 그룹 ${row.duplicate_groups ?? 0}` : '주석·중복 미기록'} · {row.invalid_policy === 'reject' ? '거부 정책' : '제외 정책'}</span>
                    <span className={row.active ? 'font-semibold text-emerald-300' : row.state === 'rejected' ? 'text-red-300' : 'text-slate-500'}>{row.active ? '활성' : row.state === 'rejected' ? '채택 불가' : '채택 전'}</span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
};
