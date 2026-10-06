import {useEffect,useRef,useState} from 'react';
import type {FlowEvaluation} from '../../services/flowEvaluationTypes';
import {wholeFlowApproval,type WholeFlowApproval,type WholeFlowPolicy} from '../../services/wholeFlowApproval';

const input='rounded border border-slate-600 bg-slate-950 p-2';
const initial:WholeFlowPolicy={policy_id:'',revision:1,minimum_normal:1,minimum_defect:1,
  maximum_escape_rate:0,maximum_overkill_rate:0,maximum_review_rate:0};

export function WholeFlowApprovalPanel({evaluation,contextKey}:{evaluation:FlowEvaluation|null;contextKey:string}) {
  const [active,setActive]=useState<WholeFlowApproval|null>(null),[loaded,setLoaded]=useState(false);
  const [policy,setPolicy]=useState<WholeFlowPolicy>(initial),[reviewer,setReviewer]=useState(''),[reason,setReason]=useState('');
  const [reviewed,setReviewed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const generation=useRef(0);
  useEffect(()=>{
    const epoch=++generation.current;setLoaded(false);setActive(null);setReviewed(false);setPolicy(initial);
    setReviewer('');setReason('');setError('');setNotice('');setBusy(false);
    wholeFlowApproval.active().then(value=>{if(generation.current===epoch){setActive(value);setLoaded(true);}})
      .catch(cause=>{if(generation.current===epoch)setError(String(cause));});
    return()=>{generation.current++;};
  },[contextKey,evaluation?.evaluation_id,evaluation?.validity.valid]);
  const eligible=loaded&&evaluation?.validity.valid&&evaluation.status==='completed'
    &&evaluation.coverage.unknown===0&&evaluation.coverage.invalidated===0&&evaluation.errors.length===0;
  const submit=async()=>{
    if(!evaluation||!eligible||!reviewed||busy)return;
    const epoch=generation.current;setBusy(true);setError('');setNotice('');
    try{
      const value=await wholeFlowApproval.approve({evaluation_id:evaluation.evaluation_id,policy,reviewer,reason,
        holdout_reviewed:reviewed,expected_revision:active?.revision_id||null});
      if(generation.current===epoch){setActive(value);setReviewed(false);setNotice('전체 흐름 검토를 저장했습니다. 패키지·대상 장치 적격성은 별도로 확인하세요.');}
    }catch(cause){if(generation.current===epoch){setError(String(cause));setLoaded(false);
      try{const value=await wholeFlowApproval.active();if(generation.current===epoch){setActive(value);setLoaded(true);}}catch{/* Preserve the original refusal. */}
    }}finally{if(generation.current===epoch)setBusy(false);}
  };
  return <details className="space-y-3 rounded border border-slate-600 p-3">
    <summary className="cursor-pointer text-cyan-200">전체 흐름 품질 검토·승인</summary>
    <p className="text-slate-400">저장된 그래프·모든 모델·정답·시험 분할과 공정 기준을 함께 고정합니다. 검토 저장은 실행 서비스 적용과 별도입니다.</p>
    {active&&<p className={active.validity.valid?'text-emerald-200':'text-amber-200'}>현재 검토: {active.revision_id} · {active.validity.valid?'근거 유효':'재평가 필요'} · 검토자 {active.reviewer}{active.validity.reasons.length>0&&` · ${active.validity.reasons.join(', ')}`}</p>}
    <div className="grid gap-2 sm:grid-cols-2">
      <label>공정 기준 ID<input aria-label="공정 기준 ID" className={input} value={policy.policy_id} onChange={e=>setPolicy({...policy,policy_id:e.target.value})}/></label>
      {([
        ['revision','기준 revision',1,100000,1],['minimum_normal','최소 정상 표본',1,5000,1],['minimum_defect','최소 불량 표본',1,5000,1],
        ['maximum_escape_rate','허용 미검률 (0–1)',0,1,.001],['maximum_overkill_rate','허용 과검률 (0–1)',0,1,.001],['maximum_review_rate','허용 검토율 (0–1)',0,1,.001],
      ] as const).map(([key,label,min,max,step])=><label key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={step} className={input} value={policy[key]} onChange={e=>setPolicy({...policy,[key]:e.target.valueAsNumber})}/></label>)}
      <label>검토자<input aria-label="전체 흐름 검토자" className={input} value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label>
    </div>
    <label className="block">검토 이유<textarea aria-label="전체 흐름 검토 이유" className={input+' block w-full'} value={reason} onChange={e=>setReason(e.target.value)}/></label>
    <label className="block"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/> 시험 정답과 전체 흐름 판정을 직접 검토했습니다.</label>
    {!eligible&&<p className="text-amber-200">모든 정답이 확인되고 실행 오류가 없는 최신 전체 흐름 평가가 필요합니다.</p>}
    <button className="rounded border border-cyan-800 p-2 text-cyan-200 disabled:opacity-40" disabled={!eligible||!reviewed||busy||!policy.policy_id.trim()||!reviewer.trim()||reason.trim().length<8} onClick={()=>void submit()}>전체 흐름 검토 저장</button>
    {busy&&<p role="status">전체 흐름 검토 저장 중…</p>}{notice&&<p role="status" className="text-emerald-200">{notice}</p>}{error&&<p role="alert" className="text-red-200">{error}</p>}
  </details>;
}
