import React,{useEffect,useState} from 'react';
import {datasetWorkflow,workflowError,type MaskImportPreview} from '../../services/datasetWorkflow';
import {resolveApiUrl} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
export const ExternalMaskExchangePanel:React.FC=()=>{
  const projectDir=useProjectStore(s=>s.projectDir);const source=useDatasetStore(s=>s.folderPath);const reviewer=useAnnotationStore(s=>s.reviewerName);
  const [directory,setDirectory]=useState('');const [policy,setPolicy]=useState('reject');const [rows,setRows]=useState<MaskImportPreview[]>([]);const [signature,setSignature]=useState('');const [originals,setOriginals]=useState(true);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  useEffect(()=>{setDirectory('');setRows([]);setSignature('');setError('');setNotice('');},[projectDir,source]);
  const run=async(action:()=>Promise<void>)=>{setBusy(true);setError('');setNotice('');try{await action();}catch(e){setError(workflowError(e));}finally{setBusy(false);}};
  const load=async(apply:boolean)=>{
    if(apply&&!reviewer.trim())throw new Error('데이터 작업자 이름을 입력하세요.');
    const next=await datasetWorkflow.importMasks(directory,apply?'apply':'preview',policy,reviewer||'operator',Object.fromEntries(rows.map(r=>[r.image_uuid,r.revision])),apply?signature:undefined);
    if(useProjectStore.getState().projectDir!==projectDir)return;
    setRows(next.preview);setSignature(next.manifest_sha256);
    if(next.applied){setNotice(`mask 라벨 ${next.preview.length}개 이미지 적용 · 이전 버전 ${next.backup_version_id} · 이미지별 검토가 필요합니다.`);setRows([]);await useDatasetStore.getState().annotationsChanged();await useAnnotationStore.getState().loadAnnotationsForCurrent();}
  };
  return <details aria-label="외부 multiclass mask 교환"><summary className="cursor-pointer font-semibold">외부 pixel mask 가져오기·내보내기 (클래스·구멍 유지)</summary><div className="mt-2 flex flex-wrap items-center gap-2">
    <button disabled={busy} onClick={()=>void run(async()=>{const path=await window.api?.selectFolder({title:'mask_manifest.json와 PNG mask가 있는 폴더'});if(path){setDirectory(path);setRows([]);setSignature('');}})} className="rounded border border-slate-600 px-3 py-2">mask 폴더 선택</button><span className="max-w-72 truncate text-slate-400" title={directory}>{directory||'mask_manifest.json 필요'}</span>
    <select aria-label="외부 mask 충돌 처리" value={policy} onChange={e=>setPolicy(e.target.value)} className="rounded border border-slate-600 bg-slate-900 p-2"><option value="reject">기존 라벨 충돌 시 중지</option><option value="replace">검토 후 기존 라벨 교체</option><option value="merge">기존 라벨에 추가</option></select>
    <button disabled={busy||!directory} onClick={()=>void run(()=>load(false))} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">mask 미리보기</button><button disabled={busy||!rows.length} onClick={()=>void run(()=>load(true))} className="rounded bg-emerald-700 px-3 py-2 disabled:opacity-40">검토한 mask 라벨 적용</button>
    <label><input type="checkbox" checked={originals} onChange={e=>setOriginals(e.target.checked)} className="mr-1"/>원본 이미지 함께 내보내기</label><button disabled={busy} onClick={()=>void run(async()=>{const next=await datasetWorkflow.exportMasks(originals);const a=document.createElement('a');a.href=resolveApiUrl(next.download_url);a.download='pixel_masks_with_source_mapping.zip';a.click();setNotice(`${next.image_count}개 이미지 · ${next.annotation_count}개 라벨 PNG·palette·원본 mapping 내보내기`);})} className="rounded border border-cyan-700 px-3 py-2">pixel mask 내보내기</button>
  </div><p className="mt-2 text-[10px] text-slate-400">8-bit PNG 클래스 ID(0=배경)와 명시적 이름·색상, 원본 상대 경로·크기를 mask_manifest.json에 지정하세요. 원본 파일은 수정하지 않습니다. 클래스별 alpha mask를 동일한 학습 라벨로 저장하며 내부 구멍을 유지합니다. 미리보기 이후 파일·라벨 수정은 적용을 중지합니다.</p>
  {rows.length>0&&<div className="mt-2 max-h-64 space-y-2 overflow-auto">{rows.map(r=><article key={r.image_uuid} className="rounded border border-slate-600 p-2"><p className={r.conflict?'text-amber-300':'text-cyan-200'}>{r.file_name} · 수정 {r.revision} · 기존 {r.existing_count} / mask 클래스 {r.incoming_count}{r.conflict?' · 충돌 있음':''}</p><p className="mt-1 text-[10px] text-slate-400">{r.classes.map(c=>`${c.id}=${c.name}`).join(' · ')}</p><div className="mt-2 flex flex-wrap gap-2">{r.preview_masks.map((mask,i)=><figure key={i}><img src={mask.mask_rle} alt={`${r.file_name} ${mask.label} pixel mask`} className="h-20 w-28 bg-slate-900 object-contain"/><figcaption style={{color:mask.color}} className="text-[10px]">{mask.label}</figcaption></figure>)}</div></article>)}</div>}
  {busy&&<p role="status" className="mt-2 text-cyan-200">mask 처리 중…</p>}{error&&<p role="alert" className="mt-2 text-red-300">{error}</p>}{notice&&<p role="status" className="mt-2 text-emerald-300">{notice}</p>}
  </details>;
};
