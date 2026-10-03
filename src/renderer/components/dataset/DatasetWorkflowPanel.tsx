import React,{useEffect,useState} from 'react';
import {datasetWorkflow,workflowError,type ImageReviewMetadata,type GroupSplit,type ImportPreview,type DuplicateGroup} from '../../services/datasetWorkflow';
import {resolveApiUrl} from '../../services/api';
import {host} from '../../services/hostAdapter';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {ProjectPreferencePanel} from './ProjectPreferencePanel';
import {ExternalMaskExchangePanel} from './ExternalMaskExchangePanel';
import {projectPreferences} from '../../services/projectPreferences';
export const DatasetWorkflowPanel:React.FC=()=>{
  const {folderPath,hasSelectedFolder,annotationsChanged,loadImages}=useDatasetStore();const {projectDir,openImageForLabeling}=useProjectStore();
  const {reviewerName,setReviewerName}=useAnnotationStore();
  const [open,setOpen]=useState(false);const [rows,setRows]=useState<ImageReviewMetadata[]>([]);const [total,setTotal]=useState(0);const [state,setState]=useState('');const [product,setProduct]=useState('');const [lot,setLot]=useState('');const [group,setGroup]=useState('');const [tag,setTag]=useState('');
  const [groupBy,setGroupBy]=useState('lot');const [ratios,setRatios]=useState([70,20,10]);const [split,setSplit]=useState<GroupSplit|null>(null);const [duplicates,setDuplicates]=useState<DuplicateGroup[]>([]);
  const [page,setPage]=useState(1);const [selected,setSelected]=useState<Set<string>>(new Set());const [bulkProduct,setBulkProduct]=useState('');const [bulkLot,setBulkLot]=useState('');const [bulkGroup,setBulkGroup]=useState('');const [bulkTags,setBulkTags]=useState('');
  const pageSize=20;const visibleRows=rows.slice((page-1)*pageSize,page*pageSize);
  const [bulkUsage,setBulkUsage]=useState('');const [palette,setPalette]=useState<Record<string,string>>({});
  useEffect(()=>{let active=true;const load=()=>projectPreferences.read().then(p=>{if(active)setPalette(p.tag_colors);}).catch(()=>undefined);if(projectDir)void load();window.addEventListener('project-preferences-changed',load);return()=>{active=false;window.removeEventListener('project-preferences-changed',load);};},[projectDir]);
  const [format,setFormat]=useState('labelme');const [importDir,setImportDir]=useState('');const [policy,setPolicy]=useState('reject');const [preview,setPreview]=useState<ImportPreview[]>([]);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const refresh=async()=>{const result=await datasetWorkflow.list({folder_path:folderPath,state,product,lot,group,tag,limit:5000});setRows(result.items);setTotal(result.total);};
  useEffect(()=>{setRows([]);setPage(1);setSelected(new Set());setSplit(null);setPreview([]);setImportDir('');setError('');setNotice('');},[projectDir,folderPath]);
  useEffect(()=>{if(!open||!hasSelectedFolder||!projectDir)return;let current=true;setBusy(true);Promise.all([datasetWorkflow.list({folder_path:folderPath,state,product,lot,group,tag,limit:5000}),datasetWorkflow.duplicates()]).then(([r,d])=>{if(current){setRows(r.items);setPage(1);setSelected(new Set());setTotal(r.total);setDuplicates(d.duplicates);}}).catch(e=>{if(current)setError(workflowError(e));}).finally(()=>{if(current)setBusy(false);});return()=>{current=false;};},[open,projectDir,folderPath,state,product,lot,group,tag,hasSelectedFolder]);
  const run=async(action:()=>Promise<void>)=>{setBusy(true);setError('');setNotice('');try{await action();}catch(e){setError(workflowError(e));}finally{setBusy(false);}};
  const importLabels=async(apply:boolean)=>{
    if(apply&&!reviewerName.trim())throw new Error('작업자 이름을 입력하세요.');
    const revisions=Object.fromEntries(preview.map(r=>[r.image_uuid,r.revision]));const result=await datasetWorkflow.import(format,importDir,apply?'apply':'preview',policy,reviewerName||'operator',revisions);
    setPreview(result.preview);if(result.applied){setNotice(`라벨 ${result.preview.length}개 이미지에 적용 · 이전 버전 ${result.backup_version_id}`);await annotationsChanged();await refresh();setPreview([]);}
  };
  const bulkEdit=async()=>{
    if(!reviewerName.trim())throw new Error('작업자 이름을 입력하세요.');
    const changes:Record<string,unknown>={};
    if(bulkProduct.trim())changes.product=bulkProduct.trim();if(bulkLot.trim())changes.lot=bulkLot.trim();if(bulkGroup.trim())changes.group=bulkGroup.trim();if(bulkTags.trim())changes.tags=bulkTags.split(',').map(t=>t.trim()).filter(Boolean);
    if(bulkUsage)changes.usage_state=bulkUsage;
    if(!Object.keys(changes).length)throw new Error('일괄 적용할 제품·로트·그룹·태그 중 하나를 입력하세요.');
    const result=await datasetWorkflow.bulkEdit(rows.filter(r=>selected.has(r.image_uuid)),reviewerName,changes);
    setNotice(`${result.updated}개 이미지 정보 저장 · 라벨 승인은 이미지별로 진행하세요.`);setSelected(new Set());if(bulkUsage)await annotationsChanged();await refresh();setSplit(null);window.dispatchEvent(new Event('dataset-statistics-changed'));
  };
  const splitData=async(apply:boolean)=>{
    if(apply&&!reviewerName.trim())throw new Error('작업자 이름을 입력하세요.');
    const result=await datasetWorkflow.split(groupBy.split(','),ratios.map(n=>n/100),apply,reviewerName||'operator');setSplit(result);
    if(result.applied){setNotice(`그룹 분할 적용 · 이전 버전 ${result.backup_version_id}`);await annotationsChanged();await loadImages(1);}
  };
  return <div className="relative z-[75] shrink-0 border-b border-slate-700 bg-[#101722] px-4 py-2 text-xs">
    <div className="flex items-center justify-between gap-2"><button type="button" onClick={()=>setOpen(!open)} disabled={!hasSelectedFolder||!projectDir} aria-expanded={open} className="rounded border border-cyan-800 px-3 py-1.5 font-semibold text-cyan-200 disabled:opacity-40">이미지 검토·그룹 분할·라벨 교환</button><ProjectPreferencePanel/></div>
    {open&&<section aria-label="데이터 검토와 라벨 교환" className="absolute inset-x-3 top-full mt-1 max-h-[75vh] space-y-4 overflow-y-auto rounded border border-slate-600 bg-[#151D2A] p-4 shadow-2xl">
      <div className="flex flex-wrap items-end gap-2"><label>작업자·검토자<input aria-label="데이터 작업자 이름" value={reviewerName} onChange={e=>setReviewerName(e.target.value)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5"/></label>
        <select aria-label="검토 상태 필터" value={state} onChange={e=>setState(e.target.value)} className="rounded border border-slate-600 bg-slate-900 p-1.5"><option value="">전체 상태</option><option value="unworked">미작업</option><option value="needs_review">검토 필요</option><option value="approved">승인됨</option></select>
        {[['제품',product,setProduct],['로트',lot,setLot],['그룹',group,setGroup],['태그',tag,setTag]].map(([name,value,setter])=><input key={String(name)} aria-label={`${name} 필터`} placeholder={`${name} 정확히 검색`} value={String(value)} onChange={e=>(setter as (v:string)=>void)(e.target.value)} className="w-28 rounded border border-slate-600 bg-slate-900 p-1.5"/>)}
      </div>
      <p className="text-slate-400">검색 결과 {total}개 · UUID와 검토 기록은 프로젝트와 라벨 세트별로 저장됩니다.</p>
      <div className="flex flex-wrap items-center gap-2"><button disabled={busy} onClick={()=>setSelected(new Set(rows.map(r=>r.image_uuid)))} className="rounded border border-slate-600 px-2 py-1">검색 결과 전체 선택 ({rows.length})</button><button onClick={()=>setSelected(new Set())} className="rounded border border-slate-600 px-2 py-1">선택 해제</button><span className="text-cyan-200">{selected.size}개 선택</span>
        {[['제품',bulkProduct,setBulkProduct],['로트',bulkLot,setBulkLot],['그룹',bulkGroup,setBulkGroup],['태그 교체',bulkTags,setBulkTags]].map(([name,value,setter])=><input key={String(name)} aria-label={`일괄 ${name}`} placeholder={`${name} (비우면 유지)`} value={String(value)} onChange={e=>(setter as (v:string)=>void)(e.target.value)} className="w-32 rounded border border-slate-600 bg-slate-900 p-1.5"/>)}<select aria-label="선택 이미지 학습 사용 여부" value={bulkUsage} onChange={e=>setBulkUsage(e.target.value)} className="rounded border border-slate-600 bg-slate-900 p-2"><option value="">사용 상태 유지</option><option value="active">학습에 사용</option><option value="not_used">미사용으로 제외</option></select><button disabled={busy||!selected.size} onClick={()=>void run(bulkEdit)} className="rounded bg-cyan-700 px-3 py-1.5 disabled:opacity-40">선택 이미지에 정보 적용</button></div>
      <div className="max-h-64 overflow-auto"><table className="w-full text-left"><thead className="sticky top-0 bg-slate-900"><tr>{['선택','원본 이미지','제품','로트','그룹','상태','검토자','열기'].map(h=><th key={h} className="p-2">{h}</th>)}</tr></thead><tbody>{visibleRows.map(r=><tr key={r.image_uuid} className="border-t border-slate-700"><td><input type="checkbox" aria-label={`${r.relative_path} 선택`} checked={selected.has(r.image_uuid)} onChange={e=>setSelected(previous=>{const next=new Set(previous);if(e.target.checked)next.add(r.image_uuid);else next.delete(r.image_uuid);return next;})}/></td><td className="max-w-64 truncate p-2" title={r.file_path}>{r.relative_path}<span className="block text-[10px] text-slate-500">{r.tags.map(t=><span key={t} className="mr-1 inline-block rounded border px-1" style={{borderColor:palette[t]||'#475569',color:palette[t]||'#94a3b8'}}>{t}</span>)}</span></td><td>{r.product}</td><td>{r.lot}</td><td>{r.group}</td><td>{({unworked:'미작업',needs_review:'검토 필요',approved:'승인됨'})[r.workflow_state]}</td><td>{r.reviewer||'—'}</td><td><button disabled={busy} onClick={()=>void run(async()=>{const opened=await openImageForLabeling(r.file_path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/,''),r.file_path);if(!opened)throw new Error('원본 이미지로 이동하지 못했습니다. 현재 라벨 저장 상태와 원본 경로를 확인하세요.');})} className="rounded border border-cyan-800 px-2 py-1 text-cyan-200">2단계 검토</button></td></tr>)}</tbody></table>{!rows.length&&!busy&&<p className="p-4 text-center text-slate-400">검색 결과가 없습니다.</p>}{total>rows.length&&<p>5,000개까지 표시합니다. 필터로 범위를 좁히세요.</p>}</div>
      <div className="flex items-center justify-between text-slate-400"><span>{page} / {Math.max(1,Math.ceil(rows.length/pageSize))} 페이지 · 페이지당 {pageSize}개</span><div className="flex gap-2"><button disabled={page<=1||busy} onClick={()=>setPage(p=>p-1)} className="rounded border border-slate-600 px-3 py-1 disabled:opacity-40">이전</button><button disabled={page*pageSize>=rows.length||busy} onClick={()=>setPage(p=>p+1)} className="rounded border border-slate-600 px-3 py-1 disabled:opacity-40">다음</button></div></div>
      <details open><summary className="cursor-pointer font-semibold">그룹 분할과 중복 이미지</summary><div className="mt-2 flex flex-wrap items-center gap-2">
        <select value={groupBy} onChange={e=>{setGroupBy(e.target.value);setSplit(null);}} aria-label="분할 그룹 기준" className="rounded border border-slate-600 bg-slate-900 p-2"><option value="product">제품</option><option value="lot">로트</option><option value="group">그룹</option><option value="product,lot">제품 + 로트</option><option value="product,lot,group">제품 + 로트 + 그룹</option></select>
        {['학습','검증','시험'].map((label,i)=><label key={label}>{label}<input aria-label={`${label} 그룹 분할 비율`} type="number" min={0} max={100} value={ratios[i]} onChange={e=>{setRatios(r=>r.map((v,j)=>j===i?Number(e.target.value):v));setSplit(null);}} className="ml-1 w-16 rounded border border-slate-600 bg-slate-900 p-2"/>%</label>)}
        <button disabled={busy} onClick={()=>void run(()=>splitData(false))} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">분할 미리보기</button>
        <button disabled={busy||!split||!split.apply_supported} onClick={()=>void run(()=>splitData(true))} className="rounded bg-cyan-700 px-3 py-2 disabled:opacity-40">분할 적용</button>
      </div>{split&&<p className="mt-2 text-cyan-200">독립 그룹 {split.group_count}개 · 학습 {split.split.train} / 검증 {split.split.val} / 시험 {split.split.test}{!split.apply_supported&&<span className="block text-amber-200">{split.apply_unavailable_reason}</span>}</p>}
      <p className="mt-2 text-slate-400">선택한 그룹과 동일 내용의 이미지는 같은 분할에 유지됩니다. 모든 이미지에 그룹 정보가 필요합니다.</p>
      {duplicates.length>0&&<ul className="mt-2 max-h-24 overflow-auto">{duplicates.map(d=><li key={d.content_hash} className={d.cross_split?'text-red-300':'text-amber-200'}>{d.cross_split?'분할 간 동일 이미지: ':'동일 내용: '}{d.images.join(', ')} · {d.splits.join(', ')}</li>)}</ul>}{!duplicates.length&&<p className="mt-2 text-emerald-300">현재 동일 내용 중복 없음</p>}</details>
      <details><summary className="cursor-pointer font-semibold">LabelMe · COCO · YOLO 라벨 가져오기/내보내기</summary><div className="mt-2 flex flex-wrap items-center gap-2"><select aria-label="라벨 교환 형식" value={format} onChange={e=>{setFormat(e.target.value);setPreview([]);}} className="rounded border border-slate-600 bg-slate-900 p-2">{['labelme','coco','yolo'].map(f=><option key={f} value={f}>{f.toUpperCase()}</option>)}</select>
        <button disabled={busy} onClick={()=>void run(async()=>{const dir=await host.selectFolder({title:'가져올 라벨 폴더 선택'});if(dir){setImportDir(dir);setPreview([]);}})} className="rounded border border-slate-600 px-3 py-2">라벨 폴더 선택</button><span className="max-w-72 truncate text-slate-400" title={importDir}>{importDir||'폴더를 선택하세요'}</span>
        <select aria-label="기존 라벨 충돌 처리" value={policy} onChange={e=>setPolicy(e.target.value)} className="rounded border border-slate-600 bg-slate-900 p-2"><option value="reject">기존 라벨 충돌 시 중지</option><option value="merge">기존 라벨에 추가</option><option value="replace">기존 라벨 교체</option></select>
        <button disabled={busy||!importDir} onClick={()=>void run(()=>importLabels(false))} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">가져오기 미리보기</button><button disabled={busy||!preview.length} onClick={()=>void run(()=>importLabels(true))} className="rounded bg-emerald-700 px-3 py-2 disabled:opacity-40">검토한 라벨 적용</button>
        <button disabled={busy} onClick={()=>void run(async()=>{const result=await datasetWorkflow.export(format);const a=document.createElement('a');a.href=resolveApiUrl(result.download_url);a.download=`${format}_annotations.zip`;a.click();setNotice(`${result.image_count}개 이미지 · ${result.annotation_count}개 라벨 내보내기 완료`);})} className="rounded border border-cyan-700 px-3 py-2">라벨 내보내기</button></div>
        {preview.length>0&&<ul className="mt-2 max-h-32 overflow-auto">{preview.map(r=><li key={r.image_uuid} className={r.conflict?'text-amber-300':'text-slate-300'}>{r.file_name} · 기존 {r.existing_count} / 가져올 {r.incoming_count}{r.conflict?' · 충돌 있음':''}</li>)}</ul>}
        <p className="mt-2 text-slate-400">원본 좌표를 유지하며 이미지 파일은 복사하지 않습니다. COCO·YOLO는 박스/다각형만 지원합니다. 브러시 마스크·COCO RLE는 명시적으로 중지합니다. YOLO는 classes.txt와 이미지 크기 정보가 필요합니다.</p>
      </details>
      <ExternalMaskExchangePanel/>
      {busy&&<p role="status" className="text-cyan-200">처리 중…</p>}{error&&<p role="alert" className="rounded border border-red-800 p-2 text-red-200">{error}</p>}{notice&&<p role="status" className="text-emerald-200">{notice}</p>}
    </section>}
  </div>;
};
