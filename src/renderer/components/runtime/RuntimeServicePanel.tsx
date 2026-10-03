import type {WorkflowStep} from '../wizard/workflowReadiness';
import {useProjectStore} from '../../stores/useProjectStore';
import { ModelOperationsPanel } from './ModelOperationsPanel';
import { FleetPanel } from './FleetPanel';
import { SpecializedApprovalPanel } from '../evaluation/SpecializedApprovalPanel';
import React, { useEffect, useRef, useState } from 'react';
import { request } from '../../services/api';
import { runtimeDeploymentApi } from '../../services/runtimeDeploymentApi';
import {SavedPackagePicker} from './SavedPackagePicker';
import {WindowsServiceSetupPanel} from './WindowsServiceSetupPanel';
import {ProtocolSettingsPanel} from './ProtocolSettingsPanel';
import {useDeliveryScope} from './useDeliveryScope';

type RuntimeIdentity = { status: string; manifest_sha256?: string; pipeline_id?: string; device?: string };
type Deployment = { deployment_id: string; reviewer: string; restored_from?: string; release: { manifest_sha256: string; device: string }; created_at: number };
type RecoveryOperation={operation_id:string;status:string;release:{manifest_sha256:string;device:string};ack:RuntimeIdentity|null;error:string|null;updated_at:number};
type ServiceState = { runtime: RuntimeIdentity; active: Deployment | null; history: Deployment[]; port: number; recovery?:{pending:RecoveryOperation|null;last_operation:RecoveryOperation|null};runtime_build?:{mode:string;status:string;build_identity_sha256?:string;source_sha256?:string};adapter_config: { enabled: boolean; modbus: Record<string, unknown> | null; mes: Record<string, unknown> | null };native_install?:{platform:string;kind:string;supported:boolean;prepared:boolean;registered:boolean;enabled:boolean;running:boolean|null;verified:boolean;command_available:boolean;error?:string;startup_scope:string;prerequisite:string} };
const EMPTY_CONFIG = '{"enabled":false,"modbus":null,"mes":null,"clear_mes_token":false}';
type ProjectScope = { projectDir: string | null;key:string };

