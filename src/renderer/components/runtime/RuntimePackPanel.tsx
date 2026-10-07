import {useEffect,useRef,useState} from 'react';
import {getApiPersistenceIdentity,getProjectContextGeneration} from '../../services/api';
import {host} from '../../services/hostAdapter';
import {productDeliveryApi,type RuntimePackInventory} from '../../services/productDeliveryApi';
import {useDeliveryScope} from './useDeliveryScope';

const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
const input='mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2';

export function RuntimePackPanel(){
 const {scope,key,projectDir}=useDeliveryScope();
 const mounted=useRef(false);
 const readGeneration=useRef(0);
 const [source,setSource]=useState(''),[document,setDocument]=useState(''),[pin,setPin]=useState('');
 const [report,setReport]=useState<RuntimePackInventory|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const capture=()=>({scope:scope.current,generation:getProjectContextGeneration(),identity:getApiPersistenceIdentity()});
 const same=(started:ReturnType<typeof capture>)=>mounted.current&&scope.current===started.scope&&getProjectContextGeneration()===started.generation&&getApiPersistenceIdentity()===started.identity;
 useEffect(()=>{
  mounted.current=true;const started=capture(),generation=++readGeneration.current;setSource('');setDocument('');setPin('');setReport(null);setBusy(false);setError('');setNotice('');
  productDeliveryApi.runtimePacks().then(value=>{if(same(started)&&readGeneration.current===generation)setReport(value);}).catch(e=>{if(same(started)&&readGeneration.current===generation)setError(String(e.message||e));});
  return()=>{mounted.current=false;};
 },[key]);
 const refresh=async()=>{const started=capture(),generation=++readGeneration.current;const value=await productDeliveryApi.runtimePacks();if(same(started)&&readGeneration.current===generation)setReport(value);};
 const act=async(action:(started:ReturnType<typeof capture>)=>Promise<void>)=>{
  const started=capture();setBusy(true);setError('');setNotice('');
  try{await action(started);}catch(e){if(same(started))setError(String((e as Error).message||e));}
  finally{if(same(started))setBusy(false);}
 };
 const pick=(kind:'source'|'inventory')=>void act(async started=>{
  const value=kind==='source'?await host.selectFolder({title:'프로젝트 안의 런타임 팩 payload 폴더',defaultPath:projectDir||undefined}):await host.selectFile({title:'런타임 팩 inventory JSON',filters:[{name:'JSON',extensions:['json']}]});
  if(value&&same(started))(kind==='source'?setSource:setDocument)(value);
 });
 const install=()=>void act(async started=>{
  ++readGeneration.current;
  const row=await productDeliveryApi.installRuntimePack({source_dir:source,inventory_path:document,expected_sha256:pin});
  if(!same(started))return;
  await refresh();if(!same(started))return;
  if(row.integrity!=='verified')throw Error(row.error||'설치 후 팩 무결성 확인 실패');
  setNotice(`${row.id} ${row.version} · 검증한 파일 보관 완료 · 실행 환경 활성화 전`);
 });
 return <fieldset aria-label="선택형 런타임 팩" className="rounded border border-slate-700 p-3">
  <legend>선택형 런타임 팩</legend>
  <p className="my-2 text-slate-400">현재 프로젝트 폴더 안에 payload와 inventory JSON을 준비하세요. 출처에서 별도로 확인한 SHA-256을 입력하면 파일을 검사한 뒤 프로젝트에 보관합니다. 공유 서버에서는 서버의 프로젝트 경로를 사용하세요.</p>
  <div className="space-y-3">
   <label className="block">팩 payload 폴더<input aria-label="런타임 팩 payload 폴더" disabled={busy} value={source} onChange={e=>setSource(e.target.value)} className={input}/></label>
   {host.can('pickPaths')&&<button type="button" disabled={busy} className={button} onClick={()=>pick('source')}>팩 폴더 선택</button>}
   <label className="block">Inventory JSON 경로<input aria-label="런타임 팩 inventory 경로" disabled={busy} value={document} onChange={e=>setDocument(e.target.value)} className={input}/></label>
   {host.can('pickPaths')&&<button type="button" disabled={busy} className={button} onClick={()=>pick('inventory')}>Inventory 파일 선택</button>}
   <label className="block">출처에서 별도로 확인한 SHA-256<input aria-label="런타임 팩 검토 SHA-256" disabled={busy} value={pin} onChange={e=>setPin(e.target.value.trim())} className={input}/></label>
   <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={busy||!source||!document||!/^[a-f0-9]{64}$/.test(pin)} onClick={install}>팩 검증·보관</button><button type="button" className={button} disabled={busy} onClick={()=>void act(refresh)}>팩 무결성 다시 확인</button></div>
  </div>
  {busy&&<p role="status" className="mt-3">팩 파일 검사 중…</p>}
  {notice&&<p role="status" className="mt-3 text-emerald-300">{notice}</p>}
  {error&&<p role="alert" className="mt-3 text-red-300">{error}</p>}
  {report&&<><p className="mt-3">실행 백엔드 {report.target.platform} {report.target.arch} · Python {report.target.python}</p>
   {report.packs.length===0?<p className="mt-2 text-slate-400">이 프로젝트에 보관한 런타임 팩이 없습니다.</p>:<ul className="mt-3 space-y-3">{report.packs.map(row=><li key={row.inventory_sha256} aria-label={`런타임 팩 ${row.id}`} className="rounded border border-slate-600 p-3">
    <p className="font-semibold">{row.id} {row.version} · {row.kind||'팩 정보 확인 필요'}</p>
    <p className={row.integrity==='verified'?'text-emerald-300':'text-red-300'}>{row.integrity==='verified'?'파일 무결성 확인됨':'파일 무결성 확인 실패'}</p>
    <p>{row.target_compatible?'OS·아키텍처·프로토콜 일치':'현재 실행 백엔드 호환성 확인 필요'}</p>
    <p>{row.python_abi.compatible?'Wheel Python·플랫폼 태그 일치':row.python_abi.state==='not_declared'?'Python ABI 명세 없음':'Wheel Python·플랫폼 호환성 확인 필요'}</p>
    <p>보관 용량 {row.total_bytes?.toLocaleString()??'확인 실패'} bytes · 설치 시 여유 공간 {row.estimated_staging_bytes?.toLocaleString()??'확인 실패'} bytes</p>
    <p className="break-all text-xs">Inventory SHA-256 {row.inventory_sha256}</p>
    {row.source&&<p className="break-all text-xs">출처 {row.source} · 라이선스 {row.license}</p>}
    {row.driver_minimum&&<p>NVIDIA 최소 driver {row.driver_minimum} · 현재 {row.observed_driver||'확인 불가'}</p>}
    {(row.error||row.compatibility_error)&&<p className="text-red-300">{row.error||row.compatibility_error}</p>}
    <p className="mt-2 text-amber-200">실행 환경 활성화 전 · 배포자 서명·대상 실행 검증 필요</p>
   </li>)}</ul>}
  </>}
 </fieldset>;
}
