import React from 'react';
import { ACTION_CATALOG } from '../../services/assistantTasks/catalog';
import type { ActionId, AssistantTask, StepStatus, TaskStatus, TaskStep } from '../../services/assistantTasks/types';

export interface AssistantTaskCardProps {
  task: AssistantTask | null;
  busy: boolean;
  loading: boolean;
  error: string | null;
  ready: boolean;
  contextMatches?: boolean;
  settingsRecoveryLabel?: string | null;
  onRestoreSettings?(id:string,revision:number): void;
  blocked?: boolean;
  onApprove(id: string, revision: number): void;
  onPause(): void;
  onResume(): void;
  onRefresh(): void;
  onRestore(): void;
  onNavigate(step: number): void;
}

const TASK_STATUS: Record<TaskStatus, string> = {
  draft: '실행 확인', ready: '실행 준비', running: '진행 중', waiting_human: '사람 검토 대기',
  waiting_job: '작업 완료 대기', paused: '일시정지', needs_reconciliation: '저장 결과 확인 필요', failed: '실패', completed: '완료',
};
const STEP_STATUS: Record<StepStatus, string> = {
  pending: '예정', running: '진행 중', waiting_human: '사람 검토 대기', waiting_job: '완료 대기',
  completed: '완료', failed: '실패', uncertain: '결과 확인 필요',
};
const STAGE: Record<ActionId, number> = {
  create_project: 1, import_dataset: 1, save_split: 1, configure_training: 3, generate_label_drafts: 2,
  start_training: 3, evaluate_model: 4, save_flow: 5, evaluate_flow: 5, inspect_test: 6, export_report: 4, open_step: 1,
};
const text = (value: unknown, limit = 200): string => typeof value === 'string' ? value.slice(0, limit) : '';
const count = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value.toLocaleString('ko-KR') : '';
const identifier = (value: unknown): string => typeof value === 'string' && /^[\w.-]{1,160}$/.test(value) ? value : '';
const title = (step: TaskStep): string => ACTION_CATALOG.find(action => action.id === step.action)?.title ?? '지원하지 않는 작업';
const stage = (step: TaskStep): number => step.action === 'open_step' && Number.isInteger(step.args.step) && Number(step.args.step) >= 1 && Number(step.args.step) <= 6 ? Number(step.args.step) : STAGE[step.action];
const tone = (status: TaskStatus | StepStatus): string => status === 'completed' ? 'success' : status === 'failed' ? 'danger'
  : ['paused', 'waiting_human', 'needs_reconciliation', 'uncertain'].includes(status) ? 'warning' : 'info';

/** Only meaningful, reviewed inputs are shown; arbitrary provider keys never become plan copy. */
function inputSummary(step: TaskStep): string[] {
  const args = step.args;
  const lines: string[] = [];
  if (step.action === 'create_project') {
    if (typeof args.name === 'string') lines.push(`이름: ${text(args.name, 120)}`);
    const taskNames: Record<string, string> = {classification: '분류', detection: '검출', segmentation: '영역 분할', anomaly: '이상 탐지'};
    if (typeof args.task === 'string' && taskNames[args.task]) lines.push(`검사 종류: ${taskNames[args.task]}`);
  }
  if (step.action === 'import_dataset') lines.push('직접 고른 원본 폴더를 가져옵니다.');
  if (step.action === 'save_split') lines.push(`학습 ${count(args.train_percent)}% · 검증 ${count(args.val_percent)}% · 시험 ${count(args.test_percent)}%`);
  if (step.action === 'configure_training') lines.push(...settingsSummary(args));
  if (step.action === 'generate_label_drafts') {
    lines.push(args.mode === 'phrase' ? '문구로 초안 준비' : '완료 모델로 초안 준비');
    if (typeof args.prompt === 'string') lines.push(`찾을 대상: ${text(args.prompt)}`);
    if (typeof args.threshold === 'number' && Number.isFinite(args.threshold)) lines.push(`초안 기준값: ${args.threshold}`);
    lines.push('초안 채택과 정답 확정은 직접 합니다.');
  }
  if (identifier(args.job_id)) lines.push(`학습 작업: ${identifier(args.job_id)}`);
  if (step.action === 'start_training') lines.push('현재 화면에서 확인한 설정으로 학습을 시작합니다.');
  if (step.action === 'open_step') lines.push(`${stage(step)}단계 화면`);
  if (step.action === 'export_report' && ['html', 'json'].includes(String(args.format))) lines.push(`보고서 형식: ${String(args.format).toUpperCase()}`);
  return lines;
}

