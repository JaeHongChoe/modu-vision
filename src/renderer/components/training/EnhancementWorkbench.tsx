import {TrainingPreparationPanel} from './TrainingPreparationPanel';
import {submitModelTraining,controlModelTraining,reconnectModelTraining} from '../../services/modelExecution';
import {JobProgressView} from './JobProgressView';
import {activeJob,watchJob,JOB_STATUS_LABELS} from './jobProgress';
import {useComputeStore} from '../../stores/useComputeStore';
import {getApiPersistenceIdentity} from '../../services/api';
import {openModelFlow} from './ProgramWorkbenchControls';
import { useEffect, useRef, useState } from 'react';
import { ImagePlus, Loader2, RefreshCw } from 'lucide-react';
import { request } from '../../services/api';
import {useTaskHandoff} from './useTaskHandoff';
import {selectHandoffRecord} from './taskHandoff';
import { useProjectStore } from '../../stores/useProjectStore';
import { WarmStartSelector } from './WarmStartSelector';
import {AutoDLWorkbench} from './AutoDLWorkbench';
import {TrainingDeviceSelector} from './ProgramWorkbenchControls';
import type {LocalTrainingDevice,PreparedDataset} from '../../services/modelTrainingProgram';

interface EnhancementModel { job_id: string; metadata: { best_epoch: number; dataset_path: string; source_dataset_path: string } }
interface EnhancementMetrics { sample_count: number; input_psnr: number; output_psnr: number; improved: boolean }
interface EnhancementJob { execution_job_id?:string;compute_profile_id?:string; job_id: string; status: string; epoch: number; epochs: number; dataset_path: string; source_dataset_path: string; error: string | null }
const activeStatus = activeJob;

