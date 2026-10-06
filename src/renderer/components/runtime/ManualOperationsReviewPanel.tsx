import {useEffect,useRef,useState} from 'react';
import {getApiPersistenceIdentity,getProjectContextGeneration,request,subscribeProjectContext} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {clearTaskHandoff} from '../training/taskHandoff';
import {evaluationOriginScope,rememberReviewOrigin} from '../labeling/productDataWorkflow';
import {useDeliveryScope} from './useDeliveryScope';
import type {SavedFlowVersion} from '../../services/api';

type Handoff={cycle_id:string;state:string;subject:null|{task:string;candidate_job_id:string;comparison_id:string;subject_sha256:string};
  reasons:string[];next_step:4|5|6|null;automatic_action:'none';service_applied:false;device_accepted:false;approval_revision_id:string|null;
  prepared_flow?:null|{version_id:string;receipt_sha256:string;flow_activated:false;service_applied:false};
  delivery?:null|{whole_flow_current:boolean;whole_flow_revision_id:string|null;service_application_recorded:boolean;
    service_runtime_ready:boolean;service_deployment_id:string|null;snapshot_sha256:string;device_accepted:false;reasons:string[]}};
const names:Record<string,string>={awaiting_human_review:'비교 근거 유효 · 사람의 검토 대기',model_approval_current:'현재 모델 승인 확인 · 패키지 준비 가능',
  revalidation_required:'근거 변경 · 재평가 필요',unbound_history:'이전 기록 · 검토 근거 연결 필요'};

