import { create } from 'zustand';
import { subscribeProjectContext } from '../../services/api';
import { flowEvaluation } from '../../services/flowEvaluation';
import { currentAssistContext, subscribeAssistContext } from '../../stores/assistContext';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { WorkspaceDialog } from '../common/WorkspaceDialog';
import { ACTION_TEXT, PERSON_STOPS, missedStillShown, type AutoChoices, type AutoRunState, type TruthConfirm } from './autoProgress';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { focusWorkflowTarget, type WorkflowStep } from './workflowReadiness';
import { useComputeStore } from '../../stores/useComputeStore';
import { taskPreviewScope } from './TaskChangeImpactDialog';
import { executionLeaseOwner } from '../../services/assistantTasks/executionLease';
import { defaultAppSplit } from '../../services/assistantTasks/appActions';
import {startOneclickTask,pauseOneclickTask} from '../../stores/useAssistantTaskStore';
import { currentTrainingSummary, subscribeTrainingSummary, trainingSummaryLine, trainingSummaryScope, type TrainingStartSummary, type TrainingSummaryProfile } from '../training/trainingStartSummary';

export const AUTO_TITLE = '자동 진행';
const TASK_TEXT: Record<string, string> = { classification: '분류', detection: '객체 검출', segmentation: '영역 분할', anomaly: '이상 탐지' };

/** The split the step-1 screen offers by default (its ratio input starts from the store's train ratio). */
export const defaultSplit = defaultAppSplit;

/** The one line of defaults said before the start; everything in it can be changed on its own step first. */
export function defaultsLine(input: { task: string | null | undefined; trainRatio?: number; device?: string | null;
  preset?: string; selectedProfileId?: string | null; profile?: TrainingSummaryProfile; computeLoaded?: boolean; live?: TrainingStartSummary | null }): string {
  const split = defaultSplit(input.trainRatio);
  return `${TASK_TEXT[input.task || ''] || input.task || '작업 종류 미정'} · 분할 ${split.train}/${split.val}/${split.test} (저장된 분할이 맞으면 그대로) · `
    + `학습은 3단계에 보이는 설정 · ${trainingSummaryLine(input)} · 검사는 시험 이미지`;
}

interface AutoProgressState {
  run: AutoRunState | null;
  cancelRequested: boolean;
  /** `choices` is what the person pressed at the last stop (only for this run; a new run asks again). */
  start: (choices?: AutoChoices) => void;
  cancel: () => void;
}

