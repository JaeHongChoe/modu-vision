import {useEffect,useRef,useState} from 'react';
import {getApiPersistenceIdentity} from '../../services/api';
import {teamDataApi,type TeamReadiness} from '../../services/teamDataApi';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {teamDataScope,reviewTrainingMessage} from '../labeling/teamDataWorkflow';
import {workflowError} from '../../services/datasetWorkflow';
export function TeamTrainingReadiness(){
 const project=useProjectStore();const compute=useComputeStore();const scope=teamDataScope({...project,...compute,apiTransportIdentity:getApiPersistenceIdentity()});const current=useRef(scope);current.current=scope;
 const [loaded,setLoaded]=useState<{scope:string;value:TeamReadiness}|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const check=async()=>{setBusy(true);setError('');try{const next=await teamDataApi.readiness();if(current.current===scope)setLoaded({scope,value:next});}catch(cause){if(current.current===scope)setError(workflowError(cause));}finally{if(current.current===scope)setBusy(false);}};
 useEffect(()=>{setLoaded(null);setError('');setBusy(false);if(project.project?.source_dataset_dir)void check();},[scope]);
 const value=loaded?.scope===scope?loaded.value:null;
 if(!project.project?.source_dataset_dir)return null;
 // The simple view states the result in one sentence; the review counts, team actions and hashes wait behind one line.
 // The training count is item 1 above (the saved split): the review counts images before labels and the split, so
 // unlabeled and test images are in it, and it is never shown here as images to train.
 if(project.viewMode==='simple')return <section aria-label="팀 검수 학습 준비" className="mt-3 rounded border border-slate-700 bg-slate-900/50 p-3 text-sm">
  <strong>팀 검수</strong>
  {value&&<p className={`mt-1 ${value.ready?'text-slate-300':'text-amber-200'}`}>{value.ready?'팀 검수 기준으로는 학습할 수 있습니다. 시험용으로 나눈 이미지는 학습에 쓰지 않고 4단계 평가에 씁니다.':reviewTrainingMessage(value)}</p>}
  {!value&&!error&&<p className="mt-1 text-slate-400">{busy?'검수 상태를 확인하는 중…':'검수 상태를 아직 확인하지 않았습니다.'}</p>}
  {error&&<p role="alert" className="mt-1 text-rose-200">{error}</p>}
  <details className="mt-2 text-xs text-slate-400"><summary className="cursor-pointer">검수 세부</summary>
   {value&&<p className="mt-1">{reviewTrainingMessage(value)}</p>}
   {value&&<p className="mt-1">라벨 기준 {value.book_version?`v${value.book_version}`:'미발행'} · 반려 {value.counts.rejected} · 불일치 {value.counts.disputed} · 미사용 {value.counts.unused}</p>}
   <div className="mt-2 flex gap-2"><button type="button" disabled={busy} onClick={()=>void check()} className="rounded border border-slate-600 px-2 py-1.5 disabled:opacity-40">{busy?'확인 중…':'대상 다시 확인'}</button><button type="button" onClick={()=>void project.setStep(2)} className="rounded border border-cyan-700 px-2 py-1.5">팀 검수 열기</button></div>
  </details>
 </section>;
 return <section aria-label="팀 검수 학습 준비" className="mt-3 rounded border border-slate-700 bg-slate-900/50 p-3 text-sm"><div className="flex flex-wrap items-center justify-between gap-2"><strong>데이터 검수</strong><div className="flex gap-2"><button type="button" disabled={busy} onClick={()=>void check()} className="rounded border border-slate-600 px-2 py-1.5 disabled:opacity-40">{busy?'확인 중…':'대상 다시 확인'}</button><button type="button" onClick={()=>void project.setStep(2)} className="rounded border border-cyan-700 px-2 py-1.5">팀 검수 열기</button></div></div>
  {value&&<><p className={`mt-2 ${value.ready?'text-slate-300':'text-amber-200'}`}>{reviewTrainingMessage(value)}</p><p className="mt-1 text-xs text-slate-400">라벨 기준 {value.book_version?`v${value.book_version}`:'미발행'} · 반려 {value.counts.rejected} · 불일치 {value.counts.disputed} · 미사용 {value.counts.unused}. 검수 정책과 대상 해시는 학습 계보에 보관합니다.</p><details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">기준·정책·대상 해시</summary><p className="break-all">{value.book_sha256||'기준 없음'}<br/>{value.policy_sha256}<br/>{value.eligibility_sha256}</p></details></>}
  {error&&<p role="alert" className="mt-2 text-rose-200">{error}</p>}
 </section>;
}
