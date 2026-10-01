import {useEffect,useRef,useState} from 'react';
import {api} from '../../services/api';
import type {ImageMeta} from '../../types';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {programButton,programInput} from './ProgramWorkbenchControls';

/** Reads the current project gallery page, never infers ground truth from names. */
export function ProjectImagePicker({value,onSelect,disabled=false,label='프로젝트 이미지 선택'}:{value:string;onSelect:(image:ImageMeta)=>void;disabled?:boolean;label?:string}) {
  const {project,projectDir}=useProjectStore();const transport=useComputeStore(state=>state.transportRevision);
  const source=project?.source_dataset_dir || '';const [page,setPage]=useState(0);const [rows,setRows]=useState<ImageMeta[]>([]);const [rowsScope,setRowsScope]=useState('');const [total,setTotal]=useState(0);const [error,setError]=useState('');const [loading,setLoading]=useState(false);
  const scope=`${projectDir}\n${source}\n${project?.active_labelset_id}\n${project?.task}\n${transport}`;const current=useRef(scope);current.current=scope;
  useEffect(()=>{setPage(0);setRows([]);setTotal(0);setError('');},[scope]);
  useEffect(()=>{
    let active=true;setRows([]);setLoading(true);if(!source){setLoading(false);return;}
    void api.dataset.getImages({folder_path:source,task:project?.task,limit:48,offset:page*48}).then(result=>{if(active&&current.current===scope){setRows(result.items);setRowsScope(scope);setTotal(result.total);setError('');}}).catch(cause=>{if(active&&current.current===scope)setError(cause instanceof Error?cause.message:String(cause));}).finally(()=>{if(active&&current.current===scope)setLoading(false);});
    return()=>{active=false;};
  },[scope,page]);
  const visible=rowsScope===scope?rows:[];
  const selected=visible.find(row=>row.file_path===value);
  return <div className="rounded border border-slate-600 bg-[#0B1520] p-3 text-sm"><label className="block text-slate-200">{label}<select aria-label={label} className={programInput} value={selected?.file_path || ''} disabled={disabled||loading} onChange={event=>{const row=visible.find(item=>item.file_path===event.target.value);if(row)onSelect(row);}}><option value="">{loading?'이미지 읽는 중…':'이미지를 선택하세요'}</option>{visible.map(row=><option key={row.file_path} value={row.file_path}>{row.file_name} · {row.split || '분할 미정'}{row.has_annotation?' · 정답 있음':''}</option>)}</select></label>
    {selected?.thumbnail_url&&<img alt={`선택 원본 ${selected.file_name}`} src={selected.thumbnail_url} className="mt-3 max-h-56 max-w-full rounded object-contain" />}
    <div className="mt-2 flex items-center gap-3 text-slate-300"><button type="button" className={programButton} disabled={disabled||loading||page===0} onClick={()=>setPage(old=>old-1)}>이전 이미지</button><span>{total?`${page*48+1}–${Math.min(total,(page+1)*48)} / ${total}`:'이미지 없음'}</span><button type="button" className={programButton} disabled={disabled||loading||(page+1)*48>=total} onClick={()=>setPage(old=>old+1)}>다음 이미지</button></div>
    {error&&<p role="alert" className="mt-2 text-rose-200">{error}</p>}
  </div>;
}
