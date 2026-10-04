import {parentCandidateLabel, parentCandidateNotice, type ParentCandidate} from './parentCandidate';
import {TrainingPreparationPanel} from './TrainingPreparationPanel';
import {openModelFlow} from './ProgramWorkbenchControls';
import { useEffect, useState } from 'react';
import { RotateCw } from 'lucide-react';
import { modelTrainingProgram, type LocalTrainingDevice, type RotationEvaluation, type RotationPrediction, type RotationRow } from '../../services/modelTrainingProgram';
import { useProgramWorkbench } from './useProgramWorkbench';
import { ProgramField, TrainingDeviceSelector, programButton, programInput, programPrimary } from './ProgramWorkbenchControls';
import { JobProgressView } from './JobProgressView';
import { AutoDLWorkbench } from './AutoDLWorkbench';
import {ProjectImagePicker} from './ProjectImagePicker';
import {projectSampleRow,replaceSampleRow} from './preparedSampleRows';

export function parseRotationRows(value: string): RotationRow[] {
  const lines = value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines.length) throw new Error('이미지별 정방향 보정각과 분할을 입력하세요.');
  return lines.map((line, index) => {
    const [image, angle, split, ...extra] = line.split('\t'); const correction = Number(angle);
    if (!image || !angle?.trim() || !Number.isFinite(correction) || correction < -180 || correction > 180 || !['train', 'val', 'test'].includes(split) || extra.length) {
      throw new Error(`${index + 1}행의 이미지·각도(-180~180)·train/val/test를 확인하세요.`);
    }
    return {image, correction_deg: correction, split: split as RotationRow['split']};
  });
}