function settingsSummary(settings: Record<string, unknown>): string[] {
  const lines: string[] = [];
  if (settings.preset === 'fast' || settings.preset === 'precision') lines.push(`학습 품질: ${settings.preset === 'fast' ? '빠르게' : '정밀하게'}`);
  const devices: Record<string, string> = {auto: '자동 선택', cpu: 'CPU', mps: 'Apple GPU', cuda: 'NVIDIA GPU'};
  if (typeof settings.device === 'string' && devices[settings.device]) lines.push(`장치: ${devices[settings.device]}`);
  if (count(settings.batch_size)) lines.push(`배치 크기: ${count(settings.batch_size)}`);
  return lines;
}

/** Saved receipts remain local and compact: no file paths, images, predictions, bindings or hashes. */
function savedSummary(receipt: Record<string, unknown> | null): Array<[string, string]> {
  if (!receipt) return [];
  const rows: Array<[string, string]> = [];
  for (const [key, label] of Object.entries({project_id: '프로젝트', job_id: '학습 작업', batch_id: '초안 묶음', version_id: '흐름 버전', pipeline_id: '검사 흐름', cohort_id: '평가 자료', evaluation_id: '평가 기록', run_id: '시험 검사', report_id: '보고서'})) {
    const value = identifier(receipt[key]); if (value) rows.push([label, value]);
  }
  for (const [key, label] of Object.entries({images: '가져온 이미지 수', total: '전체 자료 수', samples: '평가 자료 수', known: '정답 확인', unknown: '정답 미확정', processed: '처리 수', succeeded: '성공 수', failed: '실패 수'})) {
    const value = count(receipt[key]); if (value) rows.push([label, value]);
  }
  if (receipt.split && typeof receipt.split === 'object' && !Array.isArray(receipt.split)) {
    const split = receipt.split as Record<string, unknown>;
    for (const [key, label] of Object.entries({train: '학습 자료 수', val: '검증 자료 수', test: '시험 자료 수'})) {
      const value = count(split[key]); if (value) rows.push([label, value]);
    }
  }
  if (receipt.settings && typeof receipt.settings === 'object' && !Array.isArray(receipt.settings)) {
    for (const line of settingsSummary(receipt.settings as Record<string, unknown>)) {
      const [label, ...value] = line.split(': '); rows.push([`실제 ${label}`, value.join(': ')]);
    }
  }
  if (typeof receipt.report_path === 'string' && receipt.report_path) rows.push(['보고서', '저장 기록 있음 · 결과 화면에서 확인']);
  return rows;
}

