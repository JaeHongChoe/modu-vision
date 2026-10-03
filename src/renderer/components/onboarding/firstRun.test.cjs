const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-01: the first-run guide opens by itself only on a first start, lists the example's steps in order and keeps the
// example notice next to its numbers.
function load(){const name=path.join(__dirname,'firstRun.ts'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const idle={state:'idle',steps:{},error:null,result:null};
test('the guide opens by itself only before any project has data, and not once dismissed or while the example runs',()=>{const f=load();
 assert.equal(f.shouldOpenFirstRun({version:1,dismissed:false,demo:idle},false),true);
 assert.equal(f.shouldOpenFirstRun({version:1,dismissed:false,demo:idle},true),false,'a project with data is not a first start');
 assert.equal(f.shouldOpenFirstRun({version:1,dismissed:true,demo:idle},false),false,'dismissed stays dismissed');
 assert.equal(f.shouldOpenFirstRun({version:1,dismissed:false,demo:{...idle,state:'running'}},false),false);
 assert.equal(f.shouldOpenFirstRun(null,false),false,'an unknown state (older backend, no answer) does not open it');
 assert.equal(f.shouldOpenFirstRun({version:1,dismissed:false,team:true,demo:idle},false),false,'never on a team server');});
test('the example steps keep their order and states, and the summary keeps the example notice apart from a quality claim',()=>{const f=load();
 const rows=f.demoStepRows({state:'running',steps:{dataset:{status:'done'},project:{status:'done'},import:{status:'running',detail:'64장'}},error:null,result:null});
 assert.deepEqual(rows.map(row=>row.id),['dataset','project','import','train','flow','inspect']);
 assert.deepEqual(rows.map(row=>row.status),['done','done','running','pending','pending','pending']);
 assert.equal(rows[2].detail,'64장');
 assert.equal(f.demoSummary({project_id:'p',project_name:'예제',job_id:'j',flow_version_id:'v',run_id:'r',counts:{NG:5,OK:7},images:12}),'예제 검사 12장 · OK 7 · NG 5');
 assert.equal(f.demoSummary({project_id:'p',project_name:'예제',job_id:'j',flow_version_id:'v',run_id:'r',counts:{NG:5,OK:6,REVIEW:1},images:12}),'예제 검사 12장 · OK 6 · NG 5 · REVIEW 1','a REVIEW count is never dropped');
 assert.match(f.EXAMPLE_NOTICE,/품질 승인이나 배포 근거로 쓸 수 없습니다/);});
test('a new example never collides with one already made',()=>{const f=load();
 assert.equal(f.nextExampleName('예제',[]),'예제');assert.equal(f.nextExampleName('예제',undefined),'예제');
 assert.equal(f.nextExampleName('예제',[{id:'a',name:'예제',project_dir:'/a'}]),'예제 2');
 assert.equal(f.nextExampleName('예제',[{id:'a',name:'예제',project_dir:'/a'},{id:'b',name:'예제 2',project_dir:'/b'}]),'예제 3');});
