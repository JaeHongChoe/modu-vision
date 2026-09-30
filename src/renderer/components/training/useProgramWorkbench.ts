import { useCallback, useEffect, useRef, useState } from 'react';
import { useProjectStore } from '../../stores/useProjectStore';
import { activeProgramJob, modelTrainingProgram, programError, type FamilyModel, type PreparedDataset, type ProgramJob } from '../../services/modelTrainingProgram';

export function useProgramWorkbench(family: 'patch' | 'rotation') {
  const project = useProjectStore(state => state.project);
  const projectDir = useProjectStore(state => state.projectDir);
  const source = project?.source_dataset_dir || '';
  const scope = `${projectDir}\n${source}\n${project?.active_labelset_id || 'default'}`;
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
    if (isCurrent()) {setModels(result.models); setModelId(old => result.models.some(row => row.job_id === old) ? old : result.models[0]?.job_id || '');}
  }, [family, source, isCurrent]);
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
        setDatasets(prepared.datasets); setDataset(prepared.datasets.at(-1) || null);
        const own = jobs.jobs.filter(row => family === 'patch'
          ? row.task === 'patch_classification' && !!row.output_dir && row.output_dir.startsWith(`${project?.models_dir}/`)
          : row.source_dataset_path === source && row.training_provenance?.labelset_id === (project?.active_labelset_id || 'default'));
        const restored = own.find(row => activeProgramJob(row.status)) || own.at(-1);
        if (restored) setJob(restored);
      }).catch(cause => {if (active && isCurrent()) setError(programError(cause));});
    void refreshModels().catch(cause => {if (active && isCurrent()) setError(programError(cause));});
    return () => {active = false;};
  }, [family, scope, projectDir, source, isCurrent, refreshModels]);
  useEffect(() => {
    if (!job || !activeProgramJob(job.status)) return;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const row = await modelTrainingProgram[family].status(job.job_id);
        if (!active || !isCurrent()) return;
        setJob(row);
        if (row.status === 'completed') {setModelId(row.job_id); setNotice('완료 후보를 저장했습니다. 평가한 뒤 검사 플로우에 연결하세요.'); await refreshModels();}
        if (row.error) setError(typeof row.error === 'string' ? row.error : row.error.message || JSON.stringify(row.error));
        if (activeProgramJob(row.status)) timer = setTimeout(() => void poll(), 1200);
      } catch (cause) {if (active && isCurrent()) {setError(programError(cause)); timer = setTimeout(() => void poll(), 3000);}}
    };
    void poll(); return () => {active = false; clearTimeout(timer);};
  }, [family, job?.job_id, scope, isCurrent, refreshModels]);
  const selectDataset = (path: string) => setDataset(datasets.find(row => row.dataset_path === path) || null);
  const addDataset = (prepared: PreparedDataset) => {if (isCurrent()) {setDataset(prepared); setDatasets(old => [...old.filter(row => row.dataset_path !== prepared.dataset_path), prepared]);}};
  const cancel = () => action('중지 요청', async () => {
    if (!job) return;
    const result = await modelTrainingProgram[family].cancel(job.job_id);
    if (isCurrent()) setJob({...job, ...result, status: result.status === 'stopping' ? 'stopping' : result.status});
  });
  return {family, source, scope, projectDir, datasets, dataset, selectDataset, addDataset, models, modelId, setModelId,
    job, setJob, error, setError, notice, setNotice, busy, action, isCurrent, cancel,
    active: !!job && activeProgramJob(job.status), refreshModels};
}