export function EnhancementWorkbench() {
  const handoff=useTaskHandoff('enhancement');
  const source = useProjectStore((s) => s.project?.source_dataset_dir || '');
  const projectId = useProjectStore((s) => s.project?.id);
  const labelsetId = useProjectStore((s) => s.project?.active_labelset_id || 'default');
  const [datasetPath, setDatasetPath] = useState('');
  const [datasets,setDatasets] = useState<PreparedDataset[]>([]);
  const [device,setDevice] = useState<LocalTrainingDevice>('cpu');
  const [models, setModels] = useState<EnhancementModel[]>([]);
  const [jobId, setJobId] = useState('');
  const [epochs, setEpochs] = useState(1);
  const [warmParentId, setWarmParentId] = useState('');
  const [sigma, setSigma] = useState(15);
  const [imageLimit, setImageLimit] = useState(0);
  const [sampleCount, setSampleCount] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<EnhancementMetrics | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [jobs, setJobs] = useState<EnhancementJob[]>([]);
  const [job, setJob] = useState<EnhancementJob | null>(null);
  const compute=useComputeStore();
  const scope = `${projectId || ''}\n${source}\n${labelsetId}\n${compute.selectedProfileId}\n${compute.transportRevision}\n${getApiPersistenceIdentity()}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const training = job !== null && activeStatus(job.status);

  const refresh = async () => {
    const expected = scope;
    const result = await request<{ models: EnhancementModel[] }>('/api/enhancement/models');
    if (currentScope.current === expected) setModels(result.models.filter((m) => !source || m.metadata.source_dataset_path === source));
  };
  useEffect(() => {
    let current = true;
    setDatasetPath('');setDatasets([]); setModels([]); setJobId(''); setJob(null); setJobs([]); setBusy(''); setSampleCount(null); setMetrics(null); setError(''); setNotice('');
    void Promise.all([request<{datasets:PreparedDataset[]}>('/api/enhancement/datasets'),request<{models:EnhancementModel[]}>('/api/enhancement/models'),request<{jobs:EnhancementJob[]}>('/api/enhancement/jobs')]).then(([prepared,result,journal])=>{
      if(!current||currentScope.current!==scope)return;setDatasets(prepared.datasets);
      const items=result.models.filter(model=>model.metadata.source_dataset_path===source);setModels(items);
      const selected=handoff&&handoff.status!=='completed'?undefined:selectHandoffRecord(items,handoff);setJobId(selected?.job_id||'');
      const own=journal.jobs.filter(row=>row.source_dataset_path===source);setJobs(own);const restored=handoff?(handoff.transport&&handoff.transport!=='local'?own.find(row=>row.job_id===handoff.jobId):handoff.kind==='automated'?own.find(row=>row.job_id===handoff.jobId):selectHandoffRecord(own,handoff)):own.find(row=>watchJob(row.status));setJob(restored||null);
      if(handoff?.transport&&handoff.transport!=='local'&&handoff.executionJobId){void controlModelTraining<EnhancementJob>({job_id:handoff.jobId,execution_job_id:handoff.executionJobId,compute_profile_id:handoff.transport,status:handoff.status},'status',()=>Promise.reject(new Error('서버 작업 식별자가 필요합니다.'))).then(row=>{if(current&&currentScope.current===scope)setJob(row);}).catch(cause=>{if(current&&currentScope.current===scope)setError(String(cause));});}
      const path=handoff?.datasetPath||selected?.metadata.dataset_path||restored?.dataset_path;const dataset=path?prepared.datasets.find(row=>row.dataset_path===path):prepared.datasets.at(-1);
      if(handoff&&!dataset)throw new Error('선택 작업이 사용한 이미지 개선 정답 쌍을 찾지 못했습니다.');
      if(dataset){setDatasetPath(dataset.dataset_path);setSampleCount(dataset.sample_count||null);}
    }).catch(cause=>{if(current&&currentScope.current===scope)setError(cause instanceof Error?cause.message:String(cause));});
    return () => { current = false; };
  }, [scope,handoff?.jobId,handoff?.selectionId]);

  useEffect(() => {
    if (!job || !watchJob(job.status)) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const row = await controlModelTraining<EnhancementJob>(job,'status',()=>request<EnhancementJob>(`/api/enhancement/jobs/${job.job_id}`));
        if (!current || currentScope.current !== scope) return;
        setJob(row); setJobs((old) => [row, ...old.filter((item) => item.job_id !== row.job_id)]);
        if (row.status === 'completed') {
          setJobId(row.job_id); setNotice('후보 모델을 저장했습니다. 시험 평가 후 5단계에서 연결할 수 있습니다.');
          void refresh();
        }  // a failure and its next action are shown by the job's progress view
        if (watchJob(row.status)) timer = setTimeout(() => void poll(), activeStatus(row.status) ? 800 : 3000);
      } catch (e) {
        if (current) { setError(e instanceof Error ? e.message : '진행 상황을 읽지 못했습니다.'); timer = setTimeout(() => void poll(), 2000); }
      }
    };
    void poll();
    return () => { current = false; clearTimeout(timer); };
  }, [job?.job_id, scope]);

  const action = async (name: string, run: () => Promise<void>) => {
    const expected = scope;
    setBusy(name); setError(''); setNotice('');
    try { await run(); } catch (e) { if (currentScope.current === expected) setError(e instanceof Error ? e.message : '작업을 완료하지 못했습니다.'); }
    finally { if (currentScope.current === expected) setBusy(''); }
  };
  const prepare = () => action('학습 이미지 준비', async () => {
    const result = await request<{ dataset_path: string; records: unknown[]; provenance:PreparedDataset['provenance'] }>('/api/enhancement/prepare', {
      method: 'POST', body: JSON.stringify({ source_dataset_path: source, noise_sigma: sigma, ...(imageLimit ? { image_limit: imageLimit } : {}) }),
    });
    if (currentScope.current !== scope) return;
    setDatasetPath(result.dataset_path); setSampleCount(result.records.length); setMetrics(null);
    setDatasets(old=>[...old,{dataset_path:result.dataset_path,sample_count:result.records.length,provenance:result.provenance}]);
    setNotice(`원본을 보존하고 ${result.records.length}쌍을 별도 폴더에 준비했습니다.`);
  });
  const load = () => action('정답 쌍 확인', async () => {
    const result = await request<{ records: unknown[] }>(`/api/enhancement/manifest?dataset_path=${encodeURIComponent(datasetPath)}`);
    if (currentScope.current !== scope) return;
    setSampleCount(result.records.length);
  });
  const train = () => action('이미지 개선 학습', async () => {
    const options={dataset_path:datasetPath,epochs,device,background:true,...(warmParentId?{warm_start_job_id:warmParentId}:{})};
    const result=await submitModelTraining<EnhancementJob>('enhancement',options,()=>request<EnhancementJob>('/api/enhancement/train',{method:'POST',body:JSON.stringify(options)}));
    if (currentScope.current !== scope) return;
    setJob(result); setJobs((old) => [result, ...old.filter((item) => item.job_id !== result.job_id)]);
    setMetrics(null); setNotice('학습을 시작했습니다. 화면을 이동해도 작업을 다시 열 수 있습니다.');
  });
  const evaluate = () => action('시험 평가', async () => {
    const result = await request<EnhancementMetrics>('/api/enhancement/evaluate', {
      method: 'POST', body: JSON.stringify({ dataset_path: datasetPath, job_id: jobId, device }),
    });
    if (currentScope.current === scope) setMetrics(result);
  });
  const cancel = () => action('학습 중단 요청', async () => {
    if (!job) return;
    const row = await controlModelTraining<EnhancementJob>(job,'cancel',()=>request<EnhancementJob>(`/api/enhancement/jobs/${job.job_id}/cancel`,{method:'POST'}));
    if (currentScope.current === scope) setJob(row);
  });
  const reconnect = () => action('재연결 요청', async () => {
    if (!job) return;
    const row = await reconnectModelTraining<EnhancementJob>(job);
    if (currentScope.current === scope) setJob({...job, ...row});
  });
  const reopen = (id: string) => action('학습 작업 확인', async () => {
    const row = await request<EnhancementJob>(`/api/enhancement/jobs/${id}`);
    if (currentScope.current !== scope) return;
    setJob(row); setDatasetPath(row.dataset_path); setMetrics(null);
    if (row.status === 'completed') { setJobId(row.job_id); await refresh(); }
  });

  const input = 'mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 text-slate-100';
  const button = 'rounded border border-slate-600 px-3 py-2 hover:bg-slate-700 disabled:opacity-40';
  return <details open className="rounded border border-[#344255] bg-[#182332] text-sm text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 font-semibold"><ImagePlus className="h-4 w-4 text-cyan-400" />이미지 개선 모델</summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <TrainingPreparationPanel family="enhancement" model="enhancement" device={device} datasetPath={datasetPath||undefined} warmStartJobId={warmParentId||undefined} config={{epochs}} />
      <p className="leading-5 text-slate-300">입력 이미지와 개선 정답 쌍으로 학습합니다. 현재 데이터의 원본을 정답으로 두고 노이즈 입력을 별도로 준비하거나, 준비된 정답 쌍을 불러올 수 있습니다.</p>
      <div className="flex flex-wrap items-end gap-3">
        <label>입력 노이즈 강도<input aria-label="입력 노이즈 강도" type="number" min={1} max={50} value={sigma} onChange={(e) => setSigma(Math.min(50, Math.max(1, Number(e.target.value) || 1)))} className={`${input} max-w-24`} /></label>
        <label>준비 이미지 수 (0: 전체)<input aria-label="이미지 개선 준비 이미지 수" type="number" min={0} max={10000} value={imageLimit} onChange={(e) => setImageLimit(Math.max(0, Math.min(10000, Number(e.target.value) || 0)))} className={`${input} max-w-32`} /></label>
        <button type="button" disabled={!source || !!busy || training} onClick={() => void prepare()} className={button}>현재 데이터로 정답 쌍 준비</button>
      </div>
      {imageLimit > 0 && <p className="text-slate-400">경로 순서의 처음 {imageLimit}장을 준비합니다. 학습·검증·시험에는 서로 다른 원본을 배치하며 실제 선택 목록은 정답 쌍에 기록됩니다. 최소 3장의 서로 다른 이미지가 필요합니다.</p>}
      {datasets.length>0&&<label className="block">저장된 학습 이미지 쌍<select value={datasetPath} onChange={e=>{setDatasetPath(e.target.value);setSampleCount(datasets.find(row=>row.dataset_path===e.target.value)?.sample_count||null);setMetrics(null);}} className={input}>{datasets.map((row,index)=><option value={row.dataset_path} key={row.dataset_path}>정답 쌍 {index+1} · {row.sample_count}장</option>)}</select></label>}
      <label className="block">정답 쌍 폴더<input value={datasetPath} onChange={(e) => { setDatasetPath(e.target.value); setSampleCount(null); setMetrics(null); }} className={input} placeholder="pairs.json이 있는 폴더" /></label>
      <button type="button" disabled={!datasetPath || !!busy} onClick={() => void load()} className={button}>정답 쌍 확인</button>
      {sampleCount !== null && <span className="ml-3 text-emerald-300">검증된 이미지 쌍 {sampleCount}개</span>}
      <div className="flex flex-wrap items-end gap-3 border-t border-[#344255] pt-4">
        <TrainingDeviceSelector value={device} onChange={setDevice} disabled={!!busy||training}/>
        <WarmStartSelector family="enhancement" datasetPath={datasetPath} value={warmParentId} onChange={setWarmParentId} disabled={!!busy || training} refreshKey={job?.status === 'completed' ? job.job_id : null} />
        <label>학습 epoch<input aria-label="이미지 개선 학습 epoch" type="number" min={1} max={500} value={epochs} onChange={(e) => setEpochs(Math.max(1, Math.min(500, Number(e.target.value) || 1)))} className={`${input} max-w-24`} /></label>
        <button type="button" disabled={!sampleCount || !!busy || training} onClick={() => void train()} className="rounded bg-cyan-700 px-3 py-2 font-semibold hover:bg-cyan-600 disabled:opacity-40">이미지 개선 후보 학습</button>
        <label className="min-w-56 flex-1">완료 후보 모델<select value={jobId} onChange={(e) => {
          setJobId(e.target.value); setMetrics(null);
          const model = models.find((m) => m.job_id === e.target.value);
          if (model) setDatasetPath(model.metadata.dataset_path);
        }} className={input}>
          <option value="">모델 선택</option>{models.map((m) => <option value={m.job_id} key={m.job_id}>{m.job_id.slice(0, 12)} · epoch {m.metadata.best_epoch}</option>)}
        </select></label>
        <button type="button" aria-label="이미지 개선 모델 새로고침" onClick={() => void action('모델 목록 확인', refresh)} disabled={!!busy} className={button}><RefreshCw className="h-4 w-4" /></button>
        <button type="button" onClick={() => void evaluate()} disabled={!jobId || !datasetPath || !!busy} className={button}>시험 분할 평가</button>
      </div>
      {jobs.length > 0 && <label className="block">학습 작업 다시 열기<select aria-label="이미지 개선 학습 작업" value={job?.job_id || ''} onChange={(e) => { if (e.target.value) void reopen(e.target.value); }} className={input} disabled={!!busy}>
        <option value="">작업 선택</option>{jobs.map((row) => <option key={row.job_id} value={row.job_id}>{row.job_id.slice(0, 12)} · {JOB_STATUS_LABELS[row.status] || row.status} · {row.epoch}/{row.epochs}</option>)}
      </select></label>}
      <JobProgressView job={job} busy={!!busy} onCancel={() => void cancel()} onReconnect={() => void reconnect()} />
      {busy && <p role="status" className="text-cyan-300"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />{busy} 중…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-3 text-rose-200">{error}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {metrics && <div className="rounded border border-[#344255] p-3">시험 {metrics.sample_count}장 · 입력 PSNR {metrics.input_psnr.toFixed(2)} → 출력 {metrics.output_psnr.toFixed(2)} dB<br /><span className={metrics.improved ? 'text-emerald-300' : 'text-amber-300'}>{metrics.improved ? '시험 정답 대비 오차가 감소했습니다.' : '시험 정답 대비 개선이 확인되지 않았습니다.'}</span></div>}
      <button type="button" disabled={!jobId} onClick={()=>void openModelFlow('enhancement',jobId,datasetPath)} className={button}>검사 플로우·배포 패키지</button>
      <AutoDLWorkbench task="enhancement" familyDatasetPath={sampleCount?datasetPath:undefined} onComplete={()=>void refresh().catch(e=>{if(currentScope.current===scope)setError(e instanceof Error?e.message:String(e));})}/>
    </div>
  </details>;
}
