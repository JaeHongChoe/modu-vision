import {useEffect, useRef, useState} from 'react';
import {boundedZoom, canBlend, safeSnapshot, validBox, type EvidenceView} from './evidenceViewer';

/** Snapshot-only viewer. Editing remains in the revision/lease-aware labeling workspace. */
export function EvidenceImageViewer({evidence, onClose, returnLabel='원래 작업으로 돌아가기'}: {evidence: EvidenceView; onClose:()=>void; returnLabel?:string}) {
  const [layerId,setLayerId]=useState('');const [overlayId,setOverlayId]=useState('');
  const [zoom,setZoom]=useState(1);const [pan,setPan]=useState({x:0,y:0});
  const [opacity,setOpacity]=useState(.5);const [labels,setLabels]=useState(true);
  const root=useRef<HTMLDivElement>(null);const drag=useRef<{x:number;y:number;pan:{x:number;y:number}}|null>(null);
  const layers=evidence.layers.filter(layer=>safeSnapshot(layer.image));
  const selected=layers.find(layer=>layer.id===layerId)||layers[0];
  const overlays=selected?layers.filter(layer=>canBlend(selected,layer)):[];
  const overlay=overlays.find(layer=>layer.id===overlayId);
  const fit=()=>{setZoom(1);setPan({x:0,y:0});};
  useEffect(()=>{setLayerId('');setOverlayId('');setOpacity(.5);setLabels(true);fit();},[evidence.key]);
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;root.current?.querySelector<HTMLButtonElement>('button')?.focus();return()=>{if(previous?.isConnected)previous.focus();};},[]);
  const boxes=selected?evidence.boxes?.filter(row=>row.space===selected.space&&validBox(row.box,selected.size))||[]:[];
  return <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/80 p-5" onPointerDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <div ref={root} role="dialog" aria-modal="true" aria-label="이미지 판정 근거 보기" className="flex max-h-[95vh] w-[min(1150px,95vw)] flex-col rounded-xl border border-slate-500 bg-[#111B28] p-4 text-xs text-slate-200" onKeyDown={event=>{
      event.stopPropagation();
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose();return;}
      if(event.key==='Tab') {const buttons=Array.from(root.current?.querySelectorAll<HTMLElement>('button:not([disabled]),select:not([disabled]),input:not([disabled]),[tabindex="0"]')||[]);const first=buttons[0],last=buttons.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}return;}
      if(['INPUT','SELECT'].includes((event.target as HTMLElement).tagName))return;
      const shift={ArrowLeft:[-30,0],ArrowRight:[30,0],ArrowUp:[0,-30],ArrowDown:[0,30]}[event.key];
      if(shift){event.preventDefault();setPan(p=>({x:p.x+shift[0],y:p.y+shift[1]}));}
      else if(event.key==='+'||event.key==='='){event.preventDefault();setZoom(z=>boundedZoom(z*1.25));}
      else if(event.key==='-'){event.preventDefault();setZoom(z=>boundedZoom(z/1.25));}
      else if(event.key==='0'){event.preventDefault();fit();}
    }}>
      <header className="flex items-center justify-between gap-3"><h3 className="font-semibold">{evidence.title} · 읽기 전용</h3><button type="button" onClick={onClose} className="rounded border border-slate-500 px-3 py-2">{returnLabel}</button></header>
      <p className="mt-2 break-all text-slate-400">{evidence.imagePath||'저장된 중간 이미지'} · 실행 {evidence.runId||'현재 미저장 실행'} · 버전 {evidence.versionId||'기록 없음'}{evidence.nodeId&&` · 노드 ${evidence.nodeId}`}{evidence.roiId&&` · ROI ${evidence.roiId}`}</p>
      {evidence.imageSha256&&<p className="break-all text-[10px] text-slate-400">원본 SHA-256 {evidence.imageSha256}</p>}
      {evidence.warning&&<p role="alert" className="mt-2 text-amber-300">{evidence.warning}</p>}
      <div className="my-3 flex flex-wrap items-center gap-2">
        <label>표시 이미지 <select aria-label="근거 이미지 종류" value={selected?.id||''} onChange={event=>{setLayerId(event.target.value);setOverlayId('');fit();}} className="rounded bg-slate-800 p-2">{layers.map(layer=><option key={layer.id} value={layer.id}>{layer.label}</option>)}</select></label>
        <button type="button" aria-label="근거 이미지 축소" onClick={()=>setZoom(z=>boundedZoom(z/1.25))} className="rounded border border-slate-600 p-2">−</button>
        <span role="status">{Math.round(zoom*100)}%</span><button type="button" aria-label="근거 이미지 확대" onClick={()=>setZoom(z=>boundedZoom(z*1.25))} className="rounded border border-slate-600 p-2">+</button>
        <button type="button" onClick={fit} className="rounded border border-slate-600 p-2">전체 맞춤</button>
        <label>겹쳐 보기 <select aria-label="근거 겹침 이미지" value={overlay?.id||''} onChange={event=>setOverlayId(event.target.value)} className="rounded bg-slate-800 p-2"><option value="">없음</option>{overlays.map(layer=><option key={layer.id} value={layer.id}>{layer.label}</option>)}</select></label>
        <label>투명도 <input aria-label="근거 겹침 투명도" type="range" min="0" max="1" step=".05" value={opacity} disabled={!overlay} onChange={event=>setOpacity(Number(event.target.value))}/></label>
        <label><input aria-label="근거 ROI 라벨" type="checkbox" checked={labels} onChange={event=>setLabels(event.target.checked)}/> ROI 라벨</label>
      </div>
      <div tabIndex={0} aria-label="근거 이미지 이동 영역" className="relative min-h-[250px] flex-1 overflow-hidden rounded border border-slate-700 bg-black" style={{height:'min(50vh,500px)',touchAction:'none'}}
        onPointerDown={event=>{if(event.button!==0)return;event.currentTarget.setPointerCapture(event.pointerId);drag.current={x:event.clientX,y:event.clientY,pan};}}
        onPointerMove={event=>{if(drag.current)setPan({x:drag.current.pan.x+event.clientX-drag.current.x,y:drag.current.pan.y+event.clientY-drag.current.y});}}
        onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
        {selected?<div className="absolute inset-0" style={{transform:`translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,transformOrigin:'center'}}>
          <img draggable={false} alt={selected.label} src={selected.image} className="absolute inset-0 h-full w-full object-contain"/>
          {overlay&&<img draggable={false} alt={`겹침 ${overlay.label}`} src={overlay.image} style={{opacity}} className="absolute inset-0 h-full w-full object-contain"/>}
          {!!boxes.length&&selected.size&&<svg className="absolute inset-0 h-full w-full" viewBox={`0 0 ${selected.size.join(' ')}`} preserveAspectRatio="xMidYMid meet" aria-label="근거 ROI 표시">{boxes.map(row=><g key={row.id}><rect x={row.box[0]} y={row.box[1]} width={row.box[2]-row.box[0]} height={row.box[3]-row.box[1]} fill="none" stroke="#38bdf8" strokeWidth="2" vectorEffect="non-scaling-stroke"/>{labels&&<text x={row.box[0]} y={row.box[1]+14} fill="#7dd3fc" fontSize="14">{row.id}</text>}</g>)}</svg>}
        </div>:<p className="p-4 text-amber-200">검증된 저장 이미지가 없습니다. 외부 이미지 주소는 불러오지 않습니다.</p>}
      </div>
      <p className="mt-2 text-slate-400">드래그·방향키로 이동, +/− 확대·축소, 0 전체 맞춤, Esc 복귀. 다른 좌표계의 이미지는 개별 표시합니다. 저장된 판정과 라벨은 수정하지 않습니다.</p>
      {evidence.facts&&<details className="mt-2"><summary className="cursor-pointer">측정·판정 근거</summary><pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(evidence.facts,null,2)}</pre></details>}
    </div>
  </div>;
}
