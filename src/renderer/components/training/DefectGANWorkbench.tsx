import {useTrainingRuntime,TrainingRuntimeSettings} from './TrainingRuntimeSettings';
import {TrainingPreparationPanel} from './TrainingPreparationPanel';
import React, { useEffect, useRef, useState } from 'react';
import { Images, Loader2, RefreshCw } from 'lucide-react';
import { api, type DefectGANCandidate, type DefectGANModelSummary } from '../../services/api';
import { specializedApi } from '../../services/specializedApi';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import {useTaskHandoff} from './useTaskHandoff';
import {selectHandoffRecord} from './taskHandoff';
import {useSpecializedTraining} from './useSpecializedTraining';
import {WarmStartSelector} from './WarmStartSelector';
import {SpecializedTrainingStatus} from './SpecializedTrainingStatus';
import {TrainingDeviceSelector} from './ProgramWorkbenchControls';
import {AutoDLWorkbench} from './AutoDLWorkbench';
import {GANCompositionEditor} from './GANCompositionEditor';
import {GANDownstreamComparison} from './GANDownstreamComparison';
import {parseGANCrops,validateGANRegions} from './ganComposition';
import {ganWorkflow,type ExplicitGANRow,type GANRegion,type GANPreview} from '../../services/ganWorkflow';
import type {LocalTrainingDevice,PreparedDataset} from '../../services/modelTrainingProgram';
import {ProjectImagePicker} from './ProjectImagePicker';
import {annotationCrops,projectSampleRow} from './preparedSampleRows';

function errorText(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (cause && typeof cause === 'object' && 'message' in cause) return String(cause.message);
  return String(cause);
}

