"""Exact production Frames/bindBackend in an inert V8 model; no application/child.

Storage/process validation is deliberately modeled. These are read-loop lifetime
controls, not a claim of original runtime authority, CPU execution, or lease release.
"""
import json
from pathlib import Path
import subprocess

import pytest


CONTROL = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {EventEmitter}=require('node:events'),ts=require('typescript');
const source=fs.readFileSync(process.argv[1],'utf8'),scenario=process.argv[2];
let now=1000,bindingFresh=true,crossArm=false;const ownedTimers=new Set(),timerRecords=new Map();
const exports={},context={exports,module:{exports},require,Buffer,TextDecoder,performance:{now:()=>now},
 setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms);ownedTimers.add(t);timerRecords.set(t,{fn,due:now+ms});return t;},
 clearTimeout:t=>{ownedTimers.delete(t);timerRecords.delete(t);clearTimeout(t);}};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,
 target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText+
 '\nexports.__test={Frames,OwnedApplicationLaunch,canonical,hash,setBindingCheck(fn){validateBinding=fn;}};',context);
const {Frames,OwnedApplicationLaunch,canonical,hash,setBindingCheck}=exports.__test;
setBindingCheck(()=>{if(!bindingFresh)throw new Error('Modeled original binding ended');});
class Channel extends EventEmitter {
 constructor(){super();this.destroyed=false;this.writes=[];this.onWrite=()=>{};}
 write(raw,callback){this.writes.push(Buffer.from(raw));if(this.destroyed){callback(new Error('Modeled original closed channel'));return false;}
  const value=JSON.parse(raw);this.onWrite(value,Buffer.from(raw));callback();return true;}
 receive(value){this.emit('data',Buffer.from(canonical(value)+'\n'));}
 destroy(){if(!this.destroyed){this.destroyed=true;this.emit('close');}return this;}
}
const ticks=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
async function main(){
 const controller=new Channel(),backend=new Channel(),proc=new EventEmitter();
 Object.assign(proc,{pid:31,exitCode:null,signalCode:null,stdio:[null,null,null,backend]});
 const nonce='1'.repeat(32),binding={application_generation:'2'.repeat(32)},mainProcess={pid:21},writer={writer_id:'3'.repeat(32),registration_sha256:'4'.repeat(64)};
 const launch=new OwnedApplicationLaunch('/modeled-original',nonce,binding,mainProcess,'5'.repeat(64),new Frames(controller),writer);
 launch.bindingCurrent=()=>{if(!bindingFresh)throw new Error('Modeled original binding ended');if(crossArm&&scenario==='validation-crosses-deadline')now=5000;};
 launch.current=()=>launch.bindingCurrent();launch.executable=()=>({sha256:'6'.repeat(64),build:null});
 let claim,epoch,challenge,drainRequest,heldDrainAck=null,heldRelayAck=null,lastExitAck=null,exitSent=0,resolveCalls=0;
 const child={pid:41,created_at:1.25,command_sha256:'a'.repeat(64)};
 controller.onWrite=(value,raw)=>{
  if(value.kind==='main_drain_request'){
   drainRequest={schema_version:1,kind:'backend_drain_request',challenge:'7'.repeat(64),request_id:value.request_id,nonce,epoch,
    binding_sha256:hash(canonical(binding)),backend_claim_sha256:hash(canonical(claim)),...writer,closed_registry_sha256:'8'.repeat(64),budget_ms:['shorter-original-drain','late-drain-ack'].includes(scenario)?1000:value.budget_ms};
   queueMicrotask(()=>controller.receive(drainRequest));
  }else if(value.kind==='managed_drain_proof'){
   const proof=Buffer.from(value.proof_b64,'base64');const receipt=JSON.parse(proof);
   const ack={schema_version:1,kind:'managed_drain_admitted',nonce,request_id:drainRequest.request_id,receipt_sha256:hash(proof),status:receipt.status};
   if(scenario==='late-drain-ack')now=2000;
   if(scenario==='pending-drain-ack')heldDrainAck=ack;else queueMicrotask(()=>controller.receive(ack));
  }else if(value.kind==='main_backend_exit'){
   exitSent++;if(scenario==='late-exit-ack')now=5001;
   lastExitAck={schema_version:1,kind:'backend_exit_observed',nonce,request_id:value.request_id,exit_sha256:hash(canonical(value))};
   if(scenario!=='silent-no-ack')queueMicrotask(()=>controller.receive(lastExitAck));
  }else if(value.kind==='main_preflight_request'){
   const req=value.request,ack={schema_version:1,kind:'controller_preflight_reply',nonce,epoch,request_id:req.request_id,
    action:req.action,request_sha256:hash(canonical(req)),payload:req.action==='reserve'?{writer_id:'b'.repeat(32),registration_sha256:'c'.repeat(64)}:{status:req.action==='bind'?'bound':'direct_exited'}};
   if(scenario==='pending-relay')heldRelayAck=ack;else queueMicrotask(()=>controller.receive(ack));
  }
 };
 backend.onWrite=value=>{
  if(value.kind==='backend_challenge'){
   epoch=value.epoch;challenge=value.challenge;claim={schema_version:1,kind:'backend_claim',challenge,epoch,nonce,
    binding_sha256:hash(canonical(binding)),process:{pid:proc.pid,created_at:1.5,command_sha256:'9'.repeat(64)},
    executable:'/modeled-backend',executable_sha256:'6'.repeat(64),build_identity_sha256:null,frozen:false};
   queueMicrotask(()=>{backend.receive(claim);backend.receive({...claim,kind:'backend_ready'});});
  }else if(value.kind==='backend_drain_request'){
   const refused=scenario==='refused-drain';queueMicrotask(()=>backend.receive({schema_version:1,kind:'backend_managed_drain',request:value,
    backend_proof:claim,status:refused?'refused':'managed_scopes_drained',active_scopes:0,unsupported:refused?['modeled-uncovered']:[],
    scope:'reviewed_foreground_scopes_only',whole_writer_coverage:false,process_tree_exit_verified:false,can_release_launch_lease:false}));
  }
 };
 await launch.bindBackend(proc,'/modeled-backend',null);await ticks();
 const header={schema_version:1,kind:'backend_preflight_request',nonce,epoch,binding_sha256:hash(canonical(binding)),backend_claim_sha256:hash(canonical(claim)),request_id:'d'.repeat(32)};
 const plan={task:'train',device:'cpu',stages:['train'],workdir:'/modeled-original',source_sha256:'e'.repeat(64),budget_ms:10000,deadline_monotonic:12.75,command:Array(18).fill('modeled')};
 if(['unfinished-row','finished-row','pending-relay'].includes(scenario)){
  backend.receive({...header,action:'reserve',payload:plan});await ticks();
  if(scenario==='finished-row'){
   const registration={writer_id:'b'.repeat(32),registration_sha256:'c'.repeat(64)},payload={registration,child,plan_sha256:hash(canonical(plan))};
   backend.receive({...header,action:'bind',payload});await ticks();backend.receive({...header,action:'finish',payload:{...payload,returncode:0,cleanup_confirmed:true}});await ticks();
  }
 }
 if(scenario==='no-drain'||scenario==='pending-relay'){
  backend.emit('end');await ticks();assert.equal(controller.destroyed,true);assert.equal(exitSent,0);
 }else{
  const drain=launch.prepareBackendDrain(proc,4000);void drain.catch(()=>{});await ticks();
  if(scenario==='pending-drain-ack'){
   assert.ok(heldDrainAck);backend.emit('end');await ticks();assert.equal(controller.destroyed,true);assert.equal(exitSent,0);
  }else if(['refused-drain','late-drain-ack','unfinished-row'].includes(scenario)){
   await assert.rejects(drain);backend.emit('end');await ticks();assert.equal(controller.destroyed,true);assert.equal(exitSent,0);
  }else{
   const receipt=await drain;assert.equal(receipt.status,'managed_scopes_drained');
   assert.equal(receipt.can_release_launch_lease,false);assert.equal(controller.destroyed,false);
   if(scenario==='replayed-exit-ack'){
    // Observe/delegate the genuinely installed callback; never fabricate an
    // admitted receipt, exit frame, resolved promise, or callback authority.
    const originalResolve=launch.exitResolve;assert.equal(typeof originalResolve,'function');
    launch.exitResolve=()=>{resolveCalls++;originalResolve();};
   }
   if(scenario==='partial-frame')backend.emit('data',Buffer.from('{"kind":'));
   if(scenario==='malformed-frame')backend.emit('data',Buffer.from('{oops}\n'));
   if(scenario==='foreign-handle')launch.backend=new EventEmitter();
   if(scenario==='foreign-channel')proc.stdio[3]=new Channel();
   if(scenario==='foreign-pid')proc.pid=32;
   if(scenario==='binding-ended')bindingFresh=false;
   if(scenario==='late-end')now=5000;
   if(scenario==='shorter-original-drain')now=2000;
   if(scenario==='validation-crosses-deadline')crossArm=true;
   if(scenario==='changed-receipt')launch.drainReceipt.active_scopes=1;
   if(scenario==='changed-drain-request')launch.drainRequest.budget_ms=3999;
   if(scenario==='same-message-error')backend.emit('error',new Error('Private descriptor ended'));
   else backend.emit(scenario==='clean-close'?'close':'end');
   if(scenario==='end-then-error')backend.emit('error',new Error('Modeled socket error after end'));
   if(scenario==='end-then-close')backend.emit('close');
   await ticks();
   const positive=['clean-end','clean-close','end-then-close','finished-row','late-exit','late-exit-ack','nonzero-exit','signal-exit','silent-no-exit','silent-no-ack','replayed-exit-ack'].includes(scenario);
   assert.equal(controller.destroyed,!positive,'Only exact fully admitted clean backend end keeps the original controller reader');
   assert.equal(exitSent,0,'EOF never grants an exit publication');
   if(positive){
    if(scenario==='silent-no-exit'){
     now=5000;for(const [timer,row] of [...timerRecords])if(row.due<=now){clearTimeout(timer);row.fn();}
     await ticks();assert.equal(controller.destroyed,true,'Original admitted end cannot wait beyond original deadline');assert.equal(exitSent,0);await assert.rejects(launch.exitConfirmation);
     launch.refuse();backend.destroy();for(const timer of ownedTimers)clearTimeout(timer);console.log(JSON.stringify({scenario,pass:true,application:false,cpu:false}));return;
    }
    if(scenario==='late-exit')now=5000;
    proc.exitCode=scenario==='nonzero-exit'?2:0;proc.signalCode=scenario==='signal-exit'?'SIGTERM':null;
    proc.emit('exit',proc.exitCode,proc.signalCode);await ticks();
    if(scenario==='silent-no-ack'){
     assert.equal(exitSent,1);assert.equal(controller.destroyed,false);now=5000;
     for(const [timer,row] of [...timerRecords])if(row.due<=now){clearTimeout(timer);row.fn();}
     await ticks();assert.equal(controller.destroyed,true,'Original exit ACK silence cannot renew deadline');await assert.rejects(launch.confirmBackendExit(proc));
     launch.refuse();backend.destroy();for(const timer of ownedTimers)clearTimeout(timer);console.log(JSON.stringify({scenario,pass:true,application:false,cpu:false}));return;
    }
    const failed=['late-exit','late-exit-ack','nonzero-exit','signal-exit'].includes(scenario);
    if(failed){assert.equal(controller.destroyed,true);await assert.rejects(launch.confirmBackendExit(proc));}
    else{assert.equal(exitSent,1);await launch.confirmBackendExit(proc);assert.equal(controller.destroyed,false);
     if(scenario==='replayed-exit-ack'){assert.equal(resolveCalls,1);controller.receive(lastExitAck);await ticks();assert.equal(resolveCalls,1,'Identical acknowledgement was consumed twice');assert.equal(controller.destroyed,true,'Replay did not invalidate original private channel');assert.equal(exitSent,1);}}
   }
  }
 }
 launch.refuse();backend.destroy();await ticks();for(const timer of ownedTimers)clearTimeout(timer);
 console.log(JSON.stringify({scenario,pass:true,scope:'inert-original-reader-lifetime',application:false,cpu:false,process_authority:'modeled',validation:'modeled',resolve_calls:resolveCalls,identical_ack_replay_refused:scenario==='replayed-exit-ack',actual_dispatcher:true,modeled_handles_only:true}));
}
main().catch(error=>{for(const timer of ownedTimers)clearTimeout(timer);console.error(error.stack);process.exitCode=1;});
"""


@pytest.mark.no_child
@pytest.mark.parametrize("scenario", [
    "clean-end", "clean-close", "end-then-close", "finished-row",
    "no-drain", "refused-drain", "pending-relay", "pending-drain-ack",
    "unfinished-row", "partial-frame", "malformed-frame", "same-message-error",
    "end-then-error", "foreign-handle", "foreign-channel", "foreign-pid",
    "binding-ended", "validation-crosses-deadline", "changed-receipt",
    "changed-drain-request", "late-end", "late-exit", "late-exit-ack",
    "nonzero-exit", "signal-exit", "shorter-original-drain", "silent-no-exit",
    "silent-no-ack", "replayed-exit-ack", "late-drain-ack",
])
def test_original_backend_end_lifetime(scenario, tmp_path):
    source = Path(__file__).resolve().parents[2]/"src/main/applicationLaunch.ts"
    result = subprocess.run(["node", "-e", CONTROL, str(source), scenario],
                            capture_output=True, text=True, timeout=15)
    (tmp_path/"original-node-unit.stdout").write_text(result.stdout)
    (tmp_path/"original-node-unit.stderr").write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["scenario"] == scenario and receipt["pass"] is True
    assert receipt["application"] is False and receipt["cpu"] is False


FRAME_CONTROL = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{EventEmitter}=require('node:events'),ts=require('typescript');
const exports={},context={exports,module:{exports},require,Buffer,TextDecoder,performance,setTimeout,clearTimeout};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText+'\nexports.__test={Frames,PrivateFrameEnd};',context);
const {Frames,PrivateFrameEnd}=exports.__test,scenario=process.argv[2];
(async()=>{
 const channel=new EventEmitter(),frames=new Frames(channel),foreign=new Frames(new EventEmitter());
 let error;
 if(scenario==='buffer')channel.emit('data',Buffer.from('{'));
 if(scenario==='queued')channel.emit('data',Buffer.from('{}\n'));
 if(scenario==='queued')channel.emit('end');
 else if(scenario==='direct-forged')frames.fail(new PrivateFrameEnd(frames,channel,'end'));
 else if(scenario==='direct-subclass'){class ForgedEnd extends PrivateFrameEnd{};frames.fail(new ForgedEnd(frames,channel,'end'));}
 else {const waiting=frames.read(null).catch(e=>error=e);
  if(scenario==='prior-error')channel.emit('error',new Error('Private descriptor ended'));
  channel.emit(scenario==='close'?'close':'end');await waiting;}
 if(!error)await frames.read(null).catch(e=>error=e);
 if(scenario==='copy')error=Object.assign(Object.create(Object.getPrototypeOf(error)),error);
 if(scenario==='foreign')assert.equal(foreign.originalEmptyEnd(error),false);
 else assert.equal(frames.originalEmptyEnd(error),['end','close'].includes(scenario));
 assert.equal(frames.originalEmptyEnd(new Error('Private descriptor ended')),false);
 console.log(JSON.stringify({scenario,pass:true,scope:'inert-exact-original-end-token',application:false}));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
"""


@pytest.mark.no_child
@pytest.mark.parametrize("scenario", [
    "end", "close", "buffer", "queued", "prior-error", "copy", "foreign",
    "direct-forged", "direct-subclass",
])
def test_exact_original_frames_end_token(scenario, tmp_path):
    source = Path(__file__).resolve().parents[2]/"src/main/applicationLaunch.ts"
    result = subprocess.run(["node", "-e", FRAME_CONTROL, str(source), scenario],
                            capture_output=True, text=True, timeout=15)
    (tmp_path/"original-node-unit.stdout").write_text(result.stdout)
    (tmp_path/"original-node-unit.stderr").write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["scenario"] == scenario and receipt["pass"] is True
