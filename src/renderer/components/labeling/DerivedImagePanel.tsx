import React,{useEffect,useRef,useState} from 'react';
import {resolveApiUrl,getApiPersistenceIdentity} from '../../services/api';
import {host,hostErrorMessage} from '../../services/hostAdapter';
import {dataWorkbench,dataWorkbenchScope,type DerivedVersion} from '../../services/dataWorkbench';
import {workflowError} from '../../services/datasetWorkflow';
import {useComputeStore} from '../../stores/useComputeStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {cropPoint,cropRectangle} from './productDataWorkflow';
import type {AnnotationItem} from '../../types';
const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
function outline(a:AnnotationItem):string|undefined{
 if(a.polygon?.length)return a.polygon.map(p=>p.join(',')).join(' ');
 if(a.rotated_bbox){const [cx,cy,w,h,angle]=a.rotated_bbox;const r=angle*Math.PI/180;return [[-w/2,-h/2],[w/2,-h/2],[w/2,h/2],[-w/2,h/2]].map(([x,y])=>`${cx+x*Math.cos(r)-y*Math.sin(r)},${cy+x*Math.sin(r)+y*Math.cos(r)}`).join(' ');}
 if(a.bbox){const [x1,y1,x2,y2]=a.bbox;return `${x1},${y1} ${x2},${y1} ${x2},${y2} ${x1},${y2}`;}return undefined;
}
export const DerivedImagePanel:React.FC=()=>{
 const project=useProjectStore();const compute=useComputeStore();const {currentImage,metadata,isDirty,isSaving,annotationLoadStatus,annotations,reviewerName}=useAnnotationStore();
 const scope=`${dataWorkbenchScope({...project,...compute,apiTransportIdentity:getApiPersistenceIdentity()})}\n${currentImage?.file_path||''}`;const current=useRef(scope);current.current=scope;
 const [loaded,setLoaded]=useState<{scope:string;versions:DerivedVersion[]}|null>(null);const [selected,setSelected]=useState('');
 const [crop,setCrop]=useState<[number,number,number,number]|null>(null);const [anchor,setAnchor]=useState<[number,number]|null>(null);const [cropMode,setCropMode]=useState(false);
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
 const versions=loaded?.scope===scope?loaded.versions:[];const version=versions.find(row=>row.id===selected);
 const size:readonly[number,number]=version?.size||[metadata?.width||1,metadata?.height||1];const rows=version?.annotations||annotations;
 const same=()=>`${dataWorkbenchScope({...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()})}\n${useAnnotationStore.getState().currentImage?.file_path||''}`===scope;
 useEffect(()=>{let active=true;setLoaded(null);setSelected('');setCrop(null);setCropMode(false);setBusy(false);setError('');setNotice('');
  if(currentImage)void dataWorkbench.derivedHistory(currentImage.file_path).then(result=>{if(active&&same())setLoaded({scope,versions:result.versions});}).catch(cause=>{if(active&&same())setError(workflowError(cause));});return()=>{active=false;};
 },[scope]);
 const edit=async(operation:Record<string,unknown>)=>{
  if(!currentImage||!metadata||!same()||isDirty||isSaving)return;
  if(!reviewerName.trim()){setError('이미지 정보·검토에서 작업자 이름을 입력하세요.');return;}
  setBusy(true);setError('');setNotice('');try{
   const next=await dataWorkbench.derive({image_path:currentImage.file_path,expected_revision:metadata.revision,expected_sha256:metadata.content_hash,operation,actor:reviewerName,...(selected?{parent_id:selected}:{})});
   if(same()){setLoaded({scope,versions:[...versions,next]});setSelected(next.id);setCrop(null);setCropMode(false);setNotice(`파생 이미지와 라벨 ${next.annotations.length}개를 새 버전으로 저장했습니다.${next.omitted_annotation_ids.length?` 영역 밖 라벨 ${next.omitted_annotation_ids.length}개 제외.`:''}`);}
  }catch(cause){if(same())setError(workflowError(cause));}finally{if(same())setBusy(false);}
 };
 if(!currentImage)return null;
 const disabled=busy||isDirty||isSaving||annotationLoadStatus!=='ready';
 const imageUrl=version?resolveApiUrl(version.image_url):resolveApiUrl(`/api/dataset/raw/${encodeURIComponent(currentImage.file_name)}?file_path=${encodeURIComponent(currentImage.file_path)}`);
 return <details className="shrink-0 border-b border-slate-700 bg-[#111D2B] text-xs"><summary className="cursor-pointer px-4 py-2 font-semibold text-cyan-100">원본 보존 이미지 편집 · 파생 버전 {versions.length}개</summary><section aria-label="파생 이미지 편집" className="max-h-[65vh] space-y-3 overflow-auto px-4 pb-4">
  <p className="text-slate-300">현재 저장 라벨을 함께 변환합니다. 자르기·회전·반전 결과는 별도 이미지와 변경 기록으로 저장됩니다. 일부만 잘리는 회전 박스는 다각형으로 바꾼 후 편집하세요.</p>
  <label>편집 기준<select aria-label="파생 편집 기준 버전" value={selected} onChange={e=>{setSelected(e.target.value);setCrop(null);setCropMode(false);}} className="ml-2 rounded border border-slate-600 bg-slate-900 p-2"><option value="">원본과 현재 저장 라벨</option>{versions.map((row,index)=><option key={row.id} value={row.id}>버전 {index+1} · {row.operation.kind==='crop'?'자르기':row.operation.kind==='rotate'?'회전':'반전'} · {new Date(row.created_at*1000).toLocaleString('ko-KR')}</option>)}</select></label>
  <div className="flex flex-wrap gap-2"><button type="button" disabled={disabled} className={`${button} ${cropMode?'border-cyan-400 text-cyan-200':''}`} onClick={()=>{setCropMode(!cropMode);setCrop(null);}}>이미지에서 자르기 영역 선택</button>{[90,180,270].map(degrees=><button type="button" disabled={disabled} className={button} key={degrees} onClick={()=>void edit({kind:'rotate',degrees})}>{degrees}° 회전</button>)}<button type="button" disabled={disabled} className={button} onClick={()=>void edit({kind:'flip',axis:'horizontal'})}>좌우 반전</button><button type="button" disabled={disabled} className={button} onClick={()=>void edit({kind:'flip',axis:'vertical'})}>상하 반전</button></div>
  <svg role="img" aria-label="편집 원본과 변환 라벨 미리보기" viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="xMidYMid meet" className={`max-h-[360px] min-h-[180px] w-full rounded border border-slate-700 bg-black ${cropMode?'cursor-crosshair':''}`} onPointerDown={event=>{if(!cropMode||disabled)return;const point=cropPoint(event.currentTarget.getBoundingClientRect(),size,[event.clientX,event.clientY]);if(point){event.currentTarget.setPointerCapture(event.pointerId);setAnchor(point);setCrop(cropRectangle(point,point,size));}}} onPointerMove={event=>{if(!anchor||!cropMode)return;const point=cropPoint(event.currentTarget.getBoundingClientRect(),size,[event.clientX,event.clientY]);if(point)setCrop(cropRectangle(anchor,point,size));}} onPointerUp={()=>setAnchor(null)} onPointerCancel={()=>setAnchor(null)}>
   <image href={imageUrl} width={size[0]} height={size[1]}/>{rows.map((row,index)=>row.type==='brush_mask'?<image key={row.id||index} href={row.mask_rle} width={size[0]} height={size[1]} opacity={.4}/>:outline(row)&&<polygon key={row.id||index} points={outline(row)} fill="none" stroke={row.color||'#22d3ee'} strokeWidth={Math.max(...size)/250}/>)}{crop&&<rect x={crop[0]} y={crop[1]} width={crop[2]-crop[0]} height={crop[3]-crop[1]} fill="#fbbf2430" stroke="#fbbf24" strokeWidth={Math.max(...size)/200}/>}
  </svg>
  {cropMode&&<div className="flex flex-wrap items-center gap-2">{(['왼쪽','위','오른쪽','아래'] as const).map((name,index)=><label key={name}>{name}<input aria-label={`자르기 ${name} 좌표`} type="number" step="1" value={crop?.[index]??(index<2?0:size[index%2])} onChange={e=>setCrop(old=>{const next:[number,number,number,number]=old?[...old]:[0,0,size[0],size[1]];next[index]=Number(e.target.value);return next;})} className="ml-1 w-20 rounded border border-slate-600 bg-slate-900 p-2"/></label>)}<button type="button" disabled={disabled||!crop||crop[0]>=crop[2]||crop[1]>=crop[3]} className={`${button} border-cyan-600`} onClick={()=>crop&&void edit({kind:'crop',rect:crop})}>자르기 새 버전 저장</button></div>}
  {isDirty&&<p className="text-amber-200">라벨을 저장한 뒤 이미지 편집을 실행하세요.</p>}{busy&&<p role="status">이미지와 라벨을 새 버전으로 저장하는 중…</p>}{error&&<p role="alert" className="text-rose-200">{error}</p>}{notice&&<p role="status" className="text-emerald-200">{notice}</p>}
  {version&&<><button type="button" className={button} onClick={()=>void host.openPath(version.dataset_path).catch(cause=>setError(hostErrorMessage(cause,'저장 폴더를 열 수 없습니다.')))}>파생 이미지·라벨 저장 폴더 열기</button><details><summary className="cursor-pointer text-slate-400">버전 출처와 보존 해시</summary><pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({version:version.id,parent:version.parent_id,actor:version.actor,operation:version.operation,original_sha256:version.source_sha256,derived_sha256:version.derived_sha256,dataset_path:version.dataset_path},null,2)}</pre></details></>}
 </section></details>;
}