/** The run lives outside the dialog: closing the dialog does not stop it, and the footer button says it is running. */
export const useAutoProgressStore = create<AutoProgressState>((set, get) => ({
  run: null,
  cancelRequested: false,
  start: (_choices) => {
    if (get().run?.state === 'running') return;
    const fail = (reason: string) => set({cancelRequested: false, run: {state: 'failed',
      stop: {reason, step: useProjectStore.getState().activeStep}, log: [{at: Date.now(), text: reason, tone: 'error'}]}});
    if (executionLeaseOwner()!==null) {
      fail(executionLeaseOwner() === 'assistant' ? '도우미 작업이 진행 중입니다. 끝나거나 일시정지한 뒤 자동 진행을 다시 시작하세요.'
        : '다른 자동 진행이 실행 중입니다. 현재 진행이 끝난 뒤 다시 시작하세요.');
      return;
    }
    set({cancelRequested:false,run:{state:'running',log:[],stop:null}});
    void startOneclickTask((task,error)=>{
      if(error){fail(error);return;}if(!task)return;
      const active=task.steps.find(row=>row.id===task.current_step_id);
      const stopped=['waiting_human','failed','needs_reconciliation'].includes(task.status);
      const qualityMissing=task.steps.some(row=>row.receipt?.quality_status==='input_needed');
      const text=task.status==='completed'&&qualityMissing?'시험 검사 실행 완료 · 정확도·미검 검증은 독립 정답 입력이 필요합니다.':task.message||`${task.steps.filter(row=>row.status==='completed').length}/${task.steps.length}개 작업 완료`;
      const previous=get().run?.log??[];
      const log=previous.at(-1)?.text===text?previous:[...previous,{at:Date.now(),text,tone:task.status==='failed'?'error' as const:stopped?'stop' as const:task.status==='completed'?'done' as const:'info' as const}];
      for(const step of task.steps){
        const quality=step.receipt?.quality_assessment as {status?:string;reasons?:string[]}|undefined;
        const labels:Record<string,string>={qualified:'품질 기준 충족',not_qualified:'품질 기준 미달',input_needed:'품질 판정 입력 필요',pending:'품질 판정 결과 확인 중'};
        if(!quality?.status||!labels[quality.status])continue;
        const qualityText=`${step.action==='evaluate_model'?'모델':'검사 흐름'} ${labels[quality.status]}${quality.reasons?.length?' · '+quality.reasons.slice(0,2).join(' · '):''}`;
        if(!log.some(row=>row.text===qualityText))log.push({at:Date.now(),text:qualityText,tone:quality.status==='qualified'?'done':'info'});
      }
      set({run:{state:task.status==='completed'?'done':task.status==='paused'?'cancelled':task.status==='failed'?'failed':stopped?'stopped':'running',log,
        stop:stopped?{reason:text,step:active?.action==='start_training'?3:active?.action==='evaluate_model'?4:active?.action==='inspect_test'?6:active?.action==='evaluate_flow'||active?.action==='save_flow'?5:useProjectStore.getState().activeStep}:null}});
    }).catch(cause=>fail(cause instanceof Error?cause.message:'자동 진행 상태를 확인하지 못했습니다.'));
  },
  cancel: () => {set({cancelRequested:true});void pauseOneclickTask().catch(cause=>set({run:{state:'failed',log:[],stop:{reason:cause instanceof Error?cause.message:'일시정지 기록을 확인하지 못했습니다.',step:useProjectStore.getState().activeStep}}}));},
}));

/** The truth stop cleared in place: the person names themself and presses once; the run then goes on by itself. The same
 *  call as step 5's '라벨을 정답으로 확정' (each image gets its own dated declaration under the reviewer's name). */
