import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {readTrustAuthority,readBoundedStableFile,verifySignedRelease,verifyOfflineRelease} from './releaseTrust';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import type {DistributionState,NativeSignature,UpdateChannel,UpdateRelease,ManualDelivery,DeliveryRecovery,DistributionBackend} from '../types/electron';

const runFile=promisify(execFile);
const MANIFEST_LIMIT=64*1024, ARTIFACT_LIMIT=1024*1024*1024;
type Runner=(file:string,args:string[])=>Promise<{stdout:string;stderr:string}>;
export interface DistributionOptions {appVersion:string;packaged:boolean;appPath:string;executablePath:string;userDataPath:string;platform:string;arch:string;resourcesPath?:string;runner?:Runner;fetcher?:typeof fetch}

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

/** Replaces a small settings file atomically and durably: the text is written and flushed through the handle that created
 *  the temporary file (Windows flushes only through a handle opened for writing), which is then renamed over the file. */
function writeDurably(file:string,text:string,platform:string):void {
  const temporary=file+'.'+crypto.randomUUID()+'.tmp';
  try{
    const fd=fs.openSync(temporary,'wx',0o600);
    try{fs.writeFileSync(fd,text);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
    // Antivirus/indexing readers can briefly deny replacement on Windows.
    // Retain the old destination and retry the same already-flushed temporary
    // file in at most four rename attempts, with delays totaling 70 ms.
    for(let attempt=0;;attempt++){
      try{fs.renameSync(temporary,file);break;}
      catch(cause){
        const code=(cause as NodeJS.ErrnoException).code;
        if(platform!=='win32'||attempt>=3||!['EPERM','EACCES','EBUSY'].includes(code||''))throw cause;
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,10*2**attempt);
      }
    }
  }
  finally{fs.rmSync(temporary,{force:true});}
}

