import React,{useEffect,useState,useSyncExternalStore} from 'react';
import {Users,LogIn,LogOut} from 'lucide-react';
import {api,request,setSharedApiBase,setProjectContext,getApiPersistenceIdentity,getProjectContext,getProjectContextGeneration,retireLocalBrowserAuthority,type ProjectContext} from '../../services/api';
import {useProjectStore,saveOpenEdits,projectSwitchRefusal,subscribeProjectSwitchRefusal} from '../../stores/useProjectStore';
import {resetComputeTransportCache} from '../../stores/useComputeStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {telemetryService} from '../../services/websocket';
import {host} from '../../services/hostAdapter';
import type {SharedConnection} from '../../../types/electron';
import type {VisionTask} from '../../types';
type ProjectChoice={id:string;path:string;role:string};
type BrowserProjectMode='shared'|'local_host';
type BrowserAccountState={user:{id:string};projects:ProjectChoice[];selected_project_id:string|null;browser_project_mode?:unknown};
type BrowserProjectAuthority={mode:BrowserProjectMode;origin:string;actor:string;revision:number;transport:string;generation:number};
function assertBrowserAuthority(value:BrowserProjectAuthority):void {
  const current=host.shared.browserIdentity?.();
  if(!current||current.origin!==value.origin||current.origin!==window.location.origin||current.actor_id!==value.actor
    ||current.revision!==value.revision||getApiPersistenceIdentity()!==value.transport||getProjectContextGeneration()!==value.generation)
    throw new Error('계정 또는 프로젝트 연결이 변경됐습니다. 다시 로그인하고 프로젝트를 선택하세요.');
}
function ownContextChange(value:BrowserProjectAuthority,change:()=>void):void {
  assertBrowserAuthority(value);change();value.generation=getProjectContextGeneration();assertBrowserAuthority(value);
}
export const LocalProjectGate:React.FC<{projects:ProjectChoice[];mode:BrowserProjectMode|null;busy:boolean;onSelect:(id:string)=>Promise<void>;onReconnect:(id:string)=>Promise<void>;onLogout:()=>Promise<void>}>=({projects,mode,busy,onSelect,onReconnect,onLogout})=>{
  const [selection,setSelection]=useState(''),[error,setError]=useState(''),[recoverable,setRecoverable]=useState(false),[pending,setPending]=useState(false);
  const blocked=busy||pending||(mode!=='shared'&&mode!=='local_host');
  const select=async(id:string)=>{
    if(blocked)return;
    setSelection(id);setError('');setRecoverable(false);
    if(!id)return;
    setPending(true);
    try{await onSelect(id);}catch(reason){
      const value=reason as {status?:number;code?:string;message?:string};
      setRecoverable(mode==='local_host'&&value?.status===409&&value.code==='local_project_location_recovery_required');
      setError(value?.message||'프로젝트를 열지 못했습니다. 현재 폴더와 권한을 확인하세요.');
    }finally{setPending(false);}
  };
  const reconnect=async()=>{
    if(blocked||mode!=='local_host'||!recoverable||!selection)return;
    setPending(true);setError('');
    try{await onReconnect(selection);}catch(reason){setError((reason as {message?:string})?.message||'폴더 확인을 완료하지 못했습니다. 다시 선택하세요.');}
    finally{setPending(false);}
  };
  return <section className="workspace-section mt-3" aria-label="로그인한 계정의 프로젝트 선택" aria-busy={blocked}>
    <p>로그인했습니다. 작업할 프로젝트를 직접 선택하세요.</p>
    <select aria-label={mode==='local_host'?'작업할 로컬 프로젝트':'작업할 프로젝트'} className="mt-2 w-full rounded border border-[var(--workspace-border)] bg-[var(--workspace-inset)] p-2 text-sm text-[var(--workspace-text)]" value={selection} disabled={blocked} onChange={event=>select(event.target.value)}>
      <option value="">프로젝트 선택</option>{projects.map(project=><option key={project.id} value={project.id}>{project.path.split('/').pop()}</option>)}
    </select>
    {!projects.length&&<p className="mt-2 text-sm">등록된 프로젝트가 없습니다. 프로젝트 관리에서 새 프로젝트를 만드세요.</p>}
    {error&&<p role="alert" className="mt-2 text-sm text-[var(--workspace-danger)]">{error}</p>}
    {recoverable&&<div className="mt-3 space-y-2"><p className="text-sm">저장된 폴더를 다시 선택하고 이 컴퓨터의 확인창에서 재연결을 승인하세요. 승인하면 이 계정의 열린 로컬 웹 세션이 종료됩니다.</p><button type="button" className="workspace-button workspace-button--primary" disabled={blocked} onClick={reconnect}>프로젝트 폴더 다시 확인</button></div>}
    <button type="button" className="workspace-button mt-3" disabled={blocked} onClick={async()=>{if(!blocked)await onLogout();}}>로그아웃</button>
  </section>;
};

