/** Early private-descriptor binding. No renderer capability or native acceptance. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomBytes} from 'node:crypto';
import net from 'node:net';
import type {ChildProcess} from 'node:child_process';
import type {Duplex} from 'node:stream';

const LIMIT=65536,HEX32=/^[a-f0-9]{32}$/,HEX64=/^[a-f0-9]{64}$/;
const APPLICATION_MANIFEST_LIMIT=8*1024**2,APPLICATION_MEMBER_LIMIT=20000;
const CONTEXT=['VISION_APPLICATION_LAUNCH_FD','VISION_APPLICATION_LAUNCH_NONCE','VISION_APPLICATION_GENERATION','VISION_APPLICATION_DATABASE_GENERATION','VISION_APPLICATION_BACKEND_FD'];
const hash=(raw:Buffer|string)=>createHash('sha256').update(raw).digest('hex');
// Python's absolute producer clock is data here, never a Node clock origin.
// Controller/backend alone authorize its expiry. This separately bounds only
// the original Node transport lifetime; bind/finish cannot renew this row.
function preflightRelayBound(plan:any):number {
 if(!Number.isSafeInteger(plan.budget_ms)||plan.budget_ms<1||plan.budget_ms>900000||typeof plan.deadline_monotonic!=='number'||!Number.isFinite(plan.deadline_monotonic)||plan.deadline_monotonic<=0)throw new Error('Original fixed preflight plan differs');
 return performance.now()+plan.budget_ms;
}
function preflightRelayActionDeadline(rowDeadline:number,drainDeadline:number|null):number {
 const now=performance.now(),deadline=Math.min(rowDeadline,now+10000,drainDeadline??Infinity);
 if(!Number.isFinite(deadline)||deadline<=now)throw new Error('Original preflight transport deadline expired');
 return deadline;
}
const canonical=(value:any):string=>value===null||typeof value!=='object'?JSON.stringify(value):Array.isArray(value)?'['+value.map(canonical).join(',')+']':'{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
function fields(value:any,expected:string[]):void {if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==[...expected].sort().join('|'))throw new Error('Private launch fields differ');}
function identity(value:any):void {fields(value,['pid','created_at','command_sha256']);if(!Number.isSafeInteger(value.pid)||value.pid<1||!Number.isFinite(value.created_at)||value.created_at<=0||!HEX64.test(value.command_sha256))throw new Error('Private launch process identity differs');}

/** Retain Python float tokens for journal hashes; reject duplicates before parse. */
export function parsePrivateDocument(raw:Buffer,limit=LIMIT):{value:any;canonical:string} {
 if(!Number.isSafeInteger(limit)||limit<1||limit>8*1024**2||raw.length>limit)throw new Error('Private launch document exceeds its bound');
 const text=new TextDecoder('utf-8',{fatal:true}).decode(raw);let i=0,depth=0;
 const white=()=>{while(/\s/.test(text[i]||'')&&i<text.length)i++;};
 const string=():string=>{const start=i++;let escape=false;while(i<text.length){const c=text[i++];if(c==='"'&&!escape)return JSON.parse(text.slice(start,i));if(c==='\\'&&!escape)escape=true;else escape=false;}throw new Error('Invalid private launch string');};
 const item=():{value:any;canonical:string}=>{white();if(++depth>32)throw new Error('Private launch document nesting exceeds its bound');let result:{value:any;canonical:string};
  if(text[i]==='{'){i++;white();const rows=new Map<string,{value:any;canonical:string}>();if(text[i]!=='}')while(true){white();if(text[i]!=='"')throw new Error('Invalid private launch key');const key=string();if(rows.has(key))throw new Error('Duplicate private launch field');white();if(text[i++]!==':')throw new Error('Invalid private launch object');rows.set(key,item());white();if(text[i]!==',')break;i++;}if(text[i++]!=='}')throw new Error('Invalid private launch object');result={value:Object.fromEntries([...rows].map(([k,v])=>[k,v.value])),canonical:'{'+[...rows.keys()].sort().map(k=>JSON.stringify(k)+':'+rows.get(k)!.canonical).join(',')+'}'};
  }else if(text[i]==='['){i++;white();const rows=[];if(text[i]!==']')while(true){rows.push(item());white();if(text[i]!==',')break;i++;}if(text[i++]!==']')throw new Error('Invalid private launch array');result={value:rows.map(v=>v.value),canonical:'['+rows.map(v=>v.canonical).join(',')+']'};
  }else if(text[i]==='"'){const value=string();result={value,canonical:JSON.stringify(value)};
  }else{const match=/^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(i));if(!match)throw new Error('Invalid private launch value');i+=match[0].length;const value=JSON.parse(match[0]);if(typeof value==='number'&&!Number.isFinite(value))throw new Error('Nonfinite private launch value');result={value,canonical:match[0]};}depth--;return result;};
 const result=item();white();if(i!==text.length)throw new Error('Additional private launch bytes');return result;
}

