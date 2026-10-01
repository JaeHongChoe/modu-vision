import {getApiPersistenceIdentity} from '../../services/api';
import React,{useEffect,useState} from 'react';
import {useComputeStore} from '../../stores/useComputeStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {dataWorkbench,dataWorkbenchScope,type DataDiagnostic} from '../../services/dataWorkbench';
import {workflowError} from '../../services/datasetWorkflow';
const issueNames:Record<string,string>={blur:'흐림',underexposed:'어두움',overexposed:'과노출',unreadable:'읽기 실패',near_duplicate:'유사 이미지'};
const input='rounded border border-slate-600 bg-slate-900 p-1.5';
export const DataReadinessPanel:React.FC=()=>{
 const project=useProjectStore();const compute=useComputeStore();const dataset=useDatasetStore();const scope=dataWorkbenchScope({...project,...compute,apiTransportIdentity:getApiPersistenceIdentity()});
 const [loaded,setLoaded]=useState<{scope:string;report:DataDiagnostic}|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const [blur,setBlur]=useState(50);const [exposure,setExposure]=useState(.9);const [distance,setDistance]=useState(6);
 const report=loaded?.scope===scope?loaded.report:null;const same=()=>dataWorkbenchScope({...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()})===scope;
 useEffect(()=>{let current=true;setLoaded(null);setBusy(false);setError('');if(!project.project?.source_dataset_dir)return;
   void dataWorkbench.savedDiagnostics().then(value=>{if(current&&same()&&'items' in value)setLoaded({scope,report:value});}).catch(cause=>{if(current&&same())setError(workflowError(cause));});return()=>{current=false;};
 },[scope]);
 const diagnose=async()=>{if(!same())return;setBusy(true);setError('');try{const next=await dataWorkbench.diagnose({blur_threshold:blur,exposure_fraction:exposure,near_distance:distance});if(same())setLoaded({scope,report:next});}catch(cause){if(same())setError(workflowError(cause));}finally{if(same())setBusy(false);}};
 if(!project.project?.source_dataset_dir)return null;
 return <details className="shrink-0 border-b border-slate-700 bg-[#111D2B] text-xs"><summary className="cursor-pointer px-4 py-2 font-semibold text-cyan-100">데이터 준비 상태 · 품질과 중복 진단{report?` · ${report.stale?'재진단 필요':report.decision==='ready'?'검사 완료':'검토 필요'}`:''}</summary><section aria-label="데이터 준비 진단" className="max-h-[48vh] space-y-3 overflow-auto px-4 pb-4">
  <p className="text-slate-300">라벨·분할 현황과 흐림, 노출, 시각적으로 유사한 이미지를 함께 확인합니다. 유사도는 검토 후보이며 실제 동일 제품 여부는 검토자가 확인합니다.</p>
  <div className="flex flex-wrap items-end gap-2"><label>흐림 기준<input aria-label="흐림 진단 기준" type="number" min="0" value={blur} onChange={e=>setBlur(Number(e.target.value))} className={`${input} ml-2 w-20`}/></label><label>노출 픽셀 비율<input aria-label="노출 픽셀 비율" type="number" min=".01" max="1" step=".01" value={exposure} onChange={e=>setExposure(Number(e.target.value))} className={`${input} ml-2 w-20`}/></label><label>유사도 거리<input aria-label="유사 이미지 거리" type="number" min="0" max="64" value={distance} onChange={e=>setDistance(Number(e.target.value))} className={`${input} ml-2 w-16`}/></label><button type="button" disabled={busy||dataset.isLoading||dataset.folderPath!==project.project.source_dataset_dir} onClick={diagnose} className="rounded border border-cyan-700 px-3 py-2 text-cyan-100 disabled:opacity-40">준비 상태 진단</button></div>
  {busy&&<p role="status">원본 이미지 품질을 측정하는 중…</p>}{error&&<p role="alert" className="text-rose-200">{error}</p>}
  {report&&<><div className="flex flex-wrap gap-2"><span>전체 {report.summary.total}장</span><span>미라벨 {report.summary.labeling.unlabeled?.count||0}장</span>{Object.entries(report.issue_counts).map(([key,count])=><span key={key} className={count?'rounded bg-amber-950 p-1 text-amber-200':'p-1 text-slate-400'}>{`${issueNames[key]||key} ${count}`}</span>)}</div>{report.stale&&<p className="text-amber-200">이미지·라벨·분할이 변경되었습니다. 분할 열은 현재 저장값이며 품질·중복 판정은 재진단 후 갱신됩니다.</p>}
  <div className="max-h-56 overflow-auto"><table className="w-full text-left"><thead><tr><th>이미지</th><th>진단</th><th>흐림 점수</th><th>분할</th><th>다음 작업</th></tr></thead><tbody>{report.items.filter(item=>item.issues.length).map(item=><tr key={item.relative_path} className="border-t border-slate-700"><td className="p-2">{item.relative_path}</td><td>{item.issues.map(issue=>issueNames[issue]||issue).join(' · ')}</td><td>{item.blur_score?.toFixed(1)||'—'}</td><td>{item.current_split||item.split}</td><td><button type="button" className="rounded border border-slate-600 p-1.5" onClick={()=>void useProjectStore.getState().openImageForLabeling(item.file_path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/,''),item.file_path)}>이미지 검토</button></td></tr>)}</tbody></table></div>
  {!!report.near_duplicates.length&&<details><summary className="cursor-pointer text-amber-200">유사 이미지 쌍 {report.near_duplicates.length}개</summary><ul>{report.near_duplicates.map((pair,i)=><li key={i} className="border-t border-slate-700 py-1">{pair.images.join(' ↔ ')} · 거리 {pair.distance} · {pair.exact_bytes?'바이트 동일':'시각 유사'}{(pair.current_cross_split??pair.cross_split)?' · 분할 간 누수 검토':''}</li>)}</ul></details>}
  <details><summary className="cursor-pointer text-slate-400">측정 기준</summary><p>{report.measurement_limits}</p></details></>}
 </section></details>;
}
