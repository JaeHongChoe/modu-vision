const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),Module=require('node:module'),cp=require('node:child_process'),ts=require('typescript');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
// A truncate/write interval is not a counter sample. Keep the exact validated
// observation so a later rewrite cannot erase progress already seen.
function heartbeatCount(file){
 let raw;try{raw=fs.readFileSync(file,'utf8');}catch(error){if(error.code==='ENOENT')return;throw error;}
 if(!/^[1-9][0-9]*$/.test(raw))return;
 const count=Number(raw);if(Number.isSafeInteger(count))return count;
}
async function initialHeartbeat(file){
 let before;
 for(let i=0;i<50&&(before=heartbeatCount(file))===undefined;i++)await wait(20);
 assert.ok(before!==undefined,'controller must write a positive safe-integer heartbeat');return before;
}
async function advancingHeartbeat(file,before){
 assert.ok(Number.isSafeInteger(before)&&before>0);
 const deadline=performance.now()+1000;let observed;
 while(performance.now()<deadline){
  const count=heartbeatCount(file);
  if(count!==undefined&&count>before){observed=count;break;}
  await wait(20);
 }
 assert.ok(observed>before,'original controller heartbeat must advance');return observed;
}
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
  const before=await initialHeartbeat(f.heartbeat),observed=await advancingHeartbeat(f.heartbeat,before);
  assert.ok(observed>before);assert.equal(call.child.exitCode,null);assert.equal(call.child.signalCode,null);
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

async function controlledHeartbeat(t,{empty=false}={}){
 const f=fixture(t),command=path.join(f.root,'command'),stage=path.join(f.root,'stage');fs.writeFileSync(command,'hold');
 const program=`const fs=require('node:fs');process.stdout.on('error',()=>{});process.stderr.on('error',()=>{});const [h,c,s]=process.argv.slice(1);let fd;fs.writeFileSync(h,${JSON.stringify(empty?'':'1')});process.stdout.write(JSON.stringify({status:'starting'})+'\\n');setInterval(()=>{const cmd=fs.readFileSync(c,'utf8');if(cmd==='baseline')fs.writeFileSync(h,'1');else if(cmd==='progress')fs.writeFileSync(h,'2');else if(cmd==='truncate'&&fd===undefined){fd=fs.openSync(h,'w');fs.writeFileSync(s,'truncated');}else if(cmd==='resume'&&fd!==undefined){fs.writeSync(fd,'3');fs.closeSync(fd);fd=undefined;fs.writeFileSync(s,'resumed');}else if(cmd==='exit')process.exit(0);},20);`;
 const result=await f.run(process.execPath,['-e',program,f.heartbeat,command,stage]);assert.equal(JSON.parse(result.stdout).status,'starting');
 return {...f,command,stage};
}
async function commandStage(f,command,stage){
 fs.writeFileSync(f.command,command);const deadline=performance.now()+1000;
 while(!fs.existsSync(f.stage)||fs.readFileSync(f.stage,'utf8')!==stage){assert.ok(performance.now()<deadline);await wait(20);}
}
test('a validated advancing heartbeat survives the same live child truncating before its next write',async t=>{
 const f=await controlledHeartbeat(t),before=await initialHeartbeat(f.heartbeat);assert.equal(before,1);
 fs.writeFileSync(f.command,'progress');const observed=await advancingHeartbeat(f.heartbeat,before);assert.equal(observed,2);
 await commandStage(f,'truncate','truncated');assert.equal(fs.readFileSync(f.heartbeat,'utf8'),'');assert.equal(heartbeatCount(f.heartbeat),undefined);
 assert.ok(observed>before);assert.equal(f.calls[0].child.exitCode,null);assert.equal(f.calls[0].child.signalCode,null);
 await commandStage(f,'resume','resumed');assert.equal(heartbeatCount(f.heartbeat),3);
});
for(const kind of ['unchanged','exit'])test(`acknowledged controller with ${kind} heartbeat cannot satisfy progress`,async t=>{
 const f=await controlledHeartbeat(t),before=await initialHeartbeat(f.heartbeat),started=performance.now();
 if(kind==='exit')fs.writeFileSync(f.command,'exit');
 await assert.rejects(()=>advancingHeartbeat(f.heartbeat,before),/heartbeat must advance/);
 assert.ok(performance.now()-started>=1000);if(kind==='exit')assert.equal(f.calls[0].child.exitCode,0);
});
test('empty pre-write bytes cannot become a baseline, and only the original child can supply the later valid baseline',async t=>{
 const f=await controlledHeartbeat(t,{empty:true});assert.equal(heartbeatCount(f.heartbeat),undefined);
 await assert.rejects(()=>initialHeartbeat(f.heartbeat),/positive safe-integer heartbeat/);
 fs.writeFileSync(f.command,'baseline');assert.equal(await initialHeartbeat(f.heartbeat),1);
});
test('invalid or unsafe heartbeat bytes never count as baseline or progress',t=>{
 const f=fixture(t);for(const raw of ['','0','NaN','Infinity','-1','1.5','1x',' 1','1\n','9007199254740992']){fs.writeFileSync(f.heartbeat,raw);assert.equal(heartbeatCount(f.heartbeat),undefined,JSON.stringify(raw));}
});