function unlinked(file:string):void {for(let current=file;;current=path.dirname(current)){try{if(fs.lstatSync(current).isSymbolicLink())throw new Error('Private launch storage cannot follow links');}catch(error:any){if(error.code!=='ENOENT')throw error;}if(path.dirname(current)===current)break;}}
function stable(file:string,limit=LIMIT,expected?:{size:number;sha256:string}):Buffer|string {
 unlinked(file);const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0)|(fs.constants.O_NONBLOCK||0));
 try{const before=fs.fstatSync(fd);if(!before.isFile()||before.nlink!==1||before.size<1||before.size>limit||expected&&before.size!==expected.size)throw new Error('Private launch input is not a bounded regular file');
 const digest=createHash('sha256'),chunks:Buffer[]=[],buffer=Buffer.alloc(Math.min(1024*1024,before.size+1));let total=0,count;
 while((count=fs.readSync(fd,buffer,0,Math.min(buffer.length,before.size-total+1),null))>0){total+=count;if(total>before.size)throw new Error('Private launch input grew');digest.update(buffer.subarray(0,count));if(!expected)chunks.push(Buffer.from(buffer.subarray(0,count)));}
 unlinked(file);const after=fs.fstatSync(fd),named=fs.lstatSync(file);const keys=['dev','ino','size','mtimeMs','ctimeMs','nlink','mode'] as const;
 if(total!==before.size||!named.isFile()||keys.some(k=>after[k]!==before[k]||named[k]!==before[k]))throw new Error('Private launch input changed');
 const pin=digest.digest('hex');if(expected&&pin!==expected.sha256)throw new Error('Private launch artifact checksum differs');return expected?pin:Buffer.concat(chunks);
 }finally{fs.closeSync(fd);}
}
const document=(file:string)=>parsePrivateDocument(stable(file) as Buffer);

