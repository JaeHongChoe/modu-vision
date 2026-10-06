import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {UpdateRelease} from '../types/electron';

export interface TrustAuthority {
  schema_version:1; publisher:string; keys:Record<string,string>; revoked_key_ids:string[];
  allowed_origins:string[]; compatibility:Record<string,number>;
}
export interface ReleaseTarget {platform:string;arch:string;channel:string;current_version:string;origin:string}
interface Artifact {path:string;kind:'installer'|'runtime_pack';sha256:string;size:number}
export interface TrustedRelease extends UpdateRelease {publisher:string;compatibility:Record<string,number>;artifacts:Artifact[]}

export function canonical(value:unknown):string {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  const object=value as Record<string,unknown>;
  return '{'+Object.keys(object).sort().map(key=>JSON.stringify(key)+':'+canonical(object[key])).join(',')+'}';
}
function fields(value:unknown,expected:string[]):asserts value is Record<string,any> {
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==expected.sort().join('|'))
    throw new Error('Release trust document fields are invalid');
}
function base64(value:unknown):Buffer {
  if(typeof value!=='string'||!value.length||value.length>90000||! /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))throw new Error('Invalid release signature encoding');
  const bytes=Buffer.from(value,'base64');if(bytes.toString('base64')!==value)throw new Error('Noncanonical release signature encoding');return bytes;
}
function version(value:string):[string[],string[]] {
  const match=typeof value==='string'&&value.length<=128&&/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/.exec(value);
  if(!match)throw new Error('Release version is invalid');
  const prerelease=match[4]?.split('.')||[];
  if(prerelease.some(part=>/^0\d+$/.test(part)))throw new Error('Release version is invalid');
  return [match.slice(1,4),prerelease];
}
const compareNumber=(a:string,b:string)=>a.length-b.length||(a===b?0:a>b?1:-1);
function compareVersions(a:string,b:string):number {
  const [x,p]=version(a),[y,q]=version(b);
  for(let i=0;i<3;i++){const d=compareNumber(x[i],y[i]);if(d)return d;}
  if(!p.length||!q.length)return p.length===q.length?0:p.length?-1:1;
  for(let i=0;i<Math.max(p.length,q.length);i++){
    if(p[i]===q[i])continue;if(p[i]===undefined)return -1;if(q[i]===undefined)return 1;
    const n=/^\d+$/.test(p[i]),m=/^\d+$/.test(q[i]);
    if(n&&m)return compareNumber(p[i],q[i]);if(n!==m)return n?-1:1;return p[i]>q[i]?1:-1;
  }return 0;
}
function secureOrigin(value:string):string {
  const url=new URL(value);
  if(url.protocol!=='https:'||url.username||url.password||url.hash)throw new Error('Release authority requires HTTPS');
  return url.origin;
}
function authority(value:unknown):asserts value is TrustAuthority {
  fields(value,['schema_version','publisher','keys','revoked_key_ids','allowed_origins','compatibility']);
  if(value.schema_version!==1||typeof value.publisher!=='string'||!value.publisher.length||value.publisher.length>256
    ||!value.keys||typeof value.keys!=='object'||Array.isArray(value.keys)||!Object.keys(value.keys).length
    ||Object.keys(value.keys).length>32||!Array.isArray(value.revoked_key_ids)||!value.revoked_key_ids.every((s:any)=>typeof s==='string')
    ||!Array.isArray(value.allowed_origins)||!value.allowed_origins.length||value.allowed_origins.length>32
    ||!value.allowed_origins.every((s:any)=>typeof s==='string'&&secureOrigin(s)===s))throw new Error('Pinned release authority is invalid');
  fields(value.compatibility,['api_context','worker','runtime','dataset_index']);
  if(!Object.values(value.compatibility).every(n=>Number.isSafeInteger(n)&&(n as number)>0))throw new Error('Pinned compatibility matrix is invalid');
}
function unlinked(file:string):void {
  for(let current=path.resolve(file);;current=path.dirname(current)){
    if(fs.existsSync(current)&&fs.lstatSync(current).isSymbolicLink())throw new Error('Release trust storage cannot follow a link');
    if(path.dirname(current)===current)break;
  }
}
export function readBoundedStableFile(file:string,limit:number,label='Release document'):Buffer {
  if(!Number.isSafeInteger(limit)||limit<1||limit>65536)throw new Error('Release document bound is invalid');
  unlinked(file);
  const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
  try{
    const before=fs.fstatSync(fd);if(!before.isFile()||before.size>limit)throw new Error(`${label} exceeds its bound`);
    const raw=Buffer.alloc(before.size+1);let total=0,count;
    while((count=fs.readSync(fd,raw,total,raw.length-total,null))>0){total+=count;if(total>before.size)throw new Error(`${label} changed while reading`);}
    unlinked(file);const after=fs.fstatSync(fd),current=fs.lstatSync(file);
    if(total!==before.size||after.size!==before.size||after.mtimeMs!==before.mtimeMs||current.ino!==before.ino||current.dev!==before.dev
      ||!current.isFile()||current.size!==before.size||current.mtimeMs!==before.mtimeMs)throw new Error(`${label} identity changed while reading`);
    return raw.subarray(0,total);
  }finally{fs.closeSync(fd);}
}
export function readTrustAuthority(file:string):TrustAuthority {
  unlinked(file);
  if(!fs.existsSync(file))throw new Error('Provision the pinned release authority in the signed application resources');
  const raw=readBoundedStableFile(file,32768,'Pinned release authority');
  const value:unknown=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));authority(value);return value;
}

