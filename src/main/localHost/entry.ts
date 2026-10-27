/** Windowless Electron entrypoint. stdout is reserved for bounded private parent/child frames. */
import path from 'node:path';
import {Console} from 'node:console';
import type {Readable,Writable} from 'node:stream';
import {FrameDecoder,HostError,MAX_FRAME_BYTES,parseRequest,type HostRequest,type HostResponse} from './protocol';
import {LocalHostService,prepareStateDirectory} from './service';
import {electronEncryptor} from '../assistant/keyStore';

export function runProtocol(input:Readable,output:Writable,service:Pick<LocalHostService,'handle'|'dispose'>,onClose:(cause?:HostError)=>void,
 limits:{maxBytes?:number;maxOutputBytes?:number;maxConcurrent?:number;maxControlConcurrent?:number}={}):()=>void {
 const decoder=new FrameDecoder(limits.maxBytes),pending=new Set<string>();let closed=false,ordinaryPending=0,controlPending=0;
 // A bounded reserved lane keeps revocation reachable while ordinary dialogs/provider calls are saturated.
 const controls=new Set(['session.dispose','session.disposeAll','assistant.cancel','assistant.retireProject','assistant.chatgpt.cancel','assistant.chatgpt.signOut','assistant.removeKey','assistant.removeProvider','assistant.endRun']);
 const close=(cause?:HostError)=>{if(closed)return;closed=true;input.removeListener('data',data);void Promise.resolve(service.dispose()).then(()=>onClose(cause),()=>onClose(cause??new HostError('operation_refused')));};
 const reply=(value:HostResponse)=>{if(closed)return;let line=JSON.stringify(value);if(Buffer.byteLength(line)>(limits.maxOutputBytes??MAX_FRAME_BYTES))line=JSON.stringify({id:value.id,ok:false,error:{code:'response_too_large',message:'response_too_large'}});output.write(line+'\n');};
 const dispatch=(request:HostRequest)=>{const id=typeof request?.id==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(request.id)?request.id:'invalid';
  if(pending.has(id)){reply({id,ok:false,error:{code:'duplicate_request',message:'duplicate_request'}});return;}
  const control=controls.has(request?.method);
  if(control)try{parseRequest(request);}catch(cause){const code=cause instanceof HostError?cause.code:'invalid_request';reply({id,ok:false,error:{code,message:code}});return;}
  if(control?controlPending>=(limits.maxControlConcurrent??8):ordinaryPending>=(limits.maxConcurrent??16)){reply({id,ok:false,error:{code:'too_many_requests',message:'too_many_requests'}});return;}
  pending.add(id);if(control)controlPending++;else ordinaryPending++;
  void service.handle(request).then(reply,()=>reply({id,ok:false,error:{code:'operation_refused',message:'operation_refused'}})).finally(()=>{pending.delete(id);if(control)controlPending--;else ordinaryPending--;});
 };
 const data=(chunk:Buffer|string)=>{try{for(const request of decoder.push(Buffer.isBuffer(chunk)?chunk:Buffer.from(chunk)))dispatch(request);}catch(cause){close(cause instanceof HostError?cause:new HostError('invalid_request'));}};
 input.on('data',data);input.once('end',()=>{try{decoder.finish();close();}catch(cause){close(cause as HostError);}});input.once('error',()=>close(new HostError('input_closed')));output.once('error',()=>close(new HostError('output_closed')));return ()=>close();
}
export async function main(argv:string[]=process.argv.slice(2)):Promise<void> {
 // Redirect application diagnostics before importing/initializing native services.
 global.console=new Console({stdout:process.stderr,stderr:process.stderr});
 const readArg=(name:string)=>{const index=argv.indexOf(name);return index<0?undefined:argv[index+1];};
 const directory=readArg('--state-dir'),origin=readArg('--origin')??'https://localhost:8443';
 if(!directory||!path.isAbsolute(directory))throw new HostError('invalid_configuration');
 prepareStateDirectory(directory);prepareStateDirectory(path.join(directory,'electron'));
 // Electron's own caches/lease live only in the dedicated web helper home.
 const {app,dialog,shell,safeStorage}=await import('electron');
 app.setPath('userData',path.join(directory,'electron'));app.setName('Modu Vision Local Host');
 await app.whenReady();
 const ownerLock=app.requestSingleInstanceLock();if(!ownerLock)throw new HostError('helper_already_running');
 const encryptor=electronEncryptor(safeStorage);
 const stateRoot=path.dirname(directory);
 const service=new LocalHostService({stateDirectory:directory,origin,encryptor,canRecover:()=>app.hasSingleInstanceLock(),
  protectedPaths:[path.join(stateRoot,'auth'),path.join(stateRoot,'tls'),...argv.flatMap((arg,index)=>arg==='--protected-path'&&argv[index+1]?[argv[index+1]]:[])],
  distribution:{appVersion:app.getVersion(),packaged:app.isPackaged,appPath:process.platform==='darwin'?path.resolve(process.execPath,'../../..'):app.getAppPath(),
   executablePath:process.execPath,platform:process.platform,arch:process.arch,resourcesPath:process.resourcesPath},
  native:{approve:async input=>{const result=await dialog.showMessageBox({type:'question',title:'로컬 웹 기능 허용',message:'이 웹 계정의 네이티브 기능 사용을 허용하시겠습니까?',
    detail:`주소: ${input.origin}\n계정: ${input.accountLabel}\n\n파일 선택과 도우미 보호 저장소를 사용할 수 있습니다. 설치·복원 작업은 별도로 확인합니다.`,buttons:['취소','허용'],defaultId:0,cancelId:0,noLink:true});return result.response===1;},
   confirmLinkedSource:async input=>{const result=await dialog.showMessageBox({type:'question',title:'링크 대상 복사 확인',message:'검토한 링크 대상만 프로젝트 복사본으로 만드시겠습니까?',
    detail:`웹: ${origin}\n프로젝트: ${input.scope.project_id}\n작업공간: ${input.scope.workspace_id}\n복사 위치: ${input.project_dir}\n원본: ${input.source}\n\n${input.targets.map(target=>`${target.link} → ${target.path} (${target.kind})`).join('\n')}\n\n파일 ${input.file_count}개 · ${input.total_bytes} bytes\n계획 SHA-256: ${input.plan_sha256}\n\n복사한 내용은 이 프로젝트의 다른 멤버도 읽을 수 있습니다. 원본은 읽기만 합니다. 복사 뒤 ‘이 복사본으로 작업 시작’을 직접 눌러야 프로젝트 원본이 바뀝니다.`,
    buttons:['취소','검토한 대상 복사'],defaultId:0,cancelId:0,noLink:true});return result.response===1;},
   choose:options=>dialog.showOpenDialog(options),openPath:value=>shell.openPath(value),openExternal:value=>shell.openExternal(value)}});
 const stop=runProtocol(process.stdin,process.stdout,service,cause=>{if(cause)console.error(`Local host protocol stopped: ${cause.code}`);app.quit();});
 let quitSettled=false;app.on('before-quit',event=>{if(quitSettled)return;event.preventDefault();stop();void service.dispose().then(()=>{quitSettled=true;app.quit();},()=>{quitSettled=true;app.quit();});});process.once('SIGTERM',()=>app.quit());process.once('SIGINT',()=>app.quit());
 // Native adapters have been bound after app readiness. The parent verifies a framed round trip before server READY.
 process.stdout.write(JSON.stringify({event:'ready',protocol:1,platform:process.platform,pid:process.pid})+'\n');
}
// Electron's file loader keeps require.main at its bootstrap module, unlike Node.
const directElectronEntry=Boolean(process.versions.electron)&&process.type==='browser'&&typeof process.argv[1]==='string'&&path.resolve(process.argv[1])===__filename;
if(require.main===module||directElectronEntry)void main().catch(()=>{process.stderr.write('Local host startup refused. Check the dedicated helper home and native prerequisites.\n');process.exitCode=1;process.exit(1);});