export function ManualOperationsReviewPanel({cycleId,flows=[]}:{cycleId:string;flows?:SavedFlowVersion[]}){
  const {key,project,projectDir}=useDeliveryScope(cycleId);
  const compute=useComputeStore();
  const [authority,setAuthority]=useState(getProjectContextGeneration());
  const [value,setValue]=useState<Handoff|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [sourceFlow,setSourceFlow]=useState(''),[reviewer,setReviewer]=useState(''),[reason,setReason]=useState('');
  const currentKey=useRef(key);currentKey.current=key;
  const generation=useRef(0);
  useEffect(()=>subscribeProjectContext(()=>{
    generation.current++;setValue(null);setBusy(false);setError('');setSourceFlow('');setReviewer('');setReason('');setAuthority(getProjectContextGeneration());
  }),[]);
  const read=async()=>{
    const result=await request<Handoff>(`/api/model-operations/cycles/${encodeURIComponent(cycleId)}/review-handoff`);
    if(result.cycle_id!==cycleId||result.automatic_action!=='none'||result.service_applied!==false||result.device_accepted!==false)
      throw new Error('모델 개선 기록의 식별자와 검토 범위가 일치하지 않습니다.');
    if(result.delivery&&(!/^[0-9a-f]{64}$/.test(result.delivery.snapshot_sha256)||result.delivery.device_accepted!==false
      ||typeof result.delivery.whole_flow_current!=='boolean'||typeof result.delivery.service_application_recorded!=='boolean'
      ||typeof result.delivery.service_runtime_ready!=='boolean'||(result.delivery.service_runtime_ready&&!result.delivery.service_application_recorded)))
      throw new Error('후보 플로우와 서비스의 현재 확인 기록이 일치하지 않습니다.');
    return result;
  };
  useEffect(()=>{
    const epoch=++generation.current,started=getProjectContextGeneration();
    setValue(null);setBusy(true);setError('');setSourceFlow('');setReviewer('');setReason('');
    read().then(result=>{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setValue(result);})
      .catch(cause=>{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setError(String(cause));})
      .finally(()=>{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setBusy(false);});
    return()=>{generation.current++;};
  },[key,authority]);
  const eligible=!!value?.subject&&project?.task===value.subject.task
    &&((value.state==='awaiting_human_review'&&value.next_step===4)||(value.state==='model_approval_current'&&(value.next_step===6||value.next_step===5)));
  const choices=flows.filter(flow=>flow.source_dataset_path===project?.source_dataset_dir&&/^[0-9a-f]{64}$/.test(flow.pipeline_hash||''));
  const selected=choices.find(flow=>flow.version_id===sourceFlow);
  const prepare=async()=>{
    const epoch=generation.current,started=authority;
    if(!selected||!value?.subject||value.state!=='model_approval_current'||value.prepared_flow||!eligible||busy
      ||!reviewer.trim()||reason.trim().length<10||currentKey.current!==key||getProjectContextGeneration()!==started)return;
    setBusy(true);setError('');
    try{
      const fresh=await read();
      if(generation.current!==epoch||currentKey.current!==key||getProjectContextGeneration()!==started)return;
      setValue(fresh);
      if(fresh.state!==value.state||fresh.subject?.subject_sha256!==value.subject.subject_sha256||fresh.prepared_flow)
        throw new Error('모델 승인이나 검토 기록이 변경되었습니다. 최신 기록을 다시 여세요.');
      await request(`/api/model-operations/cycles/${encodeURIComponent(cycleId)}/prepare-flow`,{method:'POST',body:JSON.stringify({
        source_version_id:selected.version_id,expected_graph_sha256:selected.pipeline_hash,expected_subject_sha256:value.subject.subject_sha256,
        reviewer:reviewer.trim(),reason:reason.trim()})});
      if(generation.current!==epoch||currentKey.current!==key||getProjectContextGeneration()!==started)return;
      const reopened=await read();
      if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setValue(reopened);
    }catch(cause){if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setError(String(cause));}
    finally{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setBusy(false);}
  };
  const open=async()=>{
    const epoch=generation.current,started=authority;
    if(!eligible||!value?.subject||busy||currentKey.current!==key||getProjectContextGeneration()!==started)return;
    setBusy(true);setError('');
    try{
      const fresh=await read();
      if(generation.current!==epoch||currentKey.current!==key||getProjectContextGeneration()!==started)return;
      setValue(fresh);
      if(fresh.subject?.subject_sha256!==value.subject.subject_sha256||fresh.state!==value.state||fresh.next_step!==value.next_step
        ||fresh.prepared_flow?.receipt_sha256!==value.prepared_flow?.receipt_sha256
        ||fresh.delivery?.snapshot_sha256!==value.delivery?.snapshot_sha256)
        throw new Error('검토 상태가 변경되었습니다. 최신 근거를 확인한 뒤 다시 진행하세요.');
      const state={projectDir,project,...compute,apiTransportIdentity:getApiPersistenceIdentity()};
      clearTaskHandoff(localStorage,state);
      if(fresh.next_step===4)rememberReviewOrigin(localStorage,
        evaluationOriginScope(project?.id,project!.source_dataset_dir!,fresh.subject.task,project?.active_labelset_id||'default',state),
        fresh.subject.comparison_id,undefined,undefined,'comparison');
      if(fresh.next_step===4||fresh.next_step===5||fresh.next_step===6)await useProjectStore.getState().setStep(fresh.next_step);
    }catch(cause){if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setError(String(cause));}
    finally{if(generation.current===epoch&&currentKey.current===key&&getProjectContextGeneration()===started)setBusy(false);}
  };
  return <section aria-label="모델 개선 수동 검토 연결" className="mt-2 space-y-2 rounded border border-cyan-900 p-2">
    <p>{value?names[value.state]||'검토 상태 확인 필요':'저장된 모델 개선 근거 확인 중…'}</p>
    {value?.subject&&<p className="break-all text-slate-400">후보 {value.subject.candidate_job_id} · 비교 {value.subject.comparison_id}{value.approval_revision_id&&` · 승인 ${value.approval_revision_id}`}</p>}
    {value?.reasons.map(reason=><p key={reason} className="break-all text-amber-200">{reason}</p>)}
    {value?.subject&&project?.task!==value.subject.task&&<p className="text-amber-200">현재 프로젝트 작업을 {value.subject.task}로 선택한 뒤 같은 기록을 다시 여세요.</p>}
    <p className="text-slate-400">이 화면에서는 검토하거나 승인하지 않습니다. 모델 승인 이후에도 플로우·패키지 검증과 대상 장치 적용은 별도로 진행하세요.</p>
    {value?.state==='model_approval_current'&&!value.prepared_flow&&<fieldset className="space-y-2" disabled={busy}>
      <legend>후보 플로우 준비</legend><p>시작 모델이 포함된 저장 플로우를 선택하세요. 후보를 넣은 별도 버전만 저장하며, 활성 플로우와 서비스는 유지합니다.</p>
      <label className="block">원본 저장 플로우<select aria-label="개선 후보 원본 플로우" value={sourceFlow} onChange={event=>setSourceFlow(event.target.value)}>
        <option value="">저장 버전 선택</option>{choices.map(flow=><option key={flow.version_id} value={flow.version_id}>{flow.name} · {flow.version_id.slice(0,8)}</option>)}</select></label>
      <label className="block">준비 담당자<input aria-label="후보 플로우 준비 담당자" value={reviewer} onChange={event=>setReviewer(event.target.value)}/></label>
      <label className="block">준비 이유<input aria-label="후보 플로우 준비 이유" value={reason} onChange={event=>setReason(event.target.value)}/></label>
      <button aria-label="검토용 후보 플로우 준비" disabled={!eligible||busy||!selected||!reviewer.trim()||reason.trim().length<10} onClick={prepare}>검토용 후보 플로우 준비</button>
    </fieldset>}
    {value?.prepared_flow&&<p className="break-all text-cyan-200">후보 플로우 {value.prepared_flow.version_id} · {value.delivery?.whole_flow_current
      ?'현재 전체 흐름 검토 확인 · 패키지와 서비스 단계에서 이어가세요.'
      :'전체 흐름 검토 대기. 플로우 단계의 저장 버전에서 이 후보를 열어 평가하세요.'}</p>}
    {value?.delivery?.service_application_recorded&&<p className="break-all text-cyan-200">{value.delivery.service_runtime_ready
      ?'현재 독립 서비스 응답 확인':'서비스 적용 기록 확인 · 현재 실행 응답은 확인되지 않음'} · {value.delivery.service_deployment_id}. 실제 장치와 공정 품질은 별도로 확인하세요.</p>}
    {value?.delivery?.reasons.map(reason=><p key={reason} className="break-all text-slate-400">{reason}</p>)}
    <button aria-label="모델 개선 다음 단계 열기" className="rounded border border-cyan-800 p-2 text-cyan-200 disabled:opacity-40" disabled={!eligible||busy} onClick={open}>
      {value?.next_step===5?'후보 플로우 검토 단계 열기':value?.next_step===6?'승인 모델 패키지 단계 열기':'저장된 비교 검토 열기'}
    </button>
    {busy&&<p role="status">근거 다시 확인 중…</p>}{error&&<p role="alert" className="break-all text-red-300">{error}</p>}
  </section>;
}
