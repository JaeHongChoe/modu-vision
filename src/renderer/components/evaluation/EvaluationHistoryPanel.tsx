import { useEffect, useRef, useState } from 'react';
import { History, Loader2, RefreshCw } from 'lucide-react';
import { api, request,getApiPersistenceIdentity, type ProjectLabelSet } from '../../services/api';
import type { FlowModelTask } from '../../types';
import {useComputeStore} from '../../stores/useComputeStore';
import { useProjectStore } from '../../stores/useProjectStore';
import {EvaluationEvidencePanel} from './EvaluationEvidencePanel';
import {consumeReviewContext,evaluationOriginScope} from '../labeling/productDataWorkflow';
import type {EvidenceSample} from '../../services/evaluationEvidence';
import {evaluationTasks,readEvaluationChoice,readEvaluationView,readEvaluationOpen,rememberEvaluationPreference,type EvaluationView} from './evaluationSelection';

interface EvaluationRecord {
  evaluation_id: string; created_at: number; evidence_sha256: string;
  binding: Record<string, unknown>;
  result: { job_id?: string; metrics?: Record<string, unknown>; test_predictions?: EvidenceSample[] };
  grouped_errors: Record<string, Record<string, { samples: number; errors: number; misses: number; overkill: number; unknown_truth?: number }>>;
}

