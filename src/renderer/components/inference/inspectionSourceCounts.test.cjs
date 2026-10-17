const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');
const filename = path.join(__dirname, 'inspectionSourceCounts.ts');
const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(__dirname);
m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
const {inspectionCountKey, inspectionCounts, startInspectionCountRead} = m.exports;
const base = {sourceReady: true, sourceKey: 'source-revision-1', folderPath: '/owned/source', task: 'segmentation', projectDir: '/owned/project', project: {id: 'p1', project_dir: '/owned/project', source_dataset_dir: '/owned/source', task: 'segmentation', active_labelset_id: 'default'}, apiScope: 'local-context-generation1'};
const stats = (n=2, labelset='default') => ({total: n*3, labelset_id: labelset, assignments: {train: {count:n}, val:{count:n}, test:{count:n}, not_used:{count:0}, not_split:{count:0}}});
const deferred = () => {let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject};};
test('inspection counts use image-grain splits when training eligibility is zero', () => {
 assert.deepEqual(inspectionCounts(stats(), 'default'), {all:6,train:2,val:2,test:2});
 assert.deepEqual(inspectionCounts(stats(3), 'default'), {all:9,train:3,val:3,test:3});
 assert.deepEqual(inspectionCounts(stats(0), 'default'), {all:0,train:0,val:0,test:0});
});
test('malformed counts and a response from another active label set fail rather than display a false zero', () => {
 for (const bad of [null, {...stats(),total:-1}, {...stats(),total:7}, {...stats(),total:6.1}, {...stats(),assignments:{...stats().assignments,test:{count:NaN}}}, {...stats(),assignments:{train:{count:2}}}]) assert.throws(()=>inspectionCounts(bad,'default'));
 assert.throws(()=>inspectionCounts(stats(2,'other'),'default'),/라벨 세트/);
});
test('scope binds source, task, project, labelset and API context and refuses an unowned source', () => {
 const original=inspectionCountKey(base);assert.equal(typeof original,'string');
 for(const change of [{sourceReady:false},{project:null},{folderPath:'/foreign/source'},{projectDir:'/foreign/project'},{task:'classification'}]) assert.equal(inspectionCountKey({...base,...change}),null);
 for(const change of [{sourceKey:'source-revision-2'},{apiScope:'new-server'}, {project:{...base.project,id:'p2'}},{project:{...base.project,active_labelset_id:'other'}}]) assert.notEqual(inspectionCountKey({...base,...change}),original);
});
test('a fresh owned read publishes only verified counts and joins its settlement', async () => {
 const seen=[],source=deferred();let signal;
 const op=startInspectionCountRead({key:'p1',labelset:'default',isCurrent:()=>true,read:s=>{signal=s;return source.promise;},publish:v=>seen.push(v)});
 assert.deepEqual(seen,[{key:'p1',state:'loading'}]);source.resolve(stats());await op.settled;
 assert.deepEqual(seen.at(-1),{key:'p1',state:'ready',counts:{all:6,train:2,val:2,test:2}});assert.equal(signal.aborted,false);
});
test('late success from a switched project cannot overwrite its replacement', async () => {
 const seen=[],source=deferred();let current=true;
 const op=startInspectionCountRead({key:'old',labelset:'default',isCurrent:()=>current,read:()=>source.promise,publish:v=>seen.push(v)});
 current=false;source.resolve(stats());await op.settled;assert.equal(seen.length,1);
});
test('cancel really aborts the request and suppresses late failures or success', async () => {
 const seen=[],source=deferred();let signal;
 const op=startInspectionCountRead({key:'old',labelset:'default',isCurrent:()=>true,read:s=>{signal=s;return source.promise;},publish:v=>seen.push(v)});
 op.cancel();assert.equal(signal.aborted,true);await op.settled;source.reject(new Error('late failure'));await Promise.resolve();assert.equal(seen.length,1);
});
test('a fresh reopen after cancellation publishes its own counts', async () => {
 const seen=[],source=deferred();const first=startInspectionCountRead({key:'p1',labelset:'default',isCurrent:()=>true,read:()=>source.promise,publish:v=>seen.push(v)});first.cancel();await first.settled;
 const reopened=startInspectionCountRead({key:'p1',labelset:'default',isCurrent:()=>true,read:async()=>stats(),publish:v=>seen.push(v)});await reopened.settled;
 source.resolve(stats(9));await Promise.resolve();assert.deepEqual(seen.at(-1),{key:'p1',state:'ready',counts:{all:6,train:2,val:2,test:2}});
});
test('request errors settle with an honest missing-count state and retry can recover', async () => {
 const seen=[];const broken=startInspectionCountRead({key:'p1',labelset:'default',isCurrent:()=>true,read:async()=>{throw Error('Failed to fetch');},publish:v=>seen.push(v)});await broken.settled;
 assert.equal(seen.at(-1).state,'error');assert.match(seen.at(-1).message,/다시/);assert.equal('counts' in seen.at(-1),false);
 const retry=startInspectionCountRead({key:'p1',labelset:'default',isCurrent:()=>true,read:async()=>stats(),publish:v=>seen.push(v)});await retry.settled;assert.equal(seen.at(-1).state,'ready');
});
test('the original 10000 ms timer aborts and settles even if the transport ignores abort', async () => {
 const set=global.setTimeout,clear=global.clearTimeout,seen=[],source=deferred();let fire,delay,signal,cleared=false;
 global.setTimeout=(cb,ms)=>{fire=cb;delay=ms;return 123;};global.clearTimeout=(id)=>{assert.equal(id,123);cleared=true;};
 try{const op=startInspectionCountRead({key:'p1',labelset:'default',isCurrent:()=>true,read:s=>{signal=s;return source.promise;},publish:v=>seen.push(v)});assert.equal(delay,10000);fire();await op.settled;assert.equal(signal.aborted,true);assert.equal(cleared,true);assert.equal(seen.at(-1).state,'error');assert.equal('counts' in seen.at(-1),false);source.resolve(stats(99));await Promise.resolve();assert.equal(seen.at(-1).state,'error');}finally{global.setTimeout=set;global.clearTimeout=clear;}
});
