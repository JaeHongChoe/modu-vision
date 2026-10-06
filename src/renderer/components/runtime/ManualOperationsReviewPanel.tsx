import {useEffect,useRef,useState} from 'react';
import {getApiPersistenceIdentity,getProjectContextGeneration,request,subscribeProjectContext} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {clearTaskHandoff} from '../training/taskHandoff';
import {evaluationOriginScope,rememberReviewOrigin} from '../labeling/productDataWorkflow';
import {useDeliveryScope} from './useDeliveryScope';

type Handoff={cycle_id:string;state:string;subject:null|{task:string;candidate_job_id:string;comparison_id:string;subject_sha256:string};
  reasons:string[];next_step:4|6|null;automatic_action:'none';service_applied:false;device_accepted:false;approval_revision_id:string|null};
const names:Record<string,string>={awaiting_human_review:'비교 근거 유효 · 사람의 검토 대기',model_approval_current:'현재 모델 승인 확인 · 패키지 준비 가능',
  revalidation_required:'근거 변경 · 재평가 필요',unbound_history:'이전 기록 · 검토 근거 연결 필요'};

export function ManualOperationsReviewPanel({cycleId}:{cycleId:string}){
  const {key,project,projectDir}=useDeliveryScope(cycleId);
  const compute=useComputeStore();
  const [authority,setAuthority]=useState(getProjectContextGeneration());
  const [value,setValue]=useState<Handoff|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const currentKey=useRef(key);currentKey.current=key;
  const generation=useRef(0);
  useEffect(()=>subscribeProjectContext(()=>{
    generation.current++;setValue(null);setBusy(false);setError('');setAuthority(getProjectContextGeneration());
  }),[]);
  const read=async()=>{
    const result=await request<Handoff>(`/api/model-operations/cycles/${encodeURIComponent(cycleId)}/review-handoff`);
    if(result.cycle_id!==cycleId||result.automatic_action!=='none'||result.service_applied!==false||result.device_accepted!==false)
      throw new Error('모델 개선 기록의 식별자와 검토 범위가 일치하지 않습니다.');
    return result;
  };
  useEffect(()=>{
    const epoch=++generation.current,started=getProjectContextGeneration();
    setValue(null);setBusy(true);setError('');
    read().then(result=>{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setValue(result);})
      .catch(cause=>{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setError(String(cause));})
      .finally(()=>{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setBusy(false);});
    return()=>{generation.current++;};
  },[key,authority]);
  const eligible=!!value?.subject&&project?.task===value.subject.task
    &&((value.state==='awaiting_human_review'&&value.next_step===4)||(value.state==='model_approval_current'&&value.next_step===6));
  const open=async()=>{
    const epoch=generation.current,started=authority;
    if(!eligible||!value?.subject||busy||currentKey.current!==key||getProjectContextGeneration()!==started)return;
    setBusy(true);setError('');
    try{
      const fresh=await read();
      if(generation.current!==epoch||currentKey.current!==key||getProjectContextGeneration()!==started)return;
      setValue(fresh);
      if(fresh.subject?.subject_sha256!==value.subject.subject_sha256||fresh.state!==value.state||fresh.next_step!==value.next_step)
        throw new Error('검토 상태가 변경되었습니다. 최신 근거를 확인한 뒤 다시 진행하세요.');
      const state={projectDir,project,...compute,apiTransportIdentity:getApiPersistenceIdentity()};
      clearTaskHandoff(localStorage,state);
      if(fresh.next_step===4)rememberReviewOrigin(localStorage,
        evaluationOriginScope(project?.id,project!.source_dataset_dir!,fresh.subject.task,project?.active_labelset_id||'default',state),
        fresh.subject.comparison_id,undefined,undefined,'comparison');
      if(fresh.next_step===4||fresh.next_step===6)await useProjectStore.getState().setStep(fresh.next_step);
    }catch(cause){if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setError(String(cause));}
    finally{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setBusy(false);}
  };
  return <section aria-label="모델 개선 수동 검토 연결" className="mt-2 space-y-2 rounded border border-cyan-900 p-2">
    <p>{value?names[value.state]||'검토 상태 확인 필요':'저장된 모델 개선 근거 확인 중…'}</p>
    {value?.subject&&<p className="break-all text-slate-400">후보 {value.subject.candidate_job_id} · 비교 {value.subject.comparison_id}{value.approval_revision_id&&` · 승인 ${value.approval_revision_id}`}</p>}
    {value?.reasons.map(reason=><p key={reason} className="break-all text-amber-200">{reason}</p>)}
    {value?.subject&&project?.task!==value.subject.task&&<p className="text-amber-200">현재 프로젝트 작업을 {value.subject.task}로 선택한 뒤 같은 기록을 다시 여세요.</p>}
    <p className="text-slate-400">이 화면에서는 검토하거나 승인하지 않습니다. 모델 승인 이후에도 플로우·패키지 검증과 대상 장치 적용은 별도로 진행하세요.</p>
    <button aria-label="모델 개선 다음 단계 열기" className="rounded border border-cyan-800 p-2 text-cyan-200 disabled:opacity-40" disabled={!eligible||busy} onClick={open}>
      {value?.next_step===6?'승인 모델 패키지 단계 열기':'저장된 비교 검토 열기'}
    </button>
    {busy&&<p role="status">근거 다시 확인 중…</p>}{error&&<p role="alert" className="break-all text-red-300">{error}</p>}
  </section>;
}