/** http:// to anything but this computer: the app refuses it and it would send passwords unencrypted (F-17/B4). */
function insecureAddress(value:string):boolean{try{const url=new URL(value.trim());return url.protocol==='http:'&&!['localhost','127.0.0.1','[::1]'].includes(url.hostname);}catch{return false;}}
const taskNames:Record<VisionTask,string>={classification:'분류',detection:'객체 검출',segmentation:'영역 분할',anomaly:'이상 탐지'};
type SharedProject={id:string;path:string;role:string};
type SharedUser={id:string;username:string;administrator:boolean};
const roleNames:Record<string,string>={viewer:'보기',labeler:'라벨 작업',trainer:'학습·검사',reviewer:'검토·승인',owner:'프로젝트 관리'};
type SourceDraft={value:string;scope:string|null};
const emptySource:SourceDraft={value:'',scope:null};
function assertProjectIdle():void {
  if(useProjectStore.getState().isProjectBusy)throw new Error('프로젝트 작업이 끝난 뒤 다시 시도하세요.');
}
/** Login, project creation/switch and logout remount the step views (resetComputeTransportCache) while they run, login once
 * more after its project is accepted. A remount cancels a ZIP upload started before it, so the data step holds new
 * uploads until the whole change has finished. */
let sessionChanges=0;
async function changingSession(action:()=>Promise<void>):Promise<void>{
  sessionChanges+=1;useProjectStore.setState({sharedSessionChanging:true});
  try{await action();}finally{sessionChanges-=1;if(!sessionChanges)useProjectStore.setState({sharedSessionChanging:false});}
}
/** Account project keys select membership; manifest IDs identify data inside a workspace.
 * A native registration proves their relation, including restored copies with the same manifest ID. */
