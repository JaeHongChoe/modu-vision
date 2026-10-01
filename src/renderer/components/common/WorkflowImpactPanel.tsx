import {useEffect,useState} from 'react';
import {request} from '../../services/api';
import {useDeliveryScope} from '../runtime/useDeliveryScope';

type Evidence={state?:string;checkpoint_state?:string;data_state?:string;reason?:string;job_id?:string;version_id?:string;package_id?:string;evaluation_id?:string;revision_id?:string;name?:string};
type Impact={models:Evidence[];flows:Evidence[];model_evaluations:Evidence[];flow_evaluations:Evidence[];approvals:Evidence[];packages:Evidence[];required_actions:string[]};
const actions:Record<string,string>={review_data:'변경 라벨 검수',train_candidate:'새 데이터로 후보 재학습',evaluate_model:'모델 재평가',compare_fixed_cohort:'동일 시험 데이터로 비교',approve_model:'검토 후 모델 승인',evaluate_flow:'전체 플로우 재평가',export_package:'검증 후 패키지 다시 생성',verify_target:'대상 장치에서 실행 확인',verify_dataset_version:'학습 데이터 버전 확인'};
const states:Record<string,string>={current:'현재 입력과 일치',changed:'변경됨',revalidation_required:'재검증 필요',unverified:'근거 확인 필요',different_source:'이전 데이터 원본 · 재사용 계보 확인 필요'};

export function WorkflowImpactPanel(){
  const {scope,key,projectDir}=useDeliveryScope();
  const [open,setOpen]=useState(false),[result,setResult]=useState<Impact|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{setResult(null);setError('');setBusy(false);},[key]);
  const refresh=async()=>{
    const started=scope.current;setBusy(true);setError('');
    try{const value=await request<Impact>('/api/provenance/impact');if(scope.current===started)setResult(value);}
    catch(cause){if(scope.current===started)setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(scope.current===started)setBusy(false);}
  };
  if(!projectDir)return null;
  const groups: [string,Evidence[]][] = result ? [['학습 모델',result.models],['저장 플로우',result.flows],['모델 평가',result.model_evaluations],['승인 기록',result.approvals],['배포 패키지',result.packages]] : [];
  return <section className="relative shrink-0 border-b border-slate-700 bg-[#101722] text-xs" aria-label="데이터·모델 변경 영향">
    <button type="button" className="m-2 rounded border border-amber-800 px-3 py-2 text-amber-200" aria-expanded={open} onClick={()=>{setOpen(value=>!value);if(!open&&!result)void refresh();}}>데이터·모델 변경 영향 확인</button>
    {open&&<div className="absolute inset-x-2 top-full z-[96] max-h-[70vh] space-y-3 overflow-auto rounded border border-slate-600 bg-[#151D2A] p-4 shadow-xl">
      <header className="flex items-center justify-between gap-2"><h3 className="font-semibold text-slate-100">변경 후 필요한 작업</h3><div className="flex gap-2"><button type="button" disabled={busy} onClick={()=>void refresh()} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">현재 입력 다시 확인</button><button type="button" onClick={()=>setOpen(false)} className="rounded border border-slate-600 px-3 py-2">닫기</button></div></header>
      <p className="text-slate-400">현재 이미지·라벨·분할·체크포인트와 저장된 학습 버전을 비교합니다. 과거 결과는 보존하며, 입력이 일치해도 품질 승인이나 현장 배포 검증을 뜻하지 않습니다. 수정 후 다시 확인하세요.</p>
      {busy&&<p role="status" className="text-cyan-200">저장 근거와 현재 파일을 확인하는 중…</p>}{error&&<p role="alert" className="text-rose-200">{error}</p>}
      {result&&<><div className="flex flex-wrap gap-2">{result.required_actions.length?result.required_actions.map(action=><span key={action} className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-amber-200">{actions[action]||action}</span>):<p className="text-slate-300">현재 확인한 기록에서 추가 작업이 발견되지 않았습니다. 아래 근거의 확인 상태를 함께 보세요.</p>}</div>
        <div className="grid gap-2 md:grid-cols-2">{groups.map(([title,rows])=><details key={title} className="rounded border border-slate-700 p-3"><summary className="cursor-pointer text-slate-200">{title} · {rows.length}</summary><ul className="mt-2 space-y-2">{rows.map((row,index)=><li key={row.job_id||row.version_id||row.package_id||row.evaluation_id||row.revision_id||index} className="break-all rounded bg-slate-950 p-2"><span className="font-mono">{row.name||row.job_id||row.version_id||row.evaluation_id||row.revision_id||row.package_id}</span><p className="text-amber-200">{row.state?states[row.state]||row.state: `모델 ${states[row.checkpoint_state||'unverified']} · 데이터 ${states[row.data_state||'unverified']}`}</p>{row.reason&&<p className="text-slate-400">{row.reason}</p>}</li>)}</ul>{!rows.length&&<p className="mt-2 text-slate-400">저장 기록 없음</p>}</details>)}</div>
        <p className="text-slate-400">전체 플로우 평가 {result.flow_evaluations.length}건의 상세 입력·정답 유효성은 5단계의 전체 플로우 평가에서 확인할 수 있습니다.</p></>}
    </div>}
  </section>;
}
