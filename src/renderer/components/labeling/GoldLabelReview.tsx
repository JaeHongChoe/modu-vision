import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, getApiPersistenceIdentity, getProjectContextGeneration, subscribeProjectContext, type ProjectLabelSet } from '../../services/api';
import { teamDataApi, type QualityProfile, type QualityReport, type QualityReportSummary } from '../../services/teamDataApi';
import { workflowError, type ImageReviewMetadata } from '../../services/datasetWorkflow';
import { useProjectStore } from '../../stores/useProjectStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { countsLine, describeConflict, eligibilityLine, limitationText, notComparableText, reviewTask } from './goldReview';
import { teamDataScope } from './teamDataWorkflow';

const input = 'rounded border border-slate-600 bg-slate-950 p-2 text-sm';
const button = 'rounded border border-slate-600 px-3 py-2 text-sm disabled:opacity-40';
type CurrentReview = () => boolean;
function liveReviewScope() {
  const project = useProjectStore.getState();
  return JSON.stringify([teamDataScope({ ...project, ...useComputeStore.getState(), apiTransportIdentity: getApiPersistenceIdentity() }),
    project.project?.task, getProjectContextGeneration(), useAnnotationStore.getState().reviewerName]);
}

/** E05: a labeler's label set compared object by object with the approved labels of chosen gold images. Gold images
 * leave ordinary training and test use unless the policy below keeps them. */