type NativeRegistration={id:string;project_dir:string;permission_key?:string;project_id?:string;path?:string;registration_id?:string;workspace_id?:string;context?:ProjectContext};
function acceptedSourceScope(connection:SharedConnection|null):string|null {
  const state=useProjectStore.getState(),context=getProjectContext(),project=state.project;
  if(!connection?.project_id||state.isProjectBusy||!project
    ||context?.mode!=='team'||context.project_id!==project.id||context.actor_id!==connection.user.id)return null;
  const url=new URL(connection.server_url);
  const transport=`shared:${url.origin}${url.pathname.replace(/\/+$/,'')}`;
  if(getApiPersistenceIdentity()!==transport)return null;
  const native=host.kind()==='browser'&&!connection.remote_id&&url.origin===window.location.origin
    &&Boolean(project.registration_id||project.workspace_id||host.localBrowser?.());
  if(native){
    if(!host.localBrowser?.()||!project.registration_id||project.workspace_id!==context.workspace_id)return null;
    const registered=(state.recentProjects as NativeRegistration[]).some(row=>row.permission_key===connection.project_id
      &&row.id===project.id&&row.project_id===project.id&&row.registration_id===project.registration_id
      &&row.workspace_id===context.workspace_id&&row.path===project.project_dir&&row.project_dir===project.project_dir
      &&row.context?.workspace_id===context.workspace_id&&row.context.project_id===context.project_id
      &&row.context.actor_id===context.actor_id&&row.context.mode===context.mode);
    if(!registered)return null;
  }else if(project.id!==connection.project_id)return null;
  return JSON.stringify([transport,context.workspace_id,context.project_id,context.actor_id,project.project_dir,connection.project_id,project.registration_id??null]);
}
async function syncSharedProject():Promise<void>{
  // A failed list read must not validate the prior account/session's catalog.
  useProjectStore.setState({recentProjects:[]});
  await useProjectStore.getState().syncCurrentProject();
}
export const SharedProjectPanel:React.FC<{localAuthenticationOnly?:boolean;onAuthenticated?:()=>void}>=({localAuthenticationOnly=false,onAuthenticated}={})=>{
  const [connection,setConnection]=useState<SharedConnection|null>(null),[server,setServer]=useState(host.kind()==='browser'&&host.can('sharedSessions')?window.location.origin:''),[username,setUsername]=useState(''),[password,setPassword]=useState(''),[projects,setProjects]=useState<SharedProject[]>([]),[selection,setSelection]=useState(''),[users,setUsers]=useState<SharedUser[]>([]),[newUser,setNewUser]=useState(''),[newPassword,setNewPassword]=useState(''),[member,setMember]=useState(''),[role,setRole]=useState('viewer'),[name,setName]=useState(''),[sourceDraft,setSourceDraft]=useState<SourceDraft>(emptySource),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const visibleProject=useProjectStore(state=>state.project);
  const projectBusy=useProjectStore(state=>state.isProjectBusy);
  const dataBusy=useDatasetStore(state=>state.isLoading||state.isSplitting);
  const switchRefusal=useSyncExternalStore(subscribeProjectSwitchRefusal,projectSwitchRefusal,projectSwitchRefusal);
  const sourceScope=acceptedSourceScope(connection);
  useEffect(()=>{
    setSourceConfirmation(null);
    setSourceDraft(sourceScope?{value:visibleProject?.source_dataset_dir||'',scope:sourceScope}:emptySource);
  },[sourceScope,visibleProject?.source_dataset_dir]);
  // Declared after the other hooks so earlier hook positions stay where they were.
  const [newTask,setNewTask]=useState<VisionTask>('classification');
  const [sourceConfirmation,setSourceConfirmation]=useState<string|null>(null);
  const [browserAuthority,setBrowserAuthority]=useState<BrowserProjectAuthority|null>(null);
  const confirmationKey=sourceScope?JSON.stringify([sourceScope,sourceDraft.value]):null;
  const run=async(action:()=>Promise<void>)=>{setBusy(true);setError('');try{await action();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
  const refresh=async()=>{const result=await request<{projects:SharedProject[];selected_project_id:string|null}>('/api/accounts/me',{projectContext:null});setProjects(result.projects);if(connection?.user.administrator)setUsers((await request<{users:SharedUser[]}>('/api/accounts/users',{projectContext:null})).users);return result;};
  const activate=async(id:string,authority?:BrowserProjectAuthority)=>{
    const check=()=>{if(authority)assertBrowserAuthority(authority);};
    check();assertProjectIdle();await saveOpenEdits();check();assertProjectIdle();setSourceDraft(emptySource);
    await request('/api/accounts/select-project',{projectContext:null,method:'POST',body:JSON.stringify({project_id:id})});check();assertProjectIdle();
    const selected=await host.shared.select(id);check();
    if(authority&&(selected.server_url!==authority.origin||selected.user.id!==authority.actor||selected.project_id!==id))throw new Error('선택한 계정과 프로젝트를 다시 확인하세요.');
    const reset=()=>{setSharedApiBase(selected.transport_url??selected.server_url,host.shared.requestOptions);setProjectContext(null);resetComputeTransportCache();setConnection(selected);setSelection(id);telemetryService.disconnect();};
    if(authority)ownContextChange(authority,reset);else reset();
    assertProjectIdle();const generation=getProjectContextGeneration();await syncSharedProject();
    if(authority){
      // This operation may publish exactly one null-to-selected context. An ABA or competing publication is refused.
      authority.generation=generation+1;check();
      const context=getProjectContext();if(context?.actor_id!==authority.actor||context.project_id!==useProjectStore.getState().project?.id)throw new Error('선택한 프로젝트 문맥을 확인하지 못했습니다.');
    }
    if(host.kind()==='browser'&&!selected.remote_id&&!acceptedSourceScope(selected))throw new Error(useProjectStore.getState().projectError||'공유 프로젝트 선택을 완료하지 못했습니다. 현재 프로젝트를 다시 확인하세요.');
    await telemetryService.connect();check();
  };
  useEffect(()=>{let current=true;host.shared.connection().then(async value=>{if(!current||!value)return;setConnection(value);setSharedApiBase(value.transport_url??value.server_url,host.shared.requestOptions);setSelection(value.project_id||'');setServer(value.server_url);const result=await request<{projects:SharedProject[]}>('/api/accounts/me',{projectContext:null});if(current)setProjects(result.projects);if(value.user.administrator){const accounts=await request<{users:SharedUser[]}>('/api/accounts/users',{projectContext:null});if(current)setUsers(accounts.users);}}).catch(e=>{if(current)setError(String(e));});return()=>{current=false;};},[]);
  const login=async()=>{setBrowserAuthority(null);if(!localAuthenticationOnly){assertProjectIdle();await saveOpenEdits({allowCleanDisconnect:host.kind()==='browser'&&!connection});assertProjectIdle();}setSourceDraft(emptySource);const value=await host.shared.login({server_url:localAuthenticationOnly?window.location.origin:server,username,password});telemetryService.disconnect();if(value.remote_id)setSharedApiBase(value.transport_url??value.server_url,host.shared.requestOptions);setProjectContext(null);setPassword('');setConnection(value);setSharedApiBase(value.transport_url??value.server_url,host.shared.requestOptions);resetComputeTransportCache();useAnnotationStore.getState().setReviewerName(value.user.username);
    const transport=getApiPersistenceIdentity(),generation=getProjectContextGeneration();
    const ownBrowser=host.kind()==='browser'&&!value.remote_id&&value.server_url===window.location.origin;
    const identity=ownBrowser?host.shared.browserIdentity?.():null;
    const current=()=>getApiPersistenceIdentity()===transport&&getProjectContextGeneration()===generation&&(!ownBrowser||!!identity&&identity.origin===value.server_url&&identity.actor_id===value.user.id&&(()=>{const now=host.shared.browserIdentity?.();return now?.origin===identity.origin&&now.actor_id===identity.actor_id&&now.revision===identity.revision;})());
    const changed=()=>new Error('서버 연결이 변경됐습니다. 현재 서버의 프로젝트를 다시 선택하세요.');
    const result=await request<BrowserAccountState>('/api/accounts/me',{projectContext:null});if(!current())throw changed();
    if(ownBrowser&&(result.user?.id!==value.user.id||!['shared','local_host'].includes(result.browser_project_mode as string)))throw new Error('현재 계정의 프로젝트 연결 방식을 확인하지 못했습니다. 다시 로그인하세요.');
    setProjects(result.projects);
    if(value.user.administrator){const accounts=await request<{users:SharedUser[]}>('/api/accounts/users',{projectContext:null});if(!current())throw changed();setUsers(accounts.users);}
    if(ownBrowser){
      setSelection('');setProjectContext(null);useProjectStore.setState({project:null,projectDir:null,projectName:'프로젝트 선택',activeStep:1,recentProjects:[]});useDatasetStore.getState().setFolderPath('');await useAnnotationStore.getState().setImages([],0);if(!current())throw changed();
      setBrowserAuthority({mode:result.browser_project_mode as BrowserProjectMode,origin:value.server_url,actor:value.user.id,revision:identity!.revision,transport,generation});return;
    }
    const id=result.selected_project_id||result.projects[0]?.id;
    if(id){assertProjectIdle();await request('/api/accounts/select-project',{projectContext:null,method:'POST',body:JSON.stringify({project_id:id})});if(!current())throw changed();assertProjectIdle();const selected=await host.shared.select(id);
      if(!current()||selected.server_url!==value.server_url||selected.user.id!==value.user.id||selected.project_id!==id)throw changed();
      setSharedApiBase(selected.transport_url??selected.server_url,host.shared.requestOptions);setProjectContext(null);setConnection(selected);setSelection(id);assertProjectIdle();await syncSharedProject();
      if(!acceptedSourceScope(selected))throw new Error(useProjectStore.getState().projectError||'공유 프로젝트 선택을 완료하지 못했습니다. 현재 프로젝트를 다시 확인하세요.');
      // The immediate reset already retired old targets. Refresh this accepted
      // project now, including a re-login whose project ID did not change.
      resetComputeTransportCache();}
    else{useProjectStore.setState({project:null,projectDir:null,projectName:'공유 프로젝트 선택',activeStep:1});useDatasetStore.getState().setFolderPath('');await useAnnotationStore.getState().setImages([],0);}
    if(id)await telemetryService.connect();
    onAuthenticated?.();
  };
  const disconnect=async()=>{setBrowserAuthority(null);assertProjectIdle();await saveOpenEdits({allowCleanDisconnect:true});assertProjectIdle();setSourceDraft(emptySource);await host.shared.disconnect();setSharedApiBase(null);resetComputeTransportCache();setConnection(null);setProjects([]);setUsers([]);setSelection('');telemetryService.disconnect();assertProjectIdle();
    if(host.kind()==='browser'&&connection?.remote_id){
      const local=await host.shared.connection();setConnection(local);setSharedApiBase(local?.transport_url??local?.server_url??null,host.shared.requestOptions);setSelection(local?.project_id??'');setProjectContext(null);await useProjectStore.getState().syncCurrentProject();await telemetryService.connect();
    }else if(host.kind()==='browser'){
      // Browser logout has no local daemon to restore. Clear the clean view so
      // revoked project data is not retained behind the next login form.
      await useAnnotationStore.getState().setImages([]);
      useDatasetStore.getState().setFolderPath('');
      useProjectStore.setState({project:null,projectDir:null,projectName:'공동 작업 서버 로그인 필요',activeStep:1});
    }else{await useProjectStore.getState().syncCurrentProject();await telemetryService.connect();}
  };
  const createProject=async()=>{assertProjectIdle();await saveOpenEdits();assertProjectIdle();setSourceDraft(emptySource);
    const value=connection,transport=getApiPersistenceIdentity(),generation=getProjectContextGeneration();
    const current=()=>getApiPersistenceIdentity()===transport&&getProjectContextGeneration()===generation;
    const changed=()=>new Error('서버 연결이 변경됐습니다. 현재 서버의 프로젝트를 다시 선택하세요.');
    const project=await api.project.create({name,task:newTask});if(!current())throw changed();setName('');await refresh();if(!current())throw changed();
    assertProjectIdle();const selected=await host.shared.select(project.id);
    if(!current()||!value||selected.server_url!==value.server_url||selected.user.id!==value.user.id||selected.project_id!==project.id)throw changed();
    setSharedApiBase(selected.transport_url??selected.server_url,host.shared.requestOptions);setProjectContext(null);setConnection(selected);setSelection(project.id);telemetryService.disconnect();assertProjectIdle();await syncSharedProject();
    const state=useProjectStore.getState();
    if(state.projectError||!acceptedSourceScope(selected))throw new Error(state.projectError||'공유 프로젝트 선택을 완료하지 못했습니다. 현재 프로젝트를 다시 확인하세요.');
    // A first-project login had no context to load. Refresh only after this
    // newly created project and its account authority are accepted together.
    resetComputeTransportCache();await telemetryService.connect();};
  const gateAuthority=():BrowserProjectAuthority=>{
    if(!browserAuthority)throw new Error('현재 계정의 프로젝트 연결 방식을 확인하고 다시 로그인하세요.');
    const authority={...browserAuthority};assertBrowserAuthority(authority);return authority;
  };
  const localGateSelect=async(id:string)=>{
    if(changing)return;const authority=gateAuthority();assertProjectIdle();
    if(!projects.some(project=>project.id===id))throw new Error('현재 계정의 프로젝트를 직접 선택하세요.');
    if(authority.mode==='local_host'){
      const approval=await request<{approved:boolean}>('/api/local-host/approve',{projectContext:null,method:'POST',body:'{}'});assertBrowserAuthority(authority);
      if(!approval.approved)throw new Error('이 컴퓨터의 로컬 기능 허용이 필요합니다.');
      const catalog=await request<{registrations:Array<{registration_id:string;permission_key:string;path:string;revision:number}>}>('/api/local-host/projects/location-recovery',{projectContext:null});assertBrowserAuthority(authority);
      const row=catalog.registrations.find(value=>value.permission_key===id);
      if(row){
        const candidate=await request<Awaited<ReturnType<typeof api.project.getCurrent>>>('/api/local-host/projects/select',{projectContext:null,method:'POST',body:JSON.stringify({registration_id:row.registration_id})});assertBrowserAuthority(authority);
        await api.project.prepareContext?.(candidate);assertBrowserAuthority(authority);assertProjectIdle();const refusal=projectSwitchRefusal();if(refusal)throw new Error(refusal);
        ownContextChange(authority,()=>api.project.acceptContext(candidate));
      }
    }
    await activate(id,authority);assertBrowserAuthority(authority);onAuthenticated?.();
  };
  const localGateReconnect=async(id:string)=>{
    const authority=gateAuthority();if(authority.mode!=='local_host')throw new Error('로컬 프로젝트에서만 폴더를 다시 확인할 수 있습니다.');
    const catalog=await request<{registrations:Array<{registration_id:string;permission_key:string;path:string;revision:number}>}>('/api/local-host/projects/location-recovery',{projectContext:null});assertBrowserAuthority(authority);
    const row=catalog.registrations.find(value=>value.permission_key===id);if(!row)throw new Error('이 폴더의 소유자 계정으로 다시 확인하세요.');
    const chosen=await request<{candidate_id?:string;canceled?:boolean}>('/api/local-host/projects/location-recovery/choose',{projectContext:null,method:'POST',body:JSON.stringify({registration_id:row.registration_id,expected_revision:row.revision})});assertBrowserAuthority(authority);
    if(chosen.canceled||!chosen.candidate_id)return;
    const result=await request<{requires_relogin?:boolean;autoactivated?:boolean;canceled?:boolean}>('/api/local-host/projects/location-recovery/confirm',{projectContext:null,method:'POST',body:JSON.stringify({candidate_id:chosen.candidate_id})});assertBrowserAuthority(authority);
    if(result.requires_relogin!==true||result.autoactivated!==false)return;
    telemetryService.disconnect();retireLocalBrowserAuthority();setBrowserAuthority(null);setProjectContext(null);resetComputeTransportCache();setConnection(null);setSelection('');setProjects([]);setUsers([]);
    const retiredGeneration=getProjectContextGeneration();
    useProjectStore.setState({project:null,projectDir:null,projectName:'다시 로그인 필요',activeStep:1,recentProjects:[]});useDatasetStore.getState().setFolderPath('');await useAnnotationStore.getState().setImages([],0);
    if(host.shared.browserIdentity?.()||getApiPersistenceIdentity()!==authority.transport||getProjectContextGeneration()!==retiredGeneration)throw new Error('계정 연결이 변경됐습니다. 현재 계정을 확인하세요.');
    setError('폴더를 다시 연결했습니다. 다시 로그인하고 프로젝트를 직접 선택하세요.');
  };
  const updateSource=async()=>{assertProjectIdle();const scope=acceptedSourceScope(connection);if(!scope||sourceDraft.scope!==scope)throw new Error('공유 프로젝트 선택을 완료한 뒤 원본 경로를 다시 확인하세요.');if(!confirmationKey||sourceConfirmation!==confirmationKey)throw new Error('현재 서버와 프로젝트에 이 원본 경로를 공유할지 확인하세요.');await api.project.update({source_dataset_dir:sourceDraft.value});setSourceConfirmation(null);await useProjectStore.getState().syncCurrentProject();};
  const changing=busy||(!localAuthenticationOnly&&(projectBusy||dataBusy||switchRefusal!==null));
  const sourceReady=!!sourceScope&&sourceDraft.scope===sourceScope;
  const input='min-w-0 rounded border border-slate-600 bg-slate-950 px-2 py-1.5 text-xs text-slate-200';
  return <details className="mt-3 rounded-lg border border-slate-700 bg-[#142131] p-3" open={!!connection||localAuthenticationOnly}><summary className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-cyan-200"><Users className="h-4 w-4"/>{localAuthenticationOnly?'이 브라우저의 계정':'공동 작업 서버'}{connection&&<span className="ml-auto text-[10px] text-emerald-300">{connection.user.username}</span>}</summary>
    {connection&&localAuthenticationOnly?<LocalProjectGate projects={projects} mode={browserAuthority?.mode??null} busy={changing} onSelect={localGateSelect} onReconnect={localGateReconnect} onLogout={()=>run(()=>changingSession(disconnect))}/>:!connection?<div className="mt-3 space-y-2"><p className="text-[11px] text-slate-400">{localAuthenticationOnly?'현재 HTTPS 주소의 계정으로 다시 로그인하세요. 저장된 프로젝트와 데이터는 로그인 후 확인합니다.':'공유 서버 계정으로 프로젝트와 라벨을 함께 관리합니다.'}</p><input aria-label="공동 작업 서버 주소" disabled={localAuthenticationOnly} value={localAuthenticationOnly?window.location.origin:server} onChange={e=>setServer(e.target.value)} placeholder="https://vision.example.com 또는 로컬 SSH 터널" className={`${input} w-full`}/>{insecureAddress(server)&&<p role="alert" className="text-[11px] text-amber-200">암호화되지 않은 주소입니다. https:// 주소나 SSH 터널(http://127.0.0.1:포트)로 연결하세요.</p>}<div className="flex gap-2"><input aria-label="공유 계정 이름" value={username} onChange={e=>setUsername(e.target.value)} placeholder="계정 이름" autoComplete="username" className={`${input} flex-1`}/><input aria-label="공유 계정 비밀번호" type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="비밀번호" autoComplete="current-password" className={`${input} flex-1`}/></div><button disabled={changing||!server||!username||!password} onClick={()=>void run(()=>changingSession(login))} className="flex items-center gap-1 rounded bg-cyan-700 px-3 py-2 text-xs disabled:opacity-40"><LogIn className="h-3.5 w-3.5"/>연결·로그인</button></div>:<div className="mt-3 space-y-3">{host.kind()==='browser'&&!connection.remote_id&&<button disabled={changing} onClick={()=>{setConnection(null);setServer('');setUsername('');setPassword('');}} className="rounded border border-cyan-700 px-2 py-1 text-xs">다른 원격 서버 연결</button>}<p className="break-all text-[10px] text-slate-400">{connection.server_url} · 세션 만료 {new Date(connection.expires_at*1000).toLocaleTimeString()}</p><div className="flex gap-2"><select aria-label="공동 작업 프로젝트" value={selection} onChange={e=>void run(()=>changingSession(()=>activate(e.target.value)))} disabled={changing} className={`${input} flex-1`}><option value="">프로젝트 선택</option>{projects.map(p=><option key={p.id} value={p.id}>{p.path.split('/').pop()} · {roleNames[p.role]}</option>)}</select><button disabled={changing} onClick={()=>void run(()=>changingSession(disconnect))} aria-label="공동 작업 서버 연결 해제" className="rounded border border-slate-600 p-2"><LogOut className="h-4 w-4"/></button></div>
      {!!connection.user.administrator&&<details><summary className="cursor-pointer text-xs text-slate-300">프로젝트·계정 관리</summary><div className="mt-2 space-y-2"><div className="flex gap-2"><input aria-label="공유 프로젝트 이름" value={name} onChange={e=>setName(e.target.value)} placeholder="새 프로젝트 이름" className={`${input} flex-1`}/><select aria-label="공유 프로젝트 작업 종류" value={newTask} onChange={e=>setNewTask(e.target.value as VisionTask)} className={input}>{(Object.keys(taskNames) as VisionTask[]).map(task=><option key={task} value={task}>{taskNames[task]}</option>)}</select><button disabled={changing||!name.trim()} onClick={()=>void run(()=>changingSession(createProject))} className="rounded bg-cyan-800 px-2 text-xs">만들기</button></div>
      <div className="flex gap-2"><input aria-label="서버 데이터 원본 경로" value={sourceReady?sourceDraft.value:''} disabled={changing||!sourceReady} onChange={e=>{setSourceConfirmation(null);setSourceDraft({value:e.target.value,scope:sourceScope});}} placeholder="서버에 있는 데이터 원본 경로" className={`${input} flex-1`}/><button disabled={changing||!sourceReady||!sourceDraft.value.trim()||sourceConfirmation!==confirmationKey} onClick={()=>void run(updateSource)} className="rounded border border-cyan-700 px-2 text-xs">원본 지정</button></div>
      <label className="flex items-start gap-2 text-[11px] text-slate-400"><input type="checkbox" aria-label="원본 경로 공유 확인" checked={!!confirmationKey&&sourceConfirmation===confirmationKey} disabled={changing||!sourceReady} onChange={e=>setSourceConfirmation(e.target.checked?confirmationKey:null)}/>{connection.server_url}의 현재 프로젝트 구성원이 지정한 원본 데이터를 사용할 수 있도록 공유합니다.</label>
      <div className="flex gap-2"><input aria-label="생성할 계정 이름" value={newUser} onChange={e=>setNewUser(e.target.value)} placeholder="새 계정 이름" className={`${input} flex-1`}/><input aria-label="생성할 계정 비밀번호" value={newPassword} onChange={e=>setNewPassword(e.target.value)} type="password" placeholder="12자 이상 비밀번호" className={`${input} flex-1`}/><button disabled={busy||newPassword.length<12||!newUser} onClick={()=>void run(async()=>{await request('/api/accounts/users',{method:'POST',body:JSON.stringify({username:newUser,password:newPassword,administrator:false})});setNewPassword('');setNewUser('');await refresh();})} className="rounded border border-cyan-700 px-2 text-xs">추가</button></div>
      <div className="flex gap-2"><select aria-label="권한을 지정할 계정" value={member} onChange={e=>setMember(e.target.value)} className={`${input} flex-1`}><option value="">계정 선택</option>{users.map(u=><option key={u.id} value={u.id}>{u.username}</option>)}</select><select aria-label="프로젝트 역할" value={role} onChange={e=>setRole(e.target.value)} className={input}>{Object.entries(roleNames).map(([r,label])=><option key={r} value={r}>{label}</option>)}</select><button disabled={busy||!member||!selection} onClick={()=>void run(async()=>{await request(`/api/accounts/projects/${selection}/members`,{method:'PUT',body:JSON.stringify({user_id:member,role})});await refresh();})} className="rounded border border-cyan-700 px-2 text-xs">적용</button></div></div></details>}
      <p className="text-[10px] text-slate-500">{host.kind()==='browser'?'새로고침 후 다시 로그인하세요. 서버 쿠키는 로그아웃 또는 만료 시 해제됩니다.':'계정 세션은 앱을 닫으면 해제됩니다.'} 비밀번호는 저장하지 않습니다.</p>
    </div>}{error&&<p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}
  </details>;
};
