const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function fixture(){
 const file=path.join(__dirname,'firstUseStudy.ts');assert.ok(fs.existsSync(file),'the local first-use recorder must exist');
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
 let now=1000;const recorder=m.exports.createFirstUseStudy(()=>now);
 return{recorder,advance:ms=>{now+=ms;},start:extra=>recorder.start({mode:'automated',task:'first_flow',build:{app_version:'0.1.0',platform:'darwin',architecture:'arm64'},...extra})};
}
test('opt-in starts before setup, includes idle/model/background waits and refuses duplicate starts',()=>{
 const f=fixture();assert.equal(f.recorder.snapshot(),null);assert.equal(f.recorder.count('pointer'),false);
 assert.equal(f.start(),true);f.recorder.count('pointer');f.advance(31*60*1000);assert.equal(f.start(),false);
 const value=f.recorder.snapshot();assert.equal(value.elapsed_ms,1860000);assert.equal(value.workflow_actions.pointer,1);
 assert.equal(value.binding,'unbound');assert.equal(value.acceptance,'unverified');assert.equal(value.target_assessment,'unverified');
 assert.equal(f.recorder.stop(),true);f.advance(10000);assert.equal(f.recorder.snapshot().elapsed_ms,1860000);
});
test('setup can change scope until one explicit binding; later change seals an immutable partial',()=>{
 const f=fixture();f.start();f.advance(50);f.recorder.checkScope('new project');f.recorder.count('pointer');
 f.recorder.checkScope('new source');assert.equal(f.recorder.snapshot().status,'recording');
 assert.equal(f.recorder.bind('private scope /source/token-secret'),true);assert.equal(f.recorder.bind('other'),false);
 f.recorder.checkScope('private scope /source/token-secret');f.advance(200);f.recorder.checkScope('other');
 const partial=f.recorder.snapshot();assert.equal(partial.status,'aborted');assert.equal(partial.reason,'scope_changed');assert.equal(partial.elapsed_ms,250);
 assert.equal(partial.workflow_actions.pointer,1);assert.equal(f.recorder.count('keyboard'),false);f.advance(500);
 assert.deepEqual(f.recorder.snapshot(),partial);assert.ok(Object.isFrozen(partial)&&Object.isFrozen(partial.workflow_actions)&&Object.isFrozen(partial.milestones));
 assert.doesNotMatch(JSON.stringify(partial),/private scope|source\/token|secret/);
});
test('a bound scope becoming unavailable aborts; an unbound scope cannot bind nothing',()=>{
 const f=fixture();f.start();assert.equal(f.recorder.bind(null),false);f.recorder.checkScope(null);
 assert.equal(f.recorder.snapshot().status,'recording');f.recorder.bind('s');f.recorder.checkScope(null);
 assert.equal(f.recorder.snapshot().reason,'scope_unavailable');
});
for(const mode of ['human_self_reported','automated'])test(mode+' remains unverified with separate recorder actions and unknown external coverage',()=>{
 const f=fixture();f.start({mode});f.recorder.count('pointer');f.recorder.count('keyboard');f.recorder.count('pointer',true);f.recorder.count('keyboard',true);
 const value=f.recorder.snapshot();assert.equal(value.mode,mode);assert.equal(value.acceptance,'unverified');
 assert.deepEqual(value.workflow_actions,{pointer:1,keyboard:1});assert.deepEqual(value.recorder_actions,{pointer:1,keyboard:1});
 assert.deepEqual(value.external_actions,{source:'unobserved',count:null});assert.equal(value.target_assessment,'unverified');
 assert.equal(f.recorder.reportExternalActions(-1),false);assert.equal(f.recorder.reportExternalActions(1.5),false);assert.equal(f.recorder.reportExternalActions(Infinity),false);
 assert.equal(f.recorder.reportExternalActions(0),true);assert.deepEqual(f.recorder.snapshot().external_actions,{source:'self_reported',count:0});
 assert.equal(f.recorder.snapshot().target_assessment,'unverified','a reported zero is not independently complete click coverage');
});
test('manual/assisted image markers are self-reported and total time includes corrections after the last image',()=>{
 for(const task of ['manual_label','assisted_draft']){
  const f=fixture();f.start({task});f.advance(100);f.recorder.mark('image_complete_self_reported');f.advance(300);f.recorder.mark('image_complete_self_reported');
  f.advance(200);f.recorder.count('keyboard');f.recorder.stop();const value=f.recorder.snapshot();
  assert.equal(value.images_completed_self_reported,2);assert.equal(value.elapsed_ms,600);assert.equal(value.elapsed_ms_per_image,300);
  assert.deepEqual(value.milestones.map(row=>row.kind),['image_complete_self_reported','image_complete_self_reported']);
  assert.equal(value.acceptance,'unverified');
 }
});
test('allowlisted metadata/enums drop forged fields and no key/text/path/token payload is retained',()=>{
 const f=fixture();assert.equal(f.start({mode:'sk-private-token'}),false);
 f.start({task:'manual_label',build:{app_version:'1.2.3-sk-private-token',platform:'/private/path',architecture:'token-secret',build_sha:'a'.repeat(40),token:'sk-hidden'}});
 f.recorder.count('pointer',false,{key:'private key',value:'/secret/path'});assert.equal(f.recorder.mark('sk-private-token'),false);
 f.recorder.mark('image_complete_self_reported');const value=f.recorder.snapshot();
 assert.deepEqual(value.build,{app_version:null,platform:'unknown',architecture:null,build_sha:null});
 assert.doesNotMatch(JSON.stringify(value),/sk-private|private key|secret\/path|sk-hidden|aaaaaaa/);
 assert.deepEqual(Object.keys(value).sort(),['acceptance','binding','build','clock_anomaly','elapsed_ms','elapsed_ms_per_image','external_actions','images_completed_self_reported','milestones','milestones_truncated','mode','reason','recorder_actions','schema_version','started_at_ms','status','target_assessment','task','workflow_actions'].sort());
});
test('completed snapshots cannot be mutated by later sessions or previously returned active snapshots',()=>{
 const f=fixture();f.start();const early=f.recorder.snapshot();assert.ok(Object.isFrozen(early.build));
 f.recorder.count('pointer');assert.equal(early.workflow_actions.pointer,0);f.recorder.stop();const old=f.recorder.snapshot();
 assert.equal(f.start({task:'manual_label'}),true);f.recorder.count('keyboard');assert.equal(old.workflow_actions.keyboard,0);assert.equal(old.status,'completed');
});
test('clock reversal is explicit and cannot shrink the recorded elapsed interval',()=>{
 const f=fixture();f.start();f.advance(1000);assert.equal(f.recorder.snapshot().elapsed_ms,1000);f.advance(-500);
 assert.equal(f.recorder.snapshot().clock_anomaly,true);assert.equal(f.recorder.snapshot().elapsed_ms,1000);
});
test('milestone history is bounded without dropping aggregate image counts',()=>{
 const f=fixture();f.start({task:'manual_label'});for(let n=0;n<300;n++){f.advance(1);f.recorder.mark('image_complete_self_reported');}
 const value=f.recorder.snapshot();assert.equal(value.images_completed_self_reported,300);assert.ok(value.milestones.length<=256);assert.equal(value.milestones_truncated,true);
});

