import { setLocalHostTransport, getLocalProjectContext, getHostProjectState, type HostProjectState, type ProjectContext } from './api';
import { browserSession, setBrowserHost } from './browserSession';
import type { AssistantBridge, ElectronAPI } from '../../types/electron';

export type PickerPurpose = 'project-create'|'project-open'|'project-backup'|'project-restore-archive'|'project-restore-target'|'project-recover-target'|'dataset-source'|'label-import'|'mask-import'|'model-directory'|'pretrained-weights'|'runtime-source'|'runtime-inventory'|'project-compatibility'|'inspection-image';
export type FolderOptions = {title?:string;defaultPath?:string;purpose?:PickerPurpose};
export type FileOptions = NonNullable<Parameters<ElectronAPI['selectFile']>[0]> & {purpose?:PickerPurpose};
type Capability = 'pickPaths'|'openPaths'|'assistant'|'updates'|'projects';
type Capabilities = Partial<Record<Capability,boolean|{available:boolean;reason?:string}>>;
type Reasons = Partial<Record<Capability|'local_host',string>>;
type Grant = {id:string;path:string;purpose:PickerPurpose};
const purposes:Record<PickerPurpose,Array<[string,string,string]>> = {
 'project-create':[['POST','/api/local-host/projects/create','project_dir']],
 'project-open':[['POST','/api/local-host/projects/open','project_dir']],
 'project-backup':[['POST','/api/local-host/projects/backup','destination_dir']],
 'project-restore-archive':[['POST','/api/local-host/projects/restore','archive_path']],
 'project-restore-target':[['POST','/api/local-host/projects/restore','target_dir']],
 'project-recover-target':[['POST','/api/local-host/projects/recover','target_dir']],
 'dataset-source':[['POST','/api/dataset/import','folder_path'],['PUT','/api/project/update','source_dataset_dir']],
 'label-import':[['POST','/api/dataset/formats/import','import_dir']],
 'mask-import':[['POST','/api/dataset/masks/import','import_dir']],
 'model-directory':[['PUT','/api/label-candidates/setup','model_dir'],['PUT','/api/label-candidates/setup','mask_model_dir']],
 'pretrained-weights':[['POST','/api/training-workspace/import-weights','pretrained_checkpoint']],
 'runtime-source':[['POST','/api/product-delivery/runtime-packs/install','source_dir']],
 'runtime-inventory':[['POST','/api/product-delivery/runtime-packs/install','inventory_path']],
 'project-compatibility':[['POST','/api/project/compatibility/preview','project_dir'],['POST','/api/project/compatibility/apply','project_dir']],
 'inspection-image':[['POST','/api/flowchart/run','image_path'],['POST','/api/flowchart/partial-runs/prepare','image_path']],
};
const writes:Record<string,[string,string]>={save_split:['POST','/api/dataset/split'],assign_class:['PUT','/api/dataset/metadata/class-override'],generate_label_drafts:['POST','/api/label-suggestions/batches'],start_training:['POST','/api/training/start']};

/** Local host authority is deliberately separate from the API's current remote transport.
 * Only this tab's memory holds selection grants; the server remains authoritative. */
