const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const t=load('flowTestSet.ts');
const image=(n)=>({image_uuid:`u${n}`,sha256:`${n}`.repeat(64).slice(0,64),relative_path:`ok/${n}.png`,file_name:`${n}.png`,file_path:`/data/ok/${n}.png`});
test('saved test sets keep identities and count paths from older versions they cannot resolve',()=>{
 assert.deepEqual(t.parseSavedTestSet(null),{saved:[],legacyPaths:0});
 assert.deepEqual(t.parseSavedTestSet('{broken'),{saved:[],legacyPaths:0});
 const mixed=t.parseSavedTestSet(JSON.stringify(['/old/a.png',image(1),{bad:true},'/old/b.png']));
 assert.deepEqual(mixed,{saved:[image(1)],legacyPaths:2});
 assert.equal(t.parseSavedTestSet(JSON.stringify(Array.from({length:30},(_,n)=>image(n)))).saved.length,20,'never beyond the API limit');});
test('confirmed images stay usable and the rest stay saved with their reason instead of vanishing',()=>{
 const saved=[image(1),image(2),image(3),image(4),image(5)];
 const results=[{status:'found',current:{file_path:'/moved-root/ok/1.png'}},{status:'moved',candidates:[{}]},{status:'changed'},{status:'unreadable'},{status:'missing'}];
 const {kept,unresolved,notice}=t.applyResolution(saved,results,1);
 assert.deepEqual(kept,[{...image(1),file_path:'/moved-root/ok/1.png'}]);
 assert.deepEqual(unresolved.map(row=>[row.image_uuid,row.status,t.unresolvedReason(row)]),[['u2','moved','옮겨짐'],['u3','changed','내용 바뀜'],['u4','unreadable','읽지 못함'],['u5','missing','찾을 수 없음']]);
 assert.match(notice,/확인이 필요한 4장/);assert.match(notice,/비교에서 빠지고 목록에 남습니다/);assert.match(notice,/경로로만 저장된 1장/);
 assert.equal(t.applyResolution([image(1)],[{status:'found',current:{file_path:'/a'}}]).notice,null);});
test('toggling adds, removes and stops at the limit, counting saved images that need checking',()=>{
 let set=[];for(let n=0;n<25;n+=1)set=t.toggleTestImage(set,image(n));
 assert.equal(set.length,20);set=t.toggleTestImage(set,image(3));assert.equal(set.length,19);assert.ok(!set.some(e=>e.image_uuid==='u3'));
 assert.equal(t.toggleTestImage(set,image(40),1).length,19,'19 usable + 1 needing a check fill the 20 saved places');});
test('legacy paths are read from every key, deduplicated, and kept apart from the identity set',()=>{
 assert.deepEqual(t.legacySavedPaths(JSON.stringify(['/a','/b']),JSON.stringify(['/b','/c']),'{broken',null),['/a','/b','/c']);
 assert.equal(t.legacyStorageKey('flow-test-set:v2:x'),'flow-test-set:v2:x:paths');
 assert.deepEqual(t.parseSavedTestSet(JSON.stringify([{...image(1),status:'missing',extra:1}])).saved,[image(1)],'only identity fields are kept');});
