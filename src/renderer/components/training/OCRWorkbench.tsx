import {executeModelRecipe} from '../../services/modelExecution';
import {ModelExecutionEvidence} from './ModelExecutionEvidence';
import {useTrainingRuntime,TrainingRuntimeSettings} from './TrainingRuntimeSettings';
import {TrainingPreparationPanel} from './TrainingPreparationPanel';
import {openModelFlow} from './ProgramWorkbenchControls';
import React, { useEffect, useState } from 'react';
import { FileText, Loader2, RefreshCw } from 'lucide-react';
import { api, request, type OCREvaluation, type OCRLabelRow, type OCRModelSummary } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import {useTaskHandoff} from './useTaskHandoff';
import {selectHandoffRecord} from './taskHandoff';
import {useSpecializedTraining} from './useSpecializedTraining';
import {SpecializedTrainingStatus} from './SpecializedTrainingStatus';
import {WarmStartSelector} from './WarmStartSelector';
import {AutoDLWorkbench} from './AutoDLWorkbench';
import {TrainingDeviceSelector,programButton} from './ProgramWorkbenchControls';
import type {LocalTrainingDevice,PreparedDataset} from '../../services/modelTrainingProgram';
import {ProjectImagePicker} from './ProjectImagePicker';
import {projectSampleRow,replaceSampleRow,parseOCRRows as parseRows} from './preparedSampleRows';
import {ocrRecipePayload,ocrRecipeDraft,ocrTrainingOptions,formatOCRRate,type OCRMode,type OCRNormalizer} from './modelAdapterRecipes';

type OCRPrediction={text:string;confidence:number;model_sha256:string;mode:OCRMode;image_size:[number,number];
  regions:Array<{box:[number,number,number,number];text:string;confidence:number;line_index:number}>;
  text_rule_result:{passed:boolean;failed_rules:string[]};preview_data_url?:string};

function describeError(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (cause && typeof cause === 'object' && 'message' in cause) return String(cause.message);
  return String(cause);
}