class PrivateFrameEnd extends Error {
 constructor(readonly owner:Frames,readonly channel:Duplex,readonly event:'end'|'close'){super(event==='end'?'Private descriptor ended':'Private descriptor closed');}
}
class Frames {
 private buffer=Buffer.alloc(0);private queue:Buffer[]=[];private waiting:{resolve:(raw:Buffer)=>void;reject:(error:Error)=>void;timer:NodeJS.Timeout|null}|null=null;private failure:Error|null=null;private originalEndFailure:PrivateFrameEnd|null=null;
 constructor(readonly channel:Duplex){const ended=(event:'end'|'close')=>{if(this.failure)return;const error=new PrivateFrameEnd(this,channel,event);this.originalEndFailure=error;this.fail(error);};channel.on('data',(chunk:Buffer)=>{if(this.failure)return;this.buffer=Buffer.concat([this.buffer,chunk]);while(this.buffer.includes(10)){const at=this.buffer.indexOf(10);if(at+1>LIMIT){this.fail(new Error('Private frame exceeds its bound'));return;}const raw=this.buffer.subarray(0,at);this.buffer=this.buffer.subarray(at+1);if(this.waiting){const pending=this.waiting;this.waiting=null;if(pending.timer)clearTimeout(pending.timer);pending.resolve(raw);}else{this.queue.push(raw);if(this.queue.length>2){this.fail(new Error('Unexpected private frame replay'));return;}}}if(this.buffer.length>=LIMIT)this.fail(new Error('Private frame exceeds its bound'));});channel.on('error',(e)=>this.fail(e));channel.on('end',()=>ended('end'));channel.on('close',()=>ended('close'));}
 fail(error:Error){if(this.failure)return;this.failure=error;if(this.waiting){const pending=this.waiting;this.waiting=null;if(pending.timer)clearTimeout(pending.timer);pending.reject(error);}}
 originalEmptyEnd(error:unknown):boolean {return error===this.failure&&error===this.originalEndFailure&&error instanceof PrivateFrameEnd&&error.constructor===PrivateFrameEnd&&error.owner===this&&error.channel===this.channel&&this.waiting===null&&this.queue.length===0&&this.buffer.length===0;}
 async read(timeout:number|null=210000):Promise<Buffer>{if(this.failure)throw this.failure;if(this.queue.length)return this.queue.shift()!;if(this.waiting)throw new Error('Concurrent private frame read');return new Promise((resolve,reject)=>{const timer=timeout===null?null:setTimeout(()=>{this.waiting=null;reject(new Error('Private descriptor timeout'));},timeout);this.waiting={resolve,reject,timer};});}
 async send(value:any,absoluteDeadline?:number):Promise<void>{return this.sendCanonical(canonical(value),absoluteDeadline);}
 async sendCanonical(value:string,absoluteDeadline?:number):Promise<void>{if(this.failure)throw this.failure;const deadline=Math.min(performance.now()+10000,absoluteDeadline??Infinity),raw=Buffer.from(value+'\n');parsePrivateDocument(raw);if(raw.length>LIMIT)throw new Error('Private frame exceeds its bound');const remaining=deadline-performance.now();if(!Number.isFinite(remaining)||remaining<=0)throw new Error('Original private send deadline expired');await new Promise<void>((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Private send timeout')),remaining);this.channel.write(raw,error=>{clearTimeout(timer);error?reject(error):performance.now()>=deadline?reject(new Error('Original private send completed late')):resolve();});});}
 assertEmpty(){if(this.failure)throw this.failure;if(this.queue.length||this.buffer.length)throw new Error('Unexpected private frame replay');}
}

export class OwnedApplicationLaunch {
 readonly projects:string;readonly auth:string;private usedBackend=false;
 private backend:ChildProcess|null=null;private backendProof:any=null;private backendClaimHash='';private backendEpoch='';
 private pendingDrain:{id:string;deadline:number;resolve:(receipt:any)=>void;reject:(error:Error)=>void;timer:NodeJS.Timeout}|null=null;
 private drainRequest:any=null;private drainReceipt:any=null;private backendExitFrame:any=null;
 private drainDeadline:number|null=null;
 private exitAcknowledged=false;
 private exitResolve:(()=>void)|null=null;private exitReject:((error:Error)=>void)|null=null;private exitConfirmation:Promise<void>|null=null;
 constructor(readonly root:string,readonly nonce:string,readonly binding:any,readonly mainProcess:any,private challenge:string,private frames:Frames,private writer?:any){this.projects=path.join(root,'projects');this.auth=path.join(root,'auth');}
 get requiresWriterDrain():boolean{return this.writer!==undefined;}
 private bindingCurrent():void {validateBinding(this.root,this.nonce,this.binding,this.mainProcess);}
 private current():void {this.bindingCurrent();this.frames.assertEmpty();}
 executable(file:string):{sha256:string;build:string|null} {this.current();const application=path.join(this.root,'.application-generations',this.binding.application_generation,'application');const manifest=parsePrivateDocument(stable(path.join(application,'portable-application.json'),APPLICATION_MANIFEST_LIMIT) as Buffer,APPLICATION_MANIFEST_LIMIT).value;
 const relative=path.relative(application,file).split(path.sep).join('/');if(path.isAbsolute(relative)||relative.startsWith('../')||relative==='..')throw new Error('Backend executable escapes committed application');
 const rows=manifest.files.filter((row:any)=>row.path===relative&&row.executable===true);if(rows.length!==1)throw new Error('Backend executable is not a committed row');stable(file,1024**3,rows[0]);
 const receiptPath=path.join(path.dirname(file),'backend-release.json');const receiptRelative=path.relative(application,receiptPath).split(path.sep).join('/');const receiptRows=manifest.files.filter((row:any)=>row.path===receiptRelative);
 if(receiptRows.length!==1)return {sha256:rows[0].sha256,build:null};stable(receiptPath,8*1024**2,receiptRows[0]);const receipt=parsePrivateDocument(stable(receiptPath,8*1024**2) as Buffer,8*1024**2).value;
 if(receipt.schema_version!==1||receipt.executable!==path.basename(file)||receipt.executable_sha256!==rows[0].sha256||!HEX64.test(receipt.inventory?.build_identity_sha256))throw new Error('Committed backend receipt differs');return {sha256:rows[0].sha256,build:receipt.inventory.build_identity_sha256};}
 backendEnvironment(env:NodeJS.ProcessEnv):NodeJS.ProcessEnv {const result={...env};for(const key of CONTEXT)delete result[key];return {...result,VISION_AI_STUDIO_USER_DATA_DIR:this.root,VISION_APPLICATION_LAUNCH_NONCE:this.nonce,VISION_APPLICATION_GENERATION:this.binding.application_generation,VISION_APPLICATION_DATABASE_GENERATION:this.binding.database_generation_path,VISION_APPLICATION_BACKEND_FD:'3'};}
 async bindBackend(proc:ChildProcess,executable:string,build:string|null):Promise<void>{if(this.usedBackend)throw new Error('Owned backend restart requires a new reconciled controller epoch');this.usedBackend=true;this.current();const artifact=this.executable(executable);if(artifact.build!==build||!Number.isSafeInteger(proc.pid)||!proc.stdio[3])throw new Error('Owned backend executable or private descriptor differs');
 const channel=proc.stdio[3] as Duplex,frames=new Frames(channel),challenge=randomBytes(32).toString('hex'),epoch=randomBytes(16).toString('hex');
 await frames.send({schema_version:1,kind:'backend_challenge',challenge,epoch,nonce:this.nonce,binding:this.binding,main_process:this.mainProcess,backend_pid:proc.pid,backend_executable:executable,backend_build_identity_sha256:build,...(this.writer?{writer:this.writer}:{})});
 const claimRaw=await frames.read(),readyRaw=await frames.read(),claim=parsePrivateDocument(claimRaw).value,ready=parsePrivateDocument(readyRaw).value;
 const names=['schema_version','kind','challenge','epoch','nonce','binding_sha256','process','executable','executable_sha256','build_identity_sha256','frozen'];fields(claim,names);fields(ready,names);identity(claim.process);identity(ready.process);
 if(claim.schema_version!==1||ready.schema_version!==1||claim.kind!=='backend_claim'||ready.kind!=='backend_ready'||canonical({...claim,kind:'backend_ready'})!==canonical(ready)||ready.challenge!==challenge||ready.epoch!==epoch||ready.nonce!==this.nonce||ready.binding_sha256!==hash(canonical(this.binding))||ready.process.pid!==proc.pid||ready.executable!==executable||ready.executable_sha256!==artifact.sha256||ready.build_identity_sha256!==build||typeof ready.frozen!=='boolean'||ready.frozen!==(build!==null))throw new Error('Backend private readiness binding differs');
 this.current();frames.assertEmpty();await this.frames.send({schema_version:1,kind:'backend_proof',challenge:this.challenge,nonce:this.nonce,claim_b64:claimRaw.toString('base64'),ready_b64:readyRaw.toString('base64')});
 // One closed controller-origin execution request may follow readiness. Idle
 // waiting has no timeout; unsolicited backend data still invalidates ownership.
 this.backend=proc;this.backendProof=claim;this.backendEpoch=epoch;this.backendClaimHash=hash(parsePrivateDocument(claimRaw).canonical);
 let failed=false,cpuExecuted=false,cleanExitObserved=false;const relayRows=new Map<string,{phase:string;plan:string;registration:any;child:any;deadline:number}>();
 let admittedDrain:{receipt:any;request:any;receiptHash:string;requestHash:string;deadline:number;outerDeadline:number}|null=null,exitTimer:NodeJS.Timeout|null=null;
 type RelayPending={request:any;requestHash:string;row:{phase:string;plan:string;registration:any;child:any;deadline:number};deadline:number;timer:NodeJS.Timeout};
 type BackendPending={kind:string;deadline:number;resolve:(raw:Buffer)=>void;reject:(error:Error)=>void;timer:NodeJS.Timeout};
 type DrainAdmitted={expected:any;resolve:()=>void;reject:(error:Error)=>void;timer:NodeJS.Timeout};
 // These holders are shared by the two original reader closures. Keep their
 // declared union types on one private mutable object, so closure assignments
 // are visible to strict TypeScript control flow without type assertions.
 const pendingState:{relayPending:RelayPending|null;backendPending:BackendPending|null;drainAdmitted:DrainAdmitted|null}={relayPending:null,backendPending:null,drainAdmitted:null};
 const invalidate=()=>{if(failed)return;failed=true;const error=new Error('Original owned backend channel/ownership ended');
  this.pendingDrain?.reject(error);this.exitReject?.(error);pendingState.backendPending?.reject(error);pendingState.drainAdmitted?.reject(error);
  if(pendingState.relayPending)clearTimeout(pendingState.relayPending.timer);if(pendingState.backendPending)clearTimeout(pendingState.backendPending.timer);if(pendingState.drainAdmitted)clearTimeout(pendingState.drainAdmitted.timer);
  if(exitTimer!==null){clearTimeout(exitTimer);exitTimer=null;}
  // Keep the original pending rows and handles; a transport loss never repairs
  // or adopts a child, releases a writer, or restarts a backend.
  this.frames.channel.destroy();channel.destroy();};
 const freshBackend=()=>{if(failed||this.backend!==proc||proc.stdio[3]!==channel||proc.pid!==claim.process.pid||proc.exitCode!==null||proc.signalCode!==null)throw new Error('Original backend handle/channel differs');this.bindingCurrent();};
 // A genuine empty backend EOF may end only this reader after the controller
 // admitted the original drain. It grants no exit, ACK, writer or lease release.
 // Keep the other reader for the exact retained ChildProcess exit and its ACK.
 const cleanDrainCurrent=()=>{
  const admitted=admittedDrain;
  const exact=()=>!failed&&this.backend===proc&&proc.stdio[3]===channel&&proc.pid===claim.process.pid&&
   (proc.exitCode===null&&proc.signalCode===null||cleanExitObserved&&proc.exitCode===0&&proc.signalCode===null)&&
   admitted!==null&&this.drainReceipt===admitted.receipt&&this.drainRequest===admitted.request&&
   this.drainDeadline===admitted.outerDeadline&&performance.now()<admitted.deadline&&
   hash(canonical(admitted.receipt))===admitted.receiptHash&&hash(canonical(admitted.request))===admitted.requestHash&&
   this.pendingDrain===null&&pendingState.relayPending===null&&pendingState.backendPending===null&&pendingState.drainAdmitted===null&&
   [...relayRows.values()].every(row=>row.phase==='finished');
  if(!exact())throw new Error('Original backend end retains pending, changed or late ownership');
  this.bindingCurrent();if(!exact())throw new Error('Original backend end ownership changed during validation');
  return admitted!.deadline;
 };
 const backendReply=(kind:string,deadline:number):Promise<Buffer>=>{freshBackend();if(pendingState.backendPending||deadline<=performance.now())throw new Error('Original backend response is concurrent or late');return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{reject(new Error('Original backend response deadline expired'));invalidate();},deadline-performance.now());pendingState.backendPending={kind,deadline,resolve,reject,timer};});};
 proc.once('exit',(code,signal)=>{
  if(!admittedDrain||code!==0||signal!==null||proc.exitCode!==code||proc.signalCode!==signal){invalidate();return;}
  cleanExitObserved=true;let deadline:number;try{deadline=cleanDrainCurrent();}catch{invalidate();return;}
  this.backendExitFrame={schema_version:1,kind:'main_backend_exit',nonce:this.nonce,epoch,request_id:this.drainRequest.request_id,challenge:this.drainRequest.challenge,backend_process:claim.process,returncode:code,signal};
  void this.frames.send(this.backendExitFrame,deadline).then(()=>{cleanDrainCurrent();}).catch(invalidate);
 });
 const backendClosed=()=>{try{cleanDrainCurrent();}catch{invalidate();}};channel.once('close',backendClosed);channel.once('end',backendClosed);channel.once('error',invalidate);
 // Exactly one reader for the original backend channel. Fixed preflight data
 // is forwarded with its original Python number tokens, never decoded/reminted
 // as a controller capability. Controller will mint one receive event itself.
 void (async()=>{while(true){const raw=await frames.read(null),parsed=parsePrivateDocument(raw),request=parsed.value;freshBackend();
  if(request.kind==='backend_preflight_request'){
   fields(request,['schema_version','kind','action','nonce','epoch','binding_sha256','backend_claim_sha256','request_id','payload']);
   if(!this.writer||pendingState.relayPending||request.schema_version!==1||request.nonce!==this.nonce||request.epoch!==epoch||request.binding_sha256!==hash(canonical(this.binding))||request.backend_claim_sha256!==this.backendClaimHash||!HEX32.test(request.request_id)||!['reserve','bind','finish'].includes(request.action)||this.drainRequest&&request.action!=='finish')throw new Error('Original preflight request binding/phase differs');
   let row=relayRows.get(request.request_id);
   if(request.action==='reserve'){
    if(row||relayRows.size>=128)throw new Error('Original preflight reservation is replayed or exceeds its bound');
    fields(request.payload,['task','device','stages','workdir','source_sha256','budget_ms','deadline_monotonic','command']);
    const deadline=preflightRelayBound(request.payload);
    if(!HEX64.test(request.payload.source_sha256)||!Array.isArray(request.payload.command)||request.payload.command.length!==18)throw new Error('Original fixed preflight plan differs');
    // parsePrivateDocument preserves the original nested canonical plan too.
    const plan=parsePrivateDocument(Buffer.from(parsed.canonical)).canonical;
    const match=/"payload":(.*),"request_id":/.exec(plan);if(!match)throw new Error('Original canonical preflight plan is unavailable');
    row={phase:'reserve_sent',plan:match[1],registration:null,child:null,deadline};relayRows.set(request.request_id,row);
   }else{
    if(!row)throw new Error('Original preflight reservation is unavailable');
    fields(request.payload,['registration','child','plan_sha256',...(request.action==='finish'?['returncode','cleanup_confirmed']:[])]);identity(request.payload.child);
    if(canonical(request.payload.registration)!==canonical(row.registration)||request.payload.plan_sha256!==hash(row.plan)||request.action==='bind'&&row.phase!=='reserved'||request.action==='finish'&&(row.phase!=='bound'||canonical(request.payload.child)!==canonical(row.child)||request.payload.returncode!==0||request.payload.cleanup_confirmed!==true))throw new Error('Original preflight child sequence or cleanup differs');
    if(request.action==='bind')row.child=request.payload.child;row.phase=request.action+'_sent';
   }
   const deadline=preflightRelayActionDeadline(row.deadline,this.drainDeadline),remaining=deadline-performance.now();
   const timer=setTimeout(invalidate,remaining);pendingState.relayPending={request,requestHash:hash(parsed.canonical),row,deadline,timer};
   await this.frames.sendCanonical('{"kind":"main_preflight_request","nonce":'+JSON.stringify(this.nonce)+',"request":'+parsed.canonical+',"schema_version":1}',deadline);freshBackend();if(performance.now()>=Math.min(deadline,this.drainDeadline??Infinity))throw new Error('Original preflight request send completed late');continue;
  }
  const pending=pendingState.backendPending;
  if(!pending||request.kind!==pending.kind||performance.now()>=pending.deadline)throw new Error('Original backend response is unsolicited, replayed or late');
  clearTimeout(pending.timer);pendingState.backendPending=null;pending.resolve(raw);
 }})().catch(error=>{try{if(!frames.originalEmptyEnd(error))throw error;cleanDrainCurrent();}catch{invalidate();}});
 // Exactly one reader for the controller channel, including while CPU/drain
 // work waits for a backend response. No nested Frames.read or renewed budget.
 void (async()=>{while(true){const request=parsePrivateDocument(await this.frames.read(null)).value;
 if(request.kind==='controller_preflight_reply'){
  freshBackend();fields(request,['schema_version','kind','nonce','epoch','request_id','action','request_sha256','payload']);const pending=pendingState.relayPending;
  if(!pending||request.schema_version!==1||request.nonce!==this.nonce||request.epoch!==epoch||request.request_id!==pending.request.request_id||request.action!==pending.request.action||request.request_sha256!==pending.requestHash)throw new Error('Original preflight acknowledgement differs, expired or replayed');
  const replyDeadline=preflightRelayActionDeadline(pending.deadline,this.drainDeadline);
  if(request.action==='reserve'){fields(request.payload,['writer_id','registration_sha256']);if(!HEX32.test(request.payload.writer_id)||!HEX64.test(request.payload.registration_sha256))throw new Error('Original child registration differs');pending.row.registration=request.payload;pending.row.phase='reserved';}
  else{fields(request.payload,['status']);if(request.payload.status!==(request.action==='bind'?'bound':'direct_exited'))throw new Error('Original child status acknowledgement differs');pending.row.phase=request.action==='bind'?'bound':'finished';}
  await frames.send(request,replyDeadline);freshBackend();if(performance.now()>=Math.min(replyDeadline,this.drainDeadline??Infinity))throw new Error('Original preflight acknowledgement send completed late');clearTimeout(pending.timer);pendingState.relayPending=null;continue;
 }
 if(request.kind==='managed_drain_admitted'){
  const pending=pendingState.drainAdmitted;if(!pending||!this.pendingDrain||performance.now()>=this.pendingDrain.deadline||canonical(request)!==canonical(pending.expected))throw new Error('Original controller drain acknowledgement differs, expired or replayed');
  clearTimeout(pending.timer);pendingState.drainAdmitted=null;pending.resolve();continue;
 }
 if(request.kind==='backend_exit_observed'){
  cleanDrainCurrent();if(!cleanExitObserved)throw new Error('Original backend exit has not occurred');
  fields(request,['schema_version','kind','nonce','request_id','exit_sha256']);
  if(this.exitAcknowledged||!this.exitResolve||!this.exitConfirmation||request.schema_version!==1||request.nonce!==this.nonce||!this.backendExitFrame||request.request_id!==this.backendExitFrame.request_id||request.exit_sha256!==hash(canonical(this.backendExitFrame)))throw new Error('Original Node exit acknowledgement differs or replayed');
  cleanDrainCurrent();this.exitAcknowledged=true;if(exitTimer!==null){clearTimeout(exitTimer);exitTimer=null;}const resolve=this.exitResolve;this.exitResolve=null;resolve();continue;
 }
 if(request.kind==='backend_drain_request'){
  fields(request,['schema_version','kind','challenge','request_id','nonce','epoch','binding_sha256','backend_claim_sha256','writer_id','registration_sha256','closed_registry_sha256','budget_ms']);
  if(!this.writer||!this.pendingDrain||this.drainRequest||request.schema_version!==1||request.nonce!==this.nonce||request.epoch!==epoch||request.request_id!==this.pendingDrain.id||!HEX64.test(request.challenge)||request.binding_sha256!==hash(canonical(this.binding))||request.backend_claim_sha256!==this.backendClaimHash||request.writer_id!==this.writer.writer_id||request.registration_sha256!==this.writer.registration_sha256||!HEX64.test(request.closed_registry_sha256)||!Number.isSafeInteger(request.budget_ms)||request.budget_ms<1||request.budget_ms>4000)throw new Error('Foreign or replayed managed drain request');
  freshBackend();this.drainRequest=request;const deadline=Math.min(this.pendingDrain.deadline,performance.now()+request.budget_ms);
  const response=backendReply('backend_managed_drain',deadline);await frames.send(request,deadline);
  void (async()=>{const raw=await response,receipt=parsePrivateDocument(raw,16384).value;
   fields(receipt,['schema_version','kind','request','backend_proof','status','active_scopes','unsupported','scope','whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease']);
   if(receipt.schema_version!==1||receipt.kind!=='backend_managed_drain'||canonical(receipt.request)!==canonical(request)||canonical(receipt.backend_proof)!==canonical(claim)||!['managed_scopes_drained','refused'].includes(receipt.status)||!Number.isSafeInteger(receipt.active_scopes)||receipt.active_scopes<0||!Array.isArray(receipt.unsupported)||receipt.scope!=='reviewed_foreground_scopes_only'||receipt.whole_writer_coverage!==false||receipt.process_tree_exit_verified!==false||receipt.can_release_launch_lease!==false||receipt.status==='managed_scopes_drained'&&(receipt.active_scopes!==0||receipt.unsupported.length!==0))throw new Error('Managed backend drain exceeds its exact scope');
   freshBackend();const expected={schema_version:1,kind:'managed_drain_admitted',nonce:this.nonce,request_id:request.request_id,receipt_sha256:hash(raw),status:receipt.status};
   const admission=new Promise<void>((resolve,reject)=>{const remaining=deadline-performance.now();if(remaining<=0){reject(new Error('Original drain acknowledgement deadline expired'));return;}const timer=setTimeout(()=>{reject(new Error('Original drain acknowledgement deadline expired'));invalidate();},remaining);pendingState.drainAdmitted={expected,resolve,reject,timer};});
   await this.frames.send({schema_version:1,kind:'managed_drain_proof',nonce:this.nonce,proof_b64:raw.toString('base64')},deadline);await admission;
   if(performance.now()>=deadline)throw new Error('Original drain admission completed late');
   const pending=this.pendingDrain;if(!pending)throw new Error('Original shutdown budget already expired');clearTimeout(pending.timer);this.pendingDrain=null;
   if(receipt.status!=='managed_scopes_drained'){pending.reject(new Error('Backend retains uncovered or active writers'));return;}
   this.drainReceipt=receipt;admittedDrain={receipt,request,receiptHash:hash(canonical(receipt)),requestHash:hash(canonical(request)),deadline,outerDeadline:this.drainDeadline!};this.exitConfirmation=new Promise<void>((resolve,reject)=>{this.exitResolve=resolve;this.exitReject=reject;});void this.exitConfirmation.catch(()=>{});
   // Admission does not renew the shutdown window. Even silent EOF/exit/ACK
   // must refuse at the same original effective drain bound.
   exitTimer=setTimeout(invalidate,Math.max(0,deadline-performance.now()));
   try{cleanDrainCurrent();pending.resolve(receipt);}catch(error){pending.reject(error as Error);throw error;}
  })().catch(invalidate);continue;
 }
 if(cpuExecuted||this.drainRequest)throw new Error('CPU request replay or closed writer admission');cpuExecuted=true;
 fields(request,['schema_version','kind','challenge','request_id','nonce','epoch','binding_sha256','backend_claim_sha256','workspace_id','project_id','plan_sha256']);
 if(request.schema_version!==1||request.kind!=='cpu_execution_request'||!HEX64.test(request.challenge)||!HEX32.test(request.request_id)||request.nonce!==this.nonce||request.epoch!==epoch||request.binding_sha256!==hash(canonical(this.binding))||request.backend_claim_sha256!==this.backendClaimHash||!HEX32.test(request.workspace_id)||!HEX32.test(request.project_id)||!HEX64.test(request.plan_sha256))throw new Error('Foreign CPU execution request');
 freshBackend();const response=backendReply('cpu_execution_completed',performance.now()+210000);await frames.send(request);
 void (async()=>{const completedRaw=await response,completed=parsePrivateDocument(completedRaw,32768).value;
  fields(completed,['schema_version','kind','request','backend_proof','output_path','output_sha256','semantic_output','worker_pid','runtime_source_sha256']);
  if(completed.schema_version!==1||completed.kind!=='cpu_execution_completed'||canonical(completed.request)!==canonical(request)||canonical(completed.backend_proof)!==canonical(claim)||!HEX64.test(completed.output_sha256)||!HEX64.test(completed.runtime_source_sha256)||!Number.isSafeInteger(completed.worker_pid)||completed.worker_pid<1)throw new Error('CPU execution completion differs');
  freshBackend();await this.frames.send({schema_version:1,kind:'cpu_execution_proof',nonce:this.nonce,proof_b64:completedRaw.toString('base64')});
 })().catch(invalidate);
 }})().catch(invalidate);
 }
 async prepareBackendDrain(proc:ChildProcess,budgetMs:number):Promise<any>{
  const deadline=performance.now()+budgetMs;
  if(!this.writer||proc!==this.backend||!this.backendProof||this.pendingDrain||this.drainRequest||this.drainDeadline!==null||!Number.isSafeInteger(budgetMs)||budgetMs<1||budgetMs>4000)throw new Error('Original enrolled backend drain capability is unavailable');
  this.drainDeadline=deadline;
  validateBinding(this.root,this.nonce,this.binding,this.mainProcess);const id=randomBytes(16).toString('hex');
  const result=new Promise<any>((resolve,reject)=>{const remaining=deadline-performance.now();if(remaining<=0){reject(new Error('Original managed shutdown budget expired'));return;}const timer=setTimeout(()=>{if(this.pendingDrain?.id===id)this.pendingDrain=null;reject(new Error('Original managed shutdown budget expired'));},remaining);this.pendingDrain={id,deadline,resolve,reject,timer};});
  void this.frames.send({schema_version:1,kind:'main_drain_request',nonce:this.nonce,epoch:this.backendEpoch,binding_sha256:hash(canonical(this.binding)),backend_claim_sha256:this.backendClaimHash,request_id:id,budget_ms:Math.max(1,Math.floor(deadline-performance.now()))},deadline).catch(error=>this.pendingDrain?.reject(error));
  return result;
 }
 async confirmBackendExit(proc:ChildProcess):Promise<void>{if(proc!==this.backend||!this.exitConfirmation||!this.backendExitFrame)throw new Error('Original backend clean exit is unobserved');await this.exitConfirmation;}
 refuse(){this.frames.channel.destroy();}
}

