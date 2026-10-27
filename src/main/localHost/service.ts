import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {OpenDialogOptions} from 'electron';
import {AssistantKeyStore,type KeyEncryptor} from '../assistant/keyStore';
import {AssistantService} from '../assistant/service';
import {SiwcStore} from '../assistant/siwcStore';
import {SiwcConnection,SiwcCredentialOwner} from '../assistant/siwcConnection';
import {validateBaseUrl} from '../assistant/providers';
import {AssistantRunRegistry} from '../assistant/runHeader';
import {DistributionManager,type DistributionOptions} from '../distributionStatus';
import {PortableUpdateManager,validatedCanaryPins} from '../portableUpdate';
import {classifyOpenTarget} from '../nativeOpen';
import {HostError,MAX_FRAME_BYTES,parseRequest,type HostRequest,type HostResponse} from './protocol';
import {TabKeyStore,tabSiwcStore} from './assistantScope';
export interface LinkedSourceConsent {source:string;project_dir:string;targets:{link:string;path:string;kind:'file'|'directory'}[];plan_sha256:string;file_count:number;total_bytes:number;
 scope:{actor_id:string;session_id:string;tab_id:string;project_id:string;workspace_id:string}}
export interface NativeAdapters {
 approve(input:{origin:string;accountLabel:string}):Promise<boolean>;
 confirmLinkedSource?(input:LinkedSourceConsent):Promise<boolean>;
 choose(options:OpenDialogOptions):Promise<{canceled:boolean;filePaths:string[]}>;
 openPath(value:string):Promise<string>;openExternal(value:string):Promise<unknown>;
}
export interface LocalHostOptions {stateDirectory:string;origin:string;native:NativeAdapters;encryptor:KeyEncryptor;
 distribution:Omit<DistributionOptions,'userDataPath'>;fetchImpl?:typeof fetch;protectedPaths?:string[];canRecover?:()=>boolean}
type Actor={directory:string;keys:AssistantKeyStore;siwc:SiwcStore;credentialOwner:SiwcCredentialOwner;apiBindings:Map<string,string>};
type ProjectBinding={epoch:number;scope:string};
type ProjectRow={actor:string;session:string;tab:string;epoch:number;scope:string|null};
const PROJECT_ASSISTANT_METHODS=new Set(['assistant.send','assistant.imageIntent','assistant.beginRun','assistant.endRun']);
type Tab={actor:string;session:string;id:string;disposed:boolean;assistant:AssistantService;runs:AssistantRunRegistry;distribution:DistributionManager;portable:PortableUpdateManager};
const hash=(value:string)=>crypto.createHash('sha256').update(value).digest('hex');
const within=(p:string,root:string)=>{const relative=path.relative(root,p);return relative===''||relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative);};
export function prepareStateDirectory(directory:string):void {
  const absolute=path.resolve(directory);let current=path.parse(absolute).root;
  for(const part of absolute.slice(current.length).split(path.sep).filter(Boolean)){current=path.join(current,part);try{const s=fs.lstatSync(current);if(!s.isDirectory()||s.isSymbolicLink())throw new HostError('unsafe_storage');}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;fs.mkdirSync(current,{mode:0o700});}}
  const leaf=fs.lstatSync(absolute);if(process.platform!=='win32'&&leaf.uid!==process.getuid?.())throw new HostError('unsafe_storage');
  fs.chmodSync(absolute,0o700);
 }