/** Presentational only. Authority, stale revisions and recovery are checked by the local controller. */
export const AssistantTaskCard: React.FC<AssistantTaskCardProps> = ({task, busy, loading, error, ready, contextMatches = true, settingsRecoveryLabel = null, onRestoreSettings, blocked = false, onApprove, onPause, onResume, onRefresh, onRestore, onNavigate}) => {
  const unavailable = !ready || blocked || loading;
  const readDisabled = unavailable || busy;
  const reason = loading ? '저장된 작업 상태를 읽고 있습니다.' : !ready ? 'ChatGPT 연결과 로그인 상태를 확인하면 작업을 이어갈 수 있습니다.'
    : blocked ? '다른 도우미 작업이 끝난 뒤 진행할 수 있습니다.' : busy ? '현재 작업의 결과를 기록하고 있습니다.' : null;
  if (!task) return <section aria-label="도우미 작업" aria-busy={loading} className="workspace-section space-y-3" style={{color: 'var(--workspace-text)'}}>
    <h3>수행할 작업</h3>
    <p className="workspace-description text-xs">예: “이 폴더로 균열 검사 만들어줘”라고 요청하면 실행할 계획을 먼저 보여 줍니다.</p>
    <p role="status" className="workspace-description text-xs">{reason || '저장된 작업을 불러오거나 새 작업을 요청하세요.'}</p>
    {error && <p role="alert" className="text-xs" style={{color: 'var(--workspace-danger)'}}>{text(error, 1000)}</p>}
    <button type="button" className="workspace-button" disabled={readDisabled} onClick={() => {if (!readDisabled) onRestore();}}>저장된 작업 불러오기</button>
  </section>;

  // Captured scalars identify exactly the displayed plan, even if a caller mutates its old DTO.
  const {id, revision, status} = task;
  const completed = task.steps.filter(step => step.status === 'completed').length;
  const currentIndex = task.steps.findIndex(step => step.id === task.current_step_id);
  const active = task.steps[currentIndex] ?? task.steps.find(step => step.status !== 'completed') ?? task.steps.at(-1);
  const visible = task.steps.slice(0, 24);
  const invalidPlanSize = task.steps.length < 1 || task.steps.length > 24;
  const continueDisabled = readDisabled || invalidPlanSize || !contextMatches;
  const canNavigate = contextMatches && active && Number.isInteger(stage(active));
  const navigate = () => {if (!readDisabled && active && canNavigate) onNavigate(stage(active));};
  let primary = {label: '저장 결과 확인', disabled: readDisabled, run: onRefresh};
  if (status === 'draft') primary = {label: '계획 확인하고 실행', disabled: continueDisabled, run: () => onApprove(id, revision)};
  else if (status === 'ready') primary = {label: '작업 시작', disabled: continueDisabled, run: onResume};
  else if (status === 'running') primary = {label: '작업 일시정지', disabled: unavailable, run: onPause};
  else if (status === 'waiting_human' && canNavigate) primary = {label: '검토 화면 열기', disabled: readDisabled, run: navigate};
  else if (status === 'paused') primary = {label: '저장 결과 확인하고 이어하기', disabled: continueDisabled, run: onResume};
  else if (status === 'needs_reconciliation') primary = {label: '저장 결과 확인 후 이어하기', disabled: continueDisabled, run: onResume};
  else if (status === 'completed' && canNavigate) primary = {label: '결과 화면 열기', disabled: readDisabled, run: navigate};
  if (!contextMatches && settingsRecoveryLabel && onRestoreSettings) primary = {label: settingsRecoveryLabel, disabled: readDisabled, run: ()=>onRestoreSettings(id,revision)};

  return <section aria-label="도우미 작업" aria-busy={loading || busy} className="workspace-section min-w-0 space-y-3" style={{color: 'var(--workspace-text)', overflowWrap: 'anywhere'}}>
    <header className="flex flex-wrap items-center justify-between gap-2">
      <h3>작업 계획</h3><span className="workspace-badge" data-tone={tone(status)}>{TASK_STATUS[status]}</span>
    </header>
    <p className="font-semibold">{text(task.goal, 4096)}</p>
    <div role="status" aria-live="polite" aria-atomic="true" className="space-y-1 text-xs">
      <p>{TASK_STATUS[status]} · 완료 {completed}/{task.steps.length}개</p>
      {active && <p>현재 단계 {currentIndex >= 0 ? currentIndex + 1 : task.steps.indexOf(active) + 1}/{task.steps.length} · {title(active)}</p>}
      {reason && <p className="workspace-description">{reason}</p>}
      {task.message && <p>{text(task.message, 1000)}</p>}
    </div>
    {error && <p role="alert" className="text-xs" style={{color: 'var(--workspace-danger)'}}>{text(error, 1000)}</p>}
    {!contextMatches && <p className="workspace-description text-xs">현재 화면과 저장된 작업의 설정이 다릅니다. {settingsRecoveryLabel ? '저장 설정 복원은 학습 입력만 복원하며, 작업이나 학습을 시작하지 않습니다.' : '기록만 표시합니다. 원래 프로젝트와 자료를 연 뒤 다시 확인하세요.'}</p>}
    {status === 'draft' && <p className="workspace-description text-xs">아래 작업과 설정을 확인하세요. 확인한 계획 안에서 차례대로 실행합니다.</p>}
    {status === 'waiting_human' && <p className="workspace-description text-xs">라벨 초안 채택·정답 확정·최종 승인은 직접 합니다. 검토를 마친 뒤 결과를 확인하고 이어가세요.</p>}
    {status === 'waiting_job' && <p className="workspace-description text-xs">작업은 계속 진행됩니다. 저장된 완료 기록을 확인한 뒤 다음 단계로 이어 갑니다.</p>}
    {['paused', 'needs_reconciliation'].includes(status) && <p className="workspace-description text-xs">먼저 저장된 결과를 확인합니다. 이미 실행한 작업을 다시 시작하지 않고 이어갈 위치를 확인합니다.</p>}
    {status === 'failed' && <p className="workspace-description text-xs">실패한 단계와 저장 결과를 먼저 확인하세요. 이 버튼은 작업을 재실행하지 않습니다.</p>}
    {status === 'completed' && <p className="workspace-description text-xs">계획의 작업을 완료했습니다. 모델·검사 흐름의 최종 승인과 운영 배포는 직접 판단해야 합니다.</p>}
    <ol aria-label="실행 계획" className="max-h-80 space-y-3 overflow-y-auto pr-1 text-sm" style={{scrollbarGutter: 'stable'}}>
      {visible.map((step, index) => {
        const saved = savedSummary(step.receipt);
        return <li key={step.id} aria-current={step === active ? 'step' : undefined} className="min-w-0 border-l-2 pl-3" style={{borderColor: step === active ? 'var(--workspace-accent)' : 'var(--workspace-border)'}}>
          <div className="flex flex-wrap items-start justify-between gap-2"><p className="font-semibold">{index + 1}. {title(step)}</p><span className="workspace-badge" data-tone={tone(step.status)}>{STEP_STATUS[step.status]}</span></div>
          {step.label !== title(step) && <p className="workspace-description text-xs">{text(step.label, 160)}</p>}
          {inputSummary(step).map((line, lineIndex) => <p key={lineIndex} className="workspace-description text-xs">{line}</p>)}
          {step.error && <p role="alert" className="mt-1 text-xs" style={{color: 'var(--workspace-danger)'}}>{text(step.error, 1000)}</p>}
          {saved.length > 0 && <dl aria-label={`${index + 1}단계 저장 결과`} className="mt-2 space-y-1 text-xs">{saved.map(([label, value]) => <div key={label} className="flex flex-wrap gap-x-2"><dt style={{color: 'var(--workspace-muted)'}}>{label}</dt><dd className="min-w-0 select-text">{value}</dd></div>)}</dl>}
        </li>;
      })}
    </ol>
    {task.steps.length > visible.length && <p className="workspace-description text-xs">추가 {task.steps.length - visible.length}개 단계는 표시를 생략했습니다. 전체 계획을 다시 요청하세요.</p>}
    {task.steps.length === 0 && <p role="alert" className="text-xs" style={{color: 'var(--workspace-danger)'}}>실행할 단계가 없습니다. 작업 계획을 다시 요청하세요.</p>}
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="workspace-button workspace-button--primary" disabled={primary.disabled} onClick={() => {if (!primary.disabled) primary.run();}}>{primary.label}</button>
      {status === 'waiting_human' && <button type="button" className="workspace-button" disabled={continueDisabled} onClick={() => {if (!continueDisabled) onResume();}}>검토 결과 확인하고 이어하기</button>}
      {['ready', 'waiting_human', 'waiting_job', 'needs_reconciliation'].includes(status) && <button type="button" className="workspace-button" disabled={unavailable} onClick={() => {if (!unavailable) onPause();}}>작업 일시정지</button>}
      {canNavigate && !['waiting_human', 'completed'].includes(status) && <button type="button" className="workspace-button" disabled={readDisabled} onClick={navigate}>관련 화면 열기</button>}
    </div>
    {['ready', 'running', 'waiting_human', 'waiting_job', 'paused', 'needs_reconciliation'].includes(status) && <p className="workspace-description text-xs">일시정지는 다음 도우미 작업을 멈춥니다. 이미 시작한 학습은 별도로 계속됩니다.</p>}
  </section>;
};
