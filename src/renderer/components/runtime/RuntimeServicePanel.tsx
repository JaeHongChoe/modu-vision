import { ModelOperationsPanel } from './ModelOperationsPanel';
import { FleetPanel } from './FleetPanel';
import { SpecializedApprovalPanel } from '../evaluation/SpecializedApprovalPanel';
import React, { useEffect, useRef, useState } from 'react';
import { request } from '../../services/api';
import { runtimeDeploymentApi } from '../../services/runtimeDeploymentApi';

type RuntimeIdentity = { status: string; manifest_sha256?: string; pipeline_id?: string; device?: string };
type Deployment = { deployment_id: string; reviewer: string; restored_from?: string; release: { manifest_sha256: string; device: string }; created_at: number };
type ServiceState = { runtime: RuntimeIdentity; active: Deployment | null; history: Deployment[]; port: number; adapter_config: { enabled: boolean; modbus: Record<string, unknown> | null; mes: Record<string, unknown> | null } };
const EMPTY_CONFIG = '{"enabled":false,"modbus":null,"mes":null,"clear_mes_token":false}';
type ProjectScope = { projectDir: string | null };

export const RuntimeServicePanel: React.FC<{ projectDir: string | null }> = ({ projectDir }) => {
  const currentProject = useRef<ProjectScope>({ projectDir });
  if (currentProject.current.projectDir !== projectDir) currentProject.current = { projectDir };
  const [state, setState] = useState<ServiceState | null>(null);
  const [loadedScope, setLoadedScope] = useState<ProjectScope | null>(null);
  const [packagePath, setPackagePath] = useState('');
  const [device, setDevice] = useState('cpu');
  const [devices, setDevices] = useState<string[]>(['cpu']);
  const [reviewer, setReviewer] = useState('');
  const [target, setTarget] = useState('');
  const [config, setConfig] = useState(EMPTY_CONFIG);
  const [clearMesToken, setClearMesToken] = useState(false);
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
    setState(null); setLoadedScope(null); setConfig(EMPTY_CONFIG); setClearMesToken(false);
    setPackagePath(''); setError(''); setNotice(''); setTarget(''); setBusy(false);
    setDevice('cpu');setDevices(['cpu']);
    if(projectDir)void runtimeDeploymentApi.capabilities().then(cap=>{if(valid&&currentProject.current===started)setDevices([...cap.torch_devices,...cap.openvino.devices.map(value=>'openvino:'+value)]);}).catch(()=>{});
    if (projectDir) request<ServiceState>('/api/runtime-services').then(result => {
      if (valid && currentProject.current === started) { setState(result); setLoadedScope(started); setConfig(JSON.stringify(result.adapter_config, null, 2)); }
    }).catch(cause => { if (valid) setError(String(cause.message || cause)); });
    return () => { valid = false; };
  }, [projectDir]);
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
      if (path === '/adapters') { setConfig(JSON.stringify(refreshed.adapter_config, null, 2)); setClearMesToken(false); }
      setNotice(path === '/install' ? `설치 파일 준비: ${(result.files as string[]).join(', ')} · ${result.macos_install_command}` : path === '/adapters' ? '설정 저장됨. 적용하려면 서비스를 명시적으로 중지 후 시작하세요.' : '서비스 응답과 기록을 확인했습니다.');
    } catch (cause) { if (currentProject.current === started) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (currentProject.current === started) setBusy(false); }
  };
  if (!projectDir) return null;
  return <section className="mt-4 rounded border border-slate-600 bg-slate-950/40 p-3 text-xs" aria-label="검사 서비스 배포">
    <div className="flex items-center justify-between"><h3 className="font-bold">독립 검사 서비스</h3><button type="button" disabled={busy} onClick={() => void refresh()}>상태 새로고침</button></div>
    <p className="mt-2 text-slate-300">현재 응답: {state?.runtime.status || '확인 중'} · 장치 {state?.runtime.device || '—'} · 포트 {state?.port || '—'}</p>
    <p className="mt-1 break-all font-mono text-[10px]">실행 manifest SHA-256: {state?.runtime.manifest_sha256 || '서비스 응답 없음'}</p>
    <div className="mt-3 grid grid-cols-2 gap-2">
      <label className="col-span-2">승인된 전체 flow 패키지 폴더<input aria-label="승인 패키지 경로" className="mt-1 w-full rounded bg-slate-800 p-2" value={packagePath} onChange={event => setPackagePath(event.target.value)} /></label>
      <label>실행 장치<select aria-label="서비스 실행 장치" className="mt-1 w-full rounded bg-slate-800 p-2" value={device} onChange={event => setDevice(event.target.value)}>{devices.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
      <label>적용 검토자<input aria-label="서비스 검토자" className="mt-1 w-full rounded bg-slate-800 p-2" value={reviewer} onChange={event => setReviewer(event.target.value)} /></label>
    </div>
    <p className="mt-2 text-slate-400">응답 제한 시간과 CPU 스레드 수는 전체 flow 내보내기에서 저장합니다. 정밀도 승인 패키지는 검토한 실행 장치를 사용합니다.</p>
    <div className="mt-2 flex flex-wrap gap-2">
      <button type="button" disabled={busy || !packagePath.trim() || !reviewer.trim()} onClick={() => void action('/apply', { package_path: packagePath, device, reviewer })} className="rounded bg-blue-700 px-3 py-2 disabled:opacity-40">승인 패키지 적용</button>
      <button type="button" disabled={busy || !state?.active} onClick={() => void action('/start')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">서비스 시작</button>
      <button type="button" disabled={busy || state?.runtime.status === 'stopped'} onClick={() => void action('/stop')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">서비스 중지</button>
      <button type="button" disabled={busy || !state?.active} onClick={() => void action('/install')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">자동 시작 설치 파일 준비</button>
      <button type="button" disabled={busy || !state?.active} onClick={() => void action('/install/activate')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">macOS 자동 시작 설치</button>
      <button type="button" disabled={busy} onClick={() => void action('/install/remove')} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">자동 시작 해제</button>
    </div>
    <div className="mt-3 flex gap-2"><select aria-label="서비스 복원 이력" className="min-w-0 flex-1 rounded bg-slate-800 p-2" value={target} onChange={event => setTarget(event.target.value)}><option value="">보존된 적용 이력 선택</option>{state?.history.map(item => <option value={item.deployment_id} key={item.deployment_id}>{new Date(item.created_at * 1000).toLocaleString()} · {item.release.manifest_sha256.slice(0, 12)} · {item.reviewer}</option>)}</select><button type="button" disabled={busy || !target || !reviewer.trim()} className="rounded border border-amber-600 px-3 disabled:opacity-40" onClick={() => void action('/rollback', { deployment_id: target, reviewer })}>서비스 롤백</button></div>
    <SpecializedApprovalPanel />
    <ModelOperationsPanel />
    <FleetPanel />
    <details className="mt-3"><summary className="cursor-pointer font-semibold">Modbus TCP · HTTP MES 설정</summary>
      <p className="my-2 text-slate-400">enabled를 켠 설정은 다음 서비스 시작 때 사용합니다. ACK 실패 시 운영 판정은 REVIEW로 보존됩니다.</p>
      <p className="my-2 text-slate-400">MES token의 null은 같은 주소에 저장된 인증값을 유지합니다. 주소를 변경하려면 새 token을 입력하거나 인증값 삭제를 선택하세요.</p>
      <div className="mb-2 flex gap-2"><button type="button" onClick={() => setConfig(JSON.stringify({ enabled: false, modbus: { host: '127.0.0.1', port: 502, unit_id: 1, result_register: 10, ack_register: 11, sequence_register: 12, timeout: 2, ack_timeout: 5 }, mes: null }, null, 2))}>Modbus 설정 양식</button><button type="button" onClick={() => setConfig(JSON.stringify({ enabled: false, modbus: null, mes: { url: 'http://127.0.0.1:9000/inspection', timeout: 5, field_mapping: { job_id: 'job_id', verdict: 'model_verdict' }, ack_field: 'accepted', ack_value: true, ack_job_field: 'job_id' } }, null, 2))}>MES 설정 양식</button></div>
      <textarea aria-label="PLC MES 설정" disabled={busy || !ready} rows={12} className="w-full rounded bg-slate-800 p-2 font-mono text-[11px]" value={ready ? config : EMPTY_CONFIG} onChange={event => setConfig(event.target.value)} />
      <label className="mt-2 flex items-center gap-2"><input type="checkbox" aria-label="저장된 MES 인증값 삭제" disabled={busy || !ready} checked={clearMesToken} onChange={event => setClearMesToken(event.target.checked)} />저장된 MES 인증값 삭제</label>
      <button type="button" disabled={busy || !ready} className="mt-2 rounded border border-slate-600 px-3 py-2" onClick={() => { try { const parsed = JSON.parse(config); void action('/adapters', { ...parsed, clear_mes_token: clearMesToken || parsed.clear_mes_token === true }, 'PUT'); } catch { setError('설정 JSON 형식을 확인하세요.'); } }}>설정 검증 후 저장</button>
    </details>
    {busy && <p role="status" className="mt-2 text-blue-300">서비스 응답 확인 중…</p>}
    {error && <p role="alert" className="mt-2 break-all text-red-300">{error}</p>}
    {notice && <p role="status" className="mt-2 break-all text-emerald-300">{notice}</p>}
  </section>;
};
