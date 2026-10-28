/** Private parent/child protocol. Never accepts arbitrary main-process method names. */
export const MAX_FRAME_BYTES=8*1024*1024;
export const METHODS=new Set(['host.approve','host.approveLinkedSource','host.approveProjectLocation','dialog.folder','dialog.file','shell.open','session.dispose','session.disposeAll',
 'assistant.retireProject',
 'assistant.getState','assistant.saveProvider','assistant.removeKey','assistant.removeProvider','assistant.setActive','assistant.listModels','assistant.send','assistant.cancel','assistant.imageIntent','assistant.beginRun','assistant.endRun',
 ...['begin','acknowledgePlanUsage','resumeUsage','recover','cancel','selectAccount','selectModel','signOut'].map(k=>`assistant.chatgpt.${k}`),
 ...['status','configure','check','download','verifyOffline','selectPortableHome','inspectPortable','previewPortable','applyPortable','recoverPortable','launchPortable','inspectPortableLaunch'].map(k=>`updates.${k}`)]);
export interface HostRequest {id:string;actor_id:string;session_id:string;tab_id:string;method:string;args:Record<string,any>}
export type HostResponse={id:string;ok:true;result:unknown}|{id:string;ok:false;error:{code:string;message:string}};
export class HostError extends Error {constructor(readonly code:string,message:string=code){super(message);}}
const identity=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(v);
export function parseRequest(raw:unknown):HostRequest {
 const v=raw as HostRequest;
 if(!v||typeof v!=='object'||Array.isArray(v)||!identity(v.id)||!identity(v.actor_id)||!identity(v.session_id)||!identity(v.tab_id)
  ||typeof v.method!=='string'||!v.args||typeof v.args!=='object'||Array.isArray(v.args)
  ||Object.keys(v).some(k=>!['id','actor_id','session_id','tab_id','method','args'].includes(k)))throw new HostError('invalid_request');
 if(!METHODS.has(v.method))throw new HostError('unknown_method');return v;
}
export class FrameDecoder {
 private chunks:Buffer[]=[];private bytes=0;private failed=false;
 constructor(private readonly maxBytes=MAX_FRAME_BYTES){}
 push(chunk:Buffer):HostRequest[]{
  if(this.failed)throw new HostError('frame_too_large');const result:HostRequest[]=[];let offset=0;
  while(offset<chunk.length){const end=chunk.indexOf(10,offset),stop=end<0?chunk.length:end,part=chunk.subarray(offset,stop);
   if(this.bytes+part.length>this.maxBytes){this.failed=true;this.chunks=[];this.bytes=0;throw new HostError('frame_too_large');}
   if(part.length){this.chunks.push(part);this.bytes+=part.length;}
   if(end<0)break;
   const line=Buffer.concat(this.chunks,this.bytes);this.chunks=[];this.bytes=0;
   try{result.push(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(line)) as HostRequest);}catch(error){if(error instanceof HostError)throw error;throw new HostError('invalid_request');}
   offset=end+1;
  }return result;
 }
 finish():void {if(this.bytes)throw new HostError('truncated_frame');}
}
