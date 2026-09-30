import { useEffect, useRef, useState } from 'react';
import { FlaskConical, Square } from 'lucide-react';
import { request } from '../../services/api';
import { activeProgramJob, modelTrainingProgram, programError, type AutomatedTrainingJob, type LocalTrainingDevice, type ModelFamily, type TrialCapability } from '../../services/modelTrainingProgram';
import { useProjectStore } from '../../stores/useProjectStore';
import { ProgramField, TrainingDeviceSelector, programButton, programInput, programPrimary } from './ProgramWorkbenchControls';
import {scopedTrainingJob,type JobSnapshot} from './scopedTrainingJob';

const dimensionNames: Record<string, string> = {architectures: '모델 구조', learning_rates: '학습률', weight_decays: '가중치 감쇠',
  image_sizes: '입력 크기', batch_sizes: '배치 크기', augmentation_profiles: '증강 방법', widths: 'CNN 폭', image_widths: '문자 입력 폭', base_channels: '생성기 채널',patch_sizes:'원본 패치 크기'};
const configNames: Record<string, string> = {architectures: 'backbone', learning_rates: 'learning_rate', weight_decays: 'weight_decay', image_sizes: 'image_size',
  batch_sizes: 'batch_size', augmentation_profiles: 'augmentation_profile', widths: 'width', image_widths: 'image_width', base_channels: 'base_channels',patch_sizes:'patch_size'};
const statusLabels: Record<string, string> = {queued: '대기', running: '탐색 중', stopping: '중지 확인 중', completed: '측정 완료', failed: '실패', cancelled: '중지됨',interrupted:'실행 중단'};
const familyPrefix: Partial<Record<ModelFamily, string>> = {ocr: 'ocr', rotation: 'rotation', rotated_detection: 'rotated-detection', enhancement: 'enhancement', defect_gan: 'defect-gan'};