/** Constructed once by the owned windowless helper. HTTP authorization is repeated by the controller. */
export class LocalHostService {
 private projectRows=new Map<string,ProjectRow>();
 private actors=new Map<string,Actor>();private tabs=new Map<string,Tab>();private disposedSessions=new Set<string>();private disposedTabs=new Set<string>();private closed=false;
 constructor(readonly options:LocalHostOptions){
  if(!path.isAbsolute(options.stateDirectory)||new URL(options.origin).origin!==options.origin||!/^https:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/.test(options.origin))throw new HostError('invalid_configuration');
  this.secureDirectory(options.stateDirectory);
 }
 private secureDirectory(directory:string):void {prepareStateDirectory(directory);}
 private actor(id:string):Actor {let a=this.actors.get(id);if(!a){const directory=path.join(this.options.stateDirectory,'actors',hash(id));this.secureDirectory(directory);
  a={directory,keys:new AssistantKeyStore(path.join(directory,'api'),this.options.encryptor),siwc:new SiwcStore(path.join(directory,'chatgpt'),this.options.encryptor,{canRecover:this.options.canRecover}),credentialOwner:new SiwcCredentialOwner(),apiBindings:new Map()};this.actors.set(id,a);}return a;}
 private key(r:HostRequest):string{return JSON.stringify([r.actor_id,r.session_id,r.tab_id]);}
 private sessionKey(r:HostRequest):string{return JSON.stringify([r.actor_id,r.session_id]);}
 private live(r:HostRequest,tab?:Tab):void {if(this.closed||tab?.disposed||this.disposedSessions.has(this.sessionKey(r))||this.disposedTabs.has(this.key(r)))throw new HostError('session_disposed');}
 private tab(r:HostRequest):Tab {const key=this.key(r);let tab=this.tabs.get(key);if(!tab){if(this.tabs.size>=256)throw new HostError('too_many_tabs');const actor=this.actor(r.actor_id),directory=path.join(actor.directory,'tabs',hash(key));this.secureDirectory(directory);
  const distribution=new DistributionManager({...this.options.distribution,userDataPath:directory});
  const chatgpt=new SiwcConnection({store:tabSiwcStore(actor.siwc),credentialOwner:actor.credentialOwner,openExternal:url=>this.options.native.openExternal(url),fetchImpl:this.options.fetchImpl});
  tab={actor:r.actor_id,session:r.session_id,id:r.tab_id,disposed:false,assistant:new AssistantService({store:new TabKeyStore(actor.keys),chatgpt,fetchImpl:this.options.fetchImpl}),runs:new AssistantRunRegistry(),distribution,
   portable:new PortableUpdateManager({packaged:distribution.options.packaged,platform:distribution.options.platform,arch:distribution.options.arch,resourcesPath:distribution.options.resourcesPath||distribution.options.appPath,userDataPath:directory,appPath:distribution.options.appPath,signature:target=>distribution.signature(target),runner:this.options.distribution.runner})};this.tabs.set(key,tab);}return tab;}
 private approval(actor:Actor):boolean {const file=path.join(actor.directory,'owner-approval.json');try{const stat=fs.lstatSync(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||stat.size>4096)return false;const v=JSON.parse(fs.readFileSync(file,'utf8'));return v.protocol===1&&v.origin===this.options.origin;}catch{return false;}}
 private saveApproval(actor:Actor):void {const file=path.join(actor.directory,'owner-approval.json'),temp=path.join(actor.directory,`.approval-${crypto.randomUUID()}`);let fd:number|undefined;try{fd=fs.openSync(temp,'wx',0o600);fs.writeFileSync(fd,JSON.stringify({protocol:1,origin:this.options.origin,approvedAt:new Date().toISOString()}));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temp,file);}finally{if(fd!==undefined)fs.closeSync(fd);fs.rmSync(temp,{force:true});}}
 private allowedPath(value:unknown):string {
  if(typeof value!=='string'||!path.isAbsolute(value)||value.length>4096||/[\u0000-\u001f]/.test(value)||/^[\\/]{2}/.test(value))throw new HostError('invalid_path');
  const absolute=path.resolve(value),canonical=fs.realpathSync(absolute);
  // Protected stores cannot be exported by chooser selection or native open.
  const denied=[this.options.stateDirectory,...(this.options.protectedPaths??[])].flatMap(p=>{const absolute=path.resolve(p);try{return [absolute,fs.realpathSync(absolute)];}catch{return [absolute];}});
  if(denied.some(root=>within(absolute,root)||within(canonical,root)||fs.statSync(canonical).isDirectory()&&within(root,canonical)))throw new HostError('protected_path');return canonical;
 }
 private dialogOptions(kind:'folder'|'file',args:Record<string,any>):OpenDialogOptions {
  const options=args.options??args;
  if(options.title!==undefined&&(typeof options.title!=='string'||options.title.length>200)||options.defaultPath!==undefined&&(typeof options.defaultPath!=='string'||options.defaultPath.length>4096))throw new HostError('invalid_request');
  if(options.defaultPath)this.allowedPath(options.defaultPath);
  const result:OpenDialogOptions={properties:kind==='folder'?['openDirectory','createDirectory']:['openFile'],title:options.title,defaultPath:options.defaultPath};
  if(kind==='file'&&options.filters!==undefined){if(!Array.isArray(options.filters)||options.filters.length>20||options.filters.some((f:any)=>!f||typeof f.name!=='string'||f.name.length>100||!Array.isArray(f.extensions)||f.extensions.length>30||f.extensions.some((x:any)=>typeof x!=='string'||!/^([a-zA-Z0-9]{1,20}|\*)$/.test(x))))throw new HostError('invalid_request');result.filters=options.filters;}
  return result;
 }
 private async choose(r:HostRequest,t:Tab,options:OpenDialogOptions):Promise<string|null>{const value=await this.options.native.choose(options);this.live(r,t);if(value.canceled||!value.filePaths[0])return null;return this.allowedPath(value.filePaths[0]);}
 private retire(tab:Tab){tab.disposed=true;void tab.assistant.dispose().catch(()=>undefined);}
 async handle(raw:unknown):Promise<HostResponse>{const id=typeof (raw as any)?.id==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test((raw as any).id)?(raw as any).id:'invalid';
  try {if(Buffer.byteLength(JSON.stringify(raw))>MAX_FRAME_BYTES)throw new HostError('frame_too_large');const r=parseRequest(raw);
   if(r.method==='session.dispose'||r.method==='session.disposeAll'){for(const [key,row]of this.projectRows)if(row.actor===r.actor_id&&row.session===r.session_id&&(r.method==='session.disposeAll'||row.tab===r.tab_id))this.projectRows.delete(key);if(r.method==='session.disposeAll')this.disposedSessions.add(this.sessionKey(r));else this.disposedTabs.add(this.key(r));for(const [key,t]of this.tabs)if(t.actor===r.actor_id&&t.session===r.session_id&&(r.method==='session.disposeAll'||t.id===r.tab_id)){this.retire(t);this.tabs.delete(key);}return {id,ok:true,result:true};}
   this.live(r);
   if(r.method==='assistant.retireProject')return {id,ok:true,result:this.retireProject(r)};
   const actor=this.actor(r.actor_id);
   if(r.method==='host.approve'){if(r.args.origin!==this.options.origin||typeof r.args.accountLabel!=='string'||!r.args.accountLabel.trim()||r.args.accountLabel.length>200||/[\u0000-\u001f\u007f]/.test(r.args.accountLabel))throw new HostError('invalid_request');if(this.approval(actor))return {id,ok:true,result:{approved:true}};
    const approved=await this.options.native.approve({origin:this.options.origin,accountLabel:r.args.accountLabel});this.live(r);if(approved)this.saveApproval(actor);return {id,ok:true,result:{approved}};}
   if(!this.approval(actor))throw new HostError('host_approval_required');const tab=this.tab(r),project=PROJECT_ASSISTANT_METHODS.has(r.method)?this.bindProject(r,tab):undefined;
   const result=await this.dispatch(r,tab);this.live(r,tab);
   if(project){const current=this.projectRows.get(this.key(r));if(!current||current.epoch!==project.epoch||current.scope!==project.scope)throw new HostError('project_authority_changed');}
   return {id,ok:true,result:result??null};
  }catch(error){const code=error instanceof HostError?error.code:'operation_refused';
   // Existing prerequisite errors are application owned; never echo raw OS/provider/network failures or credential arguments.
   const message=error instanceof HostError?error.message:this.safeMessage(error);return {id,ok:false,error:{code,message}};}
 }
 private safeMessage(error:unknown):string {const text=error instanceof Error?error.message:'';if(/packaged application|Portable update currently requires|Select an owned portable installation|Select a stable or beta channel/.test(text))return text.slice(0,300);return '요청을 완료하지 못했습니다. 권한·보호 저장소·서명과 실행 조건을 확인하세요.';}
 private async approveLinkedSource(r:HostRequest,t:Tab):Promise<unknown> {
  const a=r.args,id=(v:unknown)=>typeof v==='string'&&/^[A-Za-z0-9_.:-]{1,128}$/.test(v);
  if(!id(a.project_id)||!id(a.workspace_id)||typeof a.plan_sha256!=='string'||! /^[a-f0-9]{64}$/.test(a.plan_sha256)
   ||!Number.isSafeInteger(a.file_count)||a.file_count<1||a.file_count>10000||!Number.isSafeInteger(a.total_bytes)||a.total_bytes<0||a.total_bytes>4*1024**3
   ||!Array.isArray(a.targets)||a.targets.length<1||a.targets.length>32||JSON.stringify(a.targets).length+String(a.source).length+String(a.project_dir).length>16000)throw new HostError('invalid_request');
  const source=this.allowedPath(a.source),project=this.allowedPath(a.project_dir);if(source!==a.source||project!==a.project_dir||!fs.lstatSync(project).isDirectory()||!fs.lstatSync(source).isDirectory())throw new HostError('invalid_request');
  const seen=new Set<string>();
  const targets=a.targets.map((target:any)=>{
   if(!target||typeof target.link!=='string'||!target.link||target.link.length>4096||/[\u0000-\u001f\u007f]/.test(target.link)||path.isAbsolute(target.link)||target.link.split(/[\\/]/).some((part:string)=>part==='..')
    ||!['file','directory'].includes(target.kind)||seen.has(target.link))throw new HostError('invalid_request');
   seen.add(target.link);const canonical=this.allowedPath(target.path),info=fs.lstatSync(canonical);
   if(canonical!==target.path||target.kind==='file'&&(!info.isFile()||info.nlink!==1)||target.kind==='directory'&&!info.isDirectory())throw new HostError('invalid_request');
   return {link:target.link,path:canonical,kind:target.kind as 'file'|'directory'};
  });
  const identity=(p:string)=>{const s=fs.lstatSync(p);return JSON.stringify([s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeMs,s.ctimeMs]);};
  const paths=[source,project,...targets.map((v:{path:string})=>v.path)],before=paths.map(identity);
  if(!this.options.native.confirmLinkedSource)throw new HostError('native_confirmation_unavailable');
  const approved=await this.options.native.confirmLinkedSource({source,project_dir:project,targets,plan_sha256:a.plan_sha256,file_count:a.file_count,total_bytes:a.total_bytes,
   scope:{actor_id:r.actor_id,session_id:r.session_id,tab_id:r.tab_id,project_id:a.project_id,workspace_id:a.workspace_id}});
  this.live(r,t);
  if(!approved)return {approved:false};
  if(paths.some((p,i)=>this.allowedPath(p)!==p||identity(p)!==before[i]))throw new HostError('selection_changed');
  return {approved:true,plan_sha256:a.plan_sha256};
 }
 private invalidateProject(tab:Tab|undefined):void {if(tab){tab.assistant.invalidateAuthority();tab.runs=new AssistantRunRegistry();}}
 private setProject(r:HostRequest,epoch:number,scope:string|null):void {
  const key=this.key(r);if(!this.projectRows.has(key)&&this.projectRows.size>=512)throw new HostError('too_many_project_scopes');
  this.projectRows.set(key,{actor:r.actor_id,session:r.session_id,tab:r.tab_id,epoch,scope});
 }
 private retireProject(r:HostRequest):boolean {
  const epoch=r.args.epoch;
  if(!Number.isSafeInteger(epoch)||epoch<1||epoch%2!==1||Object.keys(r.args).some(key=>key!=='epoch'))throw new HostError('invalid_project_authority');
  const row=this.projectRows.get(this.key(r));if(row&&epoch<=row.epoch)return false;
  this.setProject(r,epoch,null);this.invalidateProject(this.tabs.get(this.key(r)));return true;
 }
 private bindProject(r:HostRequest,tab:Tab):ProjectBinding {
  const value=r.args.projectAuthority;
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!['epoch','scope'].includes(key))
   ||!Number.isSafeInteger(value.epoch)||value.epoch<0||value.epoch%2!==0||typeof value.scope!=='string'
   ||! /^(?:local:[a-f0-9]{64}|remote:[A-Za-z0-9_-]{1,128})$/.test(value.scope))throw new HostError('invalid_project_authority');
  const row=this.projectRows.get(this.key(r));
  if(row&&(value.epoch<row.epoch||value.epoch===row.epoch&&value.scope!==row.scope))throw new HostError('project_authority_changed');
  if(!row||value.epoch>row.epoch){this.setProject(r,value.epoch,value.scope);this.invalidateProject(tab);}
  return {epoch:value.epoch,scope:value.scope};
 }
 private credentialsChanged(actor:string):void {for(const tab of this.tabs.values())if(tab.actor===actor)tab.assistant.invalidateAuthority();}
 private async dispatch(r:HostRequest,t:Tab):Promise<unknown>{const a=r.args,s=t.assistant,d=t.distribution,p=t.portable;
  switch(r.method){
   case 'host.approveLinkedSource':return this.approveLinkedSource(r,t);
   case 'dialog.folder':return this.choose(r,t,this.dialogOptions('folder',a));case 'dialog.file':return this.choose(r,t,this.dialogOptions('file',a));
   case 'shell.open':{const value=a.path??a.target;let target=classifyOpenTarget(value);if(target.kind==='rejected')return false;if(target.kind==='url'){await this.options.native.openExternal(target.url);return true;}this.allowedPath(target.path);return (await this.options.native.openPath(target.path))==='';}
   case 'assistant.getState':return s.state();case 'assistant.saveProvider':{const actor=this.actor(r.actor_id),input=a.input;
    const binding=JSON.stringify([validateBaseUrl(input.baseUrl),input.provider==='anthropic'?null:input.format==='responses'?'responses':'chat',input.supportsImages===true]);
    if(input.key||actor.apiBindings.get(input.provider)!==binding)this.credentialsChanged(r.actor_id);
    const result=await s.save(input);actor.apiBindings.set(input.provider,binding);return result;}
   case 'assistant.removeKey':{this.credentialsChanged(r.actor_id);return s.removeKey(a.provider);}
   case 'assistant.removeProvider':{this.credentialsChanged(r.actor_id);return s.removeProvider(a.provider);}case 'assistant.setActive':return s.setActive(a.provider);case 'assistant.listModels':return s.listModels(a.provider);case 'assistant.send':return s.send(a.input);case 'assistant.cancel':return s.cancel(a.requestId);case 'assistant.imageIntent':return s.imageIntent(a.input);case 'assistant.beginRun':return t.runs.arm(a.runId,a.tool);case 'assistant.endRun':return t.runs.disarm(a.runId);
   case 'assistant.chatgpt.begin':return s.chatgptBegin(a.input);case 'assistant.chatgpt.acknowledgePlanUsage':return s.chatgptAcknowledgePlanUsage(a.recordId);case 'assistant.chatgpt.resumeUsage':return s.chatgptResumeUsage(a.recordId);case 'assistant.chatgpt.recover':return s.chatgptRecover(a.input);case 'assistant.chatgpt.cancel':return s.chatgptCancel();case 'assistant.chatgpt.selectAccount':return s.chatgptSelectAccount(a.recordId);case 'assistant.chatgpt.selectModel':return s.chatgptSelectModel(a.recordId,a.model);case 'assistant.chatgpt.signOut':return s.chatgptSignOut(a.recordId);
   case 'updates.status':return d.status();case 'updates.configure':return d.configure(a.configuration);case 'updates.check':return d.check();case 'updates.download':return d.download();
   case 'updates.verifyOffline':{if(!d.options.packaged)throw Error('Offline release handoff requires the packaged application and its provisioned publisher authority');const file=await this.choose(r,t,{title:'서명된 오프라인 릴리스 목록 선택',properties:['openFile'],filters:[{name:'Release manifest',extensions:['json']}]});return file?d.verifyOffline(file):null;}
   case 'updates.selectPortableHome':{await p.ensureSupported();this.live(r,t);const root=await this.choose(r,t,{title:'중지된 별도 portable 설치 폴더 선택',properties:['openDirectory']});return root?p.select(root):null;}
   case 'updates.inspectPortable':return p.inspect();case 'updates.previewPortable':{const pins=validatedCanaryPins(a.canary);if(!['stable','beta'].includes(a.channel))throw Error('Select a stable or beta channel');await p.ensureSupported();this.live(r,t);const file=await this.choose(r,t,{title:'portable 앱의 서명된 오프라인 릴리스 목록 선택',properties:['openFile'],filters:[{name:'Release manifest',extensions:['json']}]});return file?p.preview(file,a.channel,pins):null;}
   case 'updates.applyPortable':return p.apply(a.id);case 'updates.recoverPortable':return p.recover(a.action,a.expected);case 'updates.launchPortable':return p.launch(a.expected);case 'updates.inspectPortableLaunch':return p.inspectLaunch(a.expected);
   default:throw new HostError('unknown_method');
  }
 }
 private closing:Promise<void>|undefined;
 dispose():Promise<void> {if(this.closing)return this.closing;this.closed=true;this.projectRows.clear();for(const t of this.tabs.values())this.retire(t);this.tabs.clear();
  const work=[...this.actors.values()].map(a=>{void a.keys.close().catch(()=>undefined);return a.credentialOwner.dispose(a.siwc);});this.actors.clear();return this.closing=Promise.allSettled(work).then(()=>undefined);}
}
