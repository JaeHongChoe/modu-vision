import type {WorkflowStep} from '../wizard/workflowReadiness';
import {useProjectStore} from '../../stores/useProjectStore';
import {useEffect,useRef,useState} from 'react';
import {request,getProjectContextGeneration,getApiPersistenceIdentity,subscribeProjectContext} from '../../services/api';
import {useDeliveryScope} from './useDeliveryScope';
import {SavedPackagePicker} from './SavedPackagePicker';
import {emergencyAcknowledged,type RollbackCapabilities,type EmergencyRollbackEvent,type EmergencyRollbackReceipt} from './emergencyRollbackPolicy';
import {fleetRollouts,rolloutControls,type FleetRollout} from '../../services/fleetRollouts';
type Target={target_id:string;name:string;url:string;token_set:boolean;credential_storage?:'server_secret_v1'|'legacy_project_database'|'unconfigured'};
type Deployment={deployment_id:string;created_at:number;reviewer:string;restored_from?:string;release:{manifest_sha256:string;device:string}};
type AgentState={target:Target;runtime:{status:string;device?:string;manifest_sha256?:string;model_sha256?:Record<string,string>};active:Deployment|null;history:Deployment[];matches_active:boolean;error?:string;emergency_rollback_events?:EmergencyRollbackEvent[]};
const control='min-w-0 rounded border border-slate-600 bg-[#0D1622] p-2 text-xs';
export function FleetPanel({onNavigate}:{onNavigate?:(step:WorkflowStep)=>void}={}){
  const [epoch,setEpoch]=useState(getProjectContextGeneration);
  const generation=getProjectContextGeneration(),identity=getApiPersistenceIdentity();
  const {projectDir,scope,key}=useDeliveryScope(JSON.stringify([epoch,generation,identity]));
  const same=()=>getProjectContextGeneration()===generation&&getApiPersistenceIdentity()===identity&&useProjectStore.getState().projectDir===projectDir;
  const currentScope=(started:{key:string})=>same()&&scope.current===started;
  useEffect(()=>subscribeProjectContext(()=>setEpoch(getProjectContextGeneration())),[]);
  const [targets,setTargets]=useState<Target[]>([]),[selected,setSelected]=useState(''),[state,setState]=useState<AgentState|null>(null),[name,setName]=useState(''),[url,setUrl]=useState(''),[token,setToken]=useState(''),[packagePath,setPackagePath]=useState(''),[reviewer,setReviewer]=useState(''),[device,setDevice]=useState('cpu'),[rollback,setRollback]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [capabilityRecord,setCapabilities]=useState<{key:string;value:RollbackCapabilities}|null>(null);
  const [reason,setReason]=useState(''),[emergencyStatus,setEmergencyStatus]=useState<'idle'|'submitting'|'acknowledged'|'failed'|'unverified'>('idle'),[emergencyError,setEmergencyError]=useState(''),[submittedReason,setSubmittedReason]=useState('');
  const lastReceipt=useRef<{scope:{key:string};targetId:string;receipt:EmergencyRollbackReceipt;reason:string}|null>(null);
  const selectedRef=useRef(selected);selectedRef.current=selected;
  const capabilities=capabilityRecord?.key===key?capabilityRecord.value:null;
  const [capabilityError,setCapabilityError]=useState('');
  const [credentialReason,setCredentialReason]=useState(''),[credentialStatus,setCredentialStatus]=useState<'idle'|'submitting'|'confirmed'|'unverified'>('idle');
  const credentialRequest=useRef<{key:string;targetId:string}|null>(null);
  const selectedTarget=targets.find(target=>target.target_id===selected);
  const canMigrateCredentials=capabilities?.actor_role==='owner'||capabilities?.actor_role==='local_owner';
  const [rollouts,setRollouts]=useState<FleetRollout[]>([]),[rolloutId,setRolloutId]=useState(''),[rollout,setRollout]=useState<FleetRollout|null>(null);
  const [rolloutTargets,setRolloutTargets]=useState<string[]>([]),[canary,setCanary]=useState(''),[batchSize,setBatchSize]=useState(5),[pauseReason,setPauseReason]=useState('');
  const rolloutRef=useRef(rolloutId);rolloutRef.current=rolloutId;
  const capabilityRequest=useRef(0);
  const refreshCapabilities=async()=>{
    if(!same())return;
    const started=scope.current,generation=++capabilityRequest.current;
    const current=()=>currentScope(started)&&capabilityRequest.current===generation;
    setCapabilities(null);setCapabilityError('');
    try{const value=await request<RollbackCapabilities>('/api/fleet/capabilities');if(current())setCapabilities({key:started.key,value});}
    catch(cause){if(current())setCapabilityError('롤백 권한 확인 실패: '+String((cause as Error).message||cause)+' · 실제 상태 확인으로 다시 확인하세요.');}
  };
  useEffect(()=>{
    let current=true;const started=scope.current;
    setTargets([]);setSelected('');selectedRef.current='';setState(null);setName('');setUrl('');setToken('');setPackagePath('');setReviewer('');setRollback('');setBusy(false);setError('');setCapabilities(null);setCapabilityError('');setReason('');setEmergencyStatus('idle');setEmergencyError('');lastReceipt.current=null;setSubmittedReason('');
    setRollouts([]);setRolloutId('');rolloutRef.current='';setRollout(null);setRolloutTargets([]);setCanary('');setBatchSize(5);setPauseReason('');
    setCredentialReason('');setCredentialStatus('idle');credentialRequest.current=null;
    if(projectDir){
      request<{targets:Target[]}>('/api/fleet/targets').then(r=>{if(current&&currentScope(started))setTargets(r.targets);}).catch(e=>{if(current&&currentScope(started))setError(String(e.message||e));});
      void refreshCapabilities();
      fleetRollouts.list().then(result=>{if(current&&currentScope(started))setRollouts(result.rollouts);}).catch(cause=>{if(current&&currentScope(started))setError(String(cause.message||cause));});
    }
    return()=>{current=false;};
  },[key]);
  const read=async(identifier:string)=>{
    if(!same())return;
    const started=scope.current;
    const current=()=>currentScope(started)&&selectedRef.current===identifier;
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
  const readRollout=async(identifier:string)=>{
    if(!same())return;
    const started=scope.current;
    const result=await fleetRollouts.read(identifier);
    if(currentScope(started)&&rolloutRef.current===identifier){setRollout(result);setRollouts(previous=>[result,...previous.filter(row=>row.plan_id!==identifier)]);}
    return result;
  };
  const createRollout=async()=>{
    if(!same())return;
    const started=scope.current;
    const result=await fleetRollouts.create({package_path:packagePath,device,reviewer,target_ids:rolloutTargets,canary_target_ids:[canary],batch_size:batchSize});
    if(!currentScope(started))return;
    rolloutRef.current=result.plan_id;setRolloutId(result.plan_id);setRollout(result);setRollouts(previous=>[result,...previous]);
  };
  const operateRollout=async(operation:'advance'|'confirm'|'pause'|'resume'|'rollback')=>{
    if(!rollout)return;
    const started=scope.current,identifier=rollout.plan_id,revision=rollout.revision;
    try{
      const result=operation==='advance'||operation==='confirm'?await fleetRollouts.advance(identifier,revision,reviewer,operation==='confirm'):
        operation==='pause'?await fleetRollouts.pause(identifier,revision,reviewer,pauseReason):
        operation==='resume'?await fleetRollouts.resume(identifier,revision,reviewer):await fleetRollouts.rollback(identifier,revision,reviewer);
      if(currentScope(started)&&rolloutRef.current===identifier){setRollout(result);setRollouts(previous=>[result,...previous.filter(row=>row.plan_id!==identifier)]);}
    }catch(cause){
      // A transport interruption can follow a committed target action; reload its
      // durable plan before offering another revision-bound command.
      if(currentScope(started)&&rolloutRef.current===identifier){try{await readRollout(identifier);}catch{/* Original command error remains visible. */}}
      throw cause;
    }
  };
  const rolloutButtons=rollout?rolloutControls(rollout):null;
  const refreshActualState=async()=>{
    if(!same())return;
    // Capability failure disables actions independently of target evidence.
    const [targetResult]=await Promise.allSettled([read(selected),refreshCapabilities()]);
    if(targetResult.status==='rejected')throw targetResult.reason;
  };
  const action=async(fn:()=>Promise<unknown>)=>{if(!same())return;const started=scope.current;setBusy(true);setError('');try{await fn();}catch(e){if(currentScope(started))setError(String((e as Error).message||e));}finally{if(currentScope(started))setBusy(false);}};
  const readCredentials=async(identifier:string)=>{
    const started=scope.current;
    const result=await request<{targets:Target[]}>('/api/fleet/targets');
    if(!currentScope(started)||selectedRef.current!==identifier)return null;
    const target=result.targets.find(row=>row.target_id===identifier);
    if(!target)throw new Error('등록된 장비를 확인하지 못했습니다.');
    setTargets(result.targets);
    return target;
  };
  const migrateCredentials=async()=>{
    if(!same()||busy||credentialRequest.current||!canMigrateCredentials||!selected||selectedTarget?.credential_storage!=='legacy_project_database'||credentialReason.trim().length<10)return;
    const started=scope.current,identifier=selected,reason=credentialReason.trim();
    const pending={key:started.key,targetId:identifier};credentialRequest.current=pending;
    const current=()=>currentScope(started)&&selectedRef.current===identifier&&credentialRequest.current===pending;
    setBusy(true);setCredentialStatus('submitting');
    try{
      try{
        const result=await request<Target>(`/api/fleet/targets/${encodeURIComponent(identifier)}/credentials/migrate`,{method:'POST',body:JSON.stringify({reason})});
        if(result.target_id!==identifier)throw new Error('응답 장비가 다릅니다.');
      }catch{
        // A lost POST response may follow a committed migration. Reconcile
        // registered storage separately; this does not contact the field agent.
      }
      if(!current())return;
      const target=await readCredentials(identifier);
      if(!current())return;
      if(target?.credential_storage!=='server_secret_v1')throw new Error('이동 확인 필요');
      setCredentialStatus('confirmed');setCredentialReason('');
    }catch{
      if(current())setCredentialStatus('unverified');
    }finally{
      if(currentScope(started)&&credentialRequest.current===pending){credentialRequest.current=null;setBusy(false);}
    }
  };
  const add=async()=>{const started=scope.current;await request('/api/fleet/targets',{method:'POST',body:JSON.stringify({name,url,token})});if(!currentScope(started))return;setToken('');setName('');setUrl('');const result=await request<{targets:Target[]}>('/api/fleet/targets');if(currentScope(started))setTargets(result.targets);};
  const deploy=async()=>{const started=scope.current;await request(`/api/fleet/targets/${encodeURIComponent(selected)}/deploy`,{method:'POST',body:JSON.stringify({package_path:packagePath,device,reviewer})});if(currentScope(started))await read(selected);};
  const restore=async()=>{const started=scope.current;await request(`/api/fleet/targets/${encodeURIComponent(selected)}/rollback`,{method:'POST',body:JSON.stringify({deployment_id:rollback,reviewer})});if(currentScope(started))await read(selected);};
  const emergencyRestore=async()=>{
    if(!same()||busy||!capabilities?.can_emergency_rollback||!selected||!rollback||!reason.trim())return;
    const started=scope.current,identifier=selected,deploymentId=rollback,incidentReason=reason.trim();
    const current=()=>currentScope(started)&&selectedRef.current===identifier;
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
    <div className="flex gap-2"><select aria-label="현장 장비 선택" className={`${control} flex-1`} disabled={busy} value={selected} onChange={e=>{const id=e.target.value;selectedRef.current=id;setSelected(id);setState(null);setRollback('');setReason('');setEmergencyStatus('idle');setEmergencyError('');lastReceipt.current=null;setSubmittedReason('');setCredentialReason('');setCredentialStatus('idle');if(id)void action(()=>read(id));}}><option value="">장비 선택</option>{targets.map(t=><option key={t.target_id} value={t.target_id}>{t.name} · {t.url}</option>)}</select><button className="text-cyan-200" disabled={busy||!selected} onClick={()=>void action(refreshActualState)}>실제 상태 확인</button></div>
    {selectedTarget&&<section aria-label="현장 장비 인증값 보관" className="workspace-section space-y-2">
      <h3>현장 장비 인증값 보관</h3>
      <p className="workspace-description">{selectedTarget.credential_storage==='server_secret_v1'?'인증값이 이 서버에 보관됩니다. 프로젝트를 다른 서버로 옮기면 연결 설정을 다시 저장하세요.':selectedTarget.credential_storage==='legacy_project_database'?'인증값이 프로젝트 안에 보관되어 있습니다. 이 서버의 저장소로 옮겨도 장비 주소와 인증값은 유지됩니다.':'인증값 보관 상태를 확인하거나 연결 설정을 저장하세요.'}</p>
      {selectedTarget.credential_storage==='legacy_project_database'&&<>
        <p id="fleet-credential-help" className="workspace-description">프로젝트 소유자가 사유를 10자 이상 입력해 옮길 수 있습니다. 장비 연결 없이 처리하며 이전 백업의 인증값까지 지우지는 않습니다.</p>
        {!canMigrateCredentials&&<p className="text-amber-200">{capabilities?'프로젝트 소유자 권한이 필요합니다.':'소유자 권한 확인 중입니다. 권한을 확인하지 못하면 실제 상태 확인을 눌러 다시 확인하세요.'}</p>}
        <label className="workspace-field">인증값 이동 사유<input aria-label="인증값 이동 사유" aria-describedby="fleet-credential-help" required minLength={10} maxLength={2000} disabled={busy||!canMigrateCredentials} value={credentialReason} onChange={event=>setCredentialReason(event.target.value)}/></label>
        <button type="button" className="workspace-button workspace-button--primary" disabled={busy||!canMigrateCredentials||credentialReason.trim().length<10} onClick={()=>void migrateCredentials()}>인증값을 서버 저장소로 옮기기</button>
      </>}
      {credentialStatus==='submitting'&&<p role="status">인증값 이동·보관 상태 확인 중…</p>}
      {credentialStatus==='confirmed'&&<p role="status" className="text-emerald-300">인증값 이동 확인됨</p>}
      {credentialStatus==='unverified'&&<p role="alert" className="text-amber-200">이동을 확인하지 못했습니다. 입력한 사유를 유지했습니다. 보관 상태를 다시 읽고 필요하면 재시도하세요.</p>}
      <button type="button" className="workspace-button" disabled={busy} onClick={()=>void action(async()=>{const target=await readCredentials(selected);if(target?.credential_storage==='server_secret_v1'&&credentialStatus==='unverified'){setCredentialStatus('confirmed');setCredentialReason('');}})}>인증값 보관 상태 다시 읽기</button>
    </section>}
    {state&&<section className="rounded border border-slate-700 bg-slate-950/40 p-2"><p>{state.runtime.status==='ready'?'장비 응답 확인됨':state.runtime.status==='stopped'?'장비 검사 서비스 중지됨':'장비 연결 확인 필요'} · {state.runtime.device||'장치 응답 없음'} · {state.matches_active?'중앙 적용 기록과 일치':'중앙 기록과 실행 상태 확인 필요'}</p><p className="mt-1 break-all font-mono text-[10px]">실행 manifest: {state.runtime.manifest_sha256||'—'}</p>{Object.entries(state.runtime.model_sha256||{}).map(([id,hash])=><p key={id} className="mt-1 break-all font-mono text-[10px]">{id} · {hash}</p>)}</section>}
    <div className="grid gap-2 sm:grid-cols-2"><SavedPackagePicker value={packagePath} onChange={setPackagePath} disabled={busy}/><details className="sm:col-span-2"><summary className="text-slate-400">고급 · 패키지 폴더 직접 입력</summary><input aria-label="현장 배포 승인 패키지" className={`${control} w-full`} value={packagePath} onChange={e=>setPackagePath(e.target.value)}/></details><label>적용 검토자<input aria-label="현장 배포 검토자" className={`${control} w-full`} value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label><label>장비 실행 자원<input aria-label="현장 실행 자원" className={`${control} w-full`} value={device} onChange={e=>setDevice(e.target.value)} placeholder="cpu / cuda:0 / openvino:CPU"/></label></div>
    {deployBlocker&&<div className="mt-2 text-xs text-amber-200"><p id="fleet-deploy-reason">적용 보류: {deployBlocker}</p><button className="workspace-button mt-2" onClick={()=>{if(selected&&!packagePath.trim())void (onNavigate||useProjectStore.getState().setStep)(6);else document.querySelector<HTMLElement>(!selected?'[aria-label="현장 장비 선택"]':'[aria-label="현장 배포 검토자"]')?.focus();}}>{!selected?'현장 장비 선택':!packagePath.trim()?'승인·패키지 확인 (6단계)':'적용 입력 확인'}</button></div>}
    <button aria-describedby={deployBlocker?'fleet-deploy-reason':undefined} disabled={busy||!selected||!packagePath.trim()||!reviewer.trim()} onClick={()=>void action(deploy)} className="rounded bg-cyan-700 px-3 py-2 disabled:opacity-40">패키지 전송·적용 응답 확인</button>
    <section aria-label="단계적 현장 배포" className="space-y-3 rounded border border-cyan-800 p-3">
      <h3 className="font-semibold text-cyan-200">카나리 · 배치 순차 배포</h3>
      <p className="leading-5 text-slate-400">위의 승인 패키지·장치·검토자를 사용해 계획을 저장합니다. 카나리 장비의 적용 응답을 확인한 후 다음 배치를 명시적으로 승인하세요. 실패·오프라인 시 중단되며, 재개는 기존 적용 장비의 실제 응답을 다시 확인합니다.</p>
      <div className="flex flex-wrap gap-3">{targets.map(target=><label key={target.target_id}><input type="checkbox" disabled={busy} checked={rolloutTargets.includes(target.target_id)} onChange={event=>{setRolloutTargets(previous=>event.target.checked?[...previous,target.target_id]:previous.filter(id=>id!==target.target_id));if(!event.target.checked&&canary===target.target_id)setCanary('');}}/> {target.name}</label>)}{!targets.length&&<span className="text-slate-400">등록한 장비가 없습니다.</span>}</div>
      <div className="flex flex-wrap items-end gap-2"><label>카나리 장비<select aria-label="카나리 장비" className={`${control} block`} disabled={busy} value={canary} onChange={event=>setCanary(event.target.value)}><option value="">선택한 장비 중 지정</option>{targets.filter(target=>rolloutTargets.includes(target.target_id)).map(target=><option key={target.target_id} value={target.target_id}>{target.name}</option>)}</select></label><label>배치 장비 수<input aria-label="배치 장비 수" type="number" min="1" max="100" disabled={busy} value={batchSize} onChange={event=>setBatchSize(Math.max(1,Math.min(100,Number(event.target.value)||1)))} className={`${control} block w-20`}/></label><button disabled={busy||!packagePath.trim()||!reviewer.trim()||!rolloutTargets.length||!rolloutTargets.includes(canary)} onClick={()=>void action(createRollout)} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">배포 계획 저장</button></div>
      <div className="flex gap-2"><select aria-label="순차 배포 계획" className={`${control} flex-1`} disabled={busy} value={rolloutId} onChange={event=>{const identifier=event.target.value;rolloutRef.current=identifier;setRolloutId(identifier);setRollout(null);if(identifier)void action(()=>readRollout(identifier));}}><option value="">저장된 계획 선택</option>{rollouts.map(plan=><option key={plan.plan_id} value={plan.plan_id}>{new Date(plan.updated_at*1000).toLocaleString()} · {plan.status} · {plan.targets.length}장비 · {plan.release.manifest_sha256.slice(0,12)}</option>)}</select><button disabled={busy||!rolloutId} onClick={()=>void action(()=>readRollout(rolloutId))} className="text-cyan-200 disabled:opacity-40">계획 기록 다시 읽기</button></div>
      {rollout&&rolloutButtons&&<>
        <p role="status">계획 상태: {rollout.status} · 개정 {rollout.revision} · 카나리 {rollout.canary_confirmed?'승인됨':'추가 배치 승인 전'} · 배치 {rollout.batch_size}장비</p>
        <p className="break-all font-mono text-[10px]">계획 manifest: {rollout.release.manifest_sha256} · {rollout.release.device}</p>
        {rollout.pause_reason&&<p role="alert" className="text-amber-200">중단 사유: {rollout.pause_reason}</p>}
        <p className="text-slate-400">계획 기록은 마지막 응답을 보존합니다. 오프라인 장비는 기존 확인된 검사 실행을 유지하고 새 명령은 실제 응답 확인까지 보류합니다.</p>
        <ul className="space-y-2">{rollout.targets.map(target=><li key={target.target_id} className="rounded border border-slate-700 p-2"><p>{targets.find(row=>row.target_id===target.target_id)?.name||target.target_id} · {rollout.canary_target_ids.includes(target.target_id)?'카나리 · ':''}{target.status}</p>{target.error&&<p className="text-amber-200">{target.error}</p>}{target.readback&&<p className="break-all text-slate-400">마지막 응답 {new Date(target.readback.observed_at*1000).toLocaleString()} · {target.readback.runtime?.status||'응답 없음'} · {target.readback.matches_active?'적용 기록과 일치':'일치 확인 필요'} · {target.readback.runtime?.manifest_sha256||'—'}</p>}</li>)}</ul>
        <div className="flex flex-wrap gap-2"><button disabled={busy||!reviewer.trim()||!rolloutButtons.advance} onClick={()=>void action(()=>operateRollout('advance'))} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-40">{rollout.status==='planned'?'카나리 적용·응답 확인':'다음 배치 적용·응답 확인'}</button><button disabled={busy||!reviewer.trim()||!rolloutButtons.confirmCanary} onClick={()=>void action(()=>operateRollout('confirm'))} className="rounded bg-emerald-800 px-3 py-2 disabled:opacity-40">카나리 응답 확인 · 다음 배치 승인</button><button disabled={busy||!reviewer.trim()||!rolloutButtons.resume} onClick={()=>void action(()=>operateRollout('resume'))} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">실제 응답 재확인·재개 준비</button><button disabled={busy||!reviewer.trim()||!capabilities?.can_rollback||!rolloutButtons.rollback} onClick={()=>void action(()=>operateRollout('rollback'))} className="rounded border border-amber-700 px-3 py-2 disabled:opacity-40">{rollout.operation==='rollback'?'다음 롤백 배치·응답 확인':'적용 장비의 이전 이력으로 롤백'}</button></div>
        <div className="flex gap-2"><input aria-label="순차 배포 중단 사유" disabled={busy} value={pauseReason} onChange={event=>setPauseReason(event.target.value)} placeholder="다음 배치 전 중단 사유" className={`${control} flex-1`}/><button disabled={busy||!reviewer.trim()||!pauseReason.trim()||!rolloutButtons.pause} onClick={()=>void action(()=>operateRollout('pause'))} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">계획 중단</button></div>
        <details><summary className="cursor-pointer text-slate-400">계획 감사 기록</summary><ul>{rollout.events?.map(event=><li key={event.event_id}>개정 {event.revision} · {event.event} · {event.reviewer} · {new Date(event.created_at*1000).toLocaleString()}</li>)}</ul><p className="text-slate-500">감사 기록은 계획 기록 다시 읽기로 갱신합니다.</p></details>
      </>}
    </section>
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
