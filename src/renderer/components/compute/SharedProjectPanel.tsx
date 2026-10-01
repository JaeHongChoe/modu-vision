import React,{useEffect,useState} from 'react';
import {Users,LogIn,LogOut} from 'lucide-react';
import {api,request,setSharedApiBase,getApiPersistenceIdentity,getProjectContext} from '../../services/api';
import {useProjectStore,saveOpenEdits} from '../../stores/useProjectStore';
import {resetComputeTransportCache} from '../../stores/useComputeStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {telemetryService} from '../../services/websocket';
import type {SharedConnection} from '../../../types/electron';
type SharedProject={id:string;path:string;role:string};
type SharedUser={id:string;username:string;administrator:boolean};
const roleNames:Record<string,string>={viewer:'보기',labeler:'라벨 작업',trainer:'학습·검사',reviewer:'검토·승인',owner:'프로젝트 관리'};
type SourceDraft={value:string;scope:string|null};
const emptySource:SourceDraft={value:'',scope:null};
function assertProjectIdle():void {
  if(useProjectStore.getState().isProjectBusy)throw new Error('프로젝트 작업이 끝난 뒤 다시 시도하세요.');
}
function acceptedSourceScope(connection:SharedConnection|null):string|null {
  const state=useProjectStore.getState(),context=getProjectContext();
  if(!connection?.project_id||state.isProjectBusy||state.project?.id!==connection.project_id
    ||context?.mode!=='team'||context.project_id!==connection.project_id||context.actor_id!==connection.user.id)return null;
  const url=new URL(connection.server_url);
  const transport=`shared:${url.origin}${url.pathname.replace(/\/+$/,'')}`;
  if(getApiPersistenceIdentity()!==transport)return null;
  return JSON.stringify([transport,context.workspace_id,context.project_id,context.actor_id,state.project.project_dir]);
}
export const SharedProjectPanel:React.FC=()=>{
  const [connection,setConnection]=useState<SharedConnection|null>(null),[server,setServer]=useState(''),[username,setUsername]=useState(''),[password,setPassword]=useState(''),[projects,setProjects]=useState<SharedProject[]>([]),[selection,setSelection]=useState(''),[users,setUsers]=useState<SharedUser[]>([]),[newUser,setNewUser]=useState(''),[newPassword,setNewPassword]=useState(''),[member,setMember]=useState(''),[role,setRole]=useState('viewer'),[name,setName]=useState(''),[sourceDraft,setSourceDraft]=useState<SourceDraft>(emptySource),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const visibleProject=useProjectStore(state=>state.project);
  const projectBusy=useProjectStore(state=>state.isProjectBusy);
  const sourceScope=acceptedSourceScope(connection);
  useEffect(()=>{
    setSourceDraft(sourceScope?{value:visibleProject?.source_dataset_dir||'',scope:sourceScope}:emptySource);
  },[sourceScope,visibleProject?.source_dataset_dir]);
  const run=async(action:()=>Promise<void>)=>{setBusy(true);setError('');try{await action();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
  const refresh=async()=>{const result=await request<{projects:SharedProject[];selected_project_id:string|null}>('/api/accounts/me');setProjects(result.projects);if(connection?.user.administrator)setUsers((await request<{users:SharedUser[]}>('/api/accounts/users')).users);return result;};
  const activate=async(id:string)=>{assertProjectIdle();await saveOpenEdits();assertProjectIdle();setSourceDraft(emptySource);await request('/api/accounts/select-project',{method:'POST',body:JSON.stringify({project_id:id})});assertProjectIdle();const selected=await window.api.selectSharedProject(id);resetComputeTransportCache();setConnection(selected);setSelection(id);telemetryService.disconnect();assertProjectIdle();await useProjectStore.getState().syncCurrentProject();await telemetryService.connect();};
  useEffect(()=>{let current=true;window.api?.getSharedConnection?.().then(async value=>{if(!current||!value)return;setConnection(value);setSharedApiBase(value.server_url);setSelection(value.project_id||'');setServer(value.server_url);const result=await request<{projects:SharedProject[]}>('/api/accounts/me');if(current)setProjects(result.projects);if(value.user.administrator){const accounts=await request<{users:SharedUser[]}>('/api/accounts/users');if(current)setUsers(accounts.users);}}).catch(e=>{if(current)setError(String(e));});return()=>{current=false;};},[]);
  const login=async()=>{assertProjectIdle();await saveOpenEdits();assertProjectIdle();setSourceDraft(emptySource);const value=await window.api.loginSharedServer({server_url:server,username,password});setPassword('');setConnection(value);setSharedApiBase(value.server_url);resetComputeTransportCache();useAnnotationStore.getState().setReviewerName(value.user.username);
    const result=await request<{projects:SharedProject[];selected_project_id:string|null}>('/api/accounts/me');setProjects(result.projects);
    if(value.user.administrator)setUsers((await request<{users:SharedUser[]}>('/api/accounts/users')).users);
    const id=result.selected_project_id||result.projects[0]?.id;
    if(id){assertProjectIdle();await request('/api/accounts/select-project',{method:'POST',body:JSON.stringify({project_id:id})});assertProjectIdle();setConnection(await window.api.selectSharedProject(id));resetComputeTransportCache();setSelection(id);assertProjectIdle();await useProjectStore.getState().syncCurrentProject();}
    else{useProjectStore.setState({project:null,projectDir:null,projectName:'공유 프로젝트 선택',activeStep:1});useDatasetStore.getState().setFolderPath('');await useAnnotationStore.getState().setImages([],0);}
    telemetryService.disconnect();if(id)await telemetryService.connect();
  };
  const disconnect=async()=>{assertProjectIdle();await saveOpenEdits();assertProjectIdle();setSourceDraft(emptySource);await window.api.disconnectSharedServer();setSharedApiBase(null);resetComputeTransportCache();setConnection(null);setProjects([]);setUsers([]);setSelection('');telemetryService.disconnect();assertProjectIdle();await useProjectStore.getState().syncCurrentProject();await telemetryService.connect();};
  const createProject=async()=>{assertProjectIdle();await saveOpenEdits();assertProjectIdle();setSourceDraft(emptySource);const project=await api.project.create({name,task:'classification'});setName('');await refresh();assertProjectIdle();setConnection(await window.api.selectSharedProject(project.id));setSelection(project.id);assertProjectIdle();await useProjectStore.getState().syncCurrentProject();};
  const updateSource=async()=>{assertProjectIdle();const scope=acceptedSourceScope(connection);if(!scope||sourceDraft.scope!==scope)throw new Error('공유 프로젝트 선택을 완료한 뒤 원본 경로를 다시 확인하세요.');await api.project.update({source_dataset_dir:sourceDraft.value});await useProjectStore.getState().syncCurrentProject();};
  const changing=busy||projectBusy;
  const sourceReady=!!sourceScope&&sourceDraft.scope===sourceScope;
  const input='min-w-0 rounded border border-slate-600 bg-slate-950 px-2 py-1.5 text-xs text-slate-200';
  return <details className="mt-3 rounded-lg border border-slate-700 bg-[#142131] p-3" open={!!connection}><summary className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-cyan-200"><Users className="h-4 w-4"/>공동 작업 서버{connection&&<span className="ml-auto text-[10px] text-emerald-300">{connection.user.username}</span>}</summary>
    {!connection?<div className="mt-3 space-y-2"><p className="text-[11px] text-slate-400">공유 서버 계정으로 프로젝트와 라벨을 함께 관리합니다.</p><input aria-label="공동 작업 서버 주소" value={server} onChange={e=>setServer(e.target.value)} placeholder="https://vision.example.com 또는 로컬 SSH 터널" className={`${input} w-full`}/><div className="flex gap-2"><input aria-label="공유 계정 이름" value={username} onChange={e=>setUsername(e.target.value)} placeholder="계정 이름" autoComplete="username" className={`${input} flex-1`}/><input aria-label="공유 계정 비밀번호" type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="비밀번호" autoComplete="current-password" className={`${input} flex-1`}/></div><button disabled={changing||!server||!username||!password} onClick={()=>void run(login)} className="flex items-center gap-1 rounded bg-cyan-700 px-3 py-2 text-xs disabled:opacity-40"><LogIn className="h-3.5 w-3.5"/>연결·로그인</button></div>:<div className="mt-3 space-y-3"><p className="break-all text-[10px] text-slate-400">{connection.server_url} · 세션 만료 {new Date(connection.expires_at*1000).toLocaleTimeString()}</p><div className="flex gap-2"><select aria-label="공동 작업 프로젝트" value={selection} onChange={e=>void run(()=>activate(e.target.value))} disabled={changing} className={`${input} flex-1`}><option value="">프로젝트 선택</option>{projects.map(p=><option key={p.id} value={p.id}>{p.path.split('/').pop()} · {roleNames[p.role]}</option>)}</select><button disabled={changing} onClick={()=>void run(disconnect)} aria-label="공동 작업 서버 연결 해제" className="rounded border border-slate-600 p-2"><LogOut className="h-4 w-4"/></button></div>
      {!!connection.user.administrator&&<details><summary className="cursor-pointer text-xs text-slate-300">프로젝트·계정 관리</summary><div className="mt-2 space-y-2"><div className="flex gap-2"><input aria-label="공유 프로젝트 이름" value={name} onChange={e=>setName(e.target.value)} placeholder="새 프로젝트 이름" className={`${input} flex-1`}/><button disabled={changing||!name.trim()} onClick={()=>void run(createProject)} className="rounded bg-cyan-800 px-2 text-xs">만들기</button></div>
      <div className="flex gap-2"><input aria-label="서버 데이터 원본 경로" value={sourceReady?sourceDraft.value:''} disabled={changing||!sourceReady} onChange={e=>setSourceDraft({value:e.target.value,scope:sourceScope})} placeholder="서버에 있는 데이터 원본 경로" className={`${input} flex-1`}/><button disabled={changing||!sourceReady||!sourceDraft.value.trim()} onClick={()=>void run(updateSource)} className="rounded border border-cyan-700 px-2 text-xs">원본 지정</button></div>
      <div className="flex gap-2"><input aria-label="생성할 계정 이름" value={newUser} onChange={e=>setNewUser(e.target.value)} placeholder="새 계정 이름" className={`${input} flex-1`}/><input aria-label="생성할 계정 비밀번호" value={newPassword} onChange={e=>setNewPassword(e.target.value)} type="password" placeholder="12자 이상 비밀번호" className={`${input} flex-1`}/><button disabled={busy||newPassword.length<12||!newUser} onClick={()=>void run(async()=>{await request('/api/accounts/users',{method:'POST',body:JSON.stringify({username:newUser,password:newPassword,administrator:false})});setNewPassword('');setNewUser('');await refresh();})} className="rounded border border-cyan-700 px-2 text-xs">추가</button></div>
      <div className="flex gap-2"><select aria-label="권한을 지정할 계정" value={member} onChange={e=>setMember(e.target.value)} className={`${input} flex-1`}><option value="">계정 선택</option>{users.map(u=><option key={u.id} value={u.id}>{u.username}</option>)}</select><select aria-label="프로젝트 역할" value={role} onChange={e=>setRole(e.target.value)} className={input}>{Object.entries(roleNames).map(([r,label])=><option key={r} value={r}>{label}</option>)}</select><button disabled={busy||!member||!selection} onClick={()=>void run(async()=>{await request(`/api/accounts/projects/${selection}/members`,{method:'PUT',body:JSON.stringify({user_id:member,role})});await refresh();})} className="rounded border border-cyan-700 px-2 text-xs">적용</button></div></div></details>}
      <p className="text-[10px] text-slate-500">계정 세션은 앱을 닫으면 해제됩니다. 비밀번호는 저장하지 않습니다.</p>
    </div>}{error&&<p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}
  </details>;
};