export class BrowserHost {
 private tab:string|null=null;
 private epoch=0;
 private approved=false;
 private readonly inFlight=new Set<AbortController>();
 private grants:Grant[]=[];
 private capabilities:Capabilities={};
 private reasons:Reasons={};
 private pending:Promise<void>|null=null;
 private run:{id:string;tool:string;expires:number;resource:string}|null=null;
 constructor(private readonly runtime:{origin:()=>string|null;fetch:typeof fetch;session:typeof browserSession;now:()=>number;context?:()=>ProjectContext|null;resource?:()=>HostProjectState}){}
 available():boolean {const origin=this.runtime.origin();if(!origin)return false;try{const url=new URL(origin);return url.protocol==='https:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname);}catch{return false;}}
 private resource():HostProjectState {return this.runtime.resource?.()??{local:true,generation:0};}
 private resourceKey():string {return JSON.stringify(this.resource());}
 can(capability:Capability):boolean {if(['pickPaths','openPaths','projects'].includes(capability)&&!this.resource().local)return false;if(!this.available()||!this.runtime.session.connection())return false;const value=this.capabilities[capability];return value===true||typeof value==='object'&&value.available===true;}
 reason(capability:Capability):string {if(['pickPaths','openPaths','projects'].includes(capability)&&!this.resource().local)return '원격 프로젝트에서는 서버 경로를 입력하거나 ZIP 파일 업로드를 사용하세요. 로컬 경로 선택·열기는 로컬 프로젝트로 돌아온 뒤 사용할 수 있습니다.';const value=this.capabilities[capability];return typeof value==='object'&&value.reason||this.reasons[capability]||this.reasons.local_host||'로컬 보조 프로그램 연결과 계정 권한을 확인하세요.';}
 private abortPending():void {for(const controller of this.inFlight)controller.abort();this.inFlight.clear();}
 private disposeTab():Promise<void>{
  if(!this.tab||!this.available()||!this.runtime.session.connection())return Promise.resolve();
  const url=this.runtime.origin()!+'/api/local-host/session/dispose';
  const options=this.runtime.session.requestOptions(url,'POST');
  const headers={'Content-Type':'application/json',...options.headers,'X-Vision-Local-Tab':this.tab};
  // Disposal must survive retirement of the ordinary request controllers; no project authority or retries.
  return this.runtime.fetch(url,{...options,method:'POST',headers,body:'{}',redirect:'error',keepalive:true,signal:AbortSignal.timeout(5000)}).then(()=>undefined,()=>undefined);
 }
 private retire(clearCapabilities:boolean):void {
  this.abortPending();this.epoch++;this.approved=false;this.tab=null;this.grants=[];this.pending=null;this.run=null;
  if(clearCapabilities){this.capabilities={};this.reasons={};}
 }
 reset():void {void this.disposeTab();this.retire(true);}
 async dispose():Promise<void>{const pending=this.disposeTab();this.retire(true);await pending;}
 projectChanged():void {void this.disposeTab();this.retire(false);}
 async prepareRequest(target?:string,method='GET',_body?:unknown):Promise<void>{
  if(!this.tab)try{await this.initialize();}catch(error){this.capabilities={};this.reasons={local_host:error instanceof Error?error.message:'로컬 보조 프로그램 연결을 확인하세요.'};}
  if(target&&method.toUpperCase()==='POST'){const url=new URL(target,this.runtime.origin()??undefined);if(url.origin===this.runtime.origin()&&/^\/api\/runtime-services\/(install\/(activate|remove)|scm\/(prepare|activate))$/.test(url.pathname))await this.approve();}
 }
 async initialize():Promise<void> {
  if(!this.available()||!this.runtime.session.connection())return;
  if(this.pending)return this.pending;
  const epoch=this.epoch;
  const work=(async()=>{if(!this.tab){const value=await this.call<{tab_id:string}>('/session',{},false);if(typeof value.tab_id!=='string'||!value.tab_id)throw new Error('로컬 탭 응답을 확인하세요.');this.tab=value.tab_id;}
   const value=await this.call<{capabilities:Capabilities;reasons?:Reasons}>('/capabilities',undefined,false);
   if(epoch!==this.epoch)throw new Error('계정 또는 프로젝트가 바뀌었습니다. 다시 시도하세요.');
   this.capabilities=value.capabilities||{};this.reasons=value.reasons||{};
  })();this.pending=work;
  try{await work;}finally{if(this.pending===work)this.pending=null;}
 }
 private async call<T>(path:string,body?:unknown,requireTab=true):Promise<T>{
  if(!this.available()||!this.runtime.session.connection()||requireTab&&!this.tab)throw new Error('로컬 계정 로그인과 보조 프로그램 연결을 확인하세요.');
  const resource=this.resource(),resourceKey=this.resourceKey(),projectAction=/^\/assistant\/(send|imageIntent|beginRun|endRun)$/.test(path),pathAction=/^\/(dialog\/|open$)/.test(path);
  if(pathAction&&!resource.local)throw new Error(this.reason('pickPaths'));
  if(projectAction&&!resource.local&&!resource.remote)throw new Error('현재 원격 프로젝트 연결과 선택을 확인하세요.');
  const epoch=this.epoch,origin=this.runtime.origin()!,method=body===undefined?'GET':'POST';
  const options=this.runtime.session.requestOptions(origin+'/api/local-host'+path,method);
  const headers:Record<string,string>={'Content-Type':'application/json',...options.headers};if(this.tab)headers['X-Vision-Local-Tab']=this.tab;
  if(projectAction&&resource.remote)headers['X-Vision-Remote-Authority']=JSON.stringify(resource.remote);
  const context=resource.local?this.runtime.context?.():null;if(context){headers['X-Vision-Context']=JSON.stringify(context);headers['X-Vision-Project']=context.project_id;}
  const controller=new AbortController();this.inFlight.add(controller);
  let response:Response,value:any;
  try{response=await this.runtime.fetch(origin+'/api/local-host'+path,{...options,method,headers,signal:controller.signal,redirect:'error',...(body===undefined?{}:{body:JSON.stringify(body)})});value=await response.json();}
  finally{this.inFlight.delete(controller);}
  if(epoch!==this.epoch||!this.runtime.session.connection()||((pathAction||projectAction)&&resourceKey!==this.resourceKey()))throw new Error('계정 또는 프로젝트가 바뀌었습니다. 다시 시도하세요.');
  if(!response.ok){if(response.status===401)this.reset();throw new Error(typeof value.detail==='string'?value.detail:value.detail?.message||value.error?.message||'로컬 기능 요청을 처리하지 못했습니다.');}
  return value as T;
 }
 private async approve():Promise<void>{if(this.approved)return;const value=await this.call<{approved:boolean}>('/approve',{});if(value.approved!==true)throw new Error('이 컴퓨터 소유자의 로컬 기능 허용이 필요합니다.');this.approved=true;}
 async prepareRemote():Promise<void>{await this.initialize();await this.approve();}
 private async choose(kind:'folder'|'file',options:FolderOptions|FileOptions={}):Promise<string|null>{
  await this.initialize();if(!this.can('pickPaths'))throw new Error(this.reason('pickPaths'));
  if(!options.purpose)throw new Error('파일 선택 목적을 확인하세요.');
  await this.approve();
  const value=await this.call<{path:string|null;grant_id?:string}>('/dialog/'+kind,options);
  if(value.path===null)return null;
  if(typeof value.path!=='string'||typeof value.grant_id!=='string'||!value.grant_id)throw new Error('파일 선택 권한 응답을 확인하세요.');
  this.grants.push({id:value.grant_id,path:value.path,purpose:options.purpose});return value.path;
 }
 selectFolder(options?:FolderOptions):Promise<string|null>{return this.choose('folder',options);}
 selectFile(options?:FileOptions):Promise<string|null>{return this.choose('file',options);}
 async openPath(path:string):Promise<boolean>{await this.initialize();if(!this.can('openPaths'))throw new Error(this.reason('openPaths'));await this.approve();return this.call('/open',{path});}
 generation():number {return this.epoch;}
 acceptResponseHeaders(path:string,method:string,headers:Headers,responseBody?:unknown):void {
  if(!this.tab||!this.runtime.session.connection()||!this.available()||!this.resource().local||method.toUpperCase()!=='POST')return;
  let url:URL;try{url=new URL(path,this.runtime.origin()!);}catch{return;}
  if(url.origin!==this.runtime.origin())return;
  const expected:Record<string,PickerPurpose>={'/api/dataset/formats/import':'label-import','/api/dataset/masks/import':'mask-import','/api/project/compatibility/preview':'project-compatibility'};
  const purpose=expected[url.pathname];if(!purpose||!responseBody||typeof responseBody!=='object'||(responseBody as {applied?:boolean}).applied===true)return;
  const encoded=headers.get('X-Vision-Path-Continuation');if(!encoded||encoded.length>16384)return;
  try{const rows:unknown=JSON.parse(encoded);if(!Array.isArray(rows)||rows.length>16)return;
   for(const row of rows){if(!row||typeof row!=='object')continue;const grant=row as {path?:unknown;grant_id?:unknown;purpose?:unknown};
    if(grant.purpose!==purpose||typeof grant.path!=='string'||!grant.path||grant.path.length>8192||typeof grant.grant_id!=='string'||!/^[A-Za-z0-9_.:-]{1,256}$/.test(grant.grant_id))continue;
    if(!this.grants.some(value=>value.id===grant.grant_id))this.grants.push({path:grant.path,id:grant.grant_id,purpose});
   }
  }catch{/* malformed continuation cannot create authority */}
 }
 requestHeaders(target:string,method='GET',body?:unknown):Record<string,string>{
  if(!this.tab||!this.available()||!this.runtime.session.connection())return {};
  let url:URL;try{url=new URL(target,this.runtime.origin()!);}catch{return {};}
  if(url.origin!==this.runtime.origin()||!url.pathname.startsWith('/api/'))return {};
  const headers:Record<string,string>={'X-Vision-Local-Tab':this.tab};
  let value:Record<string,unknown>={};try{value=typeof body==='string'?JSON.parse(body):body&&typeof body==='object'?body as Record<string,unknown>:{};}catch{/* no grants for a non-JSON body */}
  const matching=this.resource().local?this.grants.filter(grant=>purposes[grant.purpose].some(([verb,path,key])=>verb===method.toUpperCase()&&(path===url.pathname||grant.purpose==='project-restore-target'&&/^\/api\/local-host\/projects\/backups\/[A-Za-z0-9_-]{1,128}\/restore$/.test(url.pathname))&&typeof value[key]==='string'&&(value[key]===grant.path||grant.purpose==='project-restore-target'&&value[key]===grant.path.replace(/[\\/]+$/,'')+'/restored-project'))):[];
  if(matching.length){headers['X-Vision-Path-Grants']=JSON.stringify(matching.map(grant=>grant.id));this.grants=this.grants.filter(grant=>!matching.includes(grant));}
  const resource=this.resource(),remotePrefix=resource.remote?'/api/local-host/remote/'+resource.remote.remote_id:null;
  const writePath=resource.local?url.pathname:remotePrefix&&url.pathname.startsWith(remotePrefix+'/api/')?url.pathname.slice(remotePrefix.length):null;
  if(this.run&&this.run.resource===this.resourceKey()&&this.run.expires>this.runtime.now()){const write=writes[this.run.tool];if(write&&write[0]===method.toUpperCase()&&write[1]===writePath){headers['X-Modu-Assistant-Run']=this.run.id;this.run=null;}}
  return headers;
 }
 private async assistantCall<T>(action:string,args:unknown[]):Promise<T>{await this.initialize();if(!this.can('assistant'))throw new Error(this.reason('assistant'));await this.approve();return this.call('/assistant/'+action,{args});}
 assistant():AssistantBridge|null {
  if(!this.can('assistant'))return null;
  const call=<T>(action:string,...args:unknown[])=>this.assistantCall<T>(action,args);
  return {
   getState:()=>call('getState'),saveProvider:input=>call('saveProvider',input),removeKey:provider=>call('removeKey',provider),removeProvider:provider=>call('removeProvider',provider),setActive:provider=>call('setActive',provider),listModels:provider=>call('listModels',provider),send:input=>call('send',input),cancel:id=>call('cancel',id),imageIntent:input=>call('imageIntent',input),
   beginRun:async(id,tool)=>{const ok=await call<boolean>('beginRun',id,tool);if(ok&&/^run_[a-f0-9]{24}$/.test(id)&&writes[tool])this.run={id,tool,expires:this.runtime.now()+120000,resource:this.resourceKey()};return ok;},
   endRun:async id=>{if(this.run?.id===id)this.run=null;await call<void>('endRun',id);},
   chatgpt:{begin:input=>call('chatgpt.begin',input),acknowledgePlanUsage:id=>call('chatgpt.acknowledgePlanUsage',id),resumeUsage:id=>call('chatgpt.resumeUsage',id),recover:input=>call('chatgpt.recover',input),cancel:()=>call('chatgpt.cancel'),selectAccount:id=>call('chatgpt.selectAccount',id),selectModel:(id,model)=>call('chatgpt.selectModel',id,model),signOut:id=>call('chatgpt.signOut',id)},
  };
 }
 readonly updates={
  status:()=>this.update('status',[]),
  configure:(value:Parameters<ElectronAPI['configureUpdateChannel']>[0])=>this.update('configure',[value]),
  check:()=>this.update('check',[]),
  download:()=>this.update('download',[]),
  verifyOffline:()=>this.update('verifyOffline',[]),
  selectPortableHome:()=>this.update('selectPortableHome',[]),
  inspectPortable:()=>this.update('inspectPortable',[]),
  previewPortable:(channel:'stable'|'beta',pins:Parameters<ElectronAPI['previewPortableUpdate']>[1])=>this.update('previewPortable',[channel,pins]),
  applyPortable:(id:string)=>this.update('applyPortable',[id]),
  recoverPortable:(action:Parameters<ElectronAPI['recoverPortableUpdate']>[0],expected:Parameters<ElectronAPI['recoverPortableUpdate']>[1])=>this.update('recoverPortable',[action,expected]),
  launchPortable:(expected:Parameters<ElectronAPI['launchPortableUpdate']>[0])=>this.update('launchPortable',[expected]),
  inspectPortableLaunch:(expected:Parameters<ElectronAPI['inspectPortableLaunch']>[0])=>this.update('inspectPortableLaunch',[expected]),
 };
 async update<K extends keyof typeof updateMethods>(action:K,args:unknown[]):Promise<Awaited<ReturnType<ElectronAPI[typeof updateMethods[K]]>>>{await this.initialize();if(!this.can('updates'))throw new Error(this.reason('updates'));await this.approve();return this.call<Awaited<ReturnType<ElectronAPI[typeof updateMethods[K]]>>>('/updates/'+action,{args});}
}
const updateMethods={status:'getDistributionStatus',configure:'configureUpdateChannel',check:'checkForUpdate',download:'downloadUpdate',verifyOffline:'verifyOfflineUpdate',selectPortableHome:'selectPortableUpdateHome',inspectPortable:'inspectPortableUpdate',previewPortable:'previewPortableUpdate',applyPortable:'applyPortableUpdate',recoverPortable:'recoverPortableUpdate',launchPortable:'launchPortableUpdate',inspectPortableLaunch:'inspectPortableLaunch'} as const;
export const browserHost=new BrowserHost({origin:()=>typeof window!=='undefined'&&!window.api?window.location?.origin??null:null,fetch:(...args)=>fetch(...args),session:browserSession,context:getLocalProjectContext,resource:getHostProjectState,now:()=>Date.now()});

setLocalHostTransport({available:()=>browserHost.available(),canProjects:()=>browserHost.can('projects'),requestHeaders:(...args)=>browserHost.requestHeaders(...args),projectChanged:()=>browserHost.projectChanged(),prepareRequest:(...args)=>browserHost.prepareRequest(...args),generation:()=>browserHost.generation(),acceptResponseHeaders:(...args)=>browserHost.acceptResponseHeaders(...args)});

setBrowserHost(browserHost);
