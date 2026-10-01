import { Fragment, useEffect, useRef, useState } from 'react';
import { useProjectStore } from '../../stores/useProjectStore';
import { recoveryStep } from '../wizard/workflowReadiness';
import { request } from '../../services/api';
import { useDeliveryScope } from '../runtime/useDeliveryScope';
import { AsyncAction } from './AsyncAction';
import { FormField } from './FormField';
import { StatusBadge, type StatusTone } from './StatusBadge';
import { WorkspaceDialog } from './WorkspaceDialog';
import { WorkspaceFeedback } from '../layout/WorkspaceFeedback';
import { WorkspaceSection } from '../layout/WorkspaceSection';

type Evidence = { state?: string; checkpoint_state?: string; data_state?: string; reason?: string; job_id?: string; version_id?: string; package_id?: string; evaluation_id?: string; revision_id?: string; name?: string };
type Impact = { models: Evidence[]; flows: Evidence[]; model_evaluations: Evidence[]; flow_evaluations: Evidence[]; approvals: Evidence[]; packages: Evidence[]; required_actions: string[] };
type LegacyEntry = { issue: string; reason: string; source_ids: Record<string, string | null>; lineage: Record<string, string | null>; scope_matches: boolean | null };
type LegacyAction = { action: string; reason: string; source_ids: Record<string, string | null> };
type LegacyImpact = { affected: LegacyEntry[]; unaffected: LegacyEntry[]; unknown: LegacyEntry[]; next_action: LegacyAction[] };
type LegacyStatus = 'affected' | 'unknown' | 'unaffected';
const actions: Record<string, string> = { review_data: '변경 라벨 검수', train_candidate: '후보 모델 학습 검토', evaluate_model: '모델 재평가', compare_fixed_cohort: '동일 시험 데이터로 비교', approve_model: '검토 후 모델 승인', evaluate_flow: '전체 플로우 재평가', export_package: '검증 후 패키지 다시 생성', verify_target: '대상 장치에서 실행 확인', verify_dataset_version: '학습 데이터 버전 확인', inspect_training_config: '저장된 학습 설정 확인', review_approval: '승인 근거 다시 검토', inspect_evidence: '원본 기록 확인' };
const states: Record<string, string> = { current: '현재 입력과 일치', changed: '변경됨', revalidation_required: '재검증 필요', unverified: '근거 확인 필요', different_source: '이전 데이터 원본 · 재사용 계보 확인 필요' };
const legacyLabels: Record<LegacyStatus, string> = { affected: '영향 확인', unknown: '근거 부족', unaffected: '해당 문제 영향 없음' };
const legacyTones: Record<LegacyStatus, StatusTone> = { affected: 'warning', unknown: 'neutral', unaffected: 'success' };
const issues: Record<string, string> = { training_geometry: '학습 공간 증강', model_score_contract: '모델 점수 기준', evaluation_score_contract: '평가 점수 기준', flow_score_contract: '플로우 점수 기준', approval_truth_binding: '승인 정답 근거', unreadable_evidence: '기록 읽기' };
const identifiers: Record<string, string> = { job_id: '모델 ID', version_id: '플로우 버전', evaluation_id: '평가 ID', revision_id: '승인 ID', comparison_id: '비교 ID', node_id: '노드 ID', artifact_id: '기록 ID', source_dataset_path: '기록된 데이터 원본', training_source_dataset_path: '학습 데이터 원본', dataset_version_id: '데이터 버전', labelset_id: '학습 라벨셋', evaluation_labelset_id: '평가 라벨셋', parent_job_id: '부모 모델', checkpoint_sha256: '모델 해시', evidence_checkpoint_sha256: '근거의 모델 해시', calibration_id: '모델 보정 ID', evidence_calibration_id: '근거의 보정 ID' };
const message = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);

function RecordedFields({ values }: { values: Record<string, string | null> }) {
  return <dl>{Object.entries(values).filter(([, value]) => value != null).map(([key, value]) => <Fragment key={key}>
    <dt>{identifiers[key] || key}</dt><dd>{value}</dd>
  </Fragment>)}</dl>;
}