export function AutoDLWorkbench({task, familyDatasetPath, onComplete}: {task: ModelFamily; familyDatasetPath?: string; onComplete?: () => void}) {
  const project = useProjectStore(state => state.project); const projectDir = useProjectStore(state => state.projectDir);
  const source = project?.source_dataset_dir || ''; const labelset = project?.active_labelset_id || 'default';
  const scope = `${projectDir}\n${source}\n${labelset}\n${task}`; const currentScope = useRef(scope); currentScope.current = scope;
  const [capability, setCapability] = useState<TrialCapability | null>(null);
  const [values, setValues] = useState<Record<string, string>>({}); const [mode, setMode] = useState<'quick' | 'search' | 'fast_retrain'>('search');
  const [device, setDevice] = useState<LocalTrainingDevice>('cpu'); const [epochs, setEpochs] = useState(2);
  const [trials, setTrials] = useState(4); const [epochBudget, setEpochBudget] = useState(8); const [seconds, setSeconds] = useState(600);
  const [latencyObjective, setLatencyObjective] = useState(false); const [latencyWeight, setLatencyWeight] = useState(.001);
  const [parents, setParents] = useState<Array<{job_id: string; checkpoint_sha256: string}>>([]); const [parent, setParent] = useState('');
  const [jobs, setJobs] = useState<AutomatedTrainingJob[]>([]);const [snapshot,setSnapshot]=useState<JobSnapshot<AutomatedTrainingJob>|null>(null);
  const job=scopedTrainingJob(snapshot,scope,source,labelset,task);
  const setJob=(row:AutomatedTrainingJob|null)=>setSnapshot(row?{scope,job:row}:null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const completed = useRef(''); const completionCallback = useRef(onComplete); completionCallback.current = onComplete;
  useEffect(() => {
    if (job?.status === 'completed' && job.winner && completed.current !== `${scope}/${job.search_id}`) {
      completed.current = `${scope}/${job.search_id}`; completionCallback.current?.();
    }
  }, [scope,job?.search_id,job?.status]);
  const active = !!job && activeProgramJob(job.status); const preparedReady = !capability?.prepared_input || !!familyDatasetPath;
  const same = () => currentScope.current === scope;
  useEffect(() => {
    let current = true; setCapability(null); setValues({}); setJobs([]); setJob(null); setParents([]); setParent(''); setError(''); setBusy(false);
    if (!projectDir || !source) return;
    void modelTrainingProgram.automated.capabilities().then(result => {
      if (!current || !same()) return;
      const cap = result.tasks[task]; setCapability(cap || null);
      if (cap) setValues(Object.fromEntries(Object.entries(cap.search_defaults).map(([key, rows]) => [key, rows.join(', ')])));
    }).catch(cause => {if (current && same()) setError(programError(cause));});
    void modelTrainingProgram.automated.jobs().then(result => {
      if (!current || !same()) return;
      const rows = result.jobs.filter(row => row.task === task && (row.source_dataset_path || row.dataset_path) === source && row.training_provenance?.labelset_id === labelset);
      setJobs(rows); setJob(rows.find(row => activeProgramJob(row.status)) || rows.at(-1) || null);
    }).catch(cause => {if (current && same()) setError(programError(cause));});
    return () => {current = false;};
  }, [scope, projectDir, source, task, labelset]);
  const baseConfig = (): Record<string, unknown> => {
    const keys: Record<string, string> = {...configNames, architectures: capability?.architecture_key || (task === 'segmentation' ? 'model_name' : familyPrefix[task] ? 'architecture' : 'backbone')};
    return Object.fromEntries(Object.entries(values).map(([key, value]) => [keys[key], key === 'architectures' || key === 'augmentation_profiles' ? value.split(',')[0]?.trim() : Number(value.split(',')[0]?.trim())]));
  };
  useEffect(() => {
    let current = true; setParents([]); setParent('');
    if (!capability || !source || !preparedReady) return;
    const config = baseConfig(); const dataset = familyDatasetPath || source;
    const query = new URLSearchParams({dataset_path: familyPrefix[task] ? dataset : source});
    if (familyPrefix[task]) {
      for (const [key, value] of Object.entries(config)) if (['image_size', 'width', 'image_width', 'base_channels'].includes(key)) query.set(task==='ocr' && key==='image_size'?'image_height':key, String(value));
    } else {
      query.set('task', task); query.set('preset', 'fast');
      if(task==='anomaly'){
        query.set('anomaly_method','dino_synthetic');query.set('anomaly_backbone',String(config.anomaly_backbone||capability.architectures[0]));
        if(config.patch_size)query.set('patch_size',String(config.patch_size));query.set('stride','128');
      }else query.set(task === 'segmentation' ? 'model_name' : 'backbone', String(config[task === 'segmentation' ? 'model_name' : 'backbone'] || capability.architectures[0]));
    }
    let path = familyPrefix[task] ? `/api/${familyPrefix[task]}/warm-start-parents` : '/api/training/warm-start-parents';
    if(mode==='fast_retrain'){
      path='/api/automated-training/parents';query.set('task',task);query.set('dataset_path',source);
      if(familyDatasetPath)query.set('family_dataset_path',familyDatasetPath);
    }
    void request<{parents: Array<{job_id: string; checkpoint_sha256: string}>}>(`${path}?${query}`).then(result => {if (current && same()) setParents(result.parents);})
      .catch(cause => {if (current && same()) setError(programError(cause));});
    return () => {current = false;};
  }, [scope, capability, familyDatasetPath,mode, values.architectures, values.image_sizes, values.widths, values.image_widths, values.base_channels, job?.status]);
  useEffect(() => {
    if (!job || !activeProgramJob(job.status)) return;
    let current = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const row = await modelTrainingProgram.automated.status(job.search_id);
        if (!current || !same()) return;
        setJob(row); setJobs(old => [...old.filter(item => item.search_id !== row.search_id), row]);
        if (activeProgramJob(row.status)) timer = setTimeout(() => void poll(), 1500);
      } catch (cause) {if (current && same()) {setError(programError(cause)); timer = setTimeout(() => void poll(), 3000);}}
    };
    void poll(); return () => {current = false; clearTimeout(timer);};
  }, [scope, job?.search_id]);
  const start = async () => {
    if (!capability || busy || active || !preparedReady) return;
    setBusy(true); setError('');
    try {
      const search: Record<string, Array<string | number>> = {};
      for (const [key, value] of Object.entries(values)) {
        const rows = value.split(',').map(text => text.trim()).filter(Boolean);
        if (!rows.length) throw new Error(`${dimensionNames[key]} 후보를 하나 이상 입력하세요.`);
        const numeric = typeof capability.search_defaults[key]?.[0] === 'number';
        const parsed = numeric ? rows.map(Number) : rows;
        if (numeric && parsed.some(item => typeof item !== 'number' || !Number.isFinite(item))) throw new Error(`${dimensionNames[key]} 숫자를 확인하세요.`);
        search[key] = mode === 'search' ? parsed : parsed.slice(0, 1);
      }
      const result = await modelTrainingProgram.automated.start({task, dataset_path: source, ...(familyDatasetPath ? {family_dataset_path: familyDatasetPath} : {}),
        mode, device, epochs_per_trial: epochs, ...(parent ? {parent_job_id: parent} : {}),
        objective: latencyObjective ? 'loss_latency' : 'val_loss', latency_weight: latencyObjective ? latencyWeight : 0,
        budget: {max_trials: mode === 'search' ? trials : 1, max_total_epochs: epochBudget, max_seconds: seconds},
        search_space: mode === 'fast_retrain' ? {} : search, base_config: mode === 'fast_retrain' ? {} : baseConfig()});
      if (same()) {setJob(result); setJobs(old => [...old, result]);}
    } catch (cause) {if (same()) setError(programError(cause));} finally {if (same()) setBusy(false);}
  };
  const cancel = async () => {
    if (!job) return; setBusy(true);
    try {const result = await modelTrainingProgram.automated.cancel(job.search_id); if (same()) setJob(result);}
    catch (cause) {if (same()) setError(programError(cause));} finally {if (same()) setBusy(false);}
  };
  return <details className="rounded-xl border border-[#344255] bg-[#101A28] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 p-4 font-semibold"><FlaskConical className="h-4 w-4 text-violet-300" />빠른 학습 · 자동 탐색 · 설정 재사용</summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <p className="leading-5 text-slate-400">후보마다 실제 검증 지표와 검증 이미지의 추론 시간을 측정합니다. 구조·학습 조건 후보를 선언한 예산 안에서 비교하고 완료 후보를 선택합니다.{task==='anomaly'&&' 자동 탐색은 DINOv3 합성 결함 학습을 사용하며 정상·결함 검증 데이터가 필요합니다.'}</p>
      {!capability ? <p className="text-amber-300">{source ? '측정 실행 기능을 확인하고 있습니다.' : '원본 데이터와 정답을 먼저 준비하세요.'}</p> : <>
        {!preparedReady && <p className="text-amber-300">위 작업대에서 이 모델군의 학습 데이터를 준비하고 선택하세요.</p>}
        <div className="flex flex-wrap gap-2">{(['quick', 'search', 'fast_retrain'] as const).map(value => <button type="button" key={value} disabled={active || busy} onClick={() => setMode(value)} className={`${programButton} ${mode === value ? 'border-violet-400 bg-violet-950/40 text-violet-200' : ''}`}>{value === 'quick' ? '빠른 초기 학습' : value === 'search' ? '구조·조건 자동 탐색' : '부모 설정으로 재학습'}</button>)}</div>
        {mode !== 'fast_retrain' && <div className="grid gap-3 md:grid-cols-3">{Object.entries(capability.search_defaults).map(([key]) => <ProgramField key={key} label={`${dimensionNames[key] || key}${mode === 'search' ? ' 후보 · 쉼표 구분' : ' · 첫 값 사용'}`}><input disabled={active || busy} value={values[key] || ''} onChange={e => setValues(old => ({...old, [key]: e.target.value}))} className={programInput} /></ProgramField>)}</div>}
        <div className="grid gap-3 md:grid-cols-4"><ProgramField label="후보당 Epoch"><input type="number" min={1} max={500} value={epochs} onChange={e => setEpochs(Number(e.target.value))} className={programInput} /></ProgramField>
          <ProgramField label="최대 후보 수"><input type="number" min={1} max={32} disabled={mode !== 'search'} value={trials} onChange={e => setTrials(Number(e.target.value))} className={programInput} /></ProgramField>
          <ProgramField label="전체 Epoch 예산"><input type="number" min={epochs} max={512} value={epochBudget} onChange={e => setEpochBudget(Number(e.target.value))} className={programInput} /></ProgramField>
          <ProgramField label="전체 시간 예산 · 초"><input type="number" min={1} max={86400} value={seconds} onChange={e => setSeconds(Number(e.target.value))} className={programInput} /></ProgramField></div>
        <div className="grid gap-3 md:grid-cols-3"><TrainingDeviceSelector value={device} onChange={setDevice} disabled={busy || active} />
          <ProgramField label="호환 완료 부모 모델"><select value={parent} onChange={e => setParent(e.target.value)} className={programInput} disabled={busy || active}><option value="">새 후보 학습</option>{parents.map((row, index) => <option key={row.job_id} value={row.job_id}>부모 {index + 1} · SHA {row.checkpoint_sha256.slice(0, 12)}</option>)}</select></ProgramField>
          <ProgramField label="추론 시간 가중치"><input type="number" min={0} step={.001} value={latencyWeight} disabled={!latencyObjective} onChange={e => setLatencyWeight(Number(e.target.value))} className={programInput} /></ProgramField></div>
        <label className="flex items-center gap-2 text-slate-300"><input type="checkbox" checked={latencyObjective} onChange={e => setLatencyObjective(e.target.checked)} />검증 {capability.metric_key}와 모델 추론 시간(ms)을 함께 비교</label>
        <button type="button" className={programPrimary} disabled={busy || active || !preparedReady || epochs < 1 || epochBudget < epochs || (mode === 'fast_retrain' && !parent)} onClick={() => void start()}>측정 학습 시작</button>
      </>}
      {jobs.length > 0 && <ProgramField label="저장된 자동 학습 작업"><select value={job?.search_id || ''} disabled={active} className={programInput} onChange={e => setJob(jobs.find(row => row.search_id === e.target.value) || null)}>{jobs.map((row, index) => <option key={row.search_id} value={row.search_id}>작업 {index + 1} · {statusLabels[row.status] || row.status} · {new Date(row.created_at * 1000).toLocaleString()}</option>)}</select></ProgramField>}
      {job && <div className="rounded border border-[#344255] bg-[#0B1520] p-3"><div className="flex items-center justify-between"><span className="font-semibold text-violet-200">{statusLabels[job.status] || job.status} · 후보 {job.trials.length}개 · {job.epochs_consumed || 0} epoch</span>{active && <button type="button" disabled={busy} onClick={() => void cancel()} className={programButton}><Square className="mr-1 inline h-3 w-3" />탐색 중지</button>}</div>
        <div className="mt-3 overflow-auto"><table className="w-full text-left"><thead className="text-slate-400"><tr><th className="p-2">후보</th><th>설정</th><th>검증 지표</th><th>추론 ms</th><th>상태</th></tr></thead><tbody>{job.trials.map((row, index) => <tr key={row.trial_id} className="border-t border-[#243247]"><td className="p-2">{index + 1}{job.winner?.trial_id === row.trial_id && <span className="ml-1 text-emerald-300">선택</span>}</td><td className="max-w-64 break-words py-2 text-slate-400">{Object.entries(row.config).filter(([key]) => ['backbone', 'model_name', 'architecture', 'learning_rate', 'augmentation_profile', 'width', 'batch_size'].includes(key)).map(([key, value]) => `${key}: ${String(value)}`).join(' · ')}</td><td>{Object.entries(row.metrics).map(([key, value]) => `${key} ${value.toFixed(5)}`).join(' · ') || '—'}</td><td>{row.latency_ms?.toFixed(2) || '—'}</td><td>{statusLabels[row.status] || row.status}{row.progress?.epoch !== undefined && <p className="text-slate-400">epoch {row.progress.epoch}/{String(row.config.epochs || epochs)}{row.progress.batch !== undefined && ` · 배치 ${row.progress.batch}/${row.progress.batches || "?"}`}</p>}{row.error && <p className="max-w-48 text-rose-300">{row.error}</p>}</td></tr>)}</tbody></table></div>
        {job.winner && <p className="mt-3 text-emerald-300">측정 후보를 저장했습니다. 이 모델군의 완료 모델 선택기에서 평가·플로우·내보내기를 계속하세요.</p>}{job.stop_reason && <p className="mt-2 text-slate-400">종료 조건: {job.stop_reason} · 모델 추론 시간은 전체 검사 플로우 시간과 별도로 측정됩니다.</p>}{job.error && <p className="mt-2 text-rose-300">{job.error}</p>}</div>}
      {error && <p role="alert" className="rounded bg-rose-950/30 p-3 text-rose-200">{error}</p>}
    </div>
  </details>;
}