export const DefectGANWorkbench: React.FC = () => {
  const handoff=useTaskHandoff('defect_gan');
  const projectDir = useProjectStore((state) => state.projectDir);
  const projectSource=useProjectStore(state=>state.project?.source_dataset_dir ?? '');
  const activeLabelset=useProjectStore(state=>state.project?.active_labelset_id ?? 'default');
  const [datasetPath, setDatasetPath] = useState('');
  const [datasets,setDatasets]=useState<PreparedDataset[]>([]);
  const [device,setDevice]=useState<LocalTrainingDevice>('cpu');
  const [compose,setCompose]=useState(false);
  const [sourceImage,setSourceImage]=useState('');
  const [sourcePreview,setSourcePreview]=useState<GANPreview|null>(null);
  const [regions,setRegions]=useState<GANRegion[]>([]);
  const [compositionReceipt,setCompositionReceipt]=useState('');
  const [rowsText, setRowsText] = useState('');
  const [cropImage,setCropImage] = useState('');const selectedCrop=useRef('');
  const [cropRows,setCropRows] = useState<Array<{label:string;bbox:[number,number,number,number]}>>([]);
  const [cropIndex,setCropIndex] = useState(0);const [cropSplit,setCropSplit] = useState<'train'|'val'|'test'>('train');
  const [sampleCount, setSampleCount] = useState<number | null>(null);
  const [epochs, setEpochs] = useState(20);
  const [warmParentId, setWarmParentId] = useState('');
  const [count, setCount] = useState(4);
  const [seed, setSeed] = useState(0);
  const [models, setModels] = useState<DefectGANModelSummary[]>([]);
  const [jobId, setJobId] = useState('');
  const [candidates, setCandidates] = useState<DefectGANCandidate[]>([]);
  const [ganEvaluation,setGanEvaluation] = useState<{real_sample_count:number;generated_count:number;rgb_statistics_mmd:number;metric_backend:string;quality_status:string}|null>(null);
  const [generationPackage,setGenerationPackage] = useState('');
  const [adoptedPath,setAdoptedPath] = useState('');
  const [reviewer,setReviewer] = useState('');
  const [reviewReason,setReviewReason] = useState('');
  const [reviewLabel,setReviewLabel] = useState('');
  const [decisions,setDecisions] = useState<Record<string,'adopt'|'reject'>>({});
  const [reviewDir, setReviewDir] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'manifest' | 'train' | 'generate' | 'adopt' | 'evaluate' | 'export' | null>(null);

  const training=useSpecializedTraining('defect-gan',(completed)=>{
    void api.defectGAN.models().then(result=>{
      if(!sameProject())return;
      setModels(result.models);setJobId(completed.job_id);setNotice(`학습 완료 · ${completed.job_id.slice(0,8)}. 평가 후 후보를 검토하세요.`);
    }).catch(cause=>{if(sameProject())setError(cause.message ?? String(cause));});
  });

  useEffect(() => {
    setBusy(null);setDatasetPath('');setDatasets([]);setRowsText('');setSourceImage('');setSourcePreview(null);setCropImage('');selectedCrop.current='';setCropRows([]);setCropIndex(0);setRegions([]);setCompose(false);setCompositionReceipt('');
    setGenerationPackage('');setGanEvaluation(null);setAdoptedPath('');setDecisions({});setReviewer('');setReviewReason('');setReviewLabel('');setDatasetPath(''); setRowsText(''); setSampleCount(null);
    setModels([]); setJobId(''); setCandidates([]); setReviewDir(''); setNotice(''); setError('');
    if (!projectDir) return;
    let active = true;
    void Promise.all([ganWorkflow.datasets(),api.defectGAN.models()]).then(([prepared,result])=>{
      if(!active||!sameProject())return;setDatasets(prepared.datasets);setModels(result.models);
      const selected=handoff&&handoff.status!=='completed'?undefined:selectHandoffRecord(result.models,handoff);setJobId(selected?.job_id||'');
      const dataset=handoff?.datasetPath?prepared.datasets.find(row=>row.dataset_path===handoff.datasetPath):prepared.datasets.at(-1);
      if(handoff&&!dataset)throw new Error('선택 작업이 사용한 결함 생성 정답 버전을 찾지 못했습니다.');
      if(dataset){setDatasetPath(dataset.dataset_path);setSampleCount(dataset.sample_count||null);}
    }).catch(cause=>{if(active&&sameProject())setError(errorText(cause));});
    return () => { active = false; };
  }, [projectDir,projectSource,activeLabelset,handoff?.jobId,handoff?.selectionId]);

  const runtime=useTrainingRuntime(`${projectDir}\0${projectSource}\0${activeLabelset}`);

  const sameProject = () => {const state=useProjectStore.getState();return state.projectDir===projectDir && (state.project?.source_dataset_dir ?? '')===projectSource && (state.project?.active_labelset_id ?? 'default')===activeLabelset;};
  const loadManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await ganWorkflow.manifest(datasetPath.trim());
      if (!sameProject()) return;
      setRowsText(result.samples.map((row) => `${result.provenance?.source_map?.[row.image]?.source_relative_path||row.image}\t${row.bbox.join(',')}\t${row.split}${row.label?`\t${row.label}`:''}`).join('\n'));
      setSampleCount(result.sample_count);
      setNotice(`저장된 결함 영역 ${result.sample_count}개와 원본 이미지 해시를 확인했습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };
  const saveManifest = async () => {
    if (!projectSource || !projectDir || busy) return;
    let samples: ExplicitGANRow[];
    try { samples = parseGANCrops(rowsText); } catch (cause) { setError(errorText(cause)); return; }
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await ganWorkflow.prepare(projectSource, samples);
      if (!sameProject()) return;
      setDatasetPath(result.dataset_path);setDatasets(previous=>[...previous.filter(row=>row.dataset_path!==result.dataset_path),result]);setWarmParentId('');
      setSampleCount(result.sample_count);
      setNotice(`프로젝트 소유 복사본에 결함 영역 ${result.sample_count}개를 이미지 해시와 함께 저장했습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };
  const train = async () => {
    if (!datasetPath.trim() || !projectDir || !sampleCount || busy || runtime.error) return;
    setBusy('train'); setError(''); setNotice(''); setCandidates([]);
    try {
      await training.start(datasetPath.trim(),epochs,warmParentId || undefined,device,runtime.getOptions());
      if(sameProject())setNotice('학습 작업을 저장했습니다. 중지하거나 다시 열어 진행 상태를 확인할 수 있습니다.');
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };
  const generate = async () => {
    if (!projectDir || !jobId || busy) return;
    if(compose){const issue=sourcePreview?validateGANRegions(regions,sourcePreview.source_size):'원본 이미지를 읽고 영역을 지정하세요.';if(issue){setError(issue);return;}}
    setBusy('generate'); setError(''); setNotice(''); setCandidates([]); setReviewDir('');
    try {
      const result = await ganWorkflow.generate(jobId,count,seed,device,compose&&sourcePreview?{source_image_path:sourceImage,source_sha256:sourcePreview.source_sha256,regions}:undefined);
      if (!sameProject()) return;
      setCandidates(result.candidates); setReviewDir(result.review_dir);setCompositionReceipt(result.source_image_sha256?`원본 SHA ${result.source_image_sha256} · ${result.regions?.length||regions.length}영역 · 시드 ${seed}`:'');
      setNotice(`검토 대기 이미지 ${result.candidates.length}장을 만들었습니다. 학습 데이터에 자동으로 추가되지 않습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const openLastReview = async () => { setError('');try { const history=await specializedApi.ganReviews();const latest=history.reviews.find((r) => r.job_id===jobId);if(!latest)throw new Error('저장된 생성 후보가 없습니다.');const result=await ganWorkflow.openReview(latest.job_id,latest.review_id);if(sameProject()){setReviewDir(result.review_dir);setCandidates(result.candidates);setDecisions({});setAdoptedPath('');setCompositionReceipt(result.source_image_sha256?`원본 SHA ${result.source_image_sha256} · ${result.regions?.length||0}영역 · 시드 ${result.seed}`:'');} } catch(cause){if(sameProject())setError(errorText(cause));} };
  const evaluateGAN = async () => { setBusy('evaluate');setError('');try {const result=await specializedApi.evaluateGAN(jobId,datasetPath);if(sameProject())setGanEvaluation(result);} catch(cause){if(sameProject())setError(errorText(cause));}finally{if(sameProject())setBusy(null);} };
  const exportGAN = async () => { setBusy('export');setError('');try {const result=await specializedApi.exportGAN(jobId);if(sameProject())setGenerationPackage(result.package_path);} catch(cause){if(sameProject())setError(errorText(cause));}finally{if(sameProject())setBusy(null);} };
  const adopt = async () => {
    setBusy('adopt');setError('');
    try {
      const result=await specializedApi.adopt(jobId,reviewDir,Object.entries(decisions).map(([candidate_id,decision]) => ({candidate_id,decision,label:decision==='adopt'?reviewLabel:undefined,reviewer,reason:reviewReason})));
      if (!sameProject()) return;
      setAdoptedPath(result.dataset_path);setNotice(`검토 채택 ${result.adopted_count}장과 원본 ${result.real_image_count}장으로 학습 데이터가 준비되었습니다.`);setCandidates([]);
    } catch(cause) { if(sameProject()) setError(errorText(cause)); }
    finally { if(sameProject()) setBusy(null); }
  };
  return <details className="rounded-xl border border-[#344255] bg-[#141D2B] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 font-semibold text-slate-100">
      <Images className="h-4 w-4 text-violet-400" /> 결함 이미지 생성 실험 <span className="font-normal text-slate-400">학습된 GAN · 검토 대기 후보</span>
    </summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <TrainingPreparationPanel family="defect_gan" model="defect_gan" device={device} datasetPath={datasetPath||undefined} warmStartJobId={warmParentId||undefined} config={{epochs}} />
      <p className="leading-5 text-slate-400">실제 결함이 보이는 영역을 지정해 학습합니다. 생성 이미지는 원본 라벨이나 학습 분할에 자동으로 섞이지 않습니다.</p>
      <p className="break-all text-slate-400">원본 이미지 폴더: {projectSource||'프로젝트 원본 폴더를 선택하세요.'}</p>
      <label className="block">프로젝트 소유 학습 데이터<select value={datasetPath} onChange={event=>{setDatasetPath(event.target.value);setSampleCount(datasets.find(row=>row.dataset_path===event.target.value)?.sample_count||null);setWarmParentId('');}} className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] p-2"><option value="">아래 영역 표로 복사본 준비</option>{datasets.map((row,index)=><option value={row.dataset_path} key={row.dataset_path}>복사본 {index+1} · 영역 {row.sample_count||0}개</option>)}</select></label>
      <ProjectImagePicker value={cropImage} disabled={!!busy||training.active} label="실제 결함 원본 선택" onSelect={image=>{
        setCropImage(image.file_path);selectedCrop.current=image.file_path;setCropRows([]);setCropIndex(0);setError('');
        if(['train','val','test'].includes(image.split))setCropSplit(image.split as 'train'|'val'|'test');
        const expected=image.file_path;
        void api.annotations.get(image.image_id,image.file_path.slice(0,image.file_path.lastIndexOf('/')),image.file_path).then(result=>{
          if(!sameProject()||selectedCrop.current!==expected)return;
          setCropRows(annotationCrops(result.annotations || [],image.width || result.image_width || 0,image.height || result.image_height || 0));
        }).catch(cause=>{if(sameProject()&&selectedCrop.current===expected)setError(errorText(cause));});
      }} />
      <div className="grid gap-3 sm:grid-cols-2"><label className="text-sm">사람이 라벨링한 결함 영역<select value={cropIndex} onChange={event=>setCropIndex(Number(event.target.value))} className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] p-2">{!cropRows.length&&<option>bbox·polygon 정답이 없습니다. 2단계에서 지정하세요.</option>}{cropRows.map((row,index)=><option key={index} value={index}>{row.label} · {row.bbox.join(', ')}</option>)}</select></label><label className="text-sm">원본 단위 분할<select value={cropSplit} onChange={event=>setCropSplit(event.target.value as 'train'|'val'|'test')} className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] p-2"><option value="train">학습</option><option value="val">검증</option><option value="test">시험</option></select></label></div>
      <button type="button" className="rounded border border-cyan-800 p-2 text-cyan-100 disabled:opacity-40" disabled={!cropImage||!cropRows[cropIndex]||!!busy||training.active} onClick={()=>{try{const region=cropRows[cropIndex];const row=projectSampleRow(projectSource,cropImage,region.bbox.join(','),cropSplit)+'\t'+region.label;setRowsText(old=>[...old.split(/\r?\n/).filter(Boolean),row].join('\n'));setSampleCount(null);setNotice('원본 좌표·정답을 표에 추가했습니다. 영역 저장을 눌러 준비 데이터를 만드세요.');}catch(cause){setError(errorText(cause));}}}>선택 정답 영역을 학습 표에 추가</button>
      <p className="text-sm text-slate-400">지원하는 bbox·polygon 정답을 사용합니다. 마스크·회전 박스는 2단계에서 명시적인 crop 영역을 지정하거나 상세 표에서 좌표를 확인하세요.</p>
      <label className="block text-slate-300">결함 영역 표 · 이미지 상대 경로 ↹ x1,y1,x2,y2 ↹ train/val/test ↹ 선택 라벨
        <textarea value={rowsText} onChange={(event) => { setRowsText(event.target.value); setSampleCount(null); }} rows={4}
          placeholder={'images/defect_001.png\t10,20,74,84\ttrain\nimages/defect_002.png\t5,8,69,72\ttrain'}
          className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void loadManifest()} disabled={!datasetPath || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"><RefreshCw className="mr-1 inline h-3 w-3" />저장된 영역 읽기</button>
        <button type="button" onClick={() => void saveManifest()} disabled={!projectSource || !rowsText.trim() || (!!busy || training.active)} className="rounded border border-violet-700 bg-violet-950/40 px-3 py-1.5 text-violet-200 hover:bg-violet-900/40 disabled:opacity-40">원본을 보존하고 학습 복사본 준비</button>
        {sampleCount !== null && <span className="text-emerald-300">검증된 영역 {sampleCount}개</span>}
      </div>
      <TrainingDeviceSelector value={device} onChange={setDevice} disabled={!!busy||training.active}/>
      <SpecializedTrainingStatus {...training} />
      <TrainingRuntimeSettings {...runtime} disabled={!!busy||training.active} />
      <WarmStartSelector family="defect-gan" datasetPath={datasetPath} value={warmParentId} onChange={setWarmParentId} disabled={!!busy || training.active} refreshKey={training.job?.status === 'completed' ? training.job.job_id : null} />
      <label className="flex items-center gap-2"><input type="checkbox" checked={compose} disabled={!!busy||training.active} onChange={e=>setCompose(e.target.checked)}/>원본 영역에 결함 후보 합성</label>
      {compose&&<GANCompositionEditor source={projectSource} scope={`${projectDir}\0${projectSource}\0${activeLabelset}`} imagePath={sourceImage} onImageChange={setSourceImage} regions={regions} onRegionsChange={setRegions} onPreview={setSourcePreview} disabled={!!busy||training.active}/>}
      <div className="flex flex-wrap items-end gap-2 border-t border-[#344255] pt-4">
        <label>학습 epoch<input type="number" min="1" max="500" value={epochs} onChange={(event) => setEpochs(Math.max(1, Math.min(500, Number(event.target.value) || 1)))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void train()} disabled={!sampleCount || !!runtime.error || (!!busy || training.active)} className="rounded bg-violet-700 px-3 py-2 font-semibold hover:bg-violet-600 disabled:opacity-40">생성 모델 학습</button>
        <label className="min-w-[220px] flex-1">완료 후보 모델
          <select aria-label="완료된 GAN 생성 모델" disabled={!!busy||training.active} value={jobId} onChange={(event) => { setJobId(event.target.value); setCandidates([]); }} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5">
            {!models.length && <option value="">완료 모델 없음</option>}
            {models.map((model) => <option key={model.job_id} value={model.job_id}>{model.job_id.slice(0, 12)} · epoch {model.epochs} · 미검증</option>)}
          </select>
        </label>
        <label>생성 장수<input type="number" min="1" max="20" value={count} onChange={(event) => setCount(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
          className="mt-1 block w-16 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <label>생성 시드<input type="number" min="0" value={seed} onChange={(event) => setSeed(Math.max(0, Math.trunc(Number(event.target.value) || 0)))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void generate()} disabled={!jobId || (!!busy || training.active)} className="rounded border border-violet-600 px-3 py-2 text-violet-200 hover:bg-violet-950 disabled:opacity-40">후보 생성</button>
      </div>
      <div className="flex gap-2"><button disabled={!jobId || (!!busy || training.active)} onClick={() => void openLastReview()} className="rounded border border-violet-600 px-3 py-2 disabled:opacity-40">저장된 생성 후보 열기</button><button disabled={!jobId || !datasetPath || (!!busy || training.active)} onClick={() => void evaluateGAN()} className="rounded border border-violet-600 px-3 py-2 disabled:opacity-40">시험 크롭 생성 분포 비교</button><button disabled={!jobId || (!!busy || training.active)} onClick={() => void exportGAN()} className="rounded border border-violet-600 px-3 py-2 disabled:opacity-40">생성·검토 작업 패키지 내보내기</button></div>
      {ganEvaluation && <p className="text-slate-300">원본 시험 크롭 {ganEvaluation.real_sample_count}개 · 생성 {ganEvaluation.generated_count}개 · RGB 통계 MMD {ganEvaluation.rgb_statistics_mmd.toFixed(4)}<span className="block text-slate-500">색상 평균·편차의 분포 비교 자료입니다. 결함 형태의 품질 승인은 별도 검토가 필요합니다.</span></p>}
      {generationPackage && <p className="break-all rounded border border-violet-800 p-2">생성 작업 패키지: {generationPackage}<span className="block text-slate-500">generate.py에서 체크섬 검증 후 후보를 생성합니다. 검토 전에는 학습 데이터에 편입되지 않습니다.</span></p>}
      {busy && <p role="status" className="text-violet-300"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />{busy === 'train' ? 'GAN 학습 중' : '처리 중'}…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-2 text-rose-200">{error}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {compositionReceipt&&<p className="break-all font-mono text-[10px] text-violet-300">{compositionReceipt}</p>}
      <AutoDLWorkbench task="defect_gan" familyDatasetPath={datasetPath||undefined} onComplete={()=>{void api.defectGAN.models().then(result=>{if(sameProject()){setModels(result.models);setJobId((handoff&&handoff.status!=='completed'?undefined:selectHandoffRecord(result.models,handoff)?.job_id)||'');}});}}/>
      {!!candidates.length && <div className="space-y-2 rounded border border-[#344255] bg-[#0E1722] p-3">
        <div className="font-semibold text-slate-200">생성 후보 · 검토 대기</div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 md:grid-cols-6">
          {candidates.map((candidate) => <div key={candidate.id} className="rounded border border-slate-700 bg-[#141D2B] p-1.5">
            <img src={candidate.preview_data_url} alt={`결함 생성 후보 ${candidate.id}`} className="aspect-square w-full rounded object-contain" />
            <select aria-label={`후보 ${candidate.id} 검토`} disabled={candidate.status!=='synthetic_unreviewed'} value={decisions[candidate.id] || ''} onChange={(e) => setDecisions({...decisions,[candidate.id]:e.target.value as 'adopt'|'reject'})} className="w-full rounded bg-slate-800 p-1"><option value="">검토 대기</option><option value="adopt">학습 채택</option><option value="reject">반려</option></select>
            <div className="mt-1 truncate font-mono text-[10px] text-slate-400" title={candidate.path}>{candidate.id} · {candidate.status}</div>
          </div>)}
        </div>
        <div className="grid grid-cols-2 gap-2"><input aria-label="검토자" placeholder="검토자" value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="rounded bg-slate-800 p-2" /><input aria-label="채택 결함 클래스" placeholder="기존 결함 클래스 이름" value={reviewLabel} onChange={(e) => setReviewLabel(e.target.value)} className="rounded bg-slate-800 p-2" /><input aria-label="채택 검토 이유" placeholder="검토 이유" value={reviewReason} onChange={(e) => setReviewReason(e.target.value)} className="col-span-2 rounded bg-slate-800 p-2" /></div>
        <p className="text-slate-400">분류 프로젝트에서 검토한 결함 크롭을 학습 데이터로 채택합니다. 원본 검증·시험 이미지는 복사하고 생성 이미지는 학습 분할에만 추가합니다.</p>
        <button disabled={(!!busy || training.active) || !reviewer.trim() || reviewReason.trim().length<2 || !Object.keys(decisions).length || useProjectStore.getState().task!=='classification'} onClick={() => void adopt()} className="rounded bg-violet-700 px-3 py-2 disabled:opacity-40">검토 저장·학습 데이터 준비</button>
        <p className="break-all font-mono text-[10px] text-slate-400">검토 폴더: {reviewDir}</p>
      </div>}
      {adoptedPath && <div className="rounded border border-emerald-700 p-2"><p className="break-all">{adoptedPath}</p><button onClick={() => void useDatasetStore.getState().importFolder(adoptedPath,'classification').then(() => useProjectStore.getState().setStep(3))} className="mt-2 rounded bg-emerald-700 px-3 py-2">채택 데이터로 학습 준비</button></div>}
      <GANDownstreamComparison />
    </div>
  </details>;
};