export const OCRWorkbench: React.FC = () => {
  const handoff=useTaskHandoff('ocr');
  const projectDir = useProjectStore((state) => state.projectDir);
  const projectSource=useProjectStore(state=>state.project?.source_dataset_dir ?? '');
  const activeLabelset=useProjectStore(state=>state.project?.active_labelset_id ?? 'default');
  const [datasetPath, setDatasetPath] = useState('');
  const [datasets,setDatasets] = useState<PreparedDataset[]>([]);
  const [device,setDevice] = useState<LocalTrainingDevice>('cpu');
  const [rowsText, setRowsText] = useState('');
  const [selectedImage,setSelectedImage] = useState('');
  const [textTruth,setTextTruth] = useState('');
  const [truthSplit,setTruthSplit] = useState<'train'|'val'|'test'>('train');
  const [epochs, setEpochs] = useState(20);
  const [batchSize,setBatchSize]=useState('8');
  const [inputWidth,setInputWidth]=useState('128');
  const [learningRate,setLearningRate]=useState('0.001');
  const [warmParentId, setWarmParentId] = useState('');
  const [models, setModels] = useState<Array<OCRModelSummary & {metadata:OCRModelSummary['metadata'] & {dataset_path?:string}}>>([]);
  const [jobId, setJobId] = useState('');
  const [imagePath, setImagePath] = useState('');
  const [manifestCount, setManifestCount] = useState<number | null>(null);
  const [evaluation, setEvaluation] = useState<OCREvaluation | null>(null);
  const [prediction, setPrediction] = useState<OCRPrediction | null>(null);
  const [ocrMode,setOCRMode]=useState<OCRMode>('crop');
  const [charset,setCharset]=useState('');
  const [normalizer,setNormalizer]=useState<OCRNormalizer>('none');
  const [textRegex,setTextRegex]=useState('');
  const [minLength,setMinLength]=useState('');
  const [maxLength,setMaxLength]=useState('');
  const [allowedValues,setAllowedValues]=useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'manifest' | 'train' | 'evaluate' | 'predict' | null>(null);

  const restoreRecipe=(metadata:unknown)=>{
    const draft=ocrRecipeDraft((metadata as {recipe?:unknown}|undefined)?.recipe);
    setOCRMode(draft.mode);setCharset(draft.charset);setNormalizer(draft.normalizer);setTextRegex(draft.regex);
    setMinLength(draft.minLength);setMaxLength(draft.maxLength);setAllowedValues(draft.allowedValues);
    const config=(metadata as {training_config?:{batch_size?:number;image_width?:number;learning_rate?:number}}|undefined)?.training_config;
    setBatchSize(String(config?.batch_size??8));setInputWidth(String(config?.image_width??128));setLearningRate(String(config?.learning_rate??0.001));
  };
  let trainingOptions:ReturnType<typeof ocrTrainingOptions>|undefined,trainingError='';
  try{trainingOptions=ocrTrainingOptions(epochs,batchSize,inputWidth,learningRate);}catch(cause){trainingError=describeError(cause);}
  let recipe:ReturnType<typeof ocrRecipePayload>|undefined,recipeError='';
  try{recipe=ocrRecipePayload(ocrMode,charset,normalizer,textRegex,{minLength,maxLength,allowedValues});}
  catch(cause){recipeError=describeError(cause);}

  const training=useSpecializedTraining('ocr',(completed)=>{
    void api.ocr.models().then(result=>{
      if(!sameProject())return;
      setModels(result.models);setJobId(completed.job_id);setNotice(`학습 완료 · ${completed.job_id.slice(0,8)}. 평가 후 후보를 검토하세요.`);
    }).catch(cause=>{if(sameProject())setError(cause.message ?? String(cause));});
  });

  useEffect(() => {
    setBusy(null);setDatasetPath(projectSource);setDatasets([]);setRowsText('');setSelectedImage('');setTextTruth('');setImagePath('');
    setManifestCount(null);setModels([]);
    setJobId('');
    setEvaluation(null);
    setPrediction(null);
    restoreRecipe(undefined);
    if (!projectDir) return;
    let active = true;
    void Promise.all([request<{datasets:PreparedDataset[]}>('/api/ocr/datasets'),api.ocr.models()]).then(([prepared,result])=>{
      if(!active||!sameProject())return;setDatasets(prepared.datasets);setModels(result.models);
      const selected=handoff&&handoff.status!=='completed'?undefined:selectHandoffRecord(result.models,handoff);setJobId(selected?.job_id||'');
      if(selected)restoreRecipe(selected.metadata);
      const path=handoff?.datasetPath||(selected?.metadata as {dataset_path?:string}|undefined)?.dataset_path;
      const dataset=path?prepared.datasets.find(row=>row.dataset_path===path):prepared.datasets.at(-1);
      if(handoff&&!dataset)throw new Error('선택 작업이 사용한 문자 정답 버전을 찾지 못했습니다. 작업 센터에서 출처를 확인하세요.');
      if(dataset){setDatasetPath(dataset.dataset_path);setManifestCount(dataset.sample_count||null);}
    }).catch(cause=>{if(active&&sameProject())setError(describeError(cause));});
    return () => { active = false; };
  }, [projectDir,projectSource,activeLabelset,handoff?.jobId,handoff?.selectionId]);

  const runtime=useTrainingRuntime(`${projectDir}\0${projectSource}\0${activeLabelset}`);

  const sameProject = () => {const state=useProjectStore.getState();return state.projectDir===projectDir && (state.project?.source_dataset_dir ?? '')===projectSource && (state.project?.active_labelset_id ?? 'default')===activeLabelset;};

  const loadManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.ocr.manifest(datasetPath.trim());
      if (!sameProject()) return;
      setRowsText(result.samples.map((row) => `${row.image}\t${row.text}\t${row.split}`).join('\n'));
      setManifestCount(result.sample_count);
      setNotice(`${result.sample_count}개 문자 정답과 원본 해시를 확인했습니다.`);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const saveManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    let rows: OCRLabelRow[];
    try { rows = parseRows(rowsText); }
    catch (cause) { setError(describeError(cause)); return; }
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await request<PreparedDataset & {sample_count:number}>('/api/ocr/prepare',{method:'POST',body:JSON.stringify({source_dataset_path:projectSource,samples:rows})});
      if (!sameProject()) return;
      setManifestCount(result.sample_count);
      setDatasetPath(result.dataset_path);setDatasets(old=>[...old,result]);
      setNotice(`${result.sample_count}개 문자 정답을 이미지 해시와 함께 저장했습니다.`);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const train = async () => {
    if (!datasetPath.trim() || !projectDir || busy || !manifestCount || runtime.error || !recipe || !trainingOptions) return;
    setBusy('train'); setError(''); setNotice('');
    try {
      await training.start(datasetPath.trim(),epochs,warmParentId || undefined,device,{...trainingOptions,recipe,...runtime.getOptions()});
      if(sameProject())setNotice('학습 작업을 저장했습니다. 중지하거나 다시 열어 진행 상태를 확인할 수 있습니다.');
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const evaluate = async () => {
    if (!datasetPath.trim() || !jobId || busy) return;
    setBusy('evaluate'); setError(''); setEvaluation(null);
    try {
      const result = await executeModelRecipe<OCREvaluation>('ocr','evaluate',{job_id:jobId,dataset_path:datasetPath.trim(),device},()=>request('/api/ocr/evaluate',{method:'POST',body:JSON.stringify({job_id:jobId,dataset_path:datasetPath.trim(),device})}));
      if (sameProject()) setEvaluation(result);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const predict = async () => {
    if (!imagePath.trim() || !jobId || busy || !recipe) return;
    setBusy('predict'); setError(''); setPrediction(null);
    try {
      const result = await executeModelRecipe<OCRPrediction>('ocr','predict',{job_id:jobId,image_path:imagePath.trim(),device,include_preview:true,recipe},()=>request('/api/ocr/predict',{method:'POST',body:JSON.stringify({job_id:jobId,image_path:imagePath.trim(),device,include_preview:true,recipe})}));
      if (sameProject()) setPrediction(result);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  return <details open className="rounded-xl border border-[#344255] bg-[#141D2B] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 font-semibold text-slate-100">
      <FileText className="h-4 w-4 text-cyan-400" /> 문자 인식 모델 실험 <span className="font-normal text-slate-400">단일 행 인식 · 수평 다중 행 영역 제안</span>
    </summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <TrainingPreparationPanel family="ocr" model="ctc" device={device} datasetPath={datasetPath||undefined} warmStartJobId={warmParentId||undefined} config={{...trainingOptions,recipe}} />
      <ModelExecutionEvidence task="ocr" jobId={jobId} />
      <p className="leading-5 text-slate-400">문자가 한 줄로 잘린 이미지와 실제 정답 문자열이 필요합니다. 후보 모델은 자동으로 검사 플로우에 적용되지 않습니다.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label>인식 방식<select aria-label="OCR 인식 방식" value={ocrMode} onChange={event=>setOCRMode(event.target.value as OCRMode)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2"><option value="crop">잘린 단일 행 인식 (기존)</option><option value="detect_recognize">수평 문자 영역 제안 후 다중 행 인식</option></select></label>
        <label>허용 문자 집합 (비우면 학습 문자)<input value={charset} onChange={event=>setCharset(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2" /></label>
        <label>문자 정규화<select value={normalizer} onChange={event=>setNormalizer(event.target.value as OCRNormalizer)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2"><option value="none">원문 유지</option><option value="strip">앞뒤 공백 제거</option><option value="nfkc">Unicode NFKC</option><option value="nfkc_strip">NFKC · 앞뒤 공백 제거</option></select></label>
        <label>전체 문자열 정규식 규칙 (선택)<input value={textRegex} onChange={event=>setTextRegex(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2" /></label>
        <label>최소 문자 수 (선택)<input aria-label="OCR 최소 문자 수" type="number" min="0" max="10000" step="1" value={minLength} onChange={event=>setMinLength(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2" /></label>
        <label>최대 문자 수 (선택)<input aria-label="OCR 최대 문자 수" type="number" min="0" max="10000" step="1" value={maxLength} onChange={event=>setMaxLength(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2" /></label>
        <label className="sm:col-span-2">허용 문자열 (한 줄에 하나 · 앞뒤 공백도 정답에 포함)<textarea aria-label="OCR 허용 문자열 (한 줄에 하나)" value={allowedValues} onChange={event=>setAllowedValues(event.target.value)} rows={3} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] p-2" /></label>
      </div>
      {(recipeError||trainingError)&&<p role="alert" className="text-rose-200">{recipeError||trainingError}</p>}
      <p className="text-slate-500">영역 제안은 고전 영상 처리이며 단순 배경의 수평 문자에 한정됩니다. 세로 문자와 학습 기반 장면 검출은 지원하지 않습니다. 학습 정답은 비어 있지 않은 단일 행 crop이며, 한글·숫자는 학습 문자 집합에 있어야 합니다. 빈 영상은 영역 제안 모드에서 빈 결과를 반환합니다. 문자 규칙은 다중 행을 합친 전체 문자열에 적용합니다.</p>
      {datasets.length>0&&<label className="block text-slate-300">프로젝트에 저장된 문자 정답<select value={datasetPath} onChange={event=>{const row=datasets.find(item=>item.dataset_path===event.target.value);setDatasetPath(event.target.value);setManifestCount(row?.sample_count||null);}} className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2">{datasets.map((row,index)=><option key={row.dataset_path} value={row.dataset_path}>정답 {index+1} · {row.sample_count}장</option>)}</select></label>}
      <ProjectImagePicker value={selectedImage} disabled={!!busy||training.active} onSelect={image=>{setSelectedImage(image.file_path);setImagePath(image.file_path);if(['train','val','test'].includes(image.split))setTruthSplit(image.split as 'train'|'val'|'test');}} label="문자 원본 이미지 선택" />
      <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm text-slate-200">사람이 확인한 정답 문자열<input aria-label="OCR 실제 정답 문자열" value={textTruth} onChange={event=>setTextTruth(event.target.value)} disabled={!!busy||training.active} className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] p-2" /></label><label className="block text-sm text-slate-200">독립 이미지 분할<select value={truthSplit} onChange={event=>setTruthSplit(event.target.value as 'train'|'val'|'test')} className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] p-2"><option value="train">학습</option><option value="val">검증</option><option value="test">시험</option></select></label></div>
      <button type="button" className={programButton} disabled={!selectedImage||!textTruth.trim()||!!busy||training.active} onClick={()=>{try{const row=projectSampleRow(projectSource,selectedImage,textTruth,truthSplit);setRowsText(old=>replaceSampleRow(old,row));setManifestCount(null);setNotice('문자 정답을 표에 추가했습니다. 정답 저장을 눌러 준비 데이터를 만들세요.');}catch(cause){setError(describeError(cause));}}}>선택 이미지의 문자 정답 추가</button>
      <details className="rounded border border-slate-700 p-3"><summary className="cursor-pointer text-sm text-slate-300">정답 표·가져오기 상세 설정</summary>
      <label className="mt-3 block text-slate-300">문자 이미지 폴더 경로
        <input value={datasetPath} onChange={(event) => { setDatasetPath(event.target.value); setManifestCount(null); }}
          placeholder="/path/to/text-crops" className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <label className="block text-slate-300">정답 표 · 한 줄에 이미지 상대 경로 ↹ 정답 문자열 ↹ train/val/test
        <textarea value={rowsText} onChange={(event) => { setRowsText(event.target.value); setManifestCount(null); }} rows={5}
          placeholder={'images/part_001.png\tABC123\ttrain\nimages/part_002.png\tABC124\tval\nimages/part_003.png\tABC125\ttest'}
          className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      </details>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void loadManifest()} disabled={!datasetPath || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"><RefreshCw className="mr-1 inline h-3 w-3" />저장된 정답 읽기</button>
        <button type="button" onClick={() => void saveManifest()} disabled={!datasetPath || !rowsText.trim() || (!!busy || training.active)} className="rounded border border-cyan-700 bg-cyan-950/40 px-3 py-1.5 text-cyan-200 hover:bg-cyan-900/40 disabled:opacity-40">정답과 이미지 해시 저장</button>
        {manifestCount !== null && <span className="text-emerald-300">검증된 정답 {manifestCount}개</span>}
      </div>
      <SpecializedTrainingStatus {...training} />
      <TrainingRuntimeSettings {...runtime} disabled={!!busy||training.active} />
      <WarmStartSelector family="ocr" datasetPath={datasetPath} value={warmParentId} onChange={setWarmParentId} disabled={!!busy || training.active} refreshKey={training.job?.status === 'completed' ? training.job.job_id : null} />
      <div className="flex flex-wrap items-end gap-2 border-t border-[#344255] pt-4">
        <TrainingDeviceSelector value={device} onChange={setDevice} disabled={!!busy||training.active}/>
        <label>학습 epoch<input type="number" min="1" max="500" value={epochs} onChange={(event) => setEpochs(Math.max(1, Math.min(500, Number(event.target.value) || 1)))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <label>Batch<input aria-label="OCR batch 크기" type="number" min="1" max="256" step="1" value={batchSize} onChange={event=>setBatchSize(event.target.value)} className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <label>입력 폭 px (높이 32)<input aria-label="OCR 모델 입력 폭" type="number" min="8" max="4096" step="1" value={inputWidth} onChange={event=>setInputWidth(event.target.value)} className="mt-1 block w-24 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <label>학습률<input aria-label="OCR 학습률" type="number" min="0.000001" max="1" step="0.001" value={learningRate} onChange={event=>setLearningRate(event.target.value)} className="mt-1 block w-24 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void train()} disabled={!manifestCount || !!runtime.error || !!recipeError || !!trainingError || (!!busy || training.active)} className="rounded bg-cyan-700 px-3 py-2 font-semibold hover:bg-cyan-600 disabled:opacity-40">OCR 후보 학습</button>
        <label className="min-w-[220px] flex-1">완료 후보 모델
          <select value={jobId} onChange={(event) => {setJobId(event.target.value);const selected=models.find(row=>row.job_id===event.target.value);const path=selected?.metadata.dataset_path;if(path)setDatasetPath(path);restoreRecipe(selected?.metadata);setPrediction(null);setEvaluation(null);}} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5">
            {!models.length && <option value="">완료 모델 없음</option>}
            {models.map((item) => <option key={item.job_id} value={item.job_id}>{item.job_id.slice(0, 12)} · epoch {item.metadata.best_epoch || '?'}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => void evaluate()} disabled={!jobId || !datasetPath || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-2 hover:bg-slate-700 disabled:opacity-40">시험 분할 평가</button>
        <button type="button" disabled={!jobId||!!busy||training.active} onClick={()=>void openModelFlow('ocr',jobId,datasetPath)} className={programButton}>검사 플로우·배포 패키지</button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <span className="text-sm text-slate-300">위에서 선택한 프로젝트 이미지로 시험합니다.</span>
        <details className="min-w-[260px] flex-1"><summary className="cursor-pointer text-slate-400">시험 이미지 경로 상세</summary><label>한 장 시험 이미지 경로
          <input value={imagePath} onChange={(event) => setImagePath(event.target.value)} placeholder="/path/to/text-crops/test.png"
            className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-1.5 font-mono" />
        </label></details>
        <button type="button" onClick={() => void predict()} disabled={!jobId || !imagePath || !!recipeError || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-2 hover:bg-slate-700 disabled:opacity-40">문자 읽기</button>
      </div>
      {busy && <p role="status" className="text-cyan-300"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />{busy === 'train' ? '학습 중' : '처리 중'}…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-2 text-rose-200">{error}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {evaluation && <div className="rounded border border-[#344255] bg-[#0E1722] p-3">
        시험 {evaluation.sample_count}장 · 정확히 일치 {formatOCRRate(evaluation.exact_match_accuracy)} · 문자 오류율 {formatOCRRate(evaluation.character_error_rate)} · 단어 오류율 {formatOCRRate((evaluation as OCREvaluation & {word_error_rate?:number}).word_error_rate)}
        <div className="mt-2 max-h-32 overflow-y-auto font-mono text-slate-400">{evaluation.samples.map((item) => <div key={item.image} className="truncate">{item.image}: {item.reference_text} → {item.predicted_text}</div>)}</div>
      </div>}
      {prediction && <div className="rounded border border-cyan-700 bg-cyan-950/30 p-3"><p>인식 후보: <strong className="whitespace-pre-wrap text-cyan-200">{prediction.text || '(빈 문자열)'}</strong> · 후보 점수 {(prediction.confidence * 100).toFixed(1)}%</p>
        {prediction.text_rule_result&&<p className="mt-2">문자 규칙: {prediction.text_rule_result.passed?'통과':prediction.text_rule_result.failed_rules.join(', ')}</p>}
        {prediction.image_size&&<svg aria-label="OCR 원본 좌표 영역" viewBox={`0 0 ${prediction.image_size[0]} ${prediction.image_size[1]}`} className="mt-2 max-h-64 w-full bg-slate-900">{prediction.preview_data_url&&<image href={prediction.preview_data_url} width={prediction.image_size[0]} height={prediction.image_size[1]}/>}{prediction.regions?.map((region,index)=><rect key={index} x={region.box[0]} y={region.box[1]} width={region.box[2]-region.box[0]} height={region.box[3]-region.box[1]} fill="none" stroke="#22d3ee" strokeWidth={Math.max(1,prediction.image_size[0]/500)}/>)}</svg>}
        {prediction.regions?.map((region,index)=><p key={index} className="mt-1 font-mono">행 {region.line_index+1} · [{region.box.join(', ')}] · {region.text||'(빈 문자열)'}</p>)}
      </div>}
      <AutoDLWorkbench task="ocr" familyDatasetPath={datasets.some(row=>row.dataset_path===datasetPath)?datasetPath:undefined} onComplete={()=>void api.ocr.models().then(result=>{if(sameProject()){setModels(result.models);setJobId((handoff&&handoff.status!=='completed'?undefined:selectHandoffRecord(result.models,handoff)?.job_id)||'');}}).catch(cause=>{if(sameProject())setError(describeError(cause));})}/>
    </div>
  </details>;
};
