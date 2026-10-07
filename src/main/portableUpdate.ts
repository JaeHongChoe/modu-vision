/** Explicit owned portable homes; all launch authority stays in trusted main. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readTrustAuthority,readBoundedStableFile} from './releaseTrust';
import {runPersistentController} from './persistentLaunch';
import type {NativeSignature,PortableUpdateState,PortableUpdateReview,PortableRecoveryAction,PortableLaunchExpected,PortableLaunchState,PortableCanaryPins,PortableCanaryReview} from '../types/electron';

const runFile=promisify(execFile),sha=(raw:Buffer)=>crypto.createHash('sha256').update(raw).digest('hex');
const hex=(value:unknown,n=64)=>typeof value==='string'&&new RegExp(`^[0-9a-f]{${n}}$`).test(value);
export function validatedCanaryPins(value:unknown):PortableCanaryPins {
  if(!value||typeof value!=='object'||Array.isArray(value)
    ||Object.keys(value).sort().join(',')!=='plan_sha256,project_id,workspace_id')throw Error('기준 이미지 검증 계획의 세 가지 pins를 지정하세요.');
  const row=value as PortableCanaryPins;
  if(!hex(row.workspace_id,32)||!hex(row.project_id,32)||!hex(row.plan_sha256))throw Error('기준 이미지 검증 계획의 ID와 해시가 올바르지 않습니다.');
  return {workspace_id:row.workspace_id,project_id:row.project_id,plan_sha256:row.plan_sha256};
}
const canaryArgs=(pins:PortableCanaryPins)=>['--canary-workspace-id',pins.workspace_id,'--canary-project-id',pins.project_id,'--canary-plan-sha256',pins.plan_sha256];
function reviewedCanary(value:unknown,pins:PortableCanaryPins):PortableCanaryReview {
  const keys=['schema_version','protocol','required','policy','status','supported','pins','capability_sha256','candidate_runtime_source_sha256','reason','candidate_main_launch_verified','native_application_verified','frozen_backend_verified','owned_backend_execution_origin_verified','worker_process_tree_exit_verified','model_quality_verified','release_ready'];
  if(value&&typeof value==='object'&&!Array.isArray(value)&&(value as any).protocol===2)keys.push('worker_binding');
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!==keys.sort().join(','))throw Error('Invalid canary review response');
  const row=value as PortableCanaryReview;
  const ready=row.protocol===1?row.status==='source_ready':row.status==='frozen_ready';
  const protocolValid=(row.protocol===1&&row.policy==='same_reviewed_source_runtime_worker_v1'&&['source_ready','requires_target'].includes(row.status))
    ||(row.protocol===2&&row.policy==='same_reviewed_frozen_runtime_worker_v1'&&['frozen_ready','requires_target'].includes(row.status));
  if(row.schema_version!==1||row.required!==true||!protocolValid||row.supported!==ready
    ||['candidate_main_launch_verified','native_application_verified','frozen_backend_verified','owned_backend_execution_origin_verified','worker_process_tree_exit_verified','model_quality_verified','release_ready'].some(k=>(row as any)[k]!==false))throw Error('Invalid canary review binding or acceptance claim');
  const echoed=validatedCanaryPins(row.pins);
  if(Object.keys(pins).some(k=>(echoed as any)[k]!==(pins as any)[k]))throw Error('Canary review differs from the selected independent pins');
  if(ready){
    if(!hex(row.capability_sha256)||!hex(row.candidate_runtime_source_sha256)||row.reason!==null)throw Error('Invalid ready canary capability');
  }else if((row.capability_sha256!==null&&!hex(row.capability_sha256))||(row.candidate_runtime_source_sha256!==null&&!hex(row.candidate_runtime_source_sha256))
    ||typeof row.reason!=='string'||!row.reason.trim()||row.reason.length>500)throw Error('Invalid unavailable canary reason');
  if(row.protocol===2){
    if(!ready){if(row.worker_binding!==null)throw Error('Invalid unavailable canary worker binding');return {...row,pins:echoed};}
    const worker=row.worker_binding;
    const names=['protocol','executable_path','executable_sha256','build_receipt_path','build_receipt_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'];
    // These signed-manifest-relative paths describe the reviewed worker. They
    // never become renderer-selected launch arguments or filesystem authority.
    const relative=(p:unknown)=>typeof p==='string'&&p.length>0&&p.length<=4096&&!/[\\:\x00-\x1f\x7f]/.test(p)
      &&!p.startsWith('/')&&p.split('/').every(q=>q.length>0&&q!=='.'&&q!=='..'&&!/[. ]$/.test(q));
    if(!worker||typeof worker!=='object'||Array.isArray(worker)||Object.keys(worker).sort().join(',')!==names.sort().join(',')
      ||worker.protocol!==1||!relative(worker.executable_path)||!relative(worker.build_receipt_path)
      ||!['executable_sha256','build_receipt_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'].every(k=>hex((worker as any)[k]))
      ||worker.runtime_source_sha256!==row.candidate_runtime_source_sha256)throw Error('Invalid ready canary worker binding');
    return {...row,pins:echoed,worker_binding:{...worker}};
  }
  return {...row,pins:echoed};
}
function launchRefusal(row:any):string|undefined{return row&&typeof row==='object'&&!Array.isArray(row)
  &&Object.keys(row).sort().join(',')==='error,schema_version,status'&&row.schema_version===1&&row.status==='refused'
  &&typeof row.error==='string'&&row.error.length>0&&row.error.length<=2048?row.error:undefined;}
type Runner=(file:string,args:string[])=>Promise<{stdout:string;stderr:string}>;
function supportsController(inventory:any):boolean {
  const required=['scripts/frozen_backend_entry.py','backend/engine/application_launch_controller.py','backend/engine/application_launch_handshake.py','backend/engine/application_launch_lease.py'];
  return inventory?.owned_application_launch_controller_protocol===1&&Array.isArray(inventory.resources)
    &&required.every(p=>inventory.resources.filter((r:any)=>r?.path===p&&hex(r.sha256)).length===1
      &&inventory.resources.filter((r:any)=>r?.path===p).length===1);
}
export interface PortableUpdateOptions {packaged:boolean;platform:string;arch:string;resourcesPath:string;userDataPath:string;appPath:string;signature:(target?:string)=>Promise<NativeSignature>;runner?:Runner;launchRunner?:Runner}
function unlinked(value:string):string {
  if(typeof value!=='string'||!path.isAbsolute(value)||value.length>4096||value.includes('\0'))throw Error('An absolute portable path is required');
  const absolute=path.resolve(value);
  for(let p=absolute;;p=path.dirname(p)){if(fs.existsSync(p)&&fs.lstatSync(p).isSymbolicLink())throw Error('Portable update paths cannot follow links');if(p===path.dirname(p))break;}
  return absolute;
}
function overlap(a:string,b:string):boolean {return a===b||a.startsWith(b+path.sep)||b.startsWith(a+path.sep);}
function runtimeFile(file:string,limit:number,hashOnly:boolean):Buffer|string {
  file=unlinked(file);const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0)|(fs.constants.O_NONBLOCK||0));
  try{
    const before=fs.fstatSync(fd);if(!before.isFile()||before.nlink!==1||before.size<1||before.size>limit)throw Error('Frozen runtime file exceeds its bound or is not regular');
    const buffer=Buffer.alloc(1024*1024),digest=crypto.createHash('sha256'),chunks:Buffer[]=[];let count,total=0;
    while((count=fs.readSync(fd,buffer,0,buffer.length,null))>0){total+=count;if(total>before.size)throw Error('Frozen runtime grew while reading');if(hashOnly)digest.update(buffer.subarray(0,count));else chunks.push(Buffer.from(buffer.subarray(0,count)));}
    unlinked(file);const after=fs.fstatSync(fd),current=fs.lstatSync(file);
    if(total!==before.size||after.size!==before.size||after.mtimeMs!==before.mtimeMs||after.ctimeMs!==before.ctimeMs||current.ino!==before.ino||current.dev!==before.dev||!current.isFile()||current.nlink!==1||current.size!==before.size||current.mtimeMs!==before.mtimeMs||current.ctimeMs!==before.ctimeMs)throw Error('Frozen runtime identity changed while reading');
    return hashOnly?digest.digest('hex'):Buffer.concat(chunks);
  }finally{fs.closeSync(fd);}
}

export class PortableUpdateManager {
  readonly options:PortableUpdateOptions;
  private root:string|null=null;
  private busy=false;
  private review:{id:string;manifest:string;channel:'stable'|'beta';sha:string;canary:PortableCanaryPins;installable:boolean;reason:string|null}|null=null;
  private launchRequests=new Set<string>();
  constructor(options:PortableUpdateOptions){this.options=options;}

  async ensureSupported():Promise<void>{await this.trusted();}

  private async trusted(){
    const o=this.options;
    if(!o.packaged)throw Error('Portable update requires a packaged application with pinned publisher trust');
    if(!['darwin','linux'].includes(o.platform))throw Error('Portable update currently requires qualified POSIX durability');
    const authority=unlinked(path.join(o.resourcesPath,'release-trust.json')),trust=readTrustAuthority(authority);
    const authoritySHA=sha(readBoundedStableFile(authority,32768,'Pinned publisher authority'));
    const binary=unlinked(path.join(o.resourcesPath,'backend_bin','vision_ai_backend'));
    const receiptPath=path.join(path.dirname(binary),'backend-release.json'),receiptBytes=runtimeFile(receiptPath,16*1024*1024,false) as Buffer;
    const receiptSHA=sha(receiptBytes),receipt=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(receiptBytes));
    const platform=({Darwin:'darwin',Linux:'linux'} as Record<string,string>)[receipt.inventory?.platform];
    const arch=({arm64:'arm64',aarch64:'arm64',x86_64:'x64',AMD64:'x64'} as Record<string,string>)[receipt.inventory?.architecture];
    const binarySHA=runtimeFile(binary,1024*1024*1024,true);
    if(receipt.executable!=='vision_ai_backend'||receipt.executable_sha256!==binarySHA||!hex(receipt.inventory?.build_identity_sha256)||platform!==o.platform||arch!==o.arch)throw Error('Frozen runtime checksum or target differs from its inventory');
    const installed=await o.signature(),backend=await o.signature(binary);
    if(installed.status!=='verified'||backend.status!=='verified'||installed.publisher!==trust.publisher||backend.publisher!==trust.publisher)throw Error('Application and runtime need verified signatures matching the pinned publisher');
    if(sha(readBoundedStableFile(authority,32768,'Pinned publisher authority'))!==authoritySHA||runtimeFile(binary,1024*1024*1024,true)!==binarySHA||runtimeFile(receiptPath,16*1024*1024,true)!==receiptSHA)throw Error('Pinned authority, frozen runtime or inventory changed during publisher verification');
    return {authority,authoritySHA,binary,trust,inventory:receipt.inventory};
  }

  private async exclusive<T>(action:()=>Promise<T>):Promise<T>{
    if(this.busy)throw Error('A portable update command is already in progress');
    this.busy=true;try{return await action();}finally{this.busy=false;}
  }

  private async command(command:string,args:string[]):Promise<any>{
    if(!this.root)throw Error('Select an owned portable installation first');
    unlinked(this.root);const trusted=await this.trusted();
    const runner=this.options.runner||((file,argv)=>runFile(file,argv,{timeout:120000,maxBuffer:256*1024,encoding:'utf8',shell:false}));
    let output;
    try{output=await runner(trusted.binary,['--offline-application-update',command,'--root',this.root,
      '--authority',trusted.authority,'--pinned-authority-sha256',trusted.authoritySHA,...args]);}
    catch(cause){
      const raw=(cause as {stdout?:unknown}).stdout;
      if(typeof raw==='string'&&raw.length<=256*1024){let refused;try{refused=JSON.parse(raw.trim());}catch{}
        if(refused?.status==='refused'&&typeof refused.error==='string'&&refused.error.length<=2048)throw Error(refused.error);}
      throw cause;
    }
    if(output.stdout.length>256*1024)throw Error('Portable update response exceeds the bounded review');
    const result=JSON.parse(output.stdout.trim());
    if(!result||typeof result!=='object'||result.status==='refused')throw Error(result?.error||'Portable update was refused');
    return result;
  }

  private async readback():Promise<PortableUpdateState>{
    const result=await this.command('inspect',[]);
    if(!['ready','committed','recovery_required'].includes(result.status)||!hex(result.installation_id,32)
      ||typeof result.version!=='string'||!Array.isArray(result.allowed_recovery)
      ||result.allowed_recovery.some((a:unknown)=>!['finish','forward','abort'].includes(String(a)))
      ||(result.update_id!==null&&!hex(result.update_id,32))||!Number.isSafeInteger(result.database_fence)||result.database_fence<0
      ||result.application_started!==false)throw Error('Invalid owned portable inspection response');
    return {...result,root:this.root!};
  }

  async select(root:string):Promise<PortableUpdateState>{return this.exclusive(async()=>{
    this.review=null;this.root=null;const candidate=unlinked(root);
    if(!fs.statSync(candidate).isDirectory())throw Error('Select a portable installation directory');
    for(const current of [this.options.userDataPath,this.options.appPath])if(overlap(candidate,path.resolve(current)))throw Error('This selection overlaps the current application or its user home');
    this.root=candidate;try{
      const state=await this.readback();
      if(state.status==='committed'&&state.update_id&&state.database_fence>0&&supportsController((await this.trusted()).inventory)){
        const expected={installation_id:state.installation_id,update_id:state.update_id,database_fence:state.database_fence};
        return {...state,launch_state:await this.controllerCommand(expected,true)};
      }
      return state;
    }catch(error){this.root=null;throw error;}
  });}

  async inspect():Promise<PortableUpdateState>{return this.exclusive(()=>this.readback());}

  async preview(manifest:string,channel:'stable'|'beta',canary:PortableCanaryPins):Promise<PortableUpdateReview>{return this.exclusive(async()=>{
    this.review=null;
    if(!['stable','beta'].includes(channel))throw Error('Select a stable or beta channel');
    const pins=validatedCanaryPins(canary);
    manifest=unlinked(manifest);const trusted=await this.trusted();
    const target={platform:this.options.platform,arch:this.options.arch,channel,current_version:'0.0.0',origin:trusted.trust.allowed_origins[0]};
    const result=await this.command('preview',['--bundle',path.join(path.dirname(manifest),'artifacts'),'--envelope',manifest,
      '--target-json',JSON.stringify(target),'--use-owned-version',...canaryArgs(pins)]);
    if(result.status!=='reviewed'||!hex(result.plan_sha256)||!hex(result.source_sha256)||!hex(result.installation_id,32)
      ||result.authority_sha256!==trusted.authoritySHA||result.publisher!==trusted.trust.publisher||result.channel!==channel
      ||typeof result.version!=='string'||typeof result.current_version!=='string'||result.application_started!==false
      ||!['portable/v1','darwin-app/v2'].includes(result.application_layout)
      ||(result.application_layout==='darwin-app/v2'&&this.options.platform!=='darwin')
      ||(result.application_layout==='portable/v1'&&result.application_link_count!==0)
      ||!['application_file_count','application_link_count','pack_count','artifact_bytes','database_fence'].every(k=>Number.isSafeInteger(result[k])&&result[k]>=0))throw Error('Invalid portable review response');
    const preactivation=reviewedCanary(result.preactivation_canary,pins);
    this.review={id:crypto.randomUUID(),manifest,channel,sha:result.plan_sha256,canary:pins,installable:preactivation.supported,reason:preactivation.reason};
    return {...result,preactivation_canary:preactivation,installable:preactivation.supported,review_id:this.review.id,root:this.root!};
  });}

  async apply(reviewId:string):Promise<PortableUpdateState>{return this.exclusive(async()=>{
    const review=this.review;
    if(!review||typeof reviewId!=='string'||reviewId!==review.id)throw Error('Review the selected update again before installing');
    if(!review.installable)throw Error(review.reason||'기준 이미지 검증을 실행할 수 있는 대상이 필요합니다.');
    const pins=validatedCanaryPins(review.canary);
    // Consumed before the subprocess: a lost response cannot replay installation.
    this.review=null;const trusted=await this.trusted();
    const target={platform:this.options.platform,arch:this.options.arch,channel:review.channel,current_version:'0.0.0',origin:trusted.trust.allowed_origins[0]};
    const result=await this.command('install',['--bundle',path.join(path.dirname(review.manifest),'artifacts'),'--envelope',review.manifest,
      '--target-json',JSON.stringify(target),'--use-owned-version','--expected-plan-sha256',review.sha,...canaryArgs(pins)]);
    if(result.status!=='committed'||!hex(result.update_id,32))throw Error('Installation response is unconfirmed; read its state before recovery');
    return this.readback();
  });}

  async recover(action:PortableRecoveryAction,expected:{installation_id:string;update_id:string}):Promise<PortableUpdateState>{return this.exclusive(async()=>{
    this.review=null;const current=await this.readback();
    if(!expected||!hex(expected.installation_id,32)||!hex(expected.update_id,32)||expected.installation_id!==current.installation_id||expected.update_id!==current.update_id||!current.update_id||!current.allowed_recovery.includes(action))throw Error('Recovery action or installation differs from the inspected current intent');
    const result=await this.command('recover',['--intent',current.update_id,'--action',action]);
    if(!['committed','aborted'].includes(result.status))throw Error('Recovery result is unconfirmed; read its state again');
    return this.readback();
  });}

  private launchExpected(value:PortableLaunchExpected):PortableLaunchExpected {
    if(!value||typeof value!=='object'||Array.isArray(value)
      ||Object.keys(value).sort().join(',')!=='database_fence,installation_id,update_id'
      ||!hex(value.installation_id,32)||!hex(value.update_id,32)
      ||!Number.isSafeInteger(value.database_fence)||value.database_fence<1)throw Error('Invalid selected launch binding');
    return {installation_id:value.installation_id,update_id:value.update_id,database_fence:value.database_fence};
  }

  private launchResponse(stdout:string,expected:PortableLaunchExpected,startingOnly=false):PortableLaunchState {
    if(typeof stdout!=='string'||Buffer.byteLength(stdout,'utf8')>64*1024)throw Error('Invalid bounded launch response');
    let row:any;try{row=JSON.parse(stdout.trim());}catch{throw Error('Invalid launch response');}
    const refused=launchRefusal(row);if(refused)throw Error(refused);
    const keys=['schema_version','status','nonce','installation_id','update_id','database_fence','bootstrap_binding_verified','readiness','native_app_handshake_verified','backend_handshake_verified','actual_application_inference_verified','release_ready'];
    if(row?.reason!==undefined)keys.push('reason');
    if(!row||typeof row!=='object'||Array.isArray(row)||Object.keys(row).sort().join(',')!==keys.sort().join(',')
      ||row.schema_version!==1||!['absent','reserved','starting','ready','recovery_required','exited'].includes(row.status)
      ||(startingOnly&&row.status!=='starting')||row.installation_id!==expected.installation_id||row.update_id!==expected.update_id
      ||!Number.isSafeInteger(row.database_fence)||row.database_fence!==expected.database_fence
      ||(row.status==='absent'?row.nonce!==null:!hex(row.nonce,32))
      ||typeof row.bootstrap_binding_verified!=='boolean'||!['unverified','authenticated_controller_binding_only'].includes(row.readiness)
      ||row.bootstrap_binding_verified!==(row.readiness==='authenticated_controller_binding_only')
      ||(row.status==='ready'&&!row.bootstrap_binding_verified)
      ||(['absent','reserved','starting','exited'].includes(row.status)&&row.bootstrap_binding_verified)
      ||['native_app_handshake_verified','backend_handshake_verified','actual_application_inference_verified','release_ready'].some(key=>row[key]!==false)
      ||(row.reason!==undefined&&(typeof row.reason!=='string'||row.reason.length>500)))throw Error('Invalid or foreign launch binding response');
    return {...row,root:this.root!};
  }

  private async controllerCommand(expected:PortableLaunchExpected,inspect:boolean):Promise<PortableLaunchState>{
    if(!this.root)throw Error('Select an owned portable installation first');
    unlinked(this.root);const trusted=await this.trusted();
    // Never probe an unknown flag on a legacy backend: it may start desktop
    // initialization before argparse refuses it. The signed inventory declares
    // the dispatcher protocol and binds each early launch handler's source.
    if(!supportsController(trusted.inventory))throw Error('Frozen controller protocol or source inventory is unsupported');
    const args=['--owned-application-launch-controller',...(inspect?['--inspect']:[]),'--root',this.root,
      '--authority',trusted.authority,'--pinned-authority-sha256',trusted.authoritySHA,
      '--expected-installation-id',expected.installation_id,'--expected-update-id',expected.update_id,
      '--expected-database-fence',String(expected.database_fence)];
    const runner=inspect?(this.options.runner||((file:string,argv:string[])=>runFile(file,argv,{timeout:15000,maxBuffer:64*1024,encoding:'utf8',shell:false})))
      :(this.options.launchRunner||runPersistentController);
    let response;
    try{response=await runner(trusted.binary,args);}catch(cause){
      const raw=(cause as {stdout?:unknown}).stdout;
      if(typeof raw==='string'&&Buffer.byteLength(raw,'utf8')<=64*1024){let row;try{row=JSON.parse(raw.trim());}catch{}
        const refused=launchRefusal(row);if(refused)throw Error(refused);}
      throw cause;
    }
    return this.launchResponse(response.stdout,expected,!inspect);
  }

  async launch(input:PortableLaunchExpected):Promise<PortableLaunchState>{return this.exclusive(async()=>{
    const expected=this.launchExpected(input);
    const key=JSON.stringify([this.root,expected]);
    if(this.launchRequests.has(key))throw Error('Launch was already requested; read its launch state before further work');
    const current=await this.readback();
    if(current.status!=='committed'||current.installation_id!==expected.installation_id
      ||current.update_id!==expected.update_id||current.database_fence!==expected.database_fence)throw Error('Selected committed launch binding changed; read the current installation');
    this.review=null;this.launchRequests.add(key);
    return this.controllerCommand(expected,false);
  });}

  async inspectLaunch(input:PortableLaunchExpected):Promise<PortableLaunchState>{return this.exclusive(()=>
    this.controllerCommand(this.launchExpected(input),true));}
}
