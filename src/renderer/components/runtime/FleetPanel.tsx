import type {WorkflowStep} from '../wizard/workflowReadiness';
import {useProjectStore} from '../../stores/useProjectStore';
import {useEffect,useRef,useState} from 'react';
import {request} from '../../services/api';
import {useDeliveryScope} from './useDeliveryScope';
import {SavedPackagePicker} from './SavedPackagePicker';
import {emergencyAcknowledged,type RollbackCapabilities,type EmergencyRollbackEvent,type EmergencyRollbackReceipt} from './emergencyRollbackPolicy';
type Target={target_id:string;name:string;url:string;token_set:boolean};
type Deployment={deployment_id:string;created_at:number;reviewer:string;restored_from?:string;release:{manifest_sha256:string;device:string}};
type AgentState={target:Target;runtime:{status:string;device?:string;manifest_sha256?:string;model_sha256?:Record<string,string>};active:Deployment|null;history:Deployment[];matches_active:boolean;error?:string;emergency_rollback_events?:EmergencyRollbackEvent[]};
const control='min-w-0 rounded border border-slate-600 bg-[#0D1622] p-2 text-xs';
export function FleetPanel({onNavigate}:{onNavigate?:(step:WorkflowStep)=>void}={}){
  const {projectDir,scope,key}=useDeliveryScope();
  const [targets,setTargets]=useState<Target[]>([]),[selected,setSelected]=useState(''),[state,setState]=useState<AgentState|null>(null),[name,setName]=useState(''),[url,setUrl]=useState(''),[token,setToken]=useState(''),[packagePath,setPackagePath]=useState(''),[reviewer,setReviewer]=useState(''),[device,setDevice]=useState('cpu'),[rollback,setRollback]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [capabilityRecord,setCapabilities]=useState<{key:string;value:RollbackCapabilities}|null>(null);
  const [reason,setReason]=useState(''),[emergencyStatus,setEmergencyStatus]=useState<'idle'|'submitting'|'acknowledged'|'failed'|'unverified'>('idle'),[emergencyError,setEmergencyError]=useState(''),[submittedReason,setSubmittedReason]=useState('');
  const lastReceipt=useRef<{scope:{key:string};targetId:string;receipt:EmergencyRollbackReceipt;reason:string}|null>(null);
  const selectedRef=useRef(selected);selectedRef.current=selected;
  const capabilities=capabilityRecord?.key===key?capabilityRecord.value:null;
  const [capabilityError,setCapabilityError]=useState('');
  const capabilityRequest=useRef(0);
  const refreshCapabilities=async()=>{
    const started=scope.current,generation=++capabilityRequest.current;
    const current=()=>scope.current===started&&capabilityRequest.current===generation;
    setCapabilities(null);setCapabilityError('');
    try{const value=await request<RollbackCapabilities>('/api/fleet/capabilities');if(current())setCapabilities({key:started.key,value});}
    catch(cause){if(current())setCapabilityError('롤백 권한 확인 실패: '+String((cause as Error).message||cause)+' · 실제 상태 확인으로 다시 확인하세요.');}
  };
  useEffect(()=>{
    let current=true;const started=scope.current;
    setTargets([]);setSelected('');selectedRef.current='';setState(null);setName('');setUrl('');setToken('');setPackagePath('');setReviewer('');setRollback('');setBusy(false);setError('');setCapabilities(null);setCapabilityError('');setReason('');setEmergencyStatus('idle');setEmergencyError('');lastReceipt.current=null;setSubmittedReason('');
    if(projectDir){
      request<{targets:Target[]}>('/api/fleet/targets').then(r=>{if(current&&scope.current===started)setTargets(r.targets);}).catch(e=>{if(current&&scope.current===started)setError(String(e.message||e));});
      void refreshCapabilities();
    }
    return()=>{current=false;};
  },[key]);
  const read=async(identifier:string)=>{
    const started=scope.current;
    const current=()=>scope.current===started&&selectedRef.current===identifier;
    const pending=()=>lastReceipt.current?.scope===started&&lastReceipt.current.targetId===identifier?lastReceipt.current:null;
    if(current()&&pending()){
      setEmergencyStatus(previous=>previous==='submitting'?previous:'unverified');
      setEmergencyError('요청 전송됨 · 적용 확인 필요. 장비 응답과 감사 기록을 확인 중입니다.');
    }
    try{
      const result=await request<AgentState>(`/api/fleet/targets/${encodeURIComponent(identifier)}`);
      if(result.target?.target_id!==identifier)throw new Error('응답 장비가 선택한 장비와 다릅니다.');
      if(current()){
        setState(result);
        const saved=pending();
        if(saved){
          const acknowledged=emergencyAcknowledged(identifier,saved.receipt,result);
          setEmergencyStatus(acknowledged?'acknowledged':'unverified');
          setEmergencyError(acknowledged?'':'요청 전송됨 · 적용 확인 필요. 최신 장비 응답과 감사 기록이 요청한 적용 결과와 일치하지 않습니다.');
        }
      }
      return result;
    }catch(cause){
      if(current()&&pending()){
        setEmergencyStatus('unverified');
        setEmergencyError('요청 전송됨 · 적용 확인 필요. 실제 상태 확인으로 장비 응답과 감사 기록을 다시 확인하세요.');
      }
      throw cause;
    }
  };
  const refreshActualState=async()=>{
    // Capability failure disables actions independently of target evidence.
    const [targetResult]=await Promise.allSettled([read(selected),refreshCapabilities()]);
    if(targetResult.status==='rejected')throw targetResult.reason;
  };
  const action=async(fn:()=>Promise<unknown>)=>{const started=scope.current;setBusy(true);setError('');try{await fn();}catch(e){if(scope.current===started)setError(String((e as Error).message||e));}finally{if(scope.current===started)setBusy(false);}};
  const add=async()=>{const started=scope.current;await request('/api/fleet/targets',{method:'POST',body:JSON.stringify({name,url,token})});if(scope.current!==started)return;setToken('');setName('');setUrl('');const result=await request<{targets:Target[]}>('/api/fleet/targets');if(scope.current===started)setTargets(result.targets);};
  const deploy=async()=>{const started=scope.current;await request(`/api/fleet/targets/${encodeURIComponent(selected)}/deploy`,{method:'POST',body:JSON.stringify({package_path:packagePath,device,reviewer})});if(scope.current===started)await read(selected);};
  const restore=async()=>{const started=scope.current;await request(`/api/fleet/targets/${encodeURIComponent(selected)}/rollback`,{method:'POST',body:JSON.stringify({deployment_id:rollback,reviewer})});if(scope.current===started)await read(selected);};
  const emergencyRestore=async()=>{
    if(busy||!capabilities?.can_emergency_rollback||!selected||!rollback||!reason.trim())return;
    const started=scope.current,identifier=selected,deploymentId=rollback,incidentReason=reason.trim();
    const current=()=>scope.current===started&&selectedRef.current===identifier;
    lastReceipt.current=null;setSubmittedReason('');
    setBusy(true);setEmergencyStatus('submitting');setEmergencyError('');setError('');
    try{
      // Read the audit before the request and verify its new committed receipt
      // against target runtime readback before presenting an acknowledgement.
      await read(identifier);if(!current())return;
      const result=await request<EmergencyRollbackReceipt>(`/api/fleet/targets/${encodeURIComponent(identifier)}/emergency-rollback`,{method:'POST',body:JSON.stringify({deployment_id:deploymentId,reason:incidentReason})});
      if(!current())return;
      lastReceipt.current={scope:started,targetId:identifier,receipt:result,reason:incidentReason};
      setSubmittedReason(incidentReason);
      await read(identifier);
    }catch(cause){
      if(!current())return;
      let message=String((cause as Error).message||cause);
      try{await read(identifier);}catch{message+=' · 감사 기록을 다시 불러오지 못했습니다.';}
      // A successful POST is retained even if its readback is unavailable.
      // read() can verify that receipt on this retry or a later manual refresh.
      if(current()&&!lastReceipt.current){setEmergencyStatus('failed');setEmergencyError(message);}
    }finally{if(current())setBusy(false);}
  };
  const deployBlocker=busy?'응답 확인 중입니다.':!selected?'현장 장비를 선택하세요.':!packagePath.trim()?'승인 포함 패키지가 필요합니다.':!reviewer.trim()?'적용 검토자를 입력하세요.':null;
  if(!projectDir)return null;
  return <details className="mt-3 rounded-lg border border-slate-700 bg-[#142131] p-3"><summary className="cursor-pointer text-sm font-semibold text-cyan-200">중앙 · 현장 장비 모델 관리</summary><div className="mt-3 space-y-3 text-xs text-slate-200">
    <p className="leading-5 text-slate-400">장비에 Field Agent를 실행한 뒤 HTTPS 주소 또는 SSH 터널로 등록하세요. 승인된 전체 플로우를 전송하고 장비가 실제 사용하는 manifest·장치·모델 해시를 확인합니다.</p>
    <details><summary className="cursor-pointer text-slate-300">현장 장비 추가</summary><div className="mt-2 grid gap-2 sm:grid-cols-2"><input aria-label="현장 장비 이름" placeholder="장비 이름" className={control} value={name} onChange={e=>setName(e.target.value)}/><input aria-label="현장 Agent 주소" placeholder="https://cell.example.com" className={control} value={url} onChange={e=>setUrl(e.target.value)}/><input aria-label="현장 Agent 인증값" type="password" autoComplete="off" placeholder="Agent access token" className={`${control} sm:col-span-2`} value={token} onChange={e=>setToken(e.target.value)}/><button disabled={busy||!name.trim()||!url.trim()||token.length<16} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-40" onClick={()=>void action(add)}>연결 설정 저장</button></div></details>
    <div className="flex gap-2"><select aria-label="현장 장비 선택" className={`${control} flex-1`} disabled={busy} value={selected} onChange={e=>{const id=e.target.value;selectedRef.current=id;setSelected(id);setState(null);setRollback('');setReason('');setEmergencyStatus('idle');setEmergencyError('');lastReceipt.current=null;setSubmittedReason('');if(id)void action(()=>read(id));}}><option value="">장비 선택</option>{targets.map(t=><option key={t.target_id} value={t.target_id}>{t.name} · {t.url}</option>)}</select><button className="text-cyan-200" disabled={busy||!selected} onClick={()=>void action(refreshActualState)}>실제 상태 확인</button></div>
    {state&&<section className="rounded border border-slate-700 bg-slate-950/40 p-2"><p>{state.runtime.status==='ready'?'장비 응답 확인됨':state.runtime.status==='stopped'?'장비 검사 서비스 중지됨':'장비 연결 확인 필요'} · {state.runtime.device||'장치 응답 없음'} · {state.matches_active?'중앙 적용 기록과 일치':'중앙 기록과 실행 상태 확인 필요'}</p><p className="mt-1 break-all font-mono text-[10px]">실행 manifest: {state.runtime.manifest_sha256||'—'}</p>{Object.entries(state.runtime.model_sha256||{}).map(([id,hash])=><p key={id} className="mt-1 break-all font-mono text-[10px]">{id} · {hash}</p>)}</section>}
    <div className="grid gap-2 sm:grid-cols-2"><SavedPackagePicker value={packagePath} onChange={setPackagePath} disabled={busy}/><details className="sm:col-span-2"><summary className="text-slate-400">고급 · 패키지 폴더 직접 입력</summary><input aria-label="현장 배포 승인 패키지" className={`${control} w-full`} value={packagePath} onChange={e=>setPackagePath(e.target.value)}/></details><label>적용 검토자<input aria-label="현장 배포 검토자" className={`${control} w-full`} value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label><label>장비 실행 자원<input aria-label="현장 실행 자원" className={`${control} w-full`} value={device} onChange={e=>setDevice(e.target.value)} placeholder="cpu / cuda:0 / openvino:CPU"/></label></div>
    {deployBlocker&&<div className="mt-2 text-xs text-amber-200"><p id="fleet-deploy-reason">적용 보류: {deployBlocker}</p><button className="workspace-button mt-2" onClick={()=>{if(selected&&!packagePath.trim())void (onNavigate||useProjectStore.getState().setStep)(6);else document.querySelector<HTMLElement>(!selected?'[aria-label="현장 장비 선택"]':'[aria-label="현장 배포 검토자"]')?.focus();}}>{!selected?'현장 장비 선택':!packagePath.trim()?'승인·패키지 확인 (6단계)':'적용 입력 확인'}</button></div>}
    <button aria-describedby={deployBlocker?'fleet-deploy-reason':undefined} disabled={busy||!selected||!packagePath.trim()||!reviewer.trim()} onClick={()=>void action(deploy)} className="rounded bg-cyan-700 px-3 py-2 disabled:opacity-40">패키지 전송·적용 응답 확인</button>
    <div className="flex gap-2"><select aria-label="현장 배포 복원 이력" className={`${control} flex-1`} disabled={busy} value={rollback} onChange={e=>{setRollback(e.target.value);setEmergencyStatus('idle');setEmergencyError('');lastReceipt.current=null;setSubmittedReason('');}}><option value="">복원할 적용 기록</option>{state?.history.map(h=><option key={h.deployment_id} value={h.deployment_id}>{new Date(h.created_at*1000).toLocaleString()} · {h.release.manifest_sha256.slice(0,12)} · {h.release.device}</option>)}</select><button disabled={busy||!selected||!rollback||!reviewer.trim()||!capabilities?.can_rollback} onClick={()=>void action(restore)} className="rounded border border-amber-700 px-3 disabled:opacity-40">장비 롤백</button></div>
    <section aria-label="긴급 롤백" className="space-y-2 rounded-lg border border-amber-700/60 bg-amber-950/20 p-3">
      <h3 className="font-semibold text-amber-200">긴급 롤백</h3>
      <p className="leading-5 text-slate-300">선택한 장비와 복원 이력에 사고 사유를 기록합니다. 현재 승인 상태를 확인한 뒤 장비 적용 응답과 감사 기록을 확인합니다.</p>
      <p className="text-amber-200">{capabilities?(capabilities.can_emergency_rollback?`요청 계정: ${capabilities.actor_name}`:'프로젝트 소유자 권한이 필요합니다.'):capabilityError?'롤백 권한을 확인하지 못했습니다.':'롤백 권한 확인 중…'}</p>
      {capabilityError&&<p role="alert" className="text-amber-200">{capabilityError}</p>}
      <p className="text-slate-400">대상: {targets.find(target=>target.target_id===selected)?.name||'장비를 선택하세요'} · 복원 기록: {rollback||'적용 기록을 선택하세요'}</p>
      <label className="block">긴급 롤백 사유 <span className="text-amber-300">필수</span><textarea aria-label="긴급 롤백 사유" required maxLength={2000} rows={2} disabled={busy||!capabilities?.can_emergency_rollback} value={reason} onChange={event=>setReason(event.target.value)} placeholder="설비 상태와 복원이 필요한 이유를 입력하세요" className={`${control} mt-1 w-full disabled:opacity-50`}/></label>
      <button type="button" disabled={busy||!capabilities?.can_emergency_rollback||!selected||!rollback||!reason.trim()} onClick={()=>void emergencyRestore()} className="rounded bg-amber-800 px-3 py-2 font-semibold text-amber-50 disabled:opacity-40">사유 기록 후 긴급 롤백</button>
      {emergencyStatus==='submitting'&&<p role="status" className="text-amber-200">긴급 롤백 응답 확인 중…</p>}
      {emergencyStatus==='acknowledged'&&<p role="status" className="text-emerald-300">긴급 롤백 적용 응답·감사 기록 확인됨</p>}
      {emergencyStatus==='unverified'&&<p role="alert" className="break-all text-amber-200">{emergencyError}</p>}
      {(emergencyStatus==='acknowledged'||emergencyStatus==='unverified')&&submittedReason&&<p className="text-slate-300">기록된 사유: {submittedReason}</p>}
      {emergencyStatus==='failed'&&<p role="alert" className="break-all text-red-300">긴급 롤백 실패 · {emergencyError}</p>}
      <details><summary className="cursor-pointer text-amber-200">선택 장비의 긴급 롤백 감사 기록 ({state?.emergency_rollback_events?.length||0})</summary>
        <ul className="mt-2 space-y-2">{state?.emergency_rollback_events?.filter(event=>event.target_id===selected).map(event=><li key={event.event_id} className="rounded border border-slate-700 p-2"><p>{({attempted:'요청됨',committed:'적용 확인됨',denied:'권한·사유 거부됨',rejected:'거부됨'})[event.event]} · {event.actor_name} · {event.reason}</p><p className="text-slate-400">{new Date(event.created_at*1000).toLocaleString()} · {event.deployment_id}{event.detail?` · ${event.detail}`:''}</p></li>)}</ul>
      </details>
    </section>
    {busy&&<p role="status" className="text-cyan-200">장비 응답 확인 중…</p>}{error&&<p role="alert" className="break-all text-red-300">{error}</p>}
  </div></details>;
}