export function verifySignedRelease(envelope:unknown,root:unknown,target:ReleaseTarget):TrustedRelease {
  authority(root);fields(envelope,['schema_version','key_id','payload_b64','signature_b64']);
  if(envelope.schema_version!==1||typeof envelope.key_id!=='string'||! /^[A-Za-z0-9_-]{1,80}$/.test(envelope.key_id)
    ||!Object.hasOwn(root.keys,envelope.key_id)||root.revoked_key_ids.includes(envelope.key_id))throw new Error('Release signing key is not pinned or is revoked');
  const raw=base64(envelope.payload_b64),signature=base64(envelope.signature_b64);
  if(raw.length>65536||signature.length!==64)throw new Error('Release signature or manifest size is invalid');
  const text=new TextDecoder('utf-8',{fatal:true}).decode(raw),value:unknown=JSON.parse(text);
  if(canonical(value)!==text)throw new Error('Release payload must use canonical JSON without duplicate fields');
  const key=crypto.createPublicKey({key:base64(root.keys[envelope.key_id]),type:'spki',format:'der'});
  if(key.asymmetricKeyType!=='ed25519'||!crypto.verify(null,raw,key,signature))throw new Error('Release manifest signature did not verify');
  fields(value,['version','channel','platform','arch','url','sha256','size','publisher','compatibility','artifacts']);
  if(value.publisher!==root.publisher)throw new Error('Release publisher differs from the pinned authority');
  if(value.platform!==target.platform||value.arch!==target.arch||value.channel!==target.channel)throw new Error('Release target or channel differs');
  if(compareVersions(value.version,target.current_version)<0)throw new Error('Release downgrade is refused; recovery rollback requires a separate approved record');
  if(value.channel==='stable'&&version(value.version)[1].length)throw new Error('Stable releases cannot contain a prerelease version');
  if(typeof value.url!=='string'||secureOrigin(value.url)!==target.origin||!root.allowed_origins.includes(target.origin))throw new Error('Release origin is not pinned');
  if(canonical(value.compatibility)!==canonical(root.compatibility))throw new Error('Release protocol/schema compatibility differs from the provisioned matrix');
  if(!Array.isArray(value.artifacts)||!value.artifacts.length||value.artifacts.length>32)throw new Error('Release artifact inventory is invalid');
  const paths=new Set<string>();
  for(const artifact of value.artifacts){
    fields(artifact,['path','kind','sha256','size']);
    if(typeof artifact.path!=='string'||! /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(artifact.path)
      ||/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(artifact.path)||artifact.path.endsWith('.')
      ||paths.has(artifact.path.toLowerCase())||!['installer','runtime_pack'].includes(artifact.kind)
      ||! /^[a-f0-9]{64}$/.test(artifact.sha256)||!Number.isSafeInteger(artifact.size)||artifact.size<1||artifact.size>1024**3)
      throw new Error('Release artifact inventory is invalid');
    paths.add(artifact.path.toLowerCase());
  }
  const installers=value.artifacts.filter((a:Artifact)=>a.kind==='installer');
  if(installers.length!==1||installers[0].path!==path.posix.basename(new URL(value.url).pathname)
    ||installers[0].sha256!==value.sha256||installers[0].size!==value.size)throw new Error('Installer differs from the signed pack inventory');
  return value as TrustedRelease;
}

export function verifyOfflineRelease(directory:string,envelope:unknown,root:unknown,target:ReleaseTarget) {
  const release=verifySignedRelease(envelope,root,target);unlinked(directory);
  const actual=fs.readdirSync(directory).sort(),expected=release.artifacts.map(a=>a.path).sort();
  if(canonical(actual)!==canonical(expected))throw new Error('Offline artifact inventory has missing or additional files');
  for(const artifact of release.artifacts){
    const file=path.join(directory,artifact.path);unlinked(file);
    const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
    try{
      const before=fs.fstatSync(fd);if(!before.isFile()||before.size!==artifact.size)throw new Error('Offline artifact size differs');
      const buffer=Buffer.alloc(1024*1024),digest=crypto.createHash('sha256');let count,total=0;
      while((count=fs.readSync(fd,buffer,0,Math.min(buffer.length,artifact.size-total+1),null))>0){
        total+=count;if(total>artifact.size)throw new Error('Offline artifact grew while verifying');
        digest.update(buffer.subarray(0,count));
      }
      unlinked(file);const after=fs.fstatSync(fd),current=fs.lstatSync(file);
      if(digest.digest('hex')!==artifact.sha256||after.size!==before.size||after.mtimeMs!==before.mtimeMs
        ||after.ino!==before.ino||after.dev!==before.dev||!current.isFile()||current.ino!==before.ino
        ||current.dev!==before.dev||current.size!==before.size||current.mtimeMs!==before.mtimeMs)
        throw new Error('Offline artifact checksum or identity changed');
    }finally{fs.closeSync(fd);}
  }
  if(canonical(fs.readdirSync(directory).sort())!==canonical(expected))throw new Error('Offline artifact inventory changed while verifying');
  return {release,artifacts_verified:release.artifacts.length,automatic_installation:false as const,
    native_signature_acceptance:'required' as const,migration_acceptance:'required' as const};
}
