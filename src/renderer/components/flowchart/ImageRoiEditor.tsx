import React,{useEffect,useRef,useState} from 'react';
import {request,resolveApiUrl} from '../../services/api';
import {roiFromDrag,moveRoi} from './flowWorkspace';

export function ImageRoiEditor({imagePath,preview,sourceSize,roi,onChange,onEditingChange,disabled=false}:{imagePath?:string;preview?:string;sourceSize?:number[];roi:number[];onChange:(roi:number[])=>void;onEditingChange?:(editing:boolean)=>void;disabled?:boolean}) {
  const [size,setSize]=useState<number[]|null>(sourceSize||null);const [error,setError]=useState('');const [draft,setDraft]=useState(roi);const start=useRef<number[]|null>(null);
  useEffect(()=>setDraft(roi),[roi.join(',')]);
  useEffect(()=>()=>{start.current=null;onEditingChange?.(false);},[imagePath,onEditingChange]);
  useEffect(()=>{let active=true;setSize(sourceSize||null);setError('');if(imagePath)request<{width:number;height:number}>(`/api/flow-workspace/image-info?image_path=${encodeURIComponent(imagePath)}`).then(v=>{if(active)setSize([v.width,v.height]);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[imagePath,sourceSize?.join(",")]);
  if((!imagePath&&!sourceSize)||!preview)return <p className="text-slate-400">상단에서 검사 이미지를 선택하면 원본 위에 ROI를 그릴 수 있습니다.</p>;
  if(error)return <p role="alert" className="text-amber-300">{error}</p>;
  if(!size)return <p role="status">원본 이미지 크기 확인 중…</p>;
  const [w,h]=size;const [x1,y1,x2,y2]=draft;const src=resolveApiUrl(preview);
  if(w<16||h<16)return <p role="alert" className="text-amber-300">원본 {w}×{h}: ROI 편집에는 최소 16×16 픽셀 이미지가 필요합니다.</p>;
  const point=(e:React.PointerEvent<SVGSVGElement>)=>{const p=e.currentTarget.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const matrix=e.currentTarget.getScreenCTM();if(!matrix)return [0,0];const v=p.matrixTransform(matrix.inverse());return [v.x,v.y];};
  return <div className="space-y-2">
    <p className="text-xs text-slate-300">원본 {w}×{h} · 끌어서 ROI 지정 · 방향키로 이동 · Shift+방향키로 크기 조절</p>
    <svg role="application" aria-label="원본 이미지 ROI 편집" tabIndex={disabled?-1:0} viewBox={`0 0 ${w} ${h}`} style={{aspectRatio:`${w}/${h}`,maxHeight:240,width:'100%',touchAction:'none'}} className="rounded bg-slate-950 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-300 cursor-crosshair"
      onPointerDown={e=>{if(disabled)return;start.current=point(e);onEditingChange?.(true);e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={e=>{if(start.current)setDraft(roiFromDrag(start.current,point(e),size));}}
      onPointerUp={e=>{if(!start.current)return;const box=roiFromDrag(start.current,point(e),size);start.current=null;setDraft(box);onChange(box);onEditingChange?.(false);}}
      onPointerCancel={()=>{start.current=null;setDraft(roi);onEditingChange?.(false);}}
      onKeyDown={e=>{if(disabled||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();const dx=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0;const dy=e.key==='ArrowUp'?-1:e.key==='ArrowDown'?1:0;const box=e.shiftKey?roiFromDrag([x1,y1],[x2+dx,y2+dy],size):moveRoi(draft,dx,dy,size);setDraft(box);onChange(box);}}>
      <image href={src} width={w} height={h} preserveAspectRatio="none"/>
      <rect x={x1} y={y1} width={Math.max(0,x2-x1)} height={Math.max(0,y2-y1)} fill="#38bdf822" stroke="#38bdf8" strokeWidth={Math.max(w,h)/220}/>
    </svg>
    <span className="text-xs text-sky-200">잘린 영역 미리보기 · {Math.max(0,x2-x1)}×{Math.max(0,y2-y1)} px</span>
    {x2>x1&&y2>y1&&<svg aria-label="ROI 잘림 미리보기" viewBox={`${x1} ${y1} ${x2-x1} ${y2-y1}`} style={{maxHeight:140,width:'100%',aspectRatio:`${x2-x1}/${y2-y1}`}} className="rounded bg-slate-950"><image href={src} width={w} height={h}/></svg>}
  </div>;
}
