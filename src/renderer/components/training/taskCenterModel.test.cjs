const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const model=()=>load('taskCenterModel.ts');
test('S1-04: each cancel step is done only with its own evidence, never because a later stage was reached',()=>{const m=model();
 const row=(observation)=>m.normalizeTask('training',{job_id:'j',status:'aborted',observation});
 assert.equal(m.observationSummary(row(undefined)),null);
 const localExit=m.observationSummary(row({cause:'cancelled',next_action:null,cancel:{stage:'exited',requested_at:1,acknowledged_at:null,signals:[],exit_confirmed:true,reservation_released:false}}));
 assert.deepEqual(localExit.steps.map(step=>step.done),[true,false,false,true,false],'no acknowledgement and no signal were recorded');
 const full=m.observationSummary(row({cause:'cancelled',next_action:null,cancel:{stage:'released',requested_at:1,acknowledged_at:2,signals:['cooperative'],exit_confirmed:true,reservation_released:true}}));
 assert.ok(full.steps.every(step=>step.done));
 const lost=m.observationSummary(row({cause:'cancel_unconfirmed',next_action:'연결을 복구하면 같은 작업을 다시 확인합니다.',cancel:{stage:'signalled',requested_at:1,acknowledged_at:null,signals:['cooperative'],exit_confirmed:false,reservation_released:null}}));
 assert.deepEqual(lost.steps.map(step=>step.done),[true,false,true,false,false]);assert.match(lost.nextAction,/다시 확인/);});
