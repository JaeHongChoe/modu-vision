import { useRef } from 'react';
import { api, getApiPersistenceIdentity, request } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { runBatchInspection, summarizeBatch } from '../inference/batchInspection';
import { DEMO_PROJECT_NAME, DEMO_TRAINING, runDemo, type DemoDeps } from './runDemo';
import { EXAMPLE_NOTICE, demoStepRows, demoSummary, nextExampleName } from './firstRun';
import { useFirstRunStore } from './firstRunStore';

const STATUS_MARK: Record<string, string> = { pending: '○', running: '…', done: '✓', failed: '⚠' };

/** The app's own calls behind each example step (no shortcut the user's clicks would not take). */
function appDeps(): DemoDeps {
  const project = () => useProjectStore.getState();
  return {
    exampleDataset: () => request<{ folder: string; images: number; task: string }>('/api/onboarding/example-dataset', { method: 'POST' }),
    serverReason: () => {
      let team = true;
      try { team = getApiPersistenceIdentity() !== 'local'; } catch { /* an unreadable team address counts as a team connection */ }
      if (team) return '예제는 개인 모드의 이 컴퓨터에서만 만듭니다. 팀 서버 연결을 끊고 다시 시작하세요.';
      if (useComputeStore.getState().selectedProfileId) return '예제는 이 컴퓨터의 CPU에서 실행합니다. 상단 Compute에서 This computer를 선택한 뒤 다시 시작하세요.';
      return null;
    },
    createProject: data => project().createProject(data),
    projectError: () => project().projectError,
    projectBusy: () => project().isProjectBusy,
    importFolder: async folder => {
      await useDatasetStore.getState().importFolder(folder, 'classification');
      const data = useDatasetStore.getState();
      return { error: data.importError || data.sourceSaveError || null, folder: data.folderPath || null };
    },
    markExample: async () => {
      const marked = await request<{ id: string; example?: unknown }>('/api/onboarding/example-project', { method: 'POST' });
      // Only the marker is new: it joins the current project as is (a full re-sync would drop the dataset just imported).
      useProjectStore.setState(state => ({ project: state.project && state.project.id === marked.id
        ? { ...state.project, example: marked.example } as typeof state.project : state.project }));
    },
    startTraining: async folder => {
      const training = useTrainingStore.getState();
      // The example's job runs on the CPU; the user's own device for later trainings stays as it was (the request reads
      // the setting before its first await).
      const previous = training.nextDevice;
      training.setNextSettings({ device: 'cpu' });
      try {
        await training.startTraining(folder, 'classification', undefined, { ...DEMO_TRAINING });
      } finally {
        useTrainingStore.setState({ nextDevice: previous });
      }
      return useTrainingStore.getState().jobId;
    },
    trainingError: () => useTrainingStore.getState().startError,
    jobStatus: jobId => api.training.getStatus(jobId),
    trainingEnded: async jobId => {
      const training = useTrainingStore.getState();
      if (training.jobId === jobId) await training.refreshCurrentJob();
    },
    saveFlow: async (jobId, folder) => {
      const pipeline = await api.flowchart.getSingleSegmentationTemplate(jobId, 'classification');
      await api.flowchart.verifyModels({ source_dataset_path: folder, models: [{ job_id: jobId, task: 'classification' }] });
      const saved = await api.flowchart.savePipeline(pipeline, 'classification', folder);
      await useFlowchartStore.getState().loadPipeline(true, 'classification', folder);
      return saved;
    },
    inspect: async folder => {
      const projectId = project().project?.id;
      if (!projectId) throw new Error('예제 프로젝트를 찾지 못했습니다.');
      const saved = await api.flowchart.getActivePipeline(folder);
      const report = await runBatchInspection({ sourceFolder: folder, task: 'classification', scope: 'test', pipeline: saved }, {
        getActivePipeline: api.flowchart.getActivePipeline, getPipeline: api.flowchart.getPipeline,
        verifyModels: api.flowchart.verifyModels, getImages: api.dataset.getImages, run: api.flowchart.run,
        executeRow: api.inspections.executeRow,
        createRun: async (pending, pipeline) => {
          const created = await api.inspections.createRun(pending, pipeline, { execution_target: 'local', device: 'cpu', project_id: projectId });
          pending.saved_version_id = created.saved_version_id; pending.pipeline_hash = created.pipeline_hash; pending.model_sha256 = created.model_sha256;
          return created.run_id;
        },
        recordRow: (runId, row) => api.inspections.recordRow(runId, row),
        finishRun: (runId, status) => api.inspections.finishRun(runId, status),
      });
      const summary = summarizeBatch(report.rows);
      if (summary.errors || summary.unrun) throw new Error(`예제 검사 ${summary.total}장 중 ${summary.errors + summary.unrun}장을 검사하지 못했습니다.`);
      return { run_id: report.run_id || null, counts: { OK: summary.ok, NG: summary.ng, REVIEW: summary.review }, images: summary.total };
    },
    projectId: () => project().project?.id || null,
    sleep: ms => new Promise(resolve => setTimeout(resolve, ms)),
    now: () => Date.now(),
  };
}