export function RotationWorkbench() {
  const state = useProgramWorkbench('rotation');
  const [rowsText, setRowsText] = useState(''); const [sampleImage, setSampleImage] = useState('');
  const [angle, setAngle] = useState(0); const [split, setSplit] = useState<RotationRow['split']>('train');
  const [epochs, setEpochs] = useState(20); const [batch, setBatch] = useState(8); const [size, setSize] = useState(64); const [width, setWidth] = useState(16);
  const [rate, setRate] = useState(.001); const [device, setDevice] = useState<LocalTrainingDevice>('cpu');
  const [parents, setParents] = useState<ParentCandidate[]>([]); const [parent, setParent] = useState('');
  const [evaluation, setEvaluation] = useState<RotationEvaluation | null>(null); const [prediction, setPrediction] = useState<RotationPrediction | null>(null);
  const disabled = !!state.busy || state.active;
  useEffect(() => {setRowsText(''); setSampleImage(''); setEvaluation(null); setPrediction(null); setParents([]); setParent('');}, [state.scope]);
  useEffect(() => {
    let current = true; setParents([]); setParent('');
    if (state.dataset) void modelTrainingProgram.rotation.parents(state.dataset.dataset_path, size, width).then(result => {
      if (current && state.isCurrent()) setParents(result.parents);
    }).catch(cause => {if (current && state.isCurrent()) state.setError(cause.message);});
    return () => {current = false;};
  }, [state.dataset?.dataset_path, state.scope, size, width, state.job?.status]);
  const addRow = () => {
    if (!sampleImage) return;
    try {const row=projectSampleRow(state.source,sampleImage,String(angle),split);setRowsText(old=>replaceSampleRow(old,row));}
    catch(cause){state.setError(cause instanceof Error?cause.message:String(cause));}
  };
  const prepare = () => state.action('보정각 정답 준비', async () => {
    const rows = parseRotationRows(rowsText); const result = await modelTrainingProgram.rotation.prepare(state.source, rows);
    if (state.isCurrent()) {state.addDataset(result); setEvaluation(null); state.setNotice(`${result.sample_count}장과 보정각을 프로젝트에 저장했습니다.`);}
  });
  const selectedMetadata=state.models.find(row=>row.job_id===state.modelId)?.metadata;
  const selectedData=selectedMetadata?.training_provenance?.family_dataset_path||state.dataset?.dataset_path||selectedMetadata?.dataset_path;
  return <section className="rounded-xl border border-[#344255] bg-[#131D2B] p-5 text-xs text-slate-200">
    <TrainingPreparationPanel family="rotation" model="small_cnn_angle_v1" device={device} datasetPath={state.dataset?.dataset_path} warmStartJobId={parent||undefined} config={{epochs,batch_size:batch,image_size:size,width,learning_rate:rate}} />
    <h2 className="flex items-center gap-2 text-base font-semibold"><RotateCw className="h-5 w-5 text-cyan-300" />학습형 정방향 보정</h2>
    <p className="mt-2 leading-5 text-slate-400">각 이미지가 정방향이 되는 반시계 보정각을 정답으로 학습합니다. 360° 방향을 예측하고 원본 해상도를 보존한 정렬 이미지와 좌표 변환을 반환합니다.</p>
    <div className="mt-4 grid items-end gap-3 sm:grid-cols-4"><div className="sm:col-span-2"><ProjectImagePicker value={sampleImage} disabled={disabled} onSelect={row=>{setSampleImage(row.file_path);if(['train','val','test'].includes(row.split))setSplit(row.split as RotationRow['split']);}} /></div>
      <ProgramField label="정방향 반시계 보정각 · °"><input type="number" min={-180} max={180} value={angle} onChange={e => setAngle(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="이미지 분할"><select value={split} onChange={e => setSplit(e.target.value as RotationRow['split'])} className={programInput}><option value="train">train · 학습</option><option value="val">val · 선택</option><option value="test">test · 시험</option></select></ProgramField></div>
    <button type="button" className={`${programButton} mt-3`} disabled={!sampleImage || disabled || !Number.isFinite(angle)} onClick={addRow}>각도 정답 표에 추가</button>
    <details className="mt-3"><summary className="cursor-pointer text-sm text-slate-300">각도 정답 표·가져오기 상세</summary><ProgramField label="각도 정답 표 · 상대 이미지 경로 ↹ 보정각 ↹ train/val/test"><textarea rows={5} value={rowsText} onChange={e => setRowsText(e.target.value)} className={`${programInput} font-mono`} placeholder={'images/part_01.png\t-90\ttrain\nimages/part_02.png\t0\tval\nimages/part_03.png\t90\ttest'} /></ProgramField></details>
    <div className="mt-3 flex gap-3"><button type="button" onClick={() => void prepare()} disabled={!state.source || !rowsText.trim() || disabled} className={programButton}>원본과 보정각 준비</button>
      <button type="button" disabled={!state.dataset || disabled} onClick={() => void state.action('저장 정답 읽기', async () => {if (!state.dataset) return; const result = await modelTrainingProgram.rotation.manifest(state.dataset.dataset_path); if (state.isCurrent()) setRowsText(result.samples.map(row => `${row.image}\t${row.correction_deg}\t${row.split}`).join('\n'));})} className={programButton}>저장 정답 읽기</button></div>
    <div className="mt-4"><ProgramField label="프로젝트에 저장된 보정각 정답"><select value={state.dataset?.dataset_path || ''} onChange={e => state.selectDataset(e.target.value)} className={programInput} disabled={disabled}><option value="">준비 데이터 선택</option>{state.datasets.map((row, index) => <option key={row.dataset_path} value={row.dataset_path}>준비 {index + 1} · {row.sample_count}장 · train {row.provenance.split_counts?.train || 0} / val {row.provenance.split_counts?.val || 0} / test {row.provenance.split_counts?.test || 0}</option>)}</select></ProgramField></div>
    <div className="mt-5 grid gap-3 sm:grid-cols-3 lg:grid-cols-6"><ProgramField label="Epoch"><input type="number" min={1} max={500} value={epochs} onChange={e => setEpochs(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="배치 크기"><input type="number" min={1} max={128} value={batch} onChange={e => setBatch(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="모델 입력 크기"><input type="number" min={16} max={512} value={size} onChange={e => setSize(Number(e.target.value))} className={programInput} /></ProgramField>
      <ProgramField label="CNN 채널 폭"><select value={width} onChange={e => setWidth(Number(e.target.value))} className={programInput}><option value={8}>8</option><option value={16}>16</option><option value={32}>32</option></select></ProgramField>
      <ProgramField label="학습률"><input type="number" min={.000001} max={1} step={.001} value={rate} onChange={e => setRate(Number(e.target.value))} className={programInput} /></ProgramField><TrainingDeviceSelector value={device} onChange={setDevice} disabled={disabled} /></div>
    <div className="mt-3"><ProgramField label="동일 구조의 완료 부모 모델"><select value={parent} onChange={e => setParent(e.target.value)} className={programInput} disabled={disabled}><option value="">새 후보 학습</option>{parents.map((row, index) => <option key={row.job_id} value={row.job_id}>{parentCandidateLabel(row,index)}</option>)}</select></ProgramField></div>
    <p className="mt-1 text-slate-400">{parentCandidateNotice}</p>
    <button type="button" className={`${programPrimary} my-4`} disabled={!state.dataset || disabled || epochs < 1 || batch < 1 || rate <= 0} onClick={() => void state.action('보정 모델 학습 등록', async () => {
      if (!state.dataset) return; const row = await modelTrainingProgram.rotation.train({dataset_path: state.dataset.dataset_path, epochs, batch_size: batch, image_size: size, width, learning_rate: rate, device, ...(parent ? {warm_start_job_id: parent} : {})});
      if (state.isCurrent()) {state.setJob(row); setEvaluation(null); setPrediction(null);}
    })}>정방향 모델 후보 학습</button>
    <JobProgressView job={state.job} busy={!!state.busy} onCancel={() => void state.cancel()} onReconnect={() => void state.reconnect()} />
    <div className="mt-4 flex flex-wrap items-end gap-3"><div className="min-w-64 flex-1"><ProgramField label="완료 정방향 후보 모델"><select value={state.modelId} onChange={e => {state.setModelId(e.target.value); setEvaluation(null); setPrediction(null);}} className={programInput}><option value="">완료 모델 선택</option>{state.models.map((row, index) => <option key={row.job_id} value={row.job_id}>후보 {index + 1} · epoch {String(row.metadata.best_epoch || '?')}</option>)}</select></ProgramField></div>
      <button type="button" className={programButton} disabled={!state.modelId || !selectedData || disabled} onClick={() => void state.action('시험 각도 평가', async () => {const result = await modelTrainingProgram.rotation.evaluate(state.modelId, selectedData!, device); if (state.isCurrent()) setEvaluation(result);})}>시험 분할 평가</button>
      <button type="button" className={programButton} disabled={!state.modelId || !sampleImage || disabled} onClick={() => void state.action('정방향 예측', async () => {const result = await modelTrainingProgram.rotation.predict(state.modelId, sampleImage, device); if (state.isCurrent()) setPrediction(result);})}>선택 이미지 정렬</button>
      <button type="button" className={programButton} disabled={!state.modelId || disabled} onClick={() => void openModelFlow('rotation',state.modelId,selectedData)}>플로우에 연결</button>
      <button type="button" className={programButton} disabled={!state.modelId || disabled} onClick={() => void state.action('정방향 패키지 내보내기', async () => {const result = await modelTrainingProgram.rotation.export(state.modelId); if (state.isCurrent()) state.setNotice(`TorchScript 패키지: ${result.package_dir}`);})}>내보내기</button>
    </div>
    {evaluation && <div className="mt-4 rounded border border-[#344255] p-3">시험 {evaluation.sample_count}장 · 원형 각도 MAE {evaluation.angular_mae_deg.toFixed(2)}° · 10° 이내 {(evaluation.within_10_deg * 100).toFixed(1)}%</div>}
    {prediction && <div className="mt-4 rounded border border-[#344255] p-3"><p className="text-cyan-200">반시계 보정 {prediction.correction_deg.toFixed(2)}° · 출력 {prediction.output_size.join(' × ')}</p>{prediction.aligned_image_base64 && <img alt="모델이 예측한 정방향 정렬 결과" src={`data:image/png;base64,${prediction.aligned_image_base64}`} className="mt-3 max-h-96 max-w-full rounded object-contain" />}</div>}
    {state.busy && <p role="status" className="mt-3 text-cyan-300">{state.busy}…</p>}{state.notice && <p role="status" className="mt-3 break-words text-emerald-300">{state.notice}</p>}{state.error && <p role="alert" className="mt-3 rounded bg-rose-950/40 p-3 text-rose-200">{state.error}</p>}
    <div className="mt-5"><AutoDLWorkbench task="rotation" familyDatasetPath={state.dataset?.dataset_path} onComplete={()=>void state.refreshModels().catch(cause=>{if(state.isCurrent())state.setError(String(cause));})} /></div>
  </section>;
}
