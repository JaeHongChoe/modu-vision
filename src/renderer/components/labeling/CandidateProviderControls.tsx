import React,{useEffect,useState} from 'react';
import {datasetWorkflow,workflowError,type CandidateProposal,type SemanticReadiness} from '../../services/datasetWorkflow';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useModelAssistRunStore} from '../../stores/useModelAssistRunStore';
interface Props{disabled:boolean;onCreated:(proposal:CandidateProposal)=>void}
export const CandidateProviderControls:React.FC<Props>=({disabled,onCreated})=>{
  const {currentImage,annotations,selectedAnnotationId}=useAnnotationStore();const {images}=useDatasetStore();const projectDir=useProjectStore(s=>s.projectDir);
  const [backend,setBackend]=useState<'grounding_dino'|'template_match'>('grounding_dino');const [readiness,setReadiness]=useState<SemanticReadiness|null>(null);const [prompt,setPrompt]=useState('ceramic part. scratch. crack.');const [exemplar,setExemplar]=useState('');const [roi,setRoi]=useState('');const [label,setLabel]=useState('defect');const [threshold,setThreshold]=useState(.3);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{let active=true;void datasetWorkflow.semanticSetup().then(r=>{if(active)setReadiness(r);}).catch(e=>{if(active)setError(workflowError(e));});return()=>{active=false;};},[projectDir]);
  const setup=async()=>{setBusy(true);setError('');try{const path=await window.api?.selectFolder({title:'로컬 텍스트 검출 모델 폴더'});if(path)setReadiness(await datasetWorkflow.setSemanticModel(path));}catch(e){setError(workflowError(e));}finally{setBusy(false);}};
  const generate=async()=>{
    if(!currentImage)return;const started=currentImage.file_path;setBusy(true);setError('');useModelAssistRunStore.getState().begin();
    try{
      const values=roi.trim()?roi.split(',').map(v=>Number(v.trim())):undefined;
      if(values&&(values.length!==4||values.some(v=>!Number.isFinite(v))))throw new Error('예시 영역을 x1, y1, x2, y2 네 숫자로 입력하세요.');
      const result=await datasetWorkflow.generateCandidates({backend,image_path:currentImage.file_path,prompt,label,threshold,text_threshold:.25,exemplar_path:exemplar,exemplar_roi:values});
      if(useProjectStore.getState().projectDir===projectDir&&useAnnotationStore.getState().currentImage?.file_path===started)onCreated(result);
    }catch(e){setError(workflowError(e));}finally{useModelAssistRunStore.getState().end();setBusy(false);}
  };
  return <section className="space-y-2 rounded-lg border border-indigo-800 bg-[#101722] p-3" aria-label="텍스트와 예시 이미지 후보">
    <h4 className="font-semibold text-indigo-100">텍스트·예시 이미지로 영역 후보 생성</h4>
    <div className="flex gap-2"><button onClick={()=>{setBackend('grounding_dino');setThreshold(.3);}} className={`rounded border px-2 py-1 ${backend==='grounding_dino'?'border-indigo-400 text-indigo-100':'border-slate-600 text-slate-400'}`}>텍스트 검출</button><button onClick={()=>{setBackend('template_match');setThreshold(.8);}} className={`rounded border px-2 py-1 ${backend==='template_match'?'border-indigo-400 text-indigo-100':'border-slate-600 text-slate-400'}`}>예시 이미지</button></div>
    {backend==='grounding_dino'?<><label className="block">영문 대상 설명<input aria-label="텍스트 검출 프롬프트" value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="scratch. crack. ceramic part." className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2"/></label>
      <div className="flex items-center gap-2"><button disabled={busy} onClick={()=>void setup()} className="rounded border border-indigo-600 px-2 py-1">로컬 모델 선택</button><span className={readiness?.ready?'text-emerald-300':'text-amber-300'}>{readiness?.ready?'실행 준비됨':'설정 필요'}</span></div>
      <p className="break-all text-[10px] text-slate-500">{readiness?.model_dir||'모델 폴더 미설정'}</p>{readiness?.error&&<p className="text-[11px] text-amber-300">{readiness.error}</p>}
      <p className="text-[10px] leading-relaxed text-slate-400">Grounding DINO · 로컬 모델을 사용한 실제 CPU 검출. 영문 구체 명사·짧은 구문을 마침표로 구분하세요. 미세 결함과 한국어 설명의 검출 품질은 보장되지 않습니다. 결과는 반드시 검토하세요.</p>
    </>:<><label className="block">예시 이미지<select aria-label="예시 이미지 선택" value={exemplar} onChange={e=>setExemplar(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2"><option value="">현재 데이터에서 선택</option>{images.map(i=><option key={i.file_path} value={i.file_path}>{i.file_name}</option>)}</select></label>
      <label className="block">예시 영역 (선택 사항)<input aria-label="예시 이미지 영역" value={roi} onChange={e=>setRoi(e.target.value)} placeholder="x1, y1, x2, y2 · 비우면 이미지 전체" className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2"/></label>
      <button disabled={!annotations.find(a=>a.id===selectedAnnotationId)?.bbox} onClick={()=>{const a=annotations.find(a=>a.id===selectedAnnotationId);if(a?.bbox&&currentImage){setExemplar(currentImage.file_path);setRoi(a.bbox.join(', '));setLabel(a.label);}}} className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40">선택한 라벨 박스를 예시로 사용</button>
      <label className="block">후보 클래스<input aria-label="예시 후보 클래스" value={label} onChange={e=>setLabel(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2"/></label>
      <p className="text-[10px] leading-relaxed text-slate-400">OpenCV 템플릿 매칭 · 예시와 크기·방향이 같은 무늬를 찾습니다. 질감 없는 예시는 중지합니다. 대상 의미를 추측하지 않습니다.</p></>}
    <label className="block">후보 임계값 {threshold.toFixed(2)}<input type="range" min={.05} max={.99} step={.01} value={threshold} onChange={e=>setThreshold(Number(e.target.value))} className="mt-1 w-full accent-indigo-500"/></label>
    <button disabled={disabled||busy||!currentImage||(backend==='grounding_dino'?!readiness?.ready:!exemplar)} onClick={()=>void generate()} className="w-full rounded bg-indigo-700 px-3 py-2 font-semibold disabled:opacity-40">{busy?'실제 추론 중…':'영역 후보 생성'}</button>
    {error&&<p role="alert" className="rounded border border-red-800 p-2 text-red-200">{error}</p>}
  </section>;
};
