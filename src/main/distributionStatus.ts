import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import type {DistributionState,NativeSignature,UpdateChannel,UpdateRelease,ManualDelivery} from '../types/electron';

const runFile=promisify(execFile);
const MANIFEST_LIMIT=64*1024, ARTIFACT_LIMIT=1024*1024*1024;
type Runner=(file:string,args:string[])=>Promise<{stdout:string;stderr:string}>;
export interface DistributionOptions {appVersion:string;packaged:boolean;appPath:string;executablePath:string;userDataPath:string;platform:string;arch:string;runner?:Runner;fetcher?:typeof fetch}

function httpsUrl(value:string):URL {
  const url=new URL(value);
  if(url.protocol!=='https:'||url.username||url.password||url.hash)throw new Error('Use an HTTPS release URL without credentials or fragment');
  return url;
}
function version(value:string):[number,number,number,string] {
  const match=/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$/.exec(value);
  if(!match)throw new Error('Release version must be a semantic version');
  return [Number(match[1]),Number(match[2]),Number(match[3]),match[4]||''];
}
function newer(candidate:string,current:string):boolean {
  const a=version(candidate),b=version(current);
  for(let i=0;i<3;i++)if(a[i]!==b[i])return a[i]>b[i];
  if(!a[3]&&b[3])return true;
  if(a[3]&&!b[3])return false;
  const x=a[3].split('.'),y=b[3].split('.');
  for(let i=0;i<Math.max(x.length,y.length);i++){
    if(x[i]===y[i])continue;if(x[i]===undefined)return false;if(y[i]===undefined)return true;
    const numericX=/^\d+$/.test(x[i]),numericY=/^\d+$/.test(y[i]);
    if(numericX&&numericY)return Number(x[i])>Number(y[i]);
    if(numericX!==numericY)return !numericX;return x[i]>y[i];
  }
  return false;
}
function unlinked(target:string):void {
  for(let current=target;;current=path.dirname(current)){
    if(fs.existsSync(current)&&fs.lstatSync(current).isSymbolicLink())throw new Error('Distribution storage cannot follow symbolic links');
    if(path.dirname(current)===current)break;
  }
}