export function GoldLabelReview({ scope, actor, canManage, onImageOpened }: {
  scope: string; actor: string; canManage: boolean; onImageOpened?: () => void;
}) {
  const project = useProjectStore(state => state.project);
  // The review compares the project's own task; a classification or anomaly project has no objects to compare.
  const task = reviewTask(project?.task);
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [includeGold, setIncludeGold] = useState(false);
  const [labelsets, setLabelsets] = useState<{ active: string; all: ProjectLabelSet[] }>({ active: '', all: [] });
  const [approved, setApproved] = useState<ImageReviewMetadata[]>([]);
  const [reports, setReports] = useState<QualityReportSummary[]>([]);
  const [report, setReport] = useState<QualityReport | null>(null);
  const [candidate, setCandidate] = useState('');
  const [tolerance, setTolerance] = useState(0.5);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [authorityRevision, setAuthorityRevision] = useState(0);
  const props = useRef({ scope, actor, canManage });
  props.current = { scope, actor, canManage };
  const readAuthority = () => JSON.stringify([props.current.scope, props.current.actor, props.current.canManage, liveReviewScope()]);
  const renderedAuthority = readAuthority();
  const authority = useRef({ key: renderedAuthority, epoch: 0, request: 0, mounted: false, busy: false });

  useLayoutEffect(() => {
    authority.current.mounted = true;
    // Store notifications are synchronous, so leaving and returning to a scope
    // invalidates a pending continuation even if React batches both changes.
    const changed = () => {
      const next = readAuthority();
      if (next === authority.current.key) return;
      Object.assign(authority.current, { key: next, epoch: authority.current.epoch + 1,
        request: authority.current.request + 1, busy: false });
      setAuthorityRevision(value => value + 1);
    };
    const unsubscribe = [useProjectStore.subscribe(changed), useComputeStore.subscribe(changed),
      useAnnotationStore.subscribe(changed), subscribeProjectContext(changed)];
    changed();
    return () => {
      authority.current.mounted = false;
      authority.current.epoch++; authority.current.request++; authority.current.busy = false;
      unsubscribe.forEach(stop => stop());
    };
  }, []);
  useLayoutEffect(() => {
    if (renderedAuthority === authority.current.key) return;
    Object.assign(authority.current, { key: renderedAuthority, epoch: authority.current.epoch + 1,
      request: authority.current.request + 1, busy: false });
  }, [renderedAuthority]);

  const load = async (current: CurrentReview) => {
    if (!current()) return false;
    const [quality, sets, queue, saved] = await Promise.all([teamDataApi.qualityProfiles(), api.project.listLabelsets(),
      teamDataApi.queue({ state: 'approved', limit: 200 }), teamDataApi.qualityReports()]);
    if (!current()) return false;
    setProfiles(quality.profiles);
    setIncludeGold(quality.gold_policy.include_gold_in_training);
    setLabelsets({ active: sets.active_id, all: sets.labelsets });
    setApproved(queue.items);
    setReports(saved.reports);
    setCandidate(current => current || sets.labelsets.find(row => row.id !== sets.active_id)?.id || '');
    return true;
  };
  const run = async (action: (current: CurrentReview) => Promise<unknown>) => {
    const owner = authority.current;
    if (!owner.mounted || owner.busy || renderedAuthority !== owner.key || readAuthority() !== renderedAuthority) return;
    const epoch = owner.epoch; const request = ++owner.request;
    const current = () => owner.mounted && owner.epoch === epoch && owner.request === request
      && owner.key === renderedAuthority && readAuthority() === renderedAuthority;
    owner.busy = true;
    setBusy(true); setError('');
    try { await action(current); } catch (cause) { if (current()) setError(workflowError(cause)); }
    finally { if (current()) { owner.busy = false; setBusy(false); } }
  };
  useEffect(() => {
    setProfiles([]); setIncludeGold(false); setLabelsets({ active: '', all: [] }); setApproved([]);
    setReports([]); setReport(null); setChosen(new Set()); setCandidate(''); setError(''); setBusy(false);
    void run(load);
  }, [renderedAuthority, authorityRevision]);

  const create = () => run(async current => {
    if (!task || !canManage) return;
    const profile = await teamDataApi.createQualityProfile({ task, reference_labelset: labelsets.active, candidate_labelset: candidate,
      gold_images: [...chosen], tolerance, actor });
    if (!current()) return;
    setChosen(new Set());
    if (!await load(current)) return;
    const made = await teamDataApi.runQualityReport(profile.profile_id);
    if (!current()) return;
    const opened = await teamDataApi.qualityReport(made.report_id);
    if (!current()) return;
    setReport(opened);
    const saved = await teamDataApi.qualityReports();
    if (current()) setReports(saved.reports);
  });
  const runProfile = (profileId: string) => run(async current => {
    if (!canManage) return;
    const made = await teamDataApi.runQualityReport(profileId);
    if (!current()) return;
    const opened = await teamDataApi.qualityReport(made.report_id);
    if (!current()) return;
    setReport(opened);
    const saved = await teamDataApi.qualityReports();
    if (current()) setReports(saved.reports);
  });
  const open = (reportId: string) => run(async current => {
    const opened = await teamDataApi.qualityReport(reportId);
    if (current()) setReport(opened);
  });
  const retire = (profileId: string) => run(async current => {
    if (!canManage) return;
    await teamDataApi.retireQualityProfile(profileId, actor);
    if (!await load(current)) return;
    if (report?.profile_id === profileId) {
      const opened = await teamDataApi.qualityReport(report.report_id);
      if (current()) setReport(opened);
    }
  });
  const toggle = (path: string) => setChosen(current => {
    const next = new Set(current);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });
  const others = labelsets.all.filter(row => row.id !== labelsets.active);
  const name = (id: string) => labelsets.all.find(row => row.id === id)?.name || id;

  return (
    <section aria-label="정답 기준 라벨 검수" className="space-y-4">
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-3 text-sm text-rose-200">{error}</p>}
      <div className="grid gap-4 xl:grid-cols-2">
        <section aria-label="새 검수 기준" className="space-y-3 rounded border border-slate-700 p-4">
          <h3 className="font-semibold">새 검수 기준</h3>
          <p className="text-sm text-slate-400">현재 라벨셋 <strong>{name(labelsets.active)}</strong>의 승인된 이미지를 정답으로 삼아, 다른 라벨셋의 같은 이미지를 객체 단위로 비교합니다.</p>
          <div className="flex flex-wrap items-end gap-3">
            <p className="text-sm">작업 <strong>{task === 'segmentation' ? '영역 분할 (마스크)' : task === 'detection' ? '객체 검출 (상자·다각형)' : '지원하지 않음'}</strong></p>
            <label className="text-sm">비교할 라벨셋<select aria-label="비교할 라벨셋" value={candidate} onChange={event => setCandidate(event.target.value)} className={`${input} ml-2`}>
              {others.length === 0 && <option value="">다른 라벨셋 없음</option>}
              {others.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
            <label className="text-sm">일치 기준 겹침(IoU)<input aria-label="일치 기준 겹침" type="number" min="0.11" max="1" step="0.05" value={tolerance}
              onChange={event => setTolerance(Number(event.target.value))} className={`${input} ml-2 w-20`} /></label>
          </div>
          <fieldset className="space-y-1">
            <legend className="text-sm">정답 이미지 (승인된 이미지 {approved.length}장{approved.length >= 200 ? ', 앞 200장' : ''})</legend>
            <button type="button" className={button} disabled={!approved.length} onClick={() => setChosen(new Set(approved.map(row => row.file_path)))}>모두 선택</button>
            <ul className="max-h-40 overflow-auto text-sm">{approved.map(row => <li key={row.image_uuid}>
              <label className="flex items-center gap-2"><input type="checkbox" checked={chosen.has(row.file_path)} onChange={() => toggle(row.file_path)} />{row.relative_path}</label>
            </li>)}</ul>
            {!approved.length && <p className="text-sm text-slate-400">승인된 이미지가 없습니다. 작업·검수 탭에서 먼저 승인하세요.</p>}
          </fieldset>
          {!task && <p role="note" className="text-sm text-amber-200">이 프로젝트의 작업({project?.task})은 객체 단위 라벨 검수를 지원하지 않습니다. 객체 검출·영역 분할 프로젝트에서 사용하세요.</p>}
          <button type="button" className={`${button} border-cyan-600`} disabled={busy || !task || !canManage || !candidate || !chosen.size || !actor} onClick={() => void create()}>
            검수 기준 만들고 실행 ({chosen.size}장)</button>
          {!actor && <p className="text-sm text-amber-200">위의 작업자 이름을 입력하세요. 검수 기준에 만든 사람으로 기록됩니다.</p>}
          {!canManage && <p className="text-sm text-amber-200">검토자 또는 소유자만 검수 기준을 만들 수 있습니다.</p>}
          <label className="flex items-start gap-2 rounded border border-slate-700 p-3 text-sm">
            <input type="checkbox" aria-label="정답 이미지도 학습·시험에 사용" checked={includeGold} disabled={busy || !canManage}
              onChange={event => void run(async current => {
                if (!canManage) return;
                const policy = await teamDataApi.setGoldPolicy(event.target.checked, actor || undefined);
                if (current()) setIncludeGold(policy.include_gold_in_training);
              })} />
            <span><strong>정답 이미지도 학습·시험에 사용</strong><span className="mt-1 block text-slate-400">기본은 사용하지 않습니다. 검수 정답이 시험 정답으로 새지 않도록 학습·시험 내보내기에서 뺍니다.</span></span>
          </label>
        </section>
        <section aria-label="검수 기준과 결과" className="space-y-3 rounded border border-slate-700 p-4">
          <h3 className="font-semibold">검수 기준과 저장된 결과</h3>
          {!profiles.length && <p className="text-sm text-slate-400">아직 검수 기준이 없습니다.</p>}
          <ol className="max-h-72 space-y-2 overflow-auto">{profiles.map(profile => <li key={profile.profile_id} className="rounded border border-slate-700 p-2 text-sm">
            <p>{new Date(profile.created_at).toLocaleString('ko-KR')} · {profile.task === 'segmentation' ? '영역 분할' : '객체 검출'} · {name(profile.reference_labelset)} → {name(profile.candidate_labelset)} · 정답 {profile.reference_snapshot.length}장 · IoU {profile.tolerance}</p>
            <div className="mt-1 flex gap-2">
              <button type="button" className={button} disabled={busy || !canManage} onClick={() => void runProfile(profile.profile_id)}>다시 실행</button>
              <button type="button" className={button} disabled={busy || !canManage || !actor} onClick={() => void retire(profile.profile_id)}>그만 쓰기</button>
            </div>
            <ul className="mt-1 space-y-1">{reports.filter(row => row.profile_id === profile.profile_id).map(row => <li key={row.report_id}>
              <button type="button" className="text-left text-cyan-200 underline" disabled={busy} onClick={() => void open(row.report_id)}>
                {new Date(row.created_at || '').toLocaleString('ko-KR')} · {countsLine(row.counts)} · 일치 {row.agreeing_images}/{row.images}장{row.passes ? ' · 통과' : ''}</button>
            </li>)}</ul>
          </li>)}</ol>
          {reports.filter(row => row.integrity_error).map(row => <p key={row.report_id} role="alert" className="text-sm text-rose-200">결과 {row.report_id.slice(0, 8)}: {row.integrity_error}</p>)}
        </section>
      </div>
      {report && <section aria-label="라벨 검수 결과" className="space-y-2 rounded border border-slate-700 p-4">
        <h3 className="font-semibold">검수 결과 · {countsLine(report.counts)} · 일치 {report.agreeing_images}/{report.images.length}장</h3>
        <p role="status" className={report.approval_eligible ? 'text-emerald-300' : 'text-amber-200'}>{eligibilityLine(report)}</p>
        <ol className="max-h-[40vh] space-y-2 overflow-auto">{report.images.filter(image => image.conflicts.length || !image.labeled || image.error).map(image => <li key={image.relative_path} className="rounded border border-slate-800 p-2 text-sm">
          <button type="button" className="break-all text-left text-cyan-100 underline" disabled={busy}
            onClick={() => void run(async current => {
              const opened = await useProjectStore.getState().openImageForLabeling(image.relative_path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/, ''), image.image_path);
              if (opened && current()) onImageOpened?.();  // close only the review that opened the image
            })}>
            {image.relative_path}</button>
          {!image.labeled && <p className="text-amber-200">비교 라벨셋에 저장된 라벨이 없습니다.</p>}
          {image.error && <p role="alert" className="break-all text-amber-200">{notComparableText(image.error)}</p>}
          <ul className="list-disc pl-5">{image.conflicts.map(conflict => <li key={conflict.conflict_id}>{describeConflict(conflict)}</li>)}</ul>
        </li>)}</ol>
        <ul className="text-xs text-slate-400">{report.limitations.map(row => <li key={row}>{limitationText(row)}</li>)}</ul>
      </section>}
    </section>
  );
}
