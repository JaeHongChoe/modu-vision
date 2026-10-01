import {openModelFlow} from './ProgramWorkbenchControls';
import { useEffect, useState } from 'react';
import { Grid2X2 } from 'lucide-react';
import { modelTrainingProgram, type LocalTrainingDevice } from '../../services/modelTrainingProgram';
import { useProgramWorkbench } from './useProgramWorkbench';
import { ProgramField, ProgramJobStatus, TrainingDeviceSelector, programButton, programInput, programPrimary } from './ProgramWorkbenchControls';
import { AutoDLWorkbench } from './AutoDLWorkbench';
import {TrainingPreparationPanel} from './TrainingPreparationPanel';

export function PatchClassificationWorkbench() {
  const state = useProgramWorkbench('patch');
  const [patchSize, setPatchSize] = useState(256); const [stride, setStride] = useState(128);
  const [normalClass, setNormalClass] = useState('OK'); const [overlap, setOverlap] = useState(.05);
  const [backbone, setBackbone] = useState('dinov3_vits16'); const [checkpoint, setCheckpoint] = useState('');
  const [epochs, setEpochs] = useState(20); const [batch, setBatch] = useState(8); const [imageSize, setImageSize] = useState(256);
  const [learningRate, setLearningRate] = useState(.0001); const [device, setDevice] = useState<LocalTrainingDevice>('cpu');
  const [parent, setParent] = useState(''); const [parents, setParents] = useState<Array<{job_id: string; checkpoint_sha256: string}>>([]);
  const [evaluation, setEvaluation] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {setParent(''); setParents([]); setEvaluation(null);}, [state.scope]);
  useEffect(() => {
    let active = true; setParent(''); setParents([]);
    if (state.source && state.dataset) void modelTrainingProgram.patch.parents(state.source, backbone).then(result => {
      if (active && state.isCurrent()) setParents(result.parents);
    }).catch(cause => {if (active && state.isCurrent()) state.setError(cause.message);});
    return () => {active = false;};
  }, [state.source, state.dataset?.dataset_path, backbone, state.scope, state.job?.status]);
  const disabled = !!state.busy || state.active;
  const prepare = () => state.action('패치 정답 준비', async () => {
    const prepared = await modelTrainingProgram.patch.prepare({patch_size: patchSize, stride, normal_class: normalClass, minimum_overlap: overlap});
    if (!state.isCurrent()) return; state.addDataset(prepared); setEvaluation(null);
    state.setNotice(`정답 패치 ${prepared.patch_count || 0}개를 프로젝트에 준비했습니다.`);
  });
  const train = () => state.action('패치 학습 등록', async () => {
    if (!state.dataset) return;
    const row = await modelTrainingProgram.patch.train({dataset_path: state.dataset.dataset_path, backbone, epochs, batch_size: batch,
      image_size: imageSize, learning_rate: learningRate, device, ...(parent ? {warm_start_job_id: parent} : {}),
      ...(checkpoint.trim() ? {pretrained_checkpoint: checkpoint.trim()} : {})});
    if (state.isCurrent()) {state.setJob(row); setEvaluation(null);}
  });
  return <section className="rounded-xl border border-[#344255] bg-[#131D2B] p-5 text-xs text-slate-200">
    <TrainingPreparationPanel family="patch_classification" model={backbone} checkpoint={checkpoint} device={device} datasetPath={state.dataset?.dataset_path} warmStartJobId={parent||undefined} config={{backbone,epochs,batch_size:batch,image_size:imageSize,learning_rate:learningRate}} onCheckpointChange={setCheckpoint} />
    <h2 className="mt-4 flex items-center gap-2 text-base font-semibold"><Grid2X2 className="h-5 w-5 text-cyan-300" />고해상도 패치 분류</h2>
    <p className="mt-2 leading-5 text-slate-400">검수한 영역 라벨에서 원본 좌표의 패치를 준비하고 DINOv3로 분류합니다. 원본 이미지·라벨 해시와 이미지별 분할을 함께 저장합니다.</p>
    <div className="mt-4 grid gap-3 sm:grid-cols-4">
      <ProgramField label="원본 패치 크기"><input type="number" min={16} max={2048} value={patchSize} onChange={e => setPatchSize(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="패치 간격"><input type="number" min={1} max={patchSize} value={stride} onChange={e => setStride(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="정상 패치 클래스"><input value={normalClass} onChange={e => setNormalClass(e.target.value)} className={programInput} /></ProgramField>
      <ProgramField label="최소 결함 겹침 비율"><input type="number" min={.001} max={1} step={.01} value={overlap} onChange={e => setOverlap(Number(e.target.value))} className={programInput} /></ProgramField>
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-3"><button type="button" onClick={() => void prepare()} disabled={!state.source || disabled || stride > patchSize || !normalClass.trim()} className={programButton}>영역 라벨에서 패치 준비</button>
      {!state.source && <span className="text-amber-300">1단계에서 원본 데이터를 선택하세요.</span>}</div>
    <div className="mt-4"><ProgramField label="프로젝트에 저장된 패치 정답"><select value={state.dataset?.dataset_path || ''} onChange={e => state.selectDataset(e.target.value)} className={programInput} disabled={disabled}>
      <option value="">준비 데이터 선택</option>{state.datasets.map((row, index) => <option key={row.dataset_path} value={row.dataset_path}>준비 {index + 1} · 패치 {row.patch_count}개 · {row.patch_size}px / 간격 {row.stride}px</option>)}</select></ProgramField>
      {state.dataset && <p className="mt-2 text-slate-400">클래스 {(state.dataset.classes || []).join(' · ')} · 원본 크기 {state.dataset.patch_size}px · train/val/test 이미지 단위 분할</p>}</div>
    <div className="mt-5 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <ProgramField label="DINOv3 백본"><select value={backbone} onChange={e => setBackbone(e.target.value)} className={programInput}><option value="dinov3_vits16">ViT-S/16</option><option value="dinov3_vitb16">ViT-B/16</option></select></ProgramField>
      <ProgramField label="Epoch"><input type="number" min={1} max={500} value={epochs} onChange={e => setEpochs(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="배치 크기"><input type="number" min={1} max={128} value={batch} onChange={e => setBatch(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="모델 입력 크기"><input type="number" min={32} max={1024} step={16} value={imageSize} onChange={e => setImageSize(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="학습률"><input type="number" min={.000001} max={1} step={.0001} value={learningRate} onChange={e => setLearningRate(Number(e.target.value))} className={programInput} /></ProgramField>
      <TrainingDeviceSelector value={device} onChange={setDevice} disabled={disabled} />
    </div>
    <details className="mt-3 text-slate-400"><summary className="cursor-pointer">사전학습 파일 지정</summary><input aria-label="패치 DINOv3 사전학습 파일" placeholder="비워 두면 검증된 기본 pretrained 사용" value={checkpoint} onChange={e => setCheckpoint(e.target.value)} className={programInput} /></details>
    <div className="mt-3"><ProgramField label="호환 완료 모델에서 재학습"><select value={parent} onChange={e => setParent(e.target.value)} className={programInput} disabled={disabled}><option value="">새 후보 학습</option>{parents.map((row, index) => <option key={row.job_id} value={row.job_id}>부모 {index + 1} · SHA {row.checkpoint_sha256.slice(0, 12)}</option>)}</select></ProgramField></div>
    <div className="my-4"><button type="button" onClick={() => void train()} disabled={!state.dataset || disabled || epochs < 1 || batch < 1 || learningRate <= 0} className={programPrimary}>패치 분류 후보 학습</button></div>
    <ProgramJobStatus job={state.job} busy={!!state.busy} onCancel={() => void state.cancel()} />
    <div className="mt-4 flex flex-wrap items-end gap-3"><div className="min-w-64 flex-1"><ProgramField label="완료 패치 분류 후보"><select value={state.modelId} onChange={e => {state.setModelId(e.target.value); setEvaluation(null);}} className={programInput}><option value="">완료 모델 선택</option>{state.models.map((row, index) => <option key={row.job_id} value={row.job_id}>후보 {index + 1} · {row.job_id.slice(-6)}</option>)}</select></ProgramField></div>
      <button type="button" disabled={!state.modelId || disabled} className={programButton} onClick={() => void state.action('시험 평가', async () => {const result = await modelTrainingProgram.patch.evaluate(state.modelId); if (state.isCurrent()) setEvaluation(result);})}>시험 평가</button>
      <button type="button" disabled={!state.modelId || disabled} className={programButton} onClick={() => void openModelFlow('patch_classification',state.modelId,state.dataset?.dataset_path)}>플로우에 연결</button>
      <button type="button" disabled={!state.modelId || disabled} className={programButton} onClick={() => void state.action('모델 내보내기', async () => {const result = await modelTrainingProgram.patch.export(state.modelId); if (state.isCurrent()) state.setNotice(`독립 실행 패키지: ${result.package_path || result.package_dir || JSON.stringify(result)}`);})}>TorchScript 내보내기</button>
    </div>
    {evaluation && <div className="mt-3 rounded border border-[#344255] p-3"><p className="font-semibold text-cyan-200">저장된 패치 시험 평가</p><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap text-slate-400">{JSON.stringify({metrics:evaluation.metrics,confusion_matrix:evaluation.confusion_matrix,evaluation_id:evaluation.evaluation_id,dataset_provenance:evaluation.dataset_provenance}, null, 2)}</pre></div>}
    {state.busy && <p role="status" className="mt-3 text-cyan-300">{state.busy}…</p>}{state.notice && <p role="status" className="mt-3 break-words text-emerald-300">{state.notice}</p>}{state.error && <p role="alert" className="mt-3 rounded bg-rose-950/40 p-3 text-rose-200">{state.error}</p>}
    <div className="mt-5"><AutoDLWorkbench task="patch_classification" familyDatasetPath={state.dataset?.dataset_path} onComplete={()=>void state.refreshModels().catch(cause=>{if(state.isCurrent())state.setError(String(cause));})} /></div>
  </section>;
}