export class DistributionManager {
  readonly options:DistributionOptions;
  private release:UpdateRelease|null=null;
  private manifestSHA:string|null=null;
  private revision=0;
  constructor(options:DistributionOptions){
    if(fs.existsSync(options.userDataPath)&&fs.lstatSync(options.userDataPath).isSymbolicLink())throw new Error('Distribution user storage cannot be a symbolic link');
    this.options={...options,userDataPath:fs.existsSync(options.userDataPath)?fs.realpathSync(options.userDataPath):path.resolve(options.userDataPath)};
  }
  private configPath():string{return path.join(this.options.userDataPath,'distribution-channel.json');}
  private configuration():UpdateChannel|null {
    const file=this.configPath();unlinked(file);
    if(!fs.existsSync(file))return null;
    if(fs.statSync(file).size>4096)throw new Error('Update channel configuration is too large');
    const value=JSON.parse(fs.readFileSync(file,'utf8')) as UpdateChannel;
    this.validateChannel(value);return value;
  }
  private validateChannel(value:UpdateChannel):void {
    if(!value||!['stable','beta'].includes(value.channel)||typeof value.manifest_url!=='string')throw new Error('Select a stable or beta release channel and its manifest URL');
    httpsUrl(value.manifest_url);
  }
  async configure(value:UpdateChannel|null):Promise<DistributionState> {
    if(value)this.validateChannel(value);
    const file=this.configPath();unlinked(file);fs.mkdirSync(this.options.userDataPath,{recursive:true});
    if(value){const temporary=file+'.'+crypto.randomUUID()+'.tmp';try{fs.writeFileSync(temporary,JSON.stringify({channel:value.channel,manifest_url:value.manifest_url}),{mode:0o600,flag:'wx'});const fd=fs.openSync(temporary,'r');try{fs.fsyncSync(fd);}finally{fs.closeSync(fd);}fs.renameSync(temporary,file);}finally{fs.rmSync(temporary,{force:true});}}
    else fs.rmSync(file,{force:true});
    this.revision++;this.release=null;this.manifestSHA=null;return this.status();
  }
  async signature(target=this.options.platform==='darwin'?this.options.appPath:this.options.executablePath,development=!this.options.packaged):Promise<NativeSignature> {
    const checked_at=new Date().toISOString();
    const result=(status:NativeSignature['status'],reason:string,publisher?:string):NativeSignature=>({status,reason,publisher,platform:this.options.platform,checked_at});
    if(development)return result('development','Native publisher verification requires a packaged application');
    const runner=this.options.runner||((file,args)=>runFile(file,args,{timeout:15000,maxBuffer:256*1024,encoding:'utf8'}));
    try {
      if(this.options.platform==='darwin'){
        await runner('/usr/bin/codesign',['--verify','--deep','--strict',target]);
        const info=await runner('/usr/bin/codesign',['--display','--verbose=4',target]);const text=info.stdout+'\n'+info.stderr;
        const team=/TeamIdentifier=([^\s]+)/.exec(text)?.[1];
        if(/Signature=adhoc|flags=.*adhoc/i.test(text)||!team||team==='not set')return result('unsigned','Ad-hoc signing does not establish a publisher identity');
        return result('verified','Native code signature verified; notarization is a separate deployment requirement',team);
      }
      if(this.options.platform==='win32'){
        const literal=target.replace(/'/g,"''");
        const info=await runner('powershell.exe',['-NoProfile','-NonInteractive','-Command',`$s=Get-AuthenticodeSignature -LiteralPath '${literal}'; @{status=$s.Status.ToString();publisher=$s.SignerCertificate.Subject;message=$s.StatusMessage}|ConvertTo-Json -Compress`]);
        const value=JSON.parse(info.stdout);return result(value.status==='Valid'?'verified':value.status==='NotSigned'?'unsigned':'invalid',String(value.message||value.status),value.publisher||undefined);
      }
      return result('unavailable','This platform needs a separately configured publisher signature verifier');
    }catch(cause){const error=cause as NodeJS.ErrnoException;return result(error.code==='ENOENT'?'unavailable':/not signed|unsigned/i.test(String(error))?'unsigned':'invalid','Native signature verification did not succeed: '+String(error.message||error).slice(0,500));}
  }
  async status():Promise<DistributionState> {
    const configuration=this.configuration();
    return {app_version:this.options.appVersion,version_source:'electron',platform:this.options.platform,architecture:this.options.arch,signature:await this.signature(),
      update:{configured:!!configuration,configuration,status:this.release?'available':configuration?'configured':'not_configured',release:this.release,automatic_update_available:false,
        prerequisite:configuration?'Manual delivery verifies package bytes and native signature before a matching publisher handoff':'Configure the actual HTTPS release manifest URL; no release channel is supplied'}};
  }
  private async request(url:string,limit:number,timeout:number,consume:(chunk:Uint8Array)=>Promise<void>|void):Promise<void> {
    httpsUrl(url);const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);
    let response:Response|undefined;
    try{
      response=await (this.options.fetcher||fetch)(url,{redirect:'manual',signal:controller.signal});
      if(response.status!==200)throw new Error('Release request failed or redirected: HTTP '+response.status);
      const declared=Number(response.headers.get('content-length'));if(Number.isFinite(declared)&&declared>limit)throw new Error(limit===MANIFEST_LIMIT?'Release manifest exceeds 64 KiB':'Release artifact exceeds its declared size limit');
      if(!response.body)throw new Error('Release response has no body');
      const reader=response.body.getReader();let count=0;
      try{for(;;){const next=await reader.read();if(next.done)break;count+=next.value.length;if(count>limit)throw new Error(limit===MANIFEST_LIMIT?'Release manifest exceeds 64 KiB':'Release artifact exceeds its declared size limit');await consume(next.value);}}
      finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    }finally{clearTimeout(timer);}
  }
  async check():Promise<DistributionState> {
    const configuration=this.configuration();if(!configuration)throw new Error('Configure the actual release channel first');
    const started=this.revision;const chunks:Buffer[]=[];this.release=null;this.manifestSHA=null;
    await this.request(configuration.manifest_url,MANIFEST_LIMIT,15000,chunk=>{chunks.push(Buffer.from(chunk));});
    if(started!==this.revision)throw new Error('Update configuration changed during checking; check again');
    const bytes=Buffer.concat(chunks),value=JSON.parse(bytes.toString('utf8')) as UpdateRelease;
    if(!value||typeof value.version!=='string')throw new Error('Release manifest needs a version');version(value.version);
    if(value.channel!==configuration.channel)throw new Error('Release channel differs from configuration');
    if(value.platform!==this.options.platform||value.arch!==this.options.arch)throw new Error('Release platform or architecture differs from this installation');
    if(typeof value.url!=='string'||httpsUrl(value.url).origin!==httpsUrl(configuration.manifest_url).origin)throw new Error('Release artifact must use the configured manifest HTTPS origin');
    if(typeof value.sha256!=='string'||!/^[0-9a-f]{64}$/.test(value.sha256)||!Number.isSafeInteger(value.size)||value.size<1||value.size>ARTIFACT_LIMIT)throw new Error('Release needs a valid SHA256 and a package size from 1 byte to 1 GiB');
    this.release=newer(value.version,this.options.appVersion)?{version:value.version,channel:value.channel,platform:value.platform,arch:value.arch,url:value.url,sha256:value.sha256,size:value.size}:null;
    this.manifestSHA=crypto.createHash('sha256').update(bytes).digest('hex');
    const state=await this.status();state.update.status=this.release?'available':'up_to_date';return state;
  }
  async download():Promise<ManualDelivery> {
    if(!this.release||!this.manifestSHA)throw new Error('Check a newer release before manual delivery');
    const before=this.manifestSHA,release=this.release,started=this.revision;await this.check();
    if(before!==this.manifestSHA||started!==this.revision)throw new Error('Release manifest changed since checking; check again');
    const directory=path.join(this.options.userDataPath,'updates');unlinked(directory);fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const extension=path.extname(new URL(release.url).pathname);const suffix=['.dmg','.exe','.zip','.AppImage'].includes(extension)?extension:'.bin';
    const target=path.join(directory,`release-${release.version}-${release.platform}-${release.arch}${suffix}`),temporary=path.join(directory,'.'+crypto.randomUUID()+'.partial');unlinked(target);
    const fd=fs.openSync(temporary,'wx',0o600),digest=crypto.createHash('sha256');let size=0,opened=true;
    try{
      await this.request(release.url,release.size,120000,chunk=>{const buffer=Buffer.from(chunk);digest.update(buffer);size+=buffer.length;let offset=0;while(offset<buffer.length)offset+=fs.writeSync(fd,buffer,offset,buffer.length-offset);});
      if(size!==release.size||digest.digest('hex')!==release.sha256)throw new Error('Release package size or checksum differs from the manifest');
      if(started!==this.revision)throw new Error('Update configuration changed during download');
      fs.fsyncSync(fd);fs.closeSync(fd);opened=false;fs.renameSync(temporary,target);
      const signature=await this.signature(target,false),installed=await this.signature();
      const matched=signature.status==='verified'&&installed.status==='verified'&&!!signature.publisher&&signature.publisher===installed.publisher;
      return {version:release.version,path:target,sha256:release.sha256,integrity_verified:true,signature,publisher_matches_installed:matched,handoff_ready:matched,
        prerequisite:matched?'Back up the project, then install the verified package manually':'A valid artifact signature matching the installed publisher is required for a verified installation handoff; no automatic installation occurred'};
    }catch(cause){if(opened)try{fs.closeSync(fd);}catch{}throw cause;}
    finally{fs.rmSync(temporary,{force:true});}
  }
}