/** The first-run guide: the order of a first project and a small example that runs on this computer's CPU. */
export function FirstRunStudio({ onClose, onDismiss }: { onClose: () => void; onDismiss: () => void }) {
  const demo = useFirstRunStore(state => state.demo);
  const examples = useFirstRunStore(state => state.onboarding?.example_projects) || [];
  const running = demo?.state === 'running';
  const progress = useRef<HTMLOListElement>(null);
  const start = () => {
    void runDemo(appDeps(), useFirstRunStore.getState().setDemo, { projectName: nextExampleName(DEMO_PROJECT_NAME, examples),
      takenNames: examples.map(row => row.name) }).then(() => useFirstRunStore.getState().load());
    // The start button gives way to the progress list: keep keyboard focus inside the dialog.
    requestAnimationFrame(() => progress.current?.focus());
  };
  const openExample = async (projectDir: string) => {
    if (await useProjectStore.getState().openProject(projectDir)) setStep(6);
  };
  const setStep = (step: 1 | 3 | 6) => { onClose(); void useProjectStore.getState().setStep(step); };
  return <div className="space-y-5 text-sm text-slate-200">
    <section aria-labelledby="first-run-order">
      <h3 id="first-run-order" className="font-semibold text-slate-100">처음 프로젝트를 만드는 순서</h3>
      <ol className="mt-2 list-decimal space-y-2 pl-5 text-slate-300">
        <li><strong className="text-slate-100">작업 방식</strong> · 개인 모드는 이 컴퓨터에서 바로 시작합니다. 팀 서버를 쓰려면 상단의 서버 관리에서 서버를 추가하고 연결을 확인하세요.</li>
        <li><strong className="text-slate-100">이 컴퓨터·서버 사전 점검</strong> · 3단계 모델 학습 허브의 이 컴퓨터 지원 상태에서 실제 사전 점검을 실행할 수 있습니다.
          <button type="button" className="workspace-button ml-2" disabled={running} onClick={() => setStep(3)}>3단계로 이동</button></li>
        <li><strong className="text-slate-100">프로젝트 만들기</strong> · 상단 프로젝트 이름 옆의 프로젝트 관리에서 새 프로젝트를 만듭니다.</li>
        <li><strong className="text-slate-100">데이터 가져오기</strong> · 1단계 데이터 관리에서 데이터 폴더를 엽니다.
          <button type="button" className="workspace-button ml-2" disabled={running} onClick={() => setStep(1)}>1단계로 이동</button></li>
      </ol>
    </section>
    <section aria-labelledby="first-run-example" className="rounded border border-slate-600 p-3">
      <h3 id="first-run-example" className="font-semibold text-slate-100">예제로 먼저 해보기</h3>
      <p className="mt-1 text-slate-300">이 컴퓨터에서 합성 표면 이미지 64장을 만들고, CPU로 짧게 학습해 검사 플로우를 저장한 뒤 테스트 이미지를 검사합니다. 네트워크·GPU·외부 계정이 필요 없습니다.</p>
      <p className="mt-1 text-amber-200">{EXAMPLE_NOTICE}</p>
      {examples.length > 0 && !running && <ul aria-label="만들어 둔 예제 프로젝트" className="mt-3 space-y-1">{examples.map(row =>
        <li key={row.project_dir} className="flex items-center gap-2"><span className="text-slate-300">{row.name}</span>
          <button type="button" className="workspace-button" onClick={() => void openExample(row.project_dir)}>열기</button></li>)}</ul>}
      {!running ? <button type="button" className="workspace-button workspace-button--primary mt-3" onClick={start}>
        {demo?.state === 'failed' ? '예제 다시 만들기' : examples.length || demo?.state === 'ready' ? '예제 프로젝트 새로 만들기' : '예제 프로젝트 만들기'}</button> : null}
      {demo && demo.state !== 'idle' && <ol ref={progress} tabIndex={-1} aria-label="예제 진행" className="mt-3 space-y-1">{demoStepRows(demo).map(row =>
        <li key={row.id} className={row.status === 'failed' ? 'text-amber-300' : row.status === 'done' ? 'text-emerald-300' : 'text-slate-300'}>
          {STATUS_MARK[row.status]} {row.label}{row.detail ? ` · ${row.detail}` : ''}</li>)}</ol>}
      {demo?.state === 'failed' && <p role="alert" className="mt-2 text-amber-300">예제를 만들지 못했습니다: {demo.error}</p>}
      {demo?.state === 'ready' && demo.result && <div role="status" className="mt-3 space-y-2">
        <p className="text-emerald-300">{demoSummary(demo.result)}</p>
        <button type="button" className="workspace-button workspace-button--primary" onClick={() => setStep(6)}>검사 결과 보기 (6단계)</button>
      </div>}
    </section>
    <div className="flex justify-end gap-2">
      <button type="button" className="workspace-button" disabled={running} onClick={onDismiss}>다음부터 보지 않기</button>
      <button type="button" className="workspace-button" disabled={running} onClick={onClose}>닫기</button>
    </div>
  </div>;
}
