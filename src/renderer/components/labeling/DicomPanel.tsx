import React,{useEffect,useState} from 'react';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useFoundationPromptStore} from '../../stores/useFoundationPromptStore';
import {foundationLabelingApi,type DicomView} from '../../services/foundationLabelingApi';
import {workflowError} from '../../services/datasetWorkflow';
export const DicomPanel:React.FC=()=>{
  const image=useAnnotationStore(s=>s.currentImage);const projectDir=useProjectStore(s=>s.projectDir);
  const [view,setView]=useState<DicomView|null>(null);const [center,setCenter]=useState('');const [width,setWidth]=useState('');const [frame,setFrame]=useState(0);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const dicom=/\.(dcm|dicom)$/i.test(image?.file_path||'');
  useEffect(()=>{setView(null);setCenter('');setWidth('');setFrame(0);setError('');},[image?.file_path,projectDir]);
  if(!dicom||!image)return null;
  const apply=async()=>{const source=image.file_path;setBusy(true);setError('');
    try{const next=await foundationLabelingApi.dicomView({image_path:source,...(center!==''?{window_center:Number(center)}:{}),...(width!==''?{window_width:Number(width)}:{}),frame_index:frame});
      if(useProjectStore.getState().projectDir===projectDir&&useAnnotationStore.getState().currentImage?.file_path===source){setView(next);setCenter(String(next.window_center));setWidth(String(next.window_width));useFoundationPromptStore.getState().setDisplaySource(source,next.display_url);}
    }catch(e){setError(workflowError(e));}finally{setBusy(false);}
  };
  return <details className="shrink-0 border-b border-slate-700 bg-[#101722] px-4 py-2 text-xs" aria-label="DICOM 입력 표시"><summary className="cursor-pointer text-cyan-200">DICOM window · 원본 좌표/해시</summary><div className="mt-2 flex flex-wrap items-center gap-2">
    <label>Window center<input aria-label="DICOM window center" type="number" value={center} onChange={e=>setCenter(e.target.value)} placeholder="파일 기본값" className="ml-1 w-28 rounded border border-slate-600 bg-slate-900 p-1"/></label>
    <label>Window width<input aria-label="DICOM window width" type="number" min={.001} value={width} onChange={e=>setWidth(e.target.value)} placeholder="파일 기본값" className="ml-1 w-28 rounded border border-slate-600 bg-slate-900 p-1"/></label>
    <label>Frame<input aria-label="DICOM frame index" type="number" min={0} max={view?view.frames-1:undefined} value={frame} onChange={e=>setFrame(Number(e.target.value))} className="ml-1 w-20 rounded border border-slate-600 bg-slate-900 p-1"/></label>
    <button disabled={busy} onClick={()=>void apply()} className="rounded border border-cyan-600 px-3 py-1.5">{busy?'표시 영상 생성 중…':'Window 적용·정보 조회'}</button>
  </div>{view&&<p className="mt-2 break-all text-[10px] text-slate-400">{view.width}×{view.height} · {view.bits_allocated} bit · {view.modality} · {view.photometric_interpretation} · pixel spacing {view.pixel_spacing_mm.join(' × ')} mm<br/>원본 SHA256 {view.source_sha256}<br/>표시 PNG SHA256 {view.view_sha256}</p>}
    <p className="mt-2 text-[10px] text-slate-400">Window는 원본 DICOM을 수정하지 않고 프로젝트 소유 PNG를 생성합니다. 원본 크기와 좌표를 유지하며 학습의 기본 window와 다른 표시를 선택하면 그 차이를 검토하세요. pydicom과 압축 형식 decoder가 없으면 필요한 의존성을 알려줍니다.</p>{error&&<p role="alert" className="mt-2 text-red-300">{error}</p>}</details>;
};
