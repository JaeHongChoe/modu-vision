const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),Module=require('node:module'),cp=require('node:child_process'),ts=require('typescript');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fixture(t){
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'persistent-launch-test-'))),calls=[];
 const file=path.join(__dirname,'persistentLaunch.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 m.require=name=>name==='node:child_process'?{spawn(...args){const child=cp.spawn(...args);calls.push({args,child});return child;}}:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
 t.after(async()=>{for(const {child}of calls){if(child.exitCode===null&&child.signalCode===null){const exited=new Promise(resolve=>child.once('exit',resolve));child.kill('SIGTERM');await exited;}}fs.rmSync(root,{recursive:true,force:true});});
 const heartbeat=path.join(root,'owned-heartbeat');
 function program(output){return `const fs=require('node:fs');process.stdout.on('error',()=>{});process.stderr.on('error',()=>{});${output};let n=0;setInterval(()=>fs.writeFileSync(process.argv[1],String(++n)),20);`;}
 return {root,calls,heartbeat,run:m.exports.runPersistentController,program};
}
test('persistent controller keeps running after updater acknowledgement streams close, with a fixed local offline environment',async t=>{
 const f=fixture(t),previous=process.env.VISION_HANDOFF_FD;process.env.VISION_HANDOFF_FD='999';
 try{
  const result=await f.run(process.execPath,['-e',f.program(`process.stdout.write(JSON.stringify({status:'starting'})+'\\n')`),f.heartbeat]);
  assert.equal(JSON.parse(result.stdout).status,'starting');assert.equal(result.stderr,'');
  const call=f.calls[0];assert.equal(call.args[2].detached,true);assert.equal(call.args[2].shell,false);assert.deepEqual(call.args[2].stdio,['ignore','pipe','pipe']);assert.equal(call.args[2].env.VISION_HANDOFF_FD,undefined);assert.equal(call.args[2].env.HF_HUB_OFFLINE,'1');
  for(let i=0;i<50&&!fs.existsSync(f.heartbeat);i++)await wait(20);const before=Number(fs.readFileSync(f.heartbeat));
  // Observe actual progress within a fixed bound; a loaded runner need not
  // schedule the detached controller inside a single 80ms sample.
  const deadline=performance.now()+1000;
  while(Number(fs.readFileSync(f.heartbeat))<=before&&performance.now()<deadline)await wait(20);
  assert.ok(Number(fs.readFileSync(f.heartbeat))>before);assert.equal(call.child.exitCode,null);assert.equal(call.child.signalCode,null);
 }finally{if(previous===undefined)delete process.env.VISION_HANDOFF_FD;else process.env.VISION_HANDOFF_FD=previous;}
});
for(const kind of ['oversized','multiple-json-lines','invalid-utf8'])test(`unconfirmed ${kind} response closes only updater streams and preserves the original controller`,async t=>{
 const f=fixture(t);const output=kind==='oversized'?`process.stdout.write('x'.repeat(65537))`:kind==='multiple-json-lines'?`process.stdout.write('{}\\n{}\\n')`:`process.stdout.write(Buffer.from([255,10]))`;
 await assert.rejects(()=>f.run(process.execPath,['-e',f.program(output),f.heartbeat]),/response|bound/i);
 for(let i=0;i<50&&!fs.existsSync(f.heartbeat);i++)await wait(20);assert.ok(fs.existsSync(f.heartbeat));assert.equal(f.calls[0].child.exitCode,null);assert.equal(f.calls[0].child.signalCode,null);
});
test('a controller that exits without acknowledgement cannot be reported started',async t=>{
 const f=fixture(t);await assert.rejects(()=>f.run(process.execPath,['-e','process.exit(0)']),/exited.*response/i);assert.equal(f.calls[0].child.exitCode,0);
});