function TruthConfirmForm({ confirm, task, onDone }: { confirm: TruthConfirm; task: string; onDone: () => void }) {
  const stored = useAnnotationStore((state) => state.reviewerName);
  const [reviewer, setReviewer] = useState(stored || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const confirmTruth = async () => {
    if (!reviewer.trim() || busy) return;
    setBusy(true); setError('');
    try {
      const value = await flowEvaluation.confirmTruthFromLabels(confirm.cohortId, confirm.versionId, reviewer.trim());
      if (!value.declared && !value.kept) { setError(`확정할 라벨이 없습니다(미확인 ${value.skipped}장). 5단계에서 이미지별로 정답을 확인하세요.`); return; }
      useAnnotationStore.getState().setReviewerName?.(reviewer.trim());
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-slate-600 p-2">
    <p>시험 이미지 {confirm.images}장의 {task === 'detection' ? '라벨(불량 상자는 NG, 정상 표시는 OK, 라벨 없음은 미확인)' : '폴더 라벨(OK/NG)'}을 정답으로 확정합니다.</p>
    <div className="flex flex-wrap items-center gap-2">
      <label className="text-slate-300">검토자<input aria-label="자동 진행 정답 확정 검토자" value={reviewer} onChange={(event) => setReviewer(event.target.value)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5" /></label>
      <button type="button" className="workspace-button workspace-button--primary" disabled={busy || !reviewer.trim()} onClick={() => void confirmTruth()}>{busy ? '확정 중…' : '라벨을 정답으로 확정하고 이어 가기'}</button>
    </div>
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </div>;
}

const TONE: Record<string, string> = { done: 'text-emerald-300', info: 'text-slate-300', stop: 'text-amber-200', error: 'text-red-300' };

/** 원클릭 자동 진행: the defaults, what runs by itself, where it stops, and the run's own log. */
export function AutoProgressDialog({ onClose }: { onClose: () => void }) {
  const run = useAutoProgressStore((state) => state.run);
  const start = useAutoProgressStore((state) => state.start);
  const cancel = useAutoProgressStore((state) => state.cancel);
  const cancelRequested = useAutoProgressStore((state) => state.cancelRequested);
  // A stop's go-on is offered only while the screen still shows the result the person was asked about (a relabel or a
  // retrain changes it). The snapshot is a boolean: the context object is rebuilt on every read.
  const readMissedShown = () => Boolean(run?.stop?.missed && missedStillShown(run.stop.missed, currentAssistContext()));
  const missedShown = useSyncExternalStore(subscribeAssistContext, readMissedShown, readMissedShown);
  const project = useProjectStore(), dataset = useDatasetStore(), training = useTrainingStore(), compute = useComputeStore();
  const { task } = project;
  const { trainRatio } = dataset;
  const device = training.nextDevice;
  const [, setSummaryRevision] = useState(0);
  const [settingsError, setSettingsError] = useState('');
  useEffect(() => subscribeTrainingSummary(() => setSummaryRevision(value => value + 1)), []);
  useEffect(() => subscribeProjectContext(() => setSummaryRevision(value => value + 1)), []);
  const profile = compute.profiles.find(row => row.id === compute.selectedProfileId);
  const summaryScope = trainingSummaryScope(taskPreviewScope(), {
    task, folderPath: dataset.folderPath, datasetKey: dataset.datasetKey, preset: training.preset,
    nextDevice: device, nextBatchSize: training.nextBatchSize, selectedProfileId: compute.selectedProfileId, profile,
    transportRevision: compute.transportRevision, computeLoaded: compute.isLoaded, computeLoading: compute.isLoading, computeLoadError: compute.loadError,
  });
  const currentSource = Boolean(project.projectDir && dataset.folderPath && project.project?.source_dataset_dir === dataset.folderPath
    && dataset.datasetKey === `${dataset.folderPath}\0${task}` && !project.isProjectBusy && !dataset.isLoading && !dataset.importError);
  // Local model and checkpoint choices are reset when step 3 mounts; never promise a previous screen's choices.
  const live = project.activeStep === 3 && currentSource ? currentTrainingSummary(summaryScope) : null;
  const running = run?.state === 'running';
  const resumable = run && (run.state === 'stopped' || run.state === 'failed' || run.state === 'cancelled');
  const goToTrainingSettings = async () => {
    const authority = taskPreviewScope();
    const source = JSON.stringify([useDatasetStore.getState().folderPath, useDatasetStore.getState().datasetKey, useProjectStore.getState().task]);
    setSettingsError('');
    try {
      await useProjectStore.getState().setStep(3);
      if (taskPreviewScope() !== authority || JSON.stringify([useDatasetStore.getState().folderPath, useDatasetStore.getState().datasetKey, useProjectStore.getState().task]) !== source) {
        setSettingsError('프로젝트·데이터가 바뀌었습니다. 현재 작업의 학습 설정을 다시 여세요.'); return;
      }
      if (useProjectStore.getState().activeStep === 3) onClose();
      else setSettingsError('현재 편집을 저장한 뒤 학습 설정 화면을 다시 여세요.');
    } catch (cause) { setSettingsError(cause instanceof Error ? cause.message : '학습 설정 화면을 열지 못했습니다. 현재 편집을 저장한 뒤 다시 시도하세요.'); }
  };
  const goToStop = async () => {
    if (!run?.stop) return;
    onClose();
    await useProjectStore.getState().setStep(run.stop.step as WorkflowStep);
    focusWorkflowTarget(run.stop.target);
  };
  return <WorkspaceDialog title={AUTO_TITLE} description="준비된 데이터와 라벨로 학습부터 시험 검사까지 이어서 진행합니다. 끝난 단계는 다시 하지 않습니다." onClose={onClose}>
    <div className="space-y-3 text-sm text-slate-200">
      <p aria-label="자동 진행 기본값" className="rounded border border-slate-600 bg-slate-900/60 p-2">{defaultsLine({ task, trainRatio, device, preset: training.preset, selectedProfileId: compute.selectedProfileId, profile, computeLoaded: compute.isLoaded, live })}</p>
      <button type="button" className="workspace-button" disabled={running || project.isProjectBusy || dataset.isLoading} onClick={goToTrainingSettings}>학습 설정 확인·변경 (3단계)</button>
      {settingsError && <p role="alert" className="text-red-300">{settingsError}</p>}
      <details className="text-slate-300">
        <summary className="cursor-pointer">자세히 · 자동으로 하는 것과 멈추는 곳</summary>
        <p className="mt-2 font-semibold text-slate-100">자동으로 하는 것</p>
        <ul className="mt-1 list-disc pl-5">{Object.values(ACTION_TEXT).map((text) => <li key={text}>{text}</li>)}</ul>
        <p className="mt-2 font-semibold text-slate-100">필요한 입력이나 조건</p>
        <ul className="mt-1 list-disc pl-5">{PERSON_STOPS.map((text) => <li key={text}>{text}</li>)}</ul>
        <p className="mt-2 text-slate-400">분할·흐름 저장도 기존과 같은 저장 기록과 버전이 남습니다. 학습 설정이나 분할 비율을 바꾸려면 먼저 그 단계에서 바꾼 뒤 시작하세요.</p>
      </details>
      {run && run.log.length > 0 && <ol aria-label="자동 진행 기록" aria-live="polite" className="max-h-56 space-y-1 overflow-y-auto rounded border border-slate-700 p-2">
        {run.log.map((row, index) => <li key={`${row.at}-${index}`} className={TONE[row.tone]}>{row.text}</li>)}
      </ol>}
      {run?.state === 'done' && <p role="status" className="text-emerald-300">끝까지 진행했습니다.</p>}
      {run?.stop && !running && <div role={run.state === 'failed' ? 'alert' : 'status'} className="space-y-2 rounded border border-amber-600/70 bg-amber-950/30 p-2 text-amber-100">
        <p>{run.state === 'failed' ? '멈췄습니다: ' : '여기서 사람이 필요합니다: '}{run.stop.reason}</p>
        {run.stop.confirm && <TruthConfirmForm confirm={run.stop.confirm} task={task} onDone={() => start()} />}
        <div className="flex flex-wrap gap-2">
          {run.stop.deferDrafts && <button type="button" className="workspace-button" onClick={() => start({ deferDrafts: true })}>남은 초안은 두고 확정한 라벨 {run.stop.deferDrafts}장으로 학습까지 이어 가기</button>}
          {run.stop.missed && missedShown && <button type="button" className="workspace-button" onClick={() => start({ acceptMissed: run.stop?.missed })}>놓친 불량 {run.stop.missed.escapes}장을 그대로 두고 검사 흐름까지 이어 가기</button>}
          <button type="button" className="workspace-button" onClick={() => void goToStop()}>{run.stop.step}단계 그 화면으로</button>
        </div>
      </div>}
      <div className="flex justify-end gap-2">
        {running && <button type="button" className="workspace-button" disabled={cancelRequested} onClick={cancel}>{cancelRequested ? '멈추는 중…' : '자동 진행 멈추기'}</button>}
        <button type="button" className={run?.stop?.confirm && !running ? 'workspace-button' : 'workspace-button workspace-button--primary'} disabled={running} onClick={() => start()}>
          {running ? '진행 중…' : resumable ? '멈춘 곳부터 이어 가기' : run?.state === 'done' ? '다시 확인하기' : '자동 진행 시작'}
        </button>
      </div>
      {running && <p className="text-xs text-slate-400">창을 닫아도 계속 진행합니다. 진행 중인 학습은 '자동 진행 멈추기'로 멈추지 않습니다(3단계에서 멈춤).</p>}
    </div>
  </WorkspaceDialog>;
}
