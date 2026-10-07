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

class Frames {
 private buffer=Buffer.alloc(0);private queue:Buffer[]=[];private waiting:{resolve:(raw:Buffer)=>void;reject:(error:Error)=>void;timer:NodeJS.Timeout|null}|null=null;private failure:Error|null=null;
 constructor(readonly channel:Duplex){channel.on('data',(chunk:Buffer)=>{if(this.failure)return;this.buffer=Buffer.concat([this.buffer,chunk]);while(this.buffer.includes(10)){const at=this.buffer.indexOf(10);if(at+1>LIMIT){this.fail(new Error('Private frame exceeds its bound'));return;}const raw=this.buffer.subarray(0,at);this.buffer=this.buffer.subarray(at+1);if(this.waiting){const pending=this.waiting;this.waiting=null;if(pending.timer)clearTimeout(pending.timer);pending.resolve(raw);}else{this.queue.push(raw);if(this.queue.length>2){this.fail(new Error('Unexpected private frame replay'));return;}}}if(this.buffer.length>=LIMIT)this.fail(new Error('Private frame exceeds its bound'));});channel.on('error',(e)=>this.fail(e));channel.on('end',()=>this.fail(new Error('Private descriptor ended')));channel.on('close',()=>this.fail(new Error('Private descriptor closed')));}
 fail(error:Error){this.failure=error;if(this.waiting){const pending=this.waiting;this.waiting=null;if(pending.timer)clearTimeout(pending.timer);pending.reject(error);}}
 async read(timeout:number|null=210000):Promise<Buffer>{if(this.failure)throw this.failure;if(this.queue.length)return this.queue.shift()!;if(this.waiting)throw new Error('Concurrent private frame read');return new Promise((resolve,reject)=>{const timer=timeout===null?null:setTimeout(()=>{this.waiting=null;reject(new Error('Private descriptor timeout'));},timeout);this.waiting={resolve,reject,timer};});}
 async send(value:any):Promise<void>{if(this.failure)throw this.failure;const raw=Buffer.from(canonical(value)+'\n');if(raw.length>LIMIT)throw new Error('Private frame exceeds its bound');await new Promise<void>((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Private send timeout')),10000);this.channel.write(raw,error=>{clearTimeout(timer);error?reject(error):resolve();});});}
 assertEmpty(){if(this.failure)throw this.failure;if(this.queue.length||this.buffer.length)throw new Error('Unexpected private frame replay');}
}

export class OwnedApplicationLaunch {
 readonly projects:string;readonly auth:string;private usedBackend=false;
 constructor(readonly root:string,readonly nonce:string,readonly binding:any,readonly mainProcess:any,private challenge:string,private frames:Frames){this.projects=path.join(root,'projects');this.auth=path.join(root,'auth');}
 private current():void {validateBinding(this.root,this.nonce,this.binding,this.mainProcess);this.frames.assertEmpty();}
 executable(file:string):{sha256:string;build:string|null} {this.current();const application=path.join(this.root,'.application-generations',this.binding.application_generation,'application');const manifest=parsePrivateDocument(stable(path.join(application,'portable-application.json'),APPLICATION_MANIFEST_LIMIT) as Buffer,APPLICATION_MANIFEST_LIMIT).value;
 const relative=path.relative(application,file).split(path.sep).join('/');if(path.isAbsolute(relative)||relative.startsWith('../')||relative==='..')throw new Error('Backend executable escapes committed application');
 const rows=manifest.files.filter((row:any)=>row.path===relative&&row.executable===true);if(rows.length!==1)throw new Error('Backend executable is not a committed row');stable(file,1024**3,rows[0]);
 const receiptPath=path.join(path.dirname(file),'backend-release.json');const receiptRelative=path.relative(application,receiptPath).split(path.sep).join('/');const receiptRows=manifest.files.filter((row:any)=>row.path===receiptRelative);
 if(receiptRows.length!==1)return {sha256:rows[0].sha256,build:null};stable(receiptPath,8*1024**2,receiptRows[0]);const receipt=parsePrivateDocument(stable(receiptPath,8*1024**2) as Buffer,8*1024**2).value;
 if(receipt.schema_version!==1||receipt.executable!==path.basename(file)||receipt.executable_sha256!==rows[0].sha256||!HEX64.test(receipt.inventory?.build_identity_sha256))throw new Error('Committed backend receipt differs');return {sha256:rows[0].sha256,build:receipt.inventory.build_identity_sha256};}
 backendEnvironment(env:NodeJS.ProcessEnv):NodeJS.ProcessEnv {const result={...env};for(const key of CONTEXT)delete result[key];return {...result,VISION_AI_STUDIO_USER_DATA_DIR:this.root,VISION_APPLICATION_LAUNCH_NONCE:this.nonce,VISION_APPLICATION_GENERATION:this.binding.application_generation,VISION_APPLICATION_DATABASE_GENERATION:this.binding.database_generation_path,VISION_APPLICATION_BACKEND_FD:'3'};}
 async bindBackend(proc:ChildProcess,executable:string,build:string|null):Promise<void>{if(this.usedBackend)throw new Error('Owned backend restart requires a new reconciled controller epoch');this.usedBackend=true;this.current();const artifact=this.executable(executable);if(artifact.build!==build||!Number.isSafeInteger(proc.pid)||!proc.stdio[3])throw new Error('Owned backend executable or private descriptor differs');
 const channel=proc.stdio[3] as Duplex,frames=new Frames(channel),challenge=randomBytes(32).toString('hex'),epoch=randomBytes(16).toString('hex');
 await frames.send({schema_version:1,kind:'backend_challenge',challenge,epoch,nonce:this.nonce,binding:this.binding,main_process:this.mainProcess,backend_pid:proc.pid,backend_executable:executable,backend_build_identity_sha256:build});
 const claimRaw=await frames.read(),readyRaw=await frames.read(),claim=parsePrivateDocument(claimRaw).value,ready=parsePrivateDocument(readyRaw).value;
 const names=['schema_version','kind','challenge','epoch','nonce','binding_sha256','process','executable','executable_sha256','build_identity_sha256','frozen'];fields(claim,names);fields(ready,names);identity(claim.process);identity(ready.process);
 if(claim.schema_version!==1||ready.schema_version!==1||claim.kind!=='backend_claim'||ready.kind!=='backend_ready'||canonical({...claim,kind:'backend_ready'})!==canonical(ready)||ready.challenge!==challenge||ready.epoch!==epoch||ready.nonce!==this.nonce||ready.binding_sha256!==hash(canonical(this.binding))||ready.process.pid!==proc.pid||ready.executable!==executable||ready.executable_sha256!==artifact.sha256||ready.build_identity_sha256!==build||typeof ready.frozen!=='boolean'||ready.frozen!==(build!==null))throw new Error('Backend private readiness binding differs');
 this.current();frames.assertEmpty();await this.frames.send({schema_version:1,kind:'backend_proof',challenge:this.challenge,nonce:this.nonce,claim_b64:claimRaw.toString('base64'),ready_b64:readyRaw.toString('base64')});
 // One closed controller-origin execution request may follow readiness. Idle
 // waiting has no timeout; unsolicited backend data still invalidates ownership.
 let executing=false;const invalidate=()=>this.frames.channel.destroy();proc.once('exit',invalidate);channel.once('close',invalidate);channel.once('end',invalidate);channel.once('error',invalidate);channel.on('data',()=>{if(!executing)invalidate();});
 void (async()=>{const request=parsePrivateDocument(await this.frames.read(null)).value;
 fields(request,['schema_version','kind','challenge','request_id','nonce','epoch','binding_sha256','backend_claim_sha256','workspace_id','project_id','plan_sha256']);
 if(request.schema_version!==1||request.kind!=='cpu_execution_request'||!HEX64.test(request.challenge)||!HEX32.test(request.request_id)||request.nonce!==this.nonce||request.epoch!==epoch||request.binding_sha256!==hash(canonical(this.binding))||request.backend_claim_sha256!==hash(parsePrivateDocument(claimRaw).canonical)||!HEX32.test(request.workspace_id)||!HEX32.test(request.project_id)||!HEX64.test(request.plan_sha256))throw new Error('Foreign CPU execution request');
 validateBinding(this.root,this.nonce,this.binding,this.mainProcess);this.frames.assertEmpty();frames.assertEmpty();executing=true;
 await frames.send(request);const completedRaw=await frames.read();const completed=parsePrivateDocument(completedRaw,32768).value;
 fields(completed,['schema_version','kind','request','backend_proof','output_path','output_sha256','semantic_output','worker_pid','runtime_source_sha256']);
 if(completed.schema_version!==1||completed.kind!=='cpu_execution_completed'||canonical(completed.request)!==canonical(request)||canonical(completed.backend_proof)!==canonical(claim)||!HEX64.test(completed.output_sha256)||!HEX64.test(completed.runtime_source_sha256)||!Number.isSafeInteger(completed.worker_pid)||completed.worker_pid<1)throw new Error('CPU execution completion differs');
 frames.assertEmpty();validateBinding(this.root,this.nonce,this.binding,this.mainProcess);
 await this.frames.send({schema_version:1,kind:'cpu_execution_proof',nonce:this.nonce,proof_b64:completedRaw.toString('base64')});executing=false;
 await this.frames.read(null);throw new Error('CPU request replay requires recovery');
 })().catch(invalidate);
 }
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
 try{if(channel.remoteAddress||channel.localAddress)throw new Error('Owned main descriptor cannot be an Internet socket');const frame=parsePrivateDocument(await frames.read()).value;fields(frame,['schema_version','kind','challenge','nonce','binding','process','transport']);identity(frame.process);
 fields(frame.transport,['device','inode','family','type','anonymous']);if(frame.transport.family!=='AF_UNIX'||frame.transport.type!=='SOCK_STREAM'||frame.transport.anonymous!==true||typeof frame.transport.device!=='string'||typeof frame.transport.inode!=='string'||!/^(0|[1-9]\d{0,19})$/.test(frame.transport.device)||!/^(0|[1-9]\d{0,19})$/.test(frame.transport.inode)||frame.transport.device!==BigInt.asUintN(64,descriptor.dev).toString()||frame.transport.inode!==descriptor.ino.toString())throw new Error('Original controller anonymous descriptor identity differs');
 if(frame.schema_version!==1||frame.kind!=='main_challenge'||!HEX64.test(frame.challenge)||frame.nonce!==nonce||frame.process.pid!==process.pid||frame.binding.application_generation!==env.VISION_APPLICATION_GENERATION||frame.binding.database_generation_path!==env.VISION_APPLICATION_DATABASE_GENERATION)throw new Error('Main private challenge differs');
 validateBinding(configured,nonce,frame.binding,frame.process);frames.assertEmpty();await frames.send({schema_version:1,kind:'main_claim',challenge:frame.challenge,nonce,binding_sha256:hash(canonical(frame.binding)),pid:process.pid});
 const admitted=parsePrivateDocument(await frames.read()).value;const expected={schema_version:1,kind:'main_admitted',challenge:frame.challenge,nonce,binding_sha256:hash(canonical(frame.binding)),pid:process.pid};if(canonical(admitted)!==canonical(expected))throw new Error('Original controller admission differs');frames.assertEmpty();
 return new OwnedApplicationLaunch(configured,nonce,frame.binding,frame.process,frame.challenge,frames);
 }catch(error){channel.destroy();throw error;}
}