function LegacyRecords({ result }: { result: LegacyImpact }) {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<LegacyStatus | 'all'>('all');
  const groups: LegacyStatus[] = ['affected', 'unknown', 'unaffected'];
  const all = groups.flatMap(group => result[group].map(row => ({ ...row, status: group })));
  const query = search.trim().toLocaleLowerCase();
  const shown = all.filter(row => (status === 'all' || row.status === status) &&
    [row.reason, issues[row.issue] || row.issue, ...Object.values(row.source_ids), ...Object.values(row.lineage)].join(' ').toLocaleLowerCase().includes(query));
  return <div className="workspace-stack">
    <div className="workspace-toolbar" aria-label="기존 기록 영향 요약">{groups.map(group => <StatusBadge key={group} tone={legacyTones[group]}>{legacyLabels[group]} · {result[group].length}</StatusBadge>)}</div>
    <p className="workspace-description">건수는 검토 항목 수입니다. 같은 모델에 여러 항목이 있을 수 있습니다. 영향 확인은 기록된 계약의 문제이며 품질 저하 원인을 확정하지 않습니다.</p>
    <div className="workspace-toolbar workspace-form-row">
      <FormField label="기록 검색" hint="모델·평가·승인 ID 또는 기록된 원본과 이유로 검색하세요.">{props => <input {...props} type="search" value={search} onChange={event => setSearch(event.target.value)} />}</FormField>
      <FormField label="기록 상태">{props => <select {...props} value={status} onChange={event => setStatus(event.target.value as LegacyStatus | 'all')}><option value="all">모든 상태</option>{groups.map(group => <option key={group} value={group}>{legacyLabels[group]}</option>)}</select>}</FormField>
    </div>
    {!shown.length && <WorkspaceFeedback kind="empty" title={all.length ? '검색 조건에 맞는 기록이 없습니다.' : '아직 조사할 저장 기록이 없습니다.'}
      description={all.length ? '검색어나 상태를 바꿔 다시 확인하세요.' : '모델이나 평가를 저장한 뒤 현재 입력을 다시 확인하세요.'}
      action={all.length ? <AsyncAction onClick={() => { setSearch(''); setStatus('all'); }}>검색 초기화</AsyncAction> : undefined} />}
    <div className="workspace-records">{shown.map((row, index) => <article key={`${row.issue}:${JSON.stringify(row.source_ids)}:${index}`} className="workspace-record">
      <div className="workspace-toolbar"><StatusBadge tone={legacyTones[row.status]}>{legacyLabels[row.status]}</StatusBadge><h4 className="font-semibold">{issues[row.issue] || row.issue}</h4></div>
      <p className="workspace-description">{row.reason}</p>
      {row.scope_matches === false && <p className="workspace-description">현재 원본과 다른 과거 기록입니다. 저장된 출처를 확인한 후 재사용하세요.</p>}
      <RecordedFields values={row.source_ids} />
      <details className="workspace-disclosure"><summary>저장된 출처와 보정 정보</summary><RecordedFields values={row.lineage} /></details>
    </article>)}</div>
    {result.next_action.length > 0 && <div><h4 className="font-semibold">검토 후 진행할 작업</h4><p className="workspace-description">아래 작업은 안내입니다. 재학습·재평가·승인을 자동으로 실행하지 않습니다.</p>
      <ul className="workspace-records">{result.next_action.map((action, index) => <li key={index} className="workspace-record"><strong>{actions[action.action] || action.action}</strong><RecordedFields values={action.source_ids} /><p className="workspace-description">{action.reason}</p></li>)}</ul>
    </div>}
  </div>;
}

