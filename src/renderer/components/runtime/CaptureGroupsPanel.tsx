import {useEffect,useRef,useState} from 'react';
import {captureGroups,type CaptureGroupState,type CaptureJoinPolicy} from '../../services/captureGroups';
import {useFlowchartStore} from '../../stores/useFlowchartStore';
const defaults:CaptureJoinPolicy={revision:1,policy:{required_view_ids:['front','back'],timestamp_basis:'trigger_offset',max_skew_ms:20,deadline_ms:1000,late_window_ms:1000,completeness_policy:'all_required'}};
export function CaptureGroupsPanel({scopeKey}:{scopeKey:string}){
  const [state,setState]=useState<CaptureGroupState|null>(null),[draft,setDraft]=useState(defaults),[views,setViews]=useState('front, back'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const generation=useRef(0);const pipeline=useFlowchartStore(value=>value.pipeline);
  const apply=(value:CaptureGroupState)=>{setState(value);setDraft(value.policy?{...value.policy,revision:value.policy.revision+1}:defaults);setViews((value.policy?.policy.required_view_ids||defaults.policy.required_view_ids).join(', '));};
  useEffect(()=>{const started=++generation.current;setState(null);setError('');setNotice('');setDraft(defaults);setViews('front, back');setBusy(false);
    captureGroups.status().then(value=>{if(started===generation.current)apply(value);}).catch(cause=>{if(started===generation.current)setError(String(cause.message||cause));});return()=>{generation.current++;};},[scopeKey]);
  const action=async(save:boolean)=>{const started=generation.current;setBusy(true);setError('');setNotice('');try{
    if(save)await captureGroups.save({...draft,policy:{...draft.policy,required_view_ids:views.split(',').map(value=>value.trim()).filter(Boolean)}},state?.policy?.revision||0);
    const value=await captureGroups.status();if(started===generation.current){apply(value);if(save)setNotice('부품 view 수집 정책을 저장했습니다.');}
  }catch(cause){if(started===generation.current)setError(cause instanceof Error?cause.message:String(cause));}finally{if(started===generation.current)setBusy(false);}};
  const number=(key:'max_skew_ms'|'deadline_ms'|'late_window_ms',value:string)=>setDraft(previous=>({...previous,policy:{...previous.policy,[key]:Number(value)}}));
  return <details className="rounded border border-slate-700 p-3 text-xs"><summary>부품 여러 view 수집 · 전체 부품 판정</summary>
    <p>{state?.policy?`저장 정책 revision ${state.policy.revision}`:'정책 없음 · 기존 단일 프레임'} · 미완료·시간 초과·식별 충돌은 REVIEW로 확인합니다. 장비 연결 검증은 별도입니다.</p>
    <label>필수 view ID<input aria-label="필수 view ID" value={views} onChange={event=>setViews(event.target.value)}/></label>
    <label>시각 기준<select aria-label="프레임 시각 기준" value={draft.policy.timestamp_basis} onChange={event=>setDraft(previous=>({...previous,policy:{...previous.policy,timestamp_basis:event.target.value as 'shared_clock'|'trigger_offset'}}))}><option value="trigger_offset">트리거 이후 ms</option><option value="shared_clock">공유 시계 ms</option></select></label>
    <label>허용 시각 차이 ms<input aria-label="최대 view 시각 차이" type="number" min={0} value={draft.policy.max_skew_ms} onChange={event=>number('max_skew_ms',event.target.value)}/></label>
    <label>수집 마감 ms<input aria-label="부품 수집 마감" type="number" min={1} value={draft.policy.deadline_ms} onChange={event=>number('deadline_ms',event.target.value)}/></label>
    <label>늦은 프레임 기록 ms<input aria-label="늦은 프레임 기록 한도" type="number" min={0} value={draft.policy.late_window_ms||0} onChange={event=>number('late_window_ms',event.target.value)}/></label>
    <button disabled={busy||!state} onClick={()=>void action(true)}>수집 정책 저장 (소유자)</button><button disabled={busy} onClick={()=>void action(false)}>저장 정책·부품 판정 다시 확인</button>
    <button aria-label="편집 플로우에 수집 정책 연결" disabled={busy||!state?.policy||!pipeline} onClick={()=>{const current=useFlowchartStore.getState();if(current.pipeline&&state?.policy){current.replacePipeline({...current.pipeline,capture_group_policy:state.policy});setNotice('현재 편집 플로우에 정책을 연결했습니다. 플로우 저장·패키지 내보내기에서 이 revision이 보존됩니다.');}}}>현재 편집 플로우에 정책 연결</button>
    {state?.groups.map(row=><article key={`${row.part_id}:${row.trigger_id}`}><p>{`${row.part_id} / ${row.trigger_id} · ${row.state} · ${row.verdict||'판정 대기'}`}</p><p>누락 view: {row.missing_view_ids.join(', ')||'없음'} · 정책 {row.policy_revision}</p><p className="break-all">recipe {row.recipe_sha256}</p>{row.reason&&<p>{row.reason}</p>}{row.alarms?.map((alarm,index)=><p role="alert" key={index}>{alarm}</p>)}</article>)}
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  </details>;
}