function validateOriginalRoot(root:string):any {
 const owner=document(path.join(root,'.global-migration-owner.json')).value,st=fs.lstatSync(root);
 const scopes={ledger:'jobs/ledger.sqlite3',leases:'resource_leases.sqlite3',profiles:'compute_profiles.json',accounts:'auth/accounts.sqlite',context:'projects/.context.sqlite3',local_journals:'local_jobs',remote_journals:'remote_jobs'};
 if(owner.schema_version!==1||typeof owner.installation_id!=='string'||!HEX32.test(owner.installation_id)||owner.root_identity?.path!==root||owner.root_identity?.device!==st.dev||owner.root_identity?.inode!==st.ino||canonical(owner.scopes)!==canonical(scopes))throw new Error('Original installation root identity differs');return owner;
}
function validateBinding(root:string,nonce:string,binding:any,main:any):void {
 const owner=validateOriginalRoot(root);
 const active=document(path.join(root,'application-active.json')).value,database=document(path.join(root,'global-active.json')).value;
 if(active.schema_version!==1||active.installation_id!==owner.installation_id||active.update_id!==binding.update_id||active.application_generation!==binding.application_generation||canonical(active.database_pointer)!==canonical(binding.database_pointer)||canonical(database)!==canonical(binding.database_pointer)||binding.installation_id!==owner.installation_id||binding.database_generation_path!==path.join(root,'.global-generations',database.generation_id))throw new Error('Committed application/database pair differs');
 if(fs.existsSync(path.join(root,'application-update-pending.json')))throw new Error('Application update requires recovery');
 const pointer=document(path.join(root,'application-launch-lease.json')).value,journal=document(path.join(root,'.application-launches',nonce,'journal.json'));
 if(pointer.nonce!==nonce||pointer.installation_id!==owner.installation_id||pointer.revision!==journal.value.revision||pointer.record_sha256!==hash(journal.canonical)||journal.value.nonce!==nonce||!['starting','ready'].includes(journal.value.state)||journal.value.supervisor?.pid!==process.ppid||journal.value.process?.pid!==process.pid||canonical(journal.value.binding)!==canonical(binding)||canonical(journal.value.process)!==canonical(main))throw new Error('Original controller/main ownership differs');
 const application=path.join(root,'.application-generations',binding.application_generation,'application'),manifestRaw=stable(path.join(application,'portable-application.json'),APPLICATION_MANIFEST_LIMIT) as Buffer;
 if(hash(manifestRaw)!==binding.application_manifest_sha256)throw new Error('Committed application manifest differs');const manifest=parsePrivateDocument(manifestRaw,APPLICATION_MANIFEST_LIMIT).value;
 if(!Array.isArray(manifest.files)||manifest.files.length>APPLICATION_MEMBER_LIMIT||typeof manifest.entrypoint!=='string'||binding.executable!==path.join(application,manifest.entrypoint))throw new Error('Committed application entrypoint differs');
 const entry=manifest.files.filter((row:any)=>row.path===manifest.entrypoint&&row.executable===true&&row.sha256===binding.executable_sha256);if(entry.length!==1)throw new Error('Committed application executable differs');stable(binding.executable,1024**3,entry[0]);
 if(process.execPath!==binding.executable&&process.argv[1]!==binding.executable)throw new Error('This process is not the committed application executable');
}