// Reproducibility: a record names the bundle's own commit when the build was made from a clean tree (vite.config.ts writes
// it in), never a value the caller passes, and null for a dev server or a tree with changes.
test('build_sha is the bundle commit from a clean build, never the caller value',()=>{
 const file=path.join(__dirname,'firstUseStudy.ts'),m=new Module(file,module);m.filename=file;
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
 const built=m.exports.createFirstUseStudy(()=>1000,'b'.repeat(40));
 built.start({mode:'automated',task:'first_flow',build:{app_version:'0.1.0',platform:'darwin',architecture:'arm64',build_sha:'c'.repeat(40)}});
 assert.equal(built.snapshot().build.build_sha,'b'.repeat(40));
 for(const bad of ['B'.repeat(40),'b'.repeat(39),'',null]){const r=m.exports.createFirstUseStudy(()=>1000,bad);r.start({mode:'automated',task:'first_flow'});assert.equal(r.snapshot().build.build_sha,null,String(bad));}
 const unbuilt=m.exports.createFirstUseStudy(()=>1000);unbuilt.start({mode:'automated',task:'first_flow'});assert.equal(unbuilt.snapshot().build.build_sha,null,'no build constant outside a Vite build');
 const config=fs.readFileSync(path.resolve(__dirname,'../../../../vite.config.ts'),'utf8');
 assert.match(config,/define: \{\n    __MODU_BUILD_SHA__: JSON\.stringify\(cleanCommitSha\(\)\),\n  \}/);
 const start=config.indexOf('export function cleanCommitSha'),end=config.indexOf('\n}\n',start)+3;
 const js=ts.transpileModule(config.slice(start,end),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};new Function('exports','execFileSync','__dirname',js)(exports,()=>{throw new Error('git must not run in a test');},'/repo');const clean=exports.cleanCommitSha;
 const sha='d'.repeat(40);const git=(head,status)=>args=>args[0]==='rev-parse'?head+'\n':status;
 assert.equal(clean(git(sha,'')),sha);
 assert.equal(clean(git(sha,' M src/a.ts\n')),null,'tracked change');assert.equal(clean(git(sha,'?? new.ts\n')),null,'untracked file');
 assert.equal(clean(git('not-a-sha','')),null);assert.equal(clean(()=>{throw new Error('no git');}),null);
});