export class DistributionManager {
  readonly options:DistributionOptions;
  private release:UpdateRelease|null=null;
  private manifestSHA:string|null=null;
  private revision=0;
  private publisher:string|null=null;
  private authoritySHA:string|null=null;
  constructor(options:DistributionOptions){
    if(fs.existsSync(options.userDataPath)&&fs.lstatSync(options.userDataPath).isSymbolicLink())throw new Error('Distribution user storage cannot be a symbolic link');
    this.options={...options,userDataPath:fs.existsSync(options.userDataPath)?fs.realpathSync(options.userDataPath):path.resolve(options.userDataPath)};
    const journal=this.deliveryJournal();
    if(journal&&['downloading','verifying'].includes(journal.status)){
      if(journal.partial_path){const partial=this.ownedDeliveryPath(journal.partial_path);if(!path.basename(partial).startsWith('.')||!partial.endsWith('.partial'))throw new Error('Invalid interrupted download path');unlinked(partial);fs.rmSync(partial,{force:true});}
      this.saveDelivery({...journal,status:journal.status==='downloading'?'interrupted':'downloaded_unverified',checked_at:new Date().toISOString(),error:'Application interrupted before delivery acceptance'});
    }
  }
  private deliveryPath():string{return path.join(this.options.userDataPath,'distribution-delivery.json');}
  private ownedDeliveryPath(value:string):string {
    const directory=path.join(this.options.userDataPath,'updates'),resolved=path.resolve(value);
    const target=fs.existsSync(path.dirname(resolved))?path.join(fs.realpathSync(path.dirname(resolved)),path.basename(resolved)):resolved;
    if(path.dirname(target)!==directory)throw new Error('Delivery journal path differs from owned update storage');
    return target;
  }
  private deliveryJournal():(DeliveryRecovery&{partial_path?:string})|null {
    const file=this.deliveryPath();unlinked(file);if(!fs.existsSync(file))return null;
    if(fs.statSync(file).size>8192)throw new Error('Delivery journal is too large');
    const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(readBoundedStableFile(file,8192,'Delivery journal')));
    if(value.schema_version!==1||typeof value.status!=='string'||typeof value.version!=='string'||! /^[0-9a-f]{64}$/.test(value.manifest_sha256)||! /^[0-9a-f]{64}$/.test(value.candidate_sha256)||(value.installed_sha256!==null&&! /^[0-9a-f]{64}$/.test(value.installed_sha256)))throw new Error('Delivery journal identity is invalid');
    if(value.candidate_path)value.candidate_path=this.ownedDeliveryPath(value.candidate_path);
    return value;
  }
  private saveDelivery(value:DeliveryRecovery&{partial_path?:string}):void {
    const file=this.deliveryPath();unlinked(file);fs.mkdirSync(this.options.userDataPath,{recursive:true});
    writeDurably(file,JSON.stringify(value),this.options.platform);
  }
  private installedSHA():string|null {
    return fs.existsSync(this.options.executablePath)&&fs.statSync(this.options.executablePath).isFile()?crypto.createHash('sha256').update(fs.readFileSync(this.options.executablePath)).digest('hex'):null;
  }
  private deliveryRecovery():DeliveryRecovery|null {
    const journal=this.deliveryJournal();if(!journal)return null;
    const {partial_path,...visible}=journal;
    if(visible.candidate_path&&['handoff_ready','publisher_required','downloaded_unverified'].includes(visible.status)){
      unlinked(visible.candidate_path);
      if(!fs.existsSync(visible.candidate_path)||crypto.createHash('sha256').update(fs.readFileSync(visible.candidate_path)).digest('hex')!==visible.candidate_sha256||this.installedSHA()!==visible.installed_sha256){
        visible.status='invalidated';visible.error='Candidate or installed executable changed; check the release again';this.saveDelivery(visible);
      }
    }
    return visible;
  }
  private configPath():string{return path.join(this.options.userDataPath,'distribution-channel.json');}
  private configuration():UpdateChannel|null {
    const file=this.configPath();unlinked(file);
    if(!fs.existsSync(file))return null;
    if(fs.statSync(file).size>4096)throw new Error('Update channel configuration is too large');
    const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(readBoundedStableFile(file,4096,'Channel configuration'))) as UpdateChannel;
    this.validateChannel(value);return value;
  }
  private validateChannel(value:UpdateChannel):void {
    if(!value||!['stable','beta'].includes(value.channel)||typeof value.manifest_url!=='string')throw new Error('Select a stable or beta release channel and its manifest URL');
    httpsUrl(value.manifest_url);
  }
  async configure(value:UpdateChannel|null):Promise<DistributionState> {
    if(value)this.validateChannel(value);
    const file=this.configPath();unlinked(file);fs.mkdirSync(this.options.userDataPath,{recursive:true});
    if(value)writeDurably(file,JSON.stringify({channel:value.channel,manifest_url:value.manifest_url}),this.options.platform);
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
        if(/Signature=adhoc|flags=.*adhoc/i.test(text)||!team||team==='not')return result('unsigned','Code signing did not establish a publisher identity');
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
  private async backendInventory():Promise<DistributionBackend> {
    const prerequisites=['Final installer startup and physical camera/PLC acceptance require the actual target', 'Model quality approval is recorded separately from runtime acceptance'];
    if(!this.options.packaged)return {status:'development',startup_acceptance:'unverified',prerequisites:['Build and validate the frozen backend on the target OS and architecture',...prerequisites]};
    const directory=path.join(this.options.resourcesPath||process.resourcesPath||this.options.appPath,'backend_bin');
    const receiptPath=path.join(directory,'backend-release.json');
    if(!fs.existsSync(receiptPath))return {status:'missing',prerequisites:['Rebuild the frozen backend and package its dependency inventory',...prerequisites]};
    try {
      const receipt=JSON.parse(fs.readFileSync(receiptPath,'utf8'));
      const name=this.options.platform==='win32'?'vision_ai_backend.exe':'vision_ai_backend';
      const executable=path.join(directory,name);
      const digest=crypto.createHash('sha256').update(fs.readFileSync(executable)).digest('hex');
      const platform=({Darwin:'darwin',Windows:'win32',Linux:'linux'} as Record<string,string>)[receipt.inventory?.platform];
      const architecture=({arm64:'arm64',aarch64:'arm64',x86_64:'x64',AMD64:'x64',x64:'x64'} as Record<string,string>)[receipt.inventory?.architecture];
      if(receipt.executable!==name||receipt.executable_sha256!==digest||platform!==this.options.platform||architecture!==this.options.arch||! /^[0-9a-f]{64}$/.test(receipt.inventory?.build_identity_sha256))throw new Error('Backend executable or target differs from inventory');
      const acceptance=receipt.acceptance;
      const passed=acceptance?.status==='passed'&&acceptance.frozen===true&&acceptance.executable_sha256===digest&&acceptance.build_identity_sha256===receipt.inventory.build_identity_sha256&&!!acceptance.health&&!!acceptance.restart_health;
      const signature=await this.signature(executable,false);
      if(signature.status!=='verified')prerequisites.unshift('Verify the frozen backend publisher signature');
      if(!passed)prerequisites.unshift('Run frozen backend dependency imports, startup and restart acceptance on this target');
      return {status:'inventory_bound',build_identity_sha256:receipt.inventory.build_identity_sha256,executable_sha256:digest,startup_acceptance:passed?'passed':'unverified',signature,offline:receipt.inventory.offline,prerequisites};
    } catch(cause){return {status:'invalid',reason:String((cause as Error).message||cause),prerequisites:['Rebuild the changed or invalid frozen backend',...prerequisites]};}
  }
  async status():Promise<DistributionState> {
    const configuration=this.configuration();
    return {app_version:this.options.appVersion,version_source:'electron',platform:this.options.platform,architecture:this.options.arch,signature:await this.signature(),backend:await this.backendInventory(),
      update:{configured:!!configuration,configuration,status:this.release?'available':configuration?'configured':'not_configured',release:this.release,automatic_update_available:false,recovery:this.deliveryRecovery(),
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
    const started=this.revision;const chunks:Buffer[]=[];this.release=null;this.manifestSHA=null;this.publisher=null;this.authoritySHA=null;
    const trustFile=path.join(this.options.resourcesPath||process.resourcesPath||this.options.appPath,'release-trust.json');
    const trust=this.options.packaged?readTrustAuthority(trustFile):null;
    await this.request(configuration.manifest_url,MANIFEST_LIMIT,15000,chunk=>{chunks.push(Buffer.from(chunk));});
    if(started!==this.revision)throw new Error('Update configuration changed during checking; check again');
    const bytes=Buffer.concat(chunks),parsed=JSON.parse(bytes.toString('utf8'));
    const value:UpdateRelease=trust?verifySignedRelease(parsed,trust,{platform:this.options.platform,arch:this.options.arch,
      channel:configuration.channel,current_version:this.options.appVersion,origin:httpsUrl(configuration.manifest_url).origin}):parsed;
    if(trust){this.publisher=trust.publisher;this.authoritySHA=crypto.createHash('sha256').update(JSON.stringify(trust)).digest('hex');}
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
    const before=this.manifestSHA,authorityBefore=this.authoritySHA,release=this.release,started=this.revision;await this.check();
    if(before!==this.manifestSHA||authorityBefore!==this.authoritySHA||started!==this.revision)throw new Error('Release manifest changed since checking; check again');
    const directory=path.join(this.options.userDataPath,'updates');unlinked(directory);fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const extension=path.extname(new URL(release.url).pathname);const suffix=['.dmg','.exe','.zip','.AppImage'].includes(extension)?extension:'.bin';
    const target=path.join(directory,`release-${release.version}-${release.platform}-${release.arch}${suffix}`),temporary=path.join(directory,'.'+crypto.randomUUID()+'.partial');unlinked(target);
    const journal:DeliveryRecovery&{partial_path?:string}={schema_version:1,status:'downloading',version:release.version,manifest_sha256:before,candidate_sha256:release.sha256,installed_sha256:this.installedSHA(),candidate_path:target,partial_path:temporary,checked_at:new Date().toISOString()};
    this.saveDelivery(journal);
    const fd=fs.openSync(temporary,'wx',0o600),digest=crypto.createHash('sha256');let size=0,opened=true;
    try{
      await this.request(release.url,release.size,120000,chunk=>{const buffer=Buffer.from(chunk);digest.update(buffer);size+=buffer.length;let offset=0;while(offset<buffer.length)offset+=fs.writeSync(fd,buffer,offset,buffer.length-offset);});
      if(size!==release.size||digest.digest('hex')!==release.sha256)throw new Error('Release package size or checksum differs from the manifest');
      if(started!==this.revision)throw new Error('Update configuration changed during download');
      fs.fsyncSync(fd);fs.closeSync(fd);opened=false;fs.renameSync(temporary,target);
      this.saveDelivery({...journal,status:'verifying',partial_path:undefined,checked_at:new Date().toISOString()});
      const signature=await this.signature(target,false),installed=await this.signature();
      const matched=signature.status==='verified'&&installed.status==='verified'&&!!signature.publisher&&signature.publisher===installed.publisher&&(!this.options.packaged||signature.publisher===this.publisher)&&this.installedSHA()===journal.installed_sha256;
      this.saveDelivery({...journal,status:matched?'handoff_ready':'publisher_required',partial_path:undefined,checked_at:new Date().toISOString()});
      return {version:release.version,path:target,sha256:release.sha256,integrity_verified:true,signature,publisher_matches_installed:matched,handoff_ready:matched,
        prerequisite:matched?'Back up the project, then install the verified package manually':'A valid artifact signature matching the installed publisher is required for a verified installation handoff; no automatic installation occurred'};
    }catch(cause){if(opened)try{fs.closeSync(fd);}catch{}this.saveDelivery({...journal,status:'failed',partial_path:undefined,error:cause instanceof Error?cause.name:'DeliveryFailure',checked_at:new Date().toISOString()});throw cause;}
    finally{fs.rmSync(temporary,{force:true});}
  }

  /** Verify a user-selected offline bundle. No installer, migration, subprocess
   * code from the bundle, or network request is executed. */
  async verifyOffline(manifestPath:string):Promise<ManualDelivery> {
    if(!this.options.packaged)throw new Error('Offline release handoff requires the packaged application and its provisioned publisher authority');
    const trust=readTrustAuthority(path.join(this.options.resourcesPath||process.resourcesPath||this.options.appPath,'release-trust.json'));
    const raw=readBoundedStableFile(manifestPath,MANIFEST_LIMIT,'Release manifest');
    const envelope=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
    // Channel is a local choice, not a value selected by the signed feed.
    const channel=this.configuration()?.channel||'stable';
    const target={platform:this.options.platform,arch:this.options.arch,channel,current_version:this.options.appVersion,origin:trust.allowed_origins[0]};
    const sourceDirectory=path.join(path.dirname(manifestPath),'artifacts');
    const verified=verifyOfflineRelease(sourceDirectory,envelope,trust,target),release=verified.release;
    const installer=release.artifacts.find(a=>a.kind==='installer')!;
    const directory=path.join(this.options.userDataPath,'updates');unlinked(directory);fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const owned=path.join(directory,`offline-${crypto.randomUUID()}${path.extname(installer.path)}`),temporary=path.join(directory,'.'+crypto.randomUUID()+'.partial');
    const source=path.join(sourceDirectory,installer.path);unlinked(source);
    const before=this.installedSHA(),manifestSHA=crypto.createHash('sha256').update(raw).digest('hex');
    const started=this.revision;
    const journal:DeliveryRecovery&{partial_path?:string}={schema_version:1,status:'verifying',version:release.version,manifest_sha256:manifestSHA,
      candidate_sha256:release.sha256,installed_sha256:before,checked_at:new Date().toISOString()};
    let input:number|undefined,output:number|undefined;
    try{
      this.saveDelivery({...journal,status:'downloading',candidate_path:owned,partial_path:temporary});
      input=fs.openSync(source,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
      const stat=fs.fstatSync(input);if(!stat.isFile()||stat.size!==installer.size)throw new Error('Offline installer identity changed');
      output=fs.openSync(temporary,'wx',0o600);const digest=crypto.createHash('sha256'),buffer=Buffer.alloc(1024*1024);let size=0,count;
      while((count=fs.readSync(input,buffer,0,Math.min(buffer.length,installer.size-size+1),null))>0){size+=count;if(size>installer.size)throw new Error('Offline installer size changed');digest.update(buffer.subarray(0,count));let offset=0;while(offset<count)offset+=fs.writeSync(output,buffer,offset,count-offset);}
      const after=fs.fstatSync(input);
      if(size!==installer.size||digest.digest('hex')!==installer.sha256||after.mtimeMs!==stat.mtimeMs||after.ino!==stat.ino||after.dev!==stat.dev)throw new Error('Offline installer checksum or identity changed');
      fs.fsyncSync(output);fs.closeSync(output);output=undefined;fs.renameSync(temporary,owned);
      this.saveDelivery({...journal,candidate_path:owned});
      const signature=await this.signature(owned,false),installed=await this.signature();
      // Recheck trust and every optional pack after native verification awaits.
      const currentTrust=readTrustAuthority(path.join(this.options.resourcesPath||process.resourcesPath||this.options.appPath,'release-trust.json'));
      if(started!==this.revision)throw new Error('Update channel changed during offline verification');
      if(JSON.stringify(currentTrust)!==JSON.stringify(trust))throw new Error('Pinned release authority changed during verification');
      verifyOfflineRelease(sourceDirectory,envelope,currentTrust,target);
      unlinked(manifestPath);
      if(crypto.createHash('sha256').update(readBoundedStableFile(manifestPath,MANIFEST_LIMIT,'Release manifest')).digest('hex')!==manifestSHA)throw new Error('Offline manifest changed during native verification');
      if(crypto.createHash('sha256').update(fs.readFileSync(owned)).digest('hex')!==release.sha256)throw new Error('Owned installer changed during native verification');
      const matched=signature.status==='verified'&&installed.status==='verified'&&signature.publisher===trust.publisher
        &&installed.publisher===trust.publisher&&this.installedSHA()===before;
      this.saveDelivery({...journal,candidate_path:owned,status:matched?'handoff_ready':'publisher_required'});
      return {version:release.version,path:owned,sha256:release.sha256,integrity_verified:true,signature,
        publisher_matches_installed:matched,handoff_ready:matched,offline:true,artifacts_verified:verified.artifacts_verified,
        prerequisite:'All offline bundle hashes verified. Native publisher acceptance, project backup and an idle execution window are required before manual installation; no installation or migration occurred'};
    }catch(cause){this.saveDelivery({...journal,status:'failed',candidate_path:fs.existsSync(owned)?owned:undefined,error:cause instanceof Error?cause.name:'OfflineFailure'});throw cause;}
    finally{if(input!==undefined)fs.closeSync(input);if(output!==undefined)fs.closeSync(output);fs.rmSync(temporary,{force:true});}
  }
}