export async function authenticateMainLaunch(env:NodeJS.ProcessEnv=process.env):Promise<OwnedApplicationLaunch|null> {
 const configured=env.VISION_AI_STUDIO_USER_DATA_DIR,partial=CONTEXT.some(key=>env[key]!==undefined);
 if(!configured){if(partial)throw new Error('Partial owned main launch context has no root');return null;}
 if(!path.isAbsolute(configured)||path.resolve(configured)!==configured)throw new Error('Owned main root must be canonical and absolute');unlinked(configured);
 const owner=path.join(configured,'.global-migration-owner.json');unlinked(owner);
 for(const name of ['application-active.json','application-update-pending.json','application-launch-lease.json','.application-launches','application-database-ownership.lock'])unlinked(path.join(configured,name));
 if(!fs.existsSync(owner)){if(partial||['application-active.json','application-update-pending.json','application-launch-lease.json','.application-launches','application-database-ownership.lock'].some(name=>fs.existsSync(path.join(configured,name))))throw new Error('Owned main context has no original root');return null;}
 if(!partial){if(fs.existsSync(path.join(configured,'application-active.json'))||['application-launch-lease.json','.application-launches','application-database-ownership.lock'].some(name=>fs.existsSync(path.join(configured,name))))throw new Error('Owned current application requires its private descriptor');validateOriginalRoot(configured);return null;}
 validateOriginalRoot(configured);
 const fd=env.VISION_APPLICATION_LAUNCH_FD,nonce=env.VISION_APPLICATION_LAUNCH_NONCE;
 if(env.VISION_APPLICATION_BACKEND_FD!==undefined||!fd||! /^[1-9]\d*$/.test(fd)||Number(fd)<3||Number(fd)>8192||!nonce||!HEX32.test(nonce)||!HEX32.test(env.VISION_APPLICATION_GENERATION||'')||!env.VISION_APPLICATION_DATABASE_GENERATION)throw new Error('Incomplete owned main launch context');
 const descriptor=fs.fstatSync(Number(fd),{bigint:true});if(process.platform==='win32'||!descriptor.isSocket())throw new Error('Owned main descriptor must be a POSIX stream socket');
 const channel=new net.Socket({fd:Number(fd),readable:true,writable:true}),frames=new Frames(channel);
 try{if(channel.remoteAddress||channel.localAddress)throw new Error('Owned main descriptor cannot be an Internet socket');const frame=parsePrivateDocument(await frames.read()).value;fields(frame,['schema_version','kind','challenge','nonce','binding','process','transport',...('writer'in frame?['writer']:[])]);identity(frame.process);
 if('writer'in frame){fields(frame.writer,['writer_id','registration_sha256','registration_registry_sha256']);if(!HEX32.test(frame.writer.writer_id)||!HEX64.test(frame.writer.registration_sha256)||!HEX64.test(frame.writer.registration_registry_sha256))throw new Error('Original backend writer registration differs');const journal=document(path.join(configured,'.application-launches',nonce,'journal.json')).value;
 if(journal.protocol_version!==4||journal.writer_drain?.phase!=='enrolled'||['writer_id','registration_sha256','registration_registry_sha256'].some(k=>journal.writer_drain[k]!==frame.writer[k]))throw new Error('Original writer journal registration differs');}
 fields(frame.transport,['device','inode','family','type','anonymous']);if(frame.transport.family!=='AF_UNIX'||frame.transport.type!=='SOCK_STREAM'||frame.transport.anonymous!==true||typeof frame.transport.device!=='string'||typeof frame.transport.inode!=='string'||!/^(0|[1-9]\d{0,19})$/.test(frame.transport.device)||!/^(0|[1-9]\d{0,19})$/.test(frame.transport.inode)||frame.transport.device!==BigInt.asUintN(64,descriptor.dev).toString()||frame.transport.inode!==descriptor.ino.toString())throw new Error('Original controller anonymous descriptor identity differs');
 if(frame.schema_version!==1||frame.kind!=='main_challenge'||!HEX64.test(frame.challenge)||frame.nonce!==nonce||frame.process.pid!==process.pid||frame.binding.application_generation!==env.VISION_APPLICATION_GENERATION||frame.binding.database_generation_path!==env.VISION_APPLICATION_DATABASE_GENERATION)throw new Error('Main private challenge differs');
 validateBinding(configured,nonce,frame.binding,frame.process);frames.assertEmpty();await frames.send({schema_version:1,kind:'main_claim',challenge:frame.challenge,nonce,binding_sha256:hash(canonical(frame.binding)),pid:process.pid});
 const admitted=parsePrivateDocument(await frames.read()).value;const expected={schema_version:1,kind:'main_admitted',challenge:frame.challenge,nonce,binding_sha256:hash(canonical(frame.binding)),pid:process.pid};if(canonical(admitted)!==canonical(expected))throw new Error('Original controller admission differs');frames.assertEmpty();
 return new OwnedApplicationLaunch(configured,nonce,frame.binding,frame.process,frame.challenge,frames,frame.writer);
 }catch(error){channel.destroy();throw error;}
}