export function EvaluationHistoryPanel({ sourceFolder, task, jobId }: { sourceFolder: string; task: FlowModelTask; jobId: string | null }) {
  const compute={...useComputeStore(),apiTransportIdentity:getApiPersistenceIdentity()};
  const projectId = useProjectStore((s) => s.project?.id);
  const activeLabelset = useProjectStore(s => s.project?.active_labelset_id || 'default');
  const viewKey = 'vision-evaluation-view:' + evaluationOriginScope(projectId, sourceFolder, 'evaluation_history', '', compute);
  const [viewState, setViewState] = useState(() => ({ key: viewKey, ...readEvaluationView(localStorage, viewKey, task) }));
  // During a scope change, never render or persist the previous project's view.
  const scopedView = viewState.key === viewKey ? viewState : { key: viewKey, ...readEvaluationView(localStorage, viewKey, task) };
  const view = scopedView.taskExplicit ? scopedView : { ...scopedView, task };
  const { task: historyTask, labelsetId, group } = view;
  // Expanding the panel does not select data. Keep it open when this project's source folder finishes loading.
  const openKey = 'vision-evaluation-open:' + evaluationOriginScope(projectId, '', 'evaluation_history', '', compute);
  const [openState, setOpenState] = useState(() => ({ key: openKey, opened: readEvaluationOpen(localStorage, openKey) }));
  const opened = openState.key === openKey ? openState.opened : readEvaluationOpen(localStorage, openKey);
  const changeOpened = (opened: boolean) => {
    rememberEvaluationPreference(localStorage, openKey, { version: 1, opened });
    setOpenState({ key: openKey, opened });
  };
  const [revision, setRevision] = useState(0);
  const recordKey = 'vision-evaluation-record:' + evaluationOriginScope(projectId, sourceFolder, historyTask, labelsetId, compute);
  const scope = JSON.stringify([recordKey, compute.transportRevision]);
  const currentScope = useRef(scope); currentScope.current = scope;
  type HistoryState = { scope: string; records: EvaluationRecord[]; selected: string;
    originImage: { imageId?: string; filePath?: string } | null; busy: boolean; error: string };
  const emptyHistory = (): HistoryState => ({ scope, records: [], selected: '', originImage: null, busy: false, error: '' });
  const [history, setHistory] = useState<HistoryState>(emptyHistory);
  const { records, selected, originImage, busy, error } = history.scope === scope ? history : emptyHistory();
  const [labelsetState, setLabelsetState] = useState<{ key: string; items: ProjectLabelSet[]; loaded: boolean; error: string }>(
    { key: '', items: [], loaded: false, error: '' });
  const labelsetKey = JSON.stringify([viewKey, compute.transportRevision, revision]);
  const labelsets = labelsetState.key === labelsetKey ? labelsetState.items : [];
  const missingLabelset = !!labelsetId && labelsetState.key === labelsetKey && labelsetState.loaded
    && !labelsets.some(item => item.id === labelsetId);
  const catalogError = labelsetState.key === labelsetKey ? labelsetState.error : '';
  const freshCatalogReady = labelsetState.key === labelsetKey && labelsetState.loaded && !catalogError && !missingLabelset;
  const membershipScope = JSON.stringify([viewKey, compute.transportRevision, historyTask, labelsetId]);
  const confirmedMembership = useRef('');
  // A confirmed refresh outage may keep reading the same previously confirmed
  // set. Pending, missing, new-scope and unconfirmed catalogs confer no authority.
  const confirmedReadContinuity = !!labelsetId && !!catalogError && confirmedMembership.current === membershipScope;
  const canReadHistory = freshCatalogReady || confirmedReadContinuity;
  const currentCatalog = useRef(''); currentCatalog.current = canReadHistory ? labelsetKey : '';
  const currentMutationCatalog = useRef(''); currentMutationCatalog.current = freshCatalogReady ? labelsetKey : '';
  const changeView = (patch: Partial<EvaluationView>) => {
    setViewState(current => {
      const live = current.key === viewKey ? current : { key: viewKey, ...readEvaluationView(localStorage, viewKey, task) };
      const next = { ...live, ...patch, task: patch.task ?? (live.taskExplicit ? live.task : task),
        taskExplicit: patch.task !== undefined || live.taskExplicit };
      rememberEvaluationPreference(localStorage, viewKey, { version: 1, ...next });
      return next;
    });
  };
  useEffect(() => {
    setViewState(current => current.key !== viewKey ? { key: viewKey, ...readEvaluationView(localStorage, viewKey, task) }
      : !current.taskExplicit && current.task !== task ? { ...current, task } : current);
  }, [viewKey, task]);
  useEffect(() => {
    setOpenState(current => current.key === openKey ? current : { key: openKey, opened: readEvaluationOpen(localStorage, openKey) });
  }, [openKey]);
  useEffect(() => {
    let active = true;
    setLabelsetState({ key: labelsetKey, items: [], loaded: false, error: '' });
    void api.project.listLabelsets().then(response => {
      if (active) setLabelsetState({ key: labelsetKey, items: response.labelsets, loaded: true, error: '' });
    }).catch(cause => {
      if (active) setLabelsetState({ key: labelsetKey, items: [], loaded: false,
        error: cause instanceof Error ? cause.message : '라벨 세트를 불러오지 못했습니다.' });
    });
    return () => { active = false; };
  }, [labelsetKey, revision]);
  useEffect(() => {
    let current = true;
    const ownsCatalog = () => current && currentScope.current === scope && currentCatalog.current === labelsetKey;
    if (confirmedMembership.current !== membershipScope) confirmedMembership.current = '';
    if (freshCatalogReady) confirmedMembership.current = labelsetId ? membershipScope : '';
    else if (labelsetState.key === labelsetKey && labelsetState.loaded) confirmedMembership.current = '';
    setHistory({ ...emptyHistory(), busy: !!sourceFolder && (canReadHistory || (!missingLabelset && !catalogError)) });
    if (!sourceFolder || !canReadHistory) return;
    const params = new URLSearchParams({ source_dataset_path: sourceFolder, task: historyTask });
    if (labelsetId) params.set('labelset_id', labelsetId);
    void request<{ items: EvaluationRecord[] }>(`/api/evaluation/history?${params}`).then(response => {
      if (!ownsCatalog()) return;
      let origin = null;
      try { origin = consumeReviewContext(localStorage,
        evaluationOriginScope(projectId, sourceFolder, historyTask, labelsetId || activeLabelset, compute),
        response.items.map(item => item.evaluation_id)); } catch { /* Preference storage is optional. */ }
      const remembered = readEvaluationChoice(localStorage, recordKey);
      const choice = origin?.evaluation_id || remembered || response.items[0]?.evaluation_id || '';
      const found = response.items.some(item => item.evaluation_id === choice);
      setHistory({ scope, records: response.items, selected: found ? choice : '', busy: false,
        originImage: origin ? { imageId: origin.image_id || undefined, filePath: origin.file_path } : null,
        error: choice && !found ? `선택했던 평가 ${choice}를 이 데이터·모델 종류·라벨 세트에서 찾지 못했습니다. 다른 평가를 직접 선택하세요.` : '' });
      if (found) rememberEvaluationPreference(localStorage, recordKey, { version: 1, evaluation_id: choice });
      if (origin && found) {
        changeOpened(true);
      }
    }).catch(cause => {
      if (ownsCatalog()) setHistory({ ...emptyHistory(),
        error: cause instanceof Error ? cause.message : '평가 이력을 불러오지 못했습니다.' });
    });
    return () => { current = false; };
  }, [scope, sourceFolder, projectId, historyTask, labelsetId, activeLabelset, revision, labelsetKey, canReadHistory, freshCatalogReady, membershipScope, missingLabelset, catalogError]);
  const record = canReadHistory ? records.find(item => item.evaluation_id === selected) : undefined;
  const reevaluate = async () => {
    const targetJob = record?.result.job_id || (historyTask === task ? jobId : null);
    if (!targetJob || !sourceFolder || !freshCatalogReady || currentMutationCatalog.current !== labelsetKey) return;
    const expected = scope;
    const current = () => currentScope.current === expected && currentMutationCatalog.current === labelsetKey;
    setHistory(current => ({ ...current, busy: true, error: '' }));
    try {
      await request('/api/evaluation/reevaluate', { method: 'POST', body: JSON.stringify({ source_dataset_path: sourceFolder,
        task: historyTask, job_id: targetJob, ...(record?.binding.evaluation_dataset_path ? { dataset_path: record.binding.evaluation_dataset_path } : {}) }) });
      if (current()) setRevision(v => v + 1);
    } catch (cause) {
      if (current()) setHistory(current => ({ ...current,
        error: cause instanceof Error ? cause.message : '재평가하지 못했습니다.' }));
    } finally { if (current()) setHistory(current => ({ ...current, busy: false })); }
  };
  return <details open={opened} onToggle={event=>changeOpened(event.currentTarget.open)} className="rounded border border-[#344255] bg-[#182332] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 p-3 font-semibold"><History className="h-4 w-4 text-cyan-300" />평가 이력 · 제품/Lot별 오류</summary>
    <div className="space-y-3 border-t border-[#344255] p-3">
      <p className="text-slate-300">각 평가의 데이터·모델 출처와 결과를 별도 버전으로 보관합니다. 제품과 Lot은 데이터 화면에 입력한 값을 사용합니다.</p>
      <label>평가 모델 종류<select aria-label="평가 이력 모델 종류" disabled={!sourceFolder} value={historyTask} onChange={(e) => changeView({task:e.target.value})} className="ml-2 rounded border border-slate-600 bg-[#0E1722] p-2">{Object.entries(evaluationTasks).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label className="ml-3">평가 당시 라벨 세트<select aria-label="평가 라벨 세트" disabled={!sourceFolder} value={labelsetId} onChange={e=>changeView({labelsetId:e.target.value})} className="ml-2 rounded border border-slate-600 bg-[#0E1722] p-2"><option value="">전체 세트</option>{labelsetId&&!labelsets.some(item=>item.id===labelsetId)&&<option value={labelsetId}>{labelsetId} · 확인 중 또는 찾을 수 없음</option>}{labelsets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <div className="flex flex-wrap gap-2"><button type="button" disabled={!freshCatalogReady || busy || (!!error && !record) || !(record?.result.job_id || (historyTask === task && jobId))} onClick={() => void reevaluate()} className="rounded border border-cyan-700 px-3 py-2 text-cyan-200 disabled:opacity-40">선택 모델 재평가 · 새 이력 저장</button><button type="button" disabled={busy} aria-label="평가 이력 새로고침" onClick={() => setRevision((v) => v + 1)} className="rounded border border-slate-600 p-2"><RefreshCw className="h-4 w-4" /></button></div>
      {busy && <p role="status"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />평가 기록 확인 중…</p>}
      {labelsetState.key===labelsetKey&&labelsetState.error&&<p role="alert">{labelsetState.error}</p>}
      {missingLabelset&&<p role="alert">선택했던 라벨 세트 {labelsetId}를 찾지 못했습니다. 다른 세트를 직접 선택하세요.</p>}
      {error && <p role="alert" className="rounded border border-rose-700 p-2 text-rose-200">{error}</p>}
      {!busy && !records.length && <p className="text-slate-300">저장된 평가 이력이 없습니다.</p>}
      {!!records.length && <label className="block">모델별 저장 평가<select value={selected} onChange={(e) => {const id=e.target.value;if(!records.some(item=>item.evaluation_id===id))return;rememberEvaluationPreference(localStorage,recordKey,{version:1,evaluation_id:id});setHistory(current=>({...current,selected:id,originImage:null,error:''}));}} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-2">{!selected&&<option value="" disabled>평가를 직접 선택하세요.</option>}{Array.from(new Set(records.map(r=>r.result.job_id||'unknown'))).map(model=><optgroup key={model} label={`모델 ${model}`}>{records.filter(r=>r.result.job_id===model).map(item=><option key={item.evaluation_id} value={item.evaluation_id}>{new Date(item.created_at*1000).toLocaleString()} · {String(item.binding.labelset_id||'default')} · {item.evaluation_id.slice(-8)}</option>)}</optgroup>)}</select></label>}
      {record && <>
        {!!Object.keys((record.binding.threshold_settings||{}) as Record<string,unknown>).length&&<p className="rounded bg-[#0E1722] p-2 text-cyan-200">평가 임계값: {JSON.stringify(record.binding.threshold_settings)}</p>}
        <label>오류 집계<select aria-label="평가 오류 집계 기준" value={group} onChange={(e) => changeView({group:e.target.value as EvaluationView['group']})} className="ml-2 rounded border border-slate-600 bg-[#0E1722] p-2"><option value="product">제품</option><option value="lot">Lot</option><option value="ground_truth">정답 클래스</option></select></label>
        <table className="w-full border-collapse text-left"><thead><tr className="border-b border-slate-600 text-slate-300"><th className="p-2">그룹</th><th>이미지</th><th>오류</th><th>미검</th><th>과검</th><th>정답 미확인</th></tr></thead><tbody>{Object.entries(record.grouped_errors[group] || {}).map(([key, counts]) => <tr key={key} className="border-b border-[#344255]"><td className="p-2">{key === '(unassigned)' ? '미지정' : key}</td><td>{counts.samples}</td><td>{counts.errors}</td><td>{counts.misses}</td><td>{counts.overkill}</td><td>{counts.unknown_truth || 0}</td></tr>)}</tbody></table>
        {!Object.keys(record.grouped_errors[group] || {}).length && <p className="text-slate-400">이 모델은 영역·문자·복원 지표로 평가합니다. 이진 정상/불량 오류를 임의로 집계하지 않습니다.</p>}
        <details><summary className="cursor-pointer text-cyan-200">저장된 평가 지표·이미지 결과</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-[#0E1722] p-3">{JSON.stringify(record.result, null, 2)}</pre></details>
        <EvaluationEvidencePanel samples={record.result.test_predictions||[]} initialPath={originImage?.filePath} initialImageId={originImage?.imageId} originalPreview={row=>request(`/api/evaluation/history/${record.evaluation_id}/evidence-image?${new URLSearchParams({source_dataset_path:sourceFolder,task:historyTask,image_path:row.file_path||''})}`)}/>
        <details><summary className="cursor-pointer text-cyan-200">평가 버전·데이터·모델 해시 확인</summary><pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-[#0E1722] p-3">{JSON.stringify({ evaluation_id: record.evaluation_id, evidence_sha256: record.evidence_sha256, binding: record.binding }, null, 2)}</pre></details>
      </>}
    </div>
  </details>;
}
