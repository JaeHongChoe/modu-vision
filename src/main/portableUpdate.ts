/** Explicit owned portable homes only. No current-app replacement or launch. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readTrustAuthority,readBoundedStableFile} from './releaseTrust';
import type {NativeSignature,PortableUpdateState,PortableUpdateReview,PortableRecoveryAction} from '../types/electron';

const runFile=promisify(execFile),sha=(raw:Buffer)=>crypto.createHash('sha256').update(raw).digest('hex');
const hex=(value:unknown,n=64)=>typeof value==='string'&&new RegExp(`^[0-9a-f]{${n}}$`).test(value);
type Runner=(file:string,args:string[])=>Promise<{stdout:string;stderr:string}>;
export interface PortableUpdateOptions {packaged:boolean;platform:string;arch:string;resourcesPath:string;userDataPath:string;appPath:string;signature:(target?:string)=>Promise<NativeSignature>;runner?:Runner}
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
  private review:{id:string;manifest:string;channel:'stable'|'beta';sha:string}|null=null;
  constructor(options:PortableUpdateOptions){this.options=options;}

  async ensureSupported():Promise<void>{await this.trusted();}

  private async trusted(){
    const o=this.options;
    if(!o.packaged)throw Error('Portable update requires a packaged application with pinned publisher trust');
    if(!['darwin','linux'].includes(o.platform))throw Error('Portable update currently requires qualified POSIX durability');
    const authority=unlinked(path.join(o.resourcesPath,'release-trust.json')),trust=readTrustAuthority(authority);
    const authoritySHA=sha(readBoundedStableFile(authority,32768,'Pinned publisher authority'));
    const binary=unlinked(path.join(o.resourcesPath,'backend_bin','vision_ai_backend'));
    const receipt=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(runtimeFile(path.join(path.dirname(binary),'backend-release.json'),16*1024*1024,false) as Buffer));
    const platform=({Darwin:'darwin',Linux:'linux'} as Record<string,string>)[receipt.inventory?.platform];
    const arch=({arm64:'arm64',aarch64:'arm64',x86_64:'x64',AMD64:'x64'} as Record<string,string>)[receipt.inventory?.architecture];
    const binarySHA=runtimeFile(binary,1024*1024*1024,true);
    if(receipt.executable!=='vision_ai_backend'||receipt.executable_sha256!==binarySHA||!hex(receipt.inventory?.build_identity_sha256)||platform!==o.platform||arch!==o.arch)throw Error('Frozen runtime checksum or target differs from its inventory');
    const installed=await o.signature(),backend=await o.signature(binary);
    if(installed.status!=='verified'||backend.status!=='verified'||installed.publisher!==trust.publisher||backend.publisher!==trust.publisher)throw Error('Application and runtime need verified signatures matching the pinned publisher');
    if(sha(readBoundedStableFile(authority,32768,'Pinned publisher authority'))!==authoritySHA||runtimeFile(binary,1024*1024*1024,true)!==binarySHA)throw Error('Pinned authority or frozen runtime changed during publisher verification');
    return {authority,authoritySHA,binary,trust};
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
    this.root=candidate;try{return await this.readback();}catch(error){this.root=null;throw error;}
  });}

  async inspect():Promise<PortableUpdateState>{return this.exclusive(()=>this.readback());}

  async preview(manifest:string,channel:'stable'|'beta'):Promise<PortableUpdateReview>{return this.exclusive(async()=>{
    this.review=null;
    if(!['stable','beta'].includes(channel))throw Error('Select a stable or beta channel');
    manifest=unlinked(manifest);const trusted=await this.trusted();
    const target={platform:this.options.platform,arch:this.options.arch,channel,current_version:'0.0.0',origin:trusted.trust.allowed_origins[0]};
    const result=await this.command('preview',['--bundle',path.join(path.dirname(manifest),'artifacts'),'--envelope',manifest,
      '--target-json',JSON.stringify(target),'--use-owned-version']);
    if(result.status!=='reviewed'||!hex(result.plan_sha256)||!hex(result.source_sha256)||!hex(result.installation_id,32)
      ||result.authority_sha256!==trusted.authoritySHA||result.publisher!==trusted.trust.publisher||result.channel!==channel
      ||typeof result.version!=='string'||typeof result.current_version!=='string'||result.application_started!==false
      ||!['application_file_count','pack_count','artifact_bytes','database_fence'].every(k=>Number.isSafeInteger(result[k])&&result[k]>=0))throw Error('Invalid portable review response');
    this.review={id:crypto.randomUUID(),manifest,channel,sha:result.plan_sha256};
    return {...result,review_id:this.review.id,root:this.root!};
  });}

  async apply(reviewId:string):Promise<PortableUpdateState>{return this.exclusive(async()=>{
    const review=this.review;
    if(!review||typeof reviewId!=='string'||reviewId!==review.id)throw Error('Review the selected update again before installing');
    // Consumed before the subprocess: a lost response cannot replay installation.
    this.review=null;const trusted=await this.trusted();
    const target={platform:this.options.platform,arch:this.options.arch,channel:review.channel,current_version:'0.0.0',origin:trusted.trust.allowed_origins[0]};
    const result=await this.command('install',['--bundle',path.join(path.dirname(review.manifest),'artifacts'),'--envelope',review.manifest,
      '--target-json',JSON.stringify(target),'--use-owned-version','--expected-plan-sha256',review.sha]);
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
}
