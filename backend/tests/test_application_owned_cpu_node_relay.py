"""Actual Node Frames/reader code with modeled channels and birth validation.

No app/backend/model child is launched. The inert Node interpreter executes the
production source and one-use local transport sequence, not OS authority/math.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest


CONTROL=r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
const {EventEmitter}=require('node:events');let now=1000;const timers=new Set();
const exports={},context={exports,module:{exports},require,Buffer,TextDecoder,performance:{now:()=>now},
 setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);timers.add(timer);return timer;},clearTimeout:t=>{timers.delete(t);clearTimeout(t);}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText+
 '\nexports.__test={Frames,OwnedApplicationLaunch,canonical,hash,setBinding(fn){validateBinding=fn;}};',context);
const {Frames,OwnedApplicationLaunch,canonical,hash,setBinding}=exports.__test,scenario=process.argv[2];setBinding(()=>{});
class Channel extends EventEmitter{
 constructor(){super();this.destroyed=false;this.writes=[];this.onWrite=()=>{};}
 write(raw,callback){this.writes.push(Buffer.from(raw));if(this.destroyed){callback(new Error('Modeled channel ended'));return false;}this.onWrite(JSON.parse(raw),Buffer.from(raw));callback();return true;}
 receive(value){this.emit('data',Buffer.from(canonical(value)+'\n'));}
 destroy(){if(!this.destroyed){this.destroyed=true;this.emit('close');}return this;}}
const ticks=async()=>{for(let i=0;i<100;i++)await Promise.resolve();};
async function main(){const controller=new Channel(),backend=new Channel(),proc=new EventEmitter();
 Object.assign(proc,{pid:31,exitCode:null,signalCode:null,stdio:[null,null,null,backend]});
 const nonce='1'.repeat(32),binding={application_generation:'2'.repeat(32)},writer={writer_id:'3'.repeat(32),registration_sha256:'4'.repeat(64)};
 const launch=new OwnedApplicationLaunch('/modeled',nonce,binding,{pid:21},'5'.repeat(64),new Frames(controller),writer);
 launch.bindingCurrent=()=>{};launch.current=()=>{};launch.executable=()=>({sha256:'6'.repeat(64),build:null});
 let epoch,claim,cpuRequest,header,plan,completion,pubAck,drainRequest;let proofs=0,settlements=0,exits=0,concurrentDrain=null,heldDrain=null;
 const child={pid:41,created_at:1.25,command_sha256:'a'.repeat(64)},registration={writer_id:'b'.repeat(32),registration_sha256:'c'.repeat(64)};
 const emitAction=(action,payload)=>backend.receive({...header,action,payload});
 controller.onWrite=(value,raw)=>{
  if(value.kind==='main_cpu_child_request'){
   const req=value.request,answer={schema_version:1,kind:'controller_cpu_child_reply',nonce,epoch,request_id:req.request_id,
    action:req.action,request_sha256:hash(canonical(req)),payload:req.action==='reserve'?registration:{status:req.action==='bind'?'bound':'direct_exited'}};
   if(scenario==='late-child-ack')now=5000;
   if(scenario==='foreign-child-ack')answer.request_id='c'.repeat(32);
   queueMicrotask(()=>controller.receive(answer));
  }else if(value.kind==='cpu_execution_proof'){
   proofs++;const rawProof=Buffer.from(value.proof_b64,'base64');completion=JSON.parse(rawProof);
   pubAck={schema_version:1,kind:'controller_cpu_publication_ack',nonce,request_id:cpuRequest.request_id,
    request_sha256:hash(canonical(cpuRequest)),completion_sha256:hash(rawProof),receipt_sha256:'e'.repeat(64)};
   if(scenario==='foreign-publication')pubAck.completion_sha256='f'.repeat(64);
   if(scenario==='late-publication')now=5000;
   queueMicrotask(()=>controller.receive(pubAck));
  }else if(value.kind==='source_cpu_settled'){
   settlements++;assert.equal(value.receipt_sha256,pubAck.receipt_sha256);assert.equal(value.completion_sha256,pubAck.completion_sha256);
  }else if(value.kind==='main_drain_request'){
   drainRequest={schema_version:1,kind:'backend_drain_request',challenge:'7'.repeat(64),request_id:value.request_id,nonce,epoch,
    binding_sha256:hash(canonical(binding)),backend_claim_sha256:hash(canonical(claim)),...writer,closed_registry_sha256:'8'.repeat(64),budget_ms:value.budget_ms};
   queueMicrotask(()=>controller.receive(drainRequest));
  }else if(value.kind==='managed_drain_proof'){
   const rawProof=Buffer.from(value.proof_b64,'base64');queueMicrotask(()=>controller.receive({schema_version:1,kind:'managed_drain_admitted',nonce,
    request_id:drainRequest.request_id,receipt_sha256:hash(rawProof),status:'managed_scopes_drained'}));
  }else if(value.kind==='main_backend_exit'){
   exits++;queueMicrotask(()=>controller.receive({schema_version:1,kind:'backend_exit_observed',nonce,request_id:value.request_id,exit_sha256:hash(canonical(value))}));
  }
 };
 backend.onWrite=(value)=>{
  if(value.kind==='backend_challenge'){
   epoch=value.epoch;claim={schema_version:1,kind:'backend_claim',challenge:value.challenge,epoch,nonce,binding_sha256:hash(canonical(binding)),
    process:{pid:31,created_at:1.5,command_sha256:'9'.repeat(64)},executable:'/modeled-backend',executable_sha256:'6'.repeat(64),build_identity_sha256:null,frozen:false};
   queueMicrotask(()=>{backend.receive(claim);backend.receive({...claim,kind:'backend_ready'});});
  }else if(value.kind==='cpu_execution_request'){
   cpuRequest=value;header={schema_version:1,kind:'backend_cpu_child_request',nonce,epoch,binding_sha256:value.binding_sha256,
    backend_claim_sha256:value.backend_claim_sha256,request_id:value.request_id};
   plan={cpu_request:value,budget_ms:scenario==='invalid-budget'?60001:4000,deadline_monotonic:12.75,
    workdir:'/modeled/.owned-cpu-'+value.request_id,gate_fd:7,command:Array(12).fill('fixed-modeled')};
   if(scenario==='wrong-intent')plan.cpu_request={...value,project_id:'c'.repeat(32)};
   queueMicrotask(()=>emitAction('reserve',plan));
  }else if(value.kind==='controller_cpu_child_reply'){
   if(scenario==='late-child-send')now=5000;
   if(value.action==='reserve')queueMicrotask(()=>emitAction('bind',{registration,child,plan_sha256:hash(canonical(plan))}));
   else if(value.action==='bind'){
    if(scenario==='unfinished-eof'){backend.emit('end');return;}
    const completed={schema_version:1,kind:'cpu_execution_completed',request:cpuRequest,backend_proof:claim,
     output_path:'delivery/controlled.json',output_sha256:'a'.repeat(64),semantic_output:{controlled:true},worker_pid:41,runtime_source_sha256:'b'.repeat(64)};
    queueMicrotask(()=>backend.receive(completed));
   }else if(value.action==='finish'){
    const done={schema_version:1,kind:'backend_source_cpu_settled',nonce,request_id:cpuRequest.request_id,
     request_sha256:hash(canonical(cpuRequest)),completion_sha256:pubAck.completion_sha256,receipt_sha256:pubAck.receipt_sha256};
    if(scenario==='foreign-settlement')done.receipt_sha256='f'.repeat(64);
    queueMicrotask(()=>{backend.receive(done);if(heldDrain)backend.receive({schema_version:1,kind:'backend_managed_drain',request:heldDrain,backend_proof:claim,status:'managed_scopes_drained',active_scopes:0,unsupported:[],scope:'reviewed_foreground_scopes_only',whole_writer_coverage:false,process_tree_exit_verified:false,can_release_launch_lease:false});});
   }
  }else if(value.kind==='controller_cpu_publication_ack'){
   if(scenario==='late-publication-send')now=5000;
   if(scenario==='lost-publication-context')launch.bindingCurrent=()=>{throw new Error('Original modeled binding lost after ACK send');};
   if(scenario==='drain-before-finish'){concurrentDrain=launch.prepareBackendDrain(proc,4000);void concurrentDrain.catch(()=>{});}
   queueMicrotask(()=>emitAction('finish',{registration,child,plan_sha256:hash(canonical(plan)),returncode:0,
    completion_sha256:value.completion_sha256,receipt_sha256:value.receipt_sha256}));
  }else if(value.kind==='backend_drain_request'){
   if(scenario==='drain-before-finish'){heldDrain=value;return;}
   queueMicrotask(()=>backend.receive({schema_version:1,kind:'backend_managed_drain',request:value,backend_proof:claim,status:'managed_scopes_drained',
    active_scopes:0,unsupported:[],scope:'reviewed_foreground_scopes_only',whole_writer_coverage:false,process_tree_exit_verified:false,can_release_launch_lease:false}));
  }
 };
 await launch.bindBackend(proc,'/modeled-backend',null);await ticks();
 controller.receive({schema_version:1,kind:'cpu_execution_request',challenge:'d'.repeat(64),request_id:'d'.repeat(32),nonce,epoch,
  binding_sha256:hash(canonical(binding)),backend_claim_sha256:hash(canonical(claim)),workspace_id:'e'.repeat(32),project_id:'f'.repeat(32),plan_sha256:'0'.repeat(64)});
 await ticks();
 if(['complete','replayed-publication','complete-drain','late-finish-after-drain','drain-before-finish','replay-cpu-request'].includes(scenario)){
  assert.equal(controller.destroyed,false);assert.equal(proofs,1);assert.equal(settlements,1);
  if(scenario==='replayed-publication'){controller.receive(pubAck);await ticks();assert.equal(controller.destroyed,true);assert.equal(settlements,1);}
  else if(scenario==='replay-cpu-request'){controller.receive(cpuRequest);await ticks();assert.equal(controller.destroyed,true);assert.equal(settlements,1);}
  else if(scenario==='complete-drain'||scenario==='drain-before-finish'){
   const result=await (concurrentDrain??launch.prepareBackendDrain(proc,4000));assert.equal(result.status,'managed_scopes_drained');assert.equal(result.can_release_launch_lease,false);
   backend.emit('end');await ticks();assert.equal(controller.destroyed,false);
   proc.exitCode=0;proc.emit('exit',0,null);await ticks();await launch.confirmBackendExit(proc);assert.equal(exits,1);assert.equal(controller.destroyed,false);
  }else if(scenario==='late-finish-after-drain'){
   const result=await launch.prepareBackendDrain(proc,4000);assert.equal(result.status,'managed_scopes_drained');
   now=5000;emitAction('finish',{registration,child,plan_sha256:hash(canonical(plan)),returncode:0,completion_sha256:pubAck.completion_sha256,receipt_sha256:pubAck.receipt_sha256});
   await ticks();assert.equal(controller.destroyed,true);assert.equal(settlements,1);
  }
 }else {assert.equal(controller.destroyed,true);assert.equal(settlements,0);}
 launch.refuse();backend.destroy();for(const timer of timers)clearTimeout(timer);
 console.log(JSON.stringify({scenario,pass:true,app:false,model:false,birth_authentication:'modeled',original_readers:true,proofs,settlements,exits}));
}
main().catch(error=>{for(const timer of timers)clearTimeout(timer);console.error(error.stack);process.exitCode=1;});
"""


@pytest.mark.no_child
@pytest.mark.parametrize('scenario',['complete','complete-drain','replayed-publication','late-finish-after-drain',
    'invalid-budget','wrong-intent','late-child-ack','foreign-child-ack','foreign-publication','late-publication',
    'foreign-settlement','unfinished-eof','late-child-send','late-publication-send','lost-publication-context',
    'drain-before-finish','replay-cpu-request'])
def test_original_source_cpu_node_transport(scenario,tmp_path):
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run(['node','-e',CONTROL,str(root/'src/main/applicationLaunch.ts'),scenario],
        cwd=root,capture_output=True,text=True,env=os.environ,timeout=15)
    (tmp_path/'node-model.stdout').write_text(result.stdout);(tmp_path/'node-model.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stderr
    evidence=json.loads(result.stdout)
    assert evidence['scenario']==scenario and evidence['pass'] and evidence['app'] is False and evidence['model'] is False