export const RuntimeServicePanel: React.FC<{ projectDir: string | null;initialPackagePath?:string;onNavigate?:(step:WorkflowStep)=>void }> = ({ projectDir,initialPackagePath,onNavigate }) => {
  const {key}=useDeliveryScope();
  const currentProject = useRef<ProjectScope>({ projectDir,key });
  if (currentProject.current.key !== key) currentProject.current = { projectDir,key };
  const [state, setState] = useState<ServiceState | null>(null);
  const [loadedScope, setLoadedScope] = useState<ProjectScope | null>(null);
  const [packagePath, setPackagePath] = useState('');
  const [device, setDevice] = useState('cpu');
  const [devices, setDevices] = useState<string[]>(['cpu']);
  const [reviewer, setReviewer] = useState('');
  const [target, setTarget] = useState('');
  const [config, setConfig] = useState(EMPTY_CONFIG);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const ready = state !== null && loadedScope === currentProject.current;
  const refresh = async () => {
    const started = currentProject.current;
    try {
      const result = await request<ServiceState>('/api/runtime-services');
      if (currentProject.current === started) { setState(result); setLoadedScope(started); return result; }
    } catch (cause) {
      if (currentProject.current === started) setError(cause instanceof Error ? cause.message : String(cause));
    }
    return null;
  };
  useEffect(() => {
    let valid = true;
    const started = currentProject.current;
    setState(null); setLoadedScope(null); setConfig(EMPTY_CONFIG);
    setPackagePath(''); setError(''); setNotice(''); setTarget(''); setBusy(false);
    setDevice('cpu');setDevices(['cpu']);
    if(projectDir)void runtimeDeploymentApi.capabilities().then(cap=>{if(valid&&currentProject.current===started)setDevices([...cap.torch_devices,...cap.openvino.devices.map(value=>'openvino:'+value)]);}).catch(()=>{});
    if (projectDir) request<ServiceState>('/api/runtime-services').then(result => {
      if (valid && currentProject.current === started) { setState(result); setLoadedScope(started); setConfig(JSON.stringify(result.adapter_config, null, 2)); }
    }).catch(cause => { if (valid) setError(String(cause.message || cause)); });
    return () => { valid = false; };
  }, [projectDir,key]);
  useEffect(()=>{setPackagePath(initialPackagePath||'');},[initialPackagePath,key]);
  const action = async (path: string, payload?: unknown, method = 'POST') => {
    if (!ready || busy || currentProject.current.projectDir !== projectDir) return;
    const started = currentProject.current;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await request<Record<string, unknown>>('/api/runtime-services' + path, { method, ...(payload ? { body: JSON.stringify(payload) } : {}) });
      if (currentProject.current !== started) return;
      const refreshed = await refresh();
      if (currentProject.current !== started) return;
      if (!refreshed) return;
      if (path === '/adapters') { setConfig(JSON.stringify(refreshed.adapter_config, null, 2)); }
      setNotice(path === '/install' ? `설치 파일 준비: ${(result.files as string[]).join(', ')} · ${result.kind}` : path === '/adapters' ? '설정 저장됨. 적용하려면 서비스를 명시적으로 중지 후 시작하세요.' : '서비스 응답과 기록을 확인했습니다.');
    } catch (cause) { if (currentProject.current === started) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (currentProject.current === started) setBusy(false); }
  };
  if (!projectDir) return null;
  const deployBlocker=busy?'응답 확인 중입니다.':!packagePath.trim()?'승인 포함 패키지가 필요합니다.':!reviewer.trim()?'적용 검토자를 입력하세요.':null;
  return <section className="mt-4 rounded border border-slate-600 bg-slate-950/40 p-3 text-xs" aria-label="검사 서비스 배포">
    <div className="flex items-center justify-between"><h3 className="font-bold">독립 검사 서비스</h3><button type="button" disabled={busy} onClick={() => void refresh()}>상태 새로고침</button></div>
    <p className="mt-2 text-slate-300">현재 응답: {state?.runtime.status || '확인 중'} · 장치 {state?.runtime.device || '—'} · 포트 {state?.port || '—'}</p>
    {state?.native_install&&<div className="mt-2 text-slate-400"><p>자동 시작 {state.native_install.platform} · {state.native_install.kind} · {state.native_install.registered?'OS 등록 확인':'OS 미등록'} · {state.native_install.enabled?'시작 설정 켜짐':'시작 설정 꺼짐'} · {state.native_install.verified?'대상 OS 실행 검증됨':'로그인·재시작 실행 검증 필요'}</p><p>실제 검사 준비는 위의 서비스 응답에서 확인하세요. {state.native_install.kind==='windows_scm'?'Windows 시스템 부팅 서비스입니다. Session0와 실제 장비 실행 검증이 필요합니다.':state.native_install.kind==='scheduled_task'?'Windows 로그인 작업입니다. 시스템 서비스는 별도 실행 파일이 필요합니다.':state.native_install.kind==='systemd_user'?'Linux 사용자 세션 서비스입니다. 로그인 전 부팅 시작은 사용자 lingering 설정이 필요합니다.':'macOS 사용자 로그인 서비스입니다.'}</p>{!state.native_install.command_available&&<p>이 OS의 서비스 등록 명령이 필요합니다.</p>}{state.native_install.error&&<p role="alert">{state.native_install.error}</p>}</div>}
    <p className="mt-1 break-all font-mono text-[10px]">실행 manifest SHA-256: {state?.runtime.manifest_sha256 || '서비스 응답 없음'}</p>
    {state?.active&&state.runtime.status==='ready'&&state.runtime.manifest_sha256!==state.active.release.manifest_sha256&&<p role="alert" className="mt-2 text-amber-300">현재 서비스와 승인된 적용 기록의 패키지가 다릅니다. 서비스를 시작해 복구 응답을 확인하세요.</p>}
    {state?.recovery?.pending&&<div role="alert" className="mt-2 rounded border border-amber-700 p-2 text-amber-200"><p>{state.recovery.pending.status==='needs_review'?'이전 패키지 복원 응답을 확인하지 못했습니다.':'패키지 전환이 중단돼 이전 적용 상태 복구가 필요합니다.'}</p><p>서비스 시작 시 마지막으로 확인된 패키지를 복원하고 실행 응답을 검증합니다. 실패하면 적용 기록을 유지하며 검토가 필요합니다.</p></div>}
    {state?.recovery?.last_operation&&<details className="mt-2 break-all text-slate-400"><summary>업데이트·복구 기록</summary><p>{({committed:'실행 응답 확인 후 적용됨',rolled_back:'이전 패키지 실행 응답 확인 후 복원됨',rejected:'후보 적용 실패',interrupted_without_previous:'초기 적용 중단 · 승인 패키지 명시적 재적용 필요'} as Record<string,string>)[state.recovery.last_operation.status]||'복구 확인 필요'}</p><p>후보 manifest SHA-256 {state.recovery.last_operation.release.manifest_sha256}</p><p>확인된 실행 manifest SHA-256 {state.recovery.last_operation.ack?.manifest_sha256||'없음'}</p><p>확인 {new Date(state.recovery.last_operation.updated_at*1000).toLocaleString()}</p></details>}
    {state?.runtime_build&&<details className="mt-2 break-all text-slate-400"><summary>검사 런타임 식별자</summary><p>{state.runtime_build.mode==='frozen'?'독립 실행 빌드':'소스 실행'} · {state.runtime_build.status==='identified'?'식별됨':'빌드 목록 확인 필요'}</p><p className="font-mono text-[10px]">{state.runtime_build.build_identity_sha256||state.runtime_build.source_sha256||'식별자 없음'}</p><p>실행 준비, 배포자 서명, 실제 장비 검증은 각각의 확인 기록이 필요합니다.</p></details>}
    <div className="mt-3 grid grid-cols-2 gap-2">
      <SavedPackagePicker value={packagePath} onChange={setPackagePath} disabled={busy}/>
      <details className="col-span-2 text-slate-400"><summary>고급 · 패키지 폴더 직접 입력</summary><input aria-label="승인 패키지 경로" className="mt-1 w-full rounded bg-slate-800 p-2" value={packagePath} onChange={event => setPackagePath(event.target.value)} /></details>
      <label>실행 장치<select aria-label="서비스 실행 장치" className="mt-1 w-full rounded bg-slate-800 p-2" value={device} onChange={event => setDevice(event.target.value)}>{devices.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
      <label>적용 검토자<input aria-label="서비스 검토자" className="mt-1 w-full rounded bg-slate-800 p-2" value={reviewer} onChange={event => setReviewer(event.target.value)} /></label>
    </div>
    <p className="mt-2 text-slate-400">응답 제한 시간과 CPU 스레드 수는 전체 flow 내보내기에서 저장합니다. 정밀도 승인 패키지는 검토한 실행 장치를 사용합니다.</p>
    <div className="mt-2 flex flex-wrap gap-2">
    {deployBlocker&&<div className="mt-2 text-xs text-amber-200"><p id="runtime-deploy-reason">적용 보류: {deployBlocker}</p><button className="workspace-button mt-2" onClick={()=>{if(!packagePath.trim())void (onNavigate||useProjectStore.getState().setStep)(6);else document.querySelector<HTMLElement>('[aria-label="서비스 검토자"]')?.focus();}}>{!packagePath.trim()?'승인·패키지 확인 (6단계)':'적용 입력 확인'}</button></div>}
      <button aria-describedby={deployBlocker?'runtime-deploy-reason':undefined} type="button" disabled={busy || !packagePath.trim() || !reviewer.trim()} onClick={() => void action('/apply', { package_path: packagePath, device, reviewer })} className="rounded bg-blue-700 px-3 py-2 disabled:opacity-40">승인 패키지 적용</button>
      <button type="button" disabled={busy || !state?.active} onClick={() => void action('/start')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">서비스 시작</button>
      <button type="button" disabled={busy || state?.runtime.status === 'stopped'} onClick={() => void action('/stop')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">서비스 중지</button>
      <button type="button" disabled={busy || !state?.active} onClick={() => void action('/install')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">자동 시작 설치 파일 준비</button>
      <button type="button" disabled={busy || !state?.active || !state?.native_install?.supported || !state?.native_install?.command_available} onClick={() => void action('/install/activate')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">이 OS 자동 시작 설치</button>
      <button type="button" disabled={busy} onClick={() => void action('/install/remove')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">자동 시작 해제</button>
    </div>
    <div className="mt-3 flex gap-2"><select aria-label="서비스 복원 이력" className="min-w-0 flex-1 rounded bg-slate-800 p-2" value={target} onChange={event => setTarget(event.target.value)}><option value="">보존된 적용 이력 선택</option>{state?.history.map(item => <option value={item.deployment_id} key={item.deployment_id}>{new Date(item.created_at * 1000).toLocaleString()} · {item.release.manifest_sha256.slice(0, 12)} · {item.reviewer}</option>)}</select><button type="button" disabled={busy || !target || !reviewer.trim()} className="rounded border border-amber-600 px-3 disabled:opacity-40" onClick={() => void action('/rollback', { deployment_id: target, reviewer })}>서비스 롤백</button></div>
    {state?.native_install?.platform==='Windows'&&<WindowsServiceSetupPanel scopeKey={key} approved={!!state.active}/>}
    <SpecializedApprovalPanel />
    <ModelOperationsPanel />
    <FleetPanel onNavigate={onNavigate} />
    <ProtocolSettingsPanel value={ready?config:EMPTY_CONFIG} onChange={setConfig} disabled={busy||!ready} onSave={()=>{try{void action('/adapters',JSON.parse(config),'PUT');}catch{setError('설정 JSON 형식을 확인하세요.');}}}/>
    {busy && <p role="status" className="mt-2 text-blue-300">서비스 응답 확인 중…</p>}
    {error && <p role="alert" className="mt-2 break-all text-red-300">{error}</p>}
    {notice && <p role="status" className="mt-2 break-all text-emerald-300">{notice}</p>}
  </section>;
};
