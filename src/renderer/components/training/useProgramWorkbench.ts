import {controlModelTraining,reconnectModelTraining} from '../../services/modelExecution';
import {watchJob} from './jobProgress';
import {getApiPersistenceIdentity} from '../../services/api';
import {useComputeStore} from '../../stores/useComputeStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import {useTaskHandoff} from './useTaskHandoff';
import {selectHandoffRecord} from './taskHandoff';
import { useProjectStore } from '../../stores/useProjectStore';
import { activeProgramJob, modelTrainingProgram, programError, type FamilyModel, type PreparedDataset, type ProgramJob } from '../../services/modelTrainingProgram';

export function useProgramWorkbench(family: 'patch' | 'rotation') {
  const handoff=useTaskHandoff(family==='patch'?'patch_classification':'rotation');
  const project = useProjectStore(state => state.project);
  const projectDir = useProjectStore(state => state.projectDir);
  const source = project?.source_dataset_dir || '';
  const compute=useComputeStore();
  const scope = `${projectDir}\n${source}\n${project?.active_labelset_id || 'default'}\n${compute.transportRevision}\n${compute.selectedProfileId}\n${getApiPersistenceIdentity()}`;
  const current = useRef(scope); current.current = scope;
  const [datasets, setDatasets] = useState<PreparedDataset[]>([]);
  const [dataset, setDataset] = useState<PreparedDataset | null>(null);
  const [models, setModels] = useState<FamilyModel[]>([]);
  const [modelId, setModelId] = useState('');
  const [job, setJob] = useState<ProgramJob | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const mounted = useRef(true);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  const isCurrent = useCallback((expected = scope) => mounted.current && current.current === expected, [scope]);
  const refreshModels = useCallback(async () => {
    const result = family === 'patch' ? await modelTrainingProgram.patch.models(source) : await modelTrainingProgram.rotation.models();
    if (isCurrent()) {setModels(result.models);if(handoff&&handoff.status==='completed'){setModelId(selectHandoffRecord(result.models,handoff)?.job_id||'');}else setModelId(old => result.models.some(row => row.job_id === old) ? old : result.models[0]?.job_id || '');}
  }, [family, source, isCurrent,handoff?.jobId,handoff?.selectionId]);
  const action = async (name: string, run: () => Promise<void>) => {
    const expected = scope; setBusy(name); setError(''); setNotice('');
    try {await run();} catch (cause) {if (isCurrent(expected)) setError(programError(cause));}
    finally {if (isCurrent(expected)) setBusy('');}
  };
  useEffect(() => {
    let active = true;
    setDatasets([]); setDataset(null); setModels([]); setModelId(''); setJob(null); setError(''); setNotice(''); setBusy('');
    if (!projectDir || !source) return;
    void Promise.all([modelTrainingProgram[family].datasets(), family === 'patch' ? modelTrainingProgram.patch.jobs() : modelTrainingProgram.rotation.jobs()])
      .then(([prepared, jobs]) => {
        if (!active || !isCurrent()) return;
        setDatasets(prepared.datasets);const requested=handoff?.datasetPath?prepared.datasets.find(row=>row.dataset_path===handoff.datasetPath):prepared.datasets.at(-1);if(handoff&&!requested)throw new Error('선택 작업이 사용한 준비 데이터 버전을 찾지 못했습니다.');setDataset(requested||null);
        const own = jobs.jobs.filter(row => family === 'patch'
          ? row.task === 'patch_classification' && !!row.output_dir && row.output_dir.startsWith(`${project?.models_dir}/`)
          : row.source_dataset_path === source && row.training_provenance?.labelset_id === (project?.active_labelset_id || 'default'));
        const restored = handoff?(handoff.transport&&handoff.transport!=='local'?own.find(row=>row.job_id===handoff.jobId):handoff.kind==='automated'?own.find(row=>row.job_id===handoff.jobId):selectHandoffRecord(own,handoff)):own.find(row => watchJob(row.status)) || own.at(-1);
        if (restored) setJob(restored);
        if(handoff?.transport&&handoff.transport!=='local'&&handoff.executionJobId){void controlModelTraining<ProgramJob>({job_id:handoff.jobId,execution_job_id:handoff.executionJobId,compute_profile_id:handoff.transport,status:handoff.status},'status',()=>Promise.reject(new Error('서버 작업 식별자가 필요합니다.'))).then(row=>{if(active&&isCurrent())setJob(row);}).catch(cause=>{if(active&&isCurrent())setError(programError(cause));});}
      }).catch(cause => {if (active && isCurrent()) setError(programError(cause));});
    void refreshModels().catch(cause => {if (active && isCurrent()) setError(programError(cause));});
    return () => {active = false;};
  }, [family, scope, projectDir, source, isCurrent, refreshModels,handoff?.jobId,handoff?.selectionId]);
  useEffect(() => {
    if (!job || !watchJob(job.status)) return;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const row = await controlModelTraining<ProgramJob>(job,'status',()=>modelTrainingProgram[family].status(job.job_id));
        if (!active || !isCurrent()) return;
        setJob(row);
        if (row.status === 'completed') {setModelId(row.job_id); setNotice('완료 후보를 저장했습니다. 평가한 뒤 검사 플로우에 연결하세요.'); await refreshModels();}
        // The job's own error and next action are shown by its progress view; a dropped connection is read more slowly.
        if (watchJob(row.status)) timer = setTimeout(() => void poll(), activeProgramJob(row.status) ? 1200 : 3000);
      } catch (cause) {if (active && isCurrent()) {setError(programError(cause)); timer = setTimeout(() => void poll(), 3000);}}
    };
    void poll(); return () => {active = false; clearTimeout(timer);};
  }, [family, job?.job_id, scope, isCurrent, refreshModels]);
  const selectDataset = (path: string) => setDataset(datasets.find(row => row.dataset_path === path) || null);
  const addDataset = (prepared: PreparedDataset) => {if (isCurrent()) {setDataset(prepared); setDatasets(old => [...old.filter(row => row.dataset_path !== prepared.dataset_path), prepared]);}};
  const cancel = () => action('중지 요청', async () => {
    if (!job) return;
    const result = await controlModelTraining<ProgramJob>(job,'cancel',()=>modelTrainingProgram[family].cancel(job.job_id));
    if (isCurrent()) setJob({...job, ...result, status: result.status === 'stopping' ? 'stopping' : result.status});
  });
  const reconnect = () => action('재연결 요청', async () => {
    if (!job) return;
    const result = await reconnectModelTraining<ProgramJob>(job);
    if (isCurrent()) setJob({...job, ...result});
  });
  return {family, source, scope, projectDir, datasets, dataset, selectDataset, addDataset, models, modelId, setModelId,
    job, setJob, error, setError, notice, setNotice, busy, action, isCurrent, cancel, reconnect,
    active: !!job && activeProgramJob(job.status), refreshModels};
}