export function WorkflowImpactPanel() {
  const { scope, key, projectDir } = useDeliveryScope();
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<Impact | null>(null);
  const [legacy, setLegacy] = useState<LegacyImpact | null>(null);
  const [busy, setBusy] = useState(false), [legacyBusy, setLegacyBusy] = useState(false);
  const [error, setError] = useState(''), [legacyError, setLegacyError] = useState('');
  const currentGeneration = useRef(0), legacyGeneration = useRef(0);
  useEffect(() => {
    setOpen(false); setResult(null); setLegacy(null); setError(''); setLegacyError(''); setBusy(false); setLegacyBusy(false);
    return () => { currentGeneration.current++; legacyGeneration.current++; };
  }, [key]);
  const close = () => {
    currentGeneration.current++; legacyGeneration.current++;
    setBusy(false); setLegacyBusy(false); setOpen(false);
  };
  const refreshCurrent = async () => {
    const started = scope.current, generation = ++currentGeneration.current;
    const isCurrent = () => scope.current === started && currentGeneration.current === generation;
    setBusy(true); setError('');
    try { const value = await request<Impact>('/api/provenance/impact'); if (isCurrent()) setResult(value); }
    catch (cause) { if (isCurrent()) setError(message(cause)); }
    finally { if (isCurrent()) setBusy(false); }
  };
  const refreshLegacy = async () => {
    const started = scope.current, generation = ++legacyGeneration.current;
    const isCurrent = () => scope.current === started && legacyGeneration.current === generation;
    setLegacyBusy(true); setLegacyError('');
    try { const value = await request<LegacyImpact>('/api/model-operations/legacy-impact'); if (isCurrent()) setLegacy(value); }
    catch (cause) { if (isCurrent()) setLegacyError(message(cause)); }
    finally { if (isCurrent()) setLegacyBusy(false); }
  };
  const refresh = () => { void refreshCurrent(); void refreshLegacy(); };
  if (!projectDir) return null;
  const groups: [string, Evidence[]][] = result ? [['학습 모델', result.models], ['저장 플로우', result.flows], ['모델 평가', result.model_evaluations], ['승인 기록', result.approvals], ['배포 패키지', result.packages]] : [];
  return <section className="workspace-launcher shrink-0" aria-label="데이터·모델 변경 영향">
    <button type="button" className="workspace-button" aria-haspopup="dialog" aria-expanded={open} onClick={() => { setOpen(true); refresh(); }}>데이터·모델 변경 영향 확인</button>
    {open && <WorkspaceDialog title="데이터·모델 변경 영향" description="현재 입력과 과거 기록을 함께 확인하고, 필요한 검토 작업을 정리합니다." onClose={close}>
      <div className="workspace-stack">
        <div className="workspace-toolbar"><AsyncAction pending={busy || legacyBusy} pendingLabel="확인 중…" onClick={refresh}>현재 입력 다시 확인</AsyncAction><p className="workspace-description">이전 결과는 보존됩니다. 데이터 수정 후 다시 확인하세요.</p></div>
        <WorkspaceSection title="기존 모델·승인 영향 조사" description="품질 승인·현재 배포 자격을 확인한 결과가 아닙니다.">
          {legacyBusy && <WorkspaceFeedback kind="loading" title="기존 기록을 확인하는 중…" />}
          {legacyError && <WorkspaceFeedback kind="error" title="기존 기록을 불러오지 못했습니다." description={legacyError}
            action={<AsyncAction pending={legacyBusy} onClick={() => void refreshLegacy()}>기존 기록 다시 확인</AsyncAction>} />}
          {!legacyBusy && !legacyError && legacy && <LegacyRecords key={key} result={legacy} />}
        </WorkspaceSection>
        <WorkspaceSection title="현재 입력과 저장 이력" description="이미지·라벨·분할·체크포인트를 저장된 학습 버전과 비교합니다. 입력이 일치해도 품질 승인이나 현장 배포 검증을 뜻하지 않습니다.">
          {busy && <WorkspaceFeedback kind="loading" title="현재 입력을 확인하는 중…" />}
          {error && <WorkspaceFeedback kind="error" title="현재 입력을 비교하지 못했습니다." description={error}
            action={<AsyncAction pending={busy} onClick={() => void refreshCurrent()}>현재 입력 비교 다시 시도</AsyncAction>} />}
          {result && <div className="workspace-stack">
            {error && <p className="workspace-description">아래는 마지막 조회 결과입니다. 현재 입력을 다시 확인하세요.</p>}
            <div className="workspace-toolbar">{result.required_actions.length ? result.required_actions.map(action => <button key={action} className="workspace-button" onClick={()=>{close();void useProjectStore.getState().setStep(recoveryStep(action));}}>{actions[action] || action} → {recoveryStep(action)}단계</button>) : <p className="workspace-description">확인한 기록에서 추가 작업이 발견되지 않았습니다. 아래 근거의 확인 상태를 함께 보세요.</p>}</div>
            <div className="workspace-grid">{groups.map(([title, rows]) => <details key={title} className="workspace-record workspace-disclosure"><summary>{title} · {rows.length}</summary>
              <ul className="workspace-records">{rows.map((row, index) => <li key={index}><strong>{row.name || row.job_id || row.version_id || row.evaluation_id || row.revision_id || row.package_id}</strong><p><StatusBadge tone={row.state === 'current' ? 'success' : 'neutral'}>{row.state ? states[row.state] || row.state : `모델 ${states[row.checkpoint_state || 'unverified']} · 데이터 ${states[row.data_state || 'unverified']}`}</StatusBadge></p>{row.reason && <p className="workspace-description">{row.reason}</p>}</li>)}</ul>
              {!rows.length && <p className="workspace-description">저장 기록 없음</p>}
            </details>)}</div>
            <p className="workspace-description">전체 플로우 평가 {result.flow_evaluations.length}건의 상세 근거는 5단계의 전체 플로우 평가에서 확인하세요.</p>
          </div>}
        </WorkspaceSection>
      </div>
    </WorkspaceDialog>}
  </section>;
}
