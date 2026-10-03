const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file,mocks={}){const name=path.resolve(__dirname,file);if(!fs.existsSync(name))return {};const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const req=m.require.bind(m);m.require=ref=>mocks[ref]??req(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const image=(id,sha='a'.repeat(64))=>({image_uuid:id,sha256:sha,relative_path:`ok/${id}.png`,file_name:`${id}.png`,file_path:`/s/ok/${id}.png`});
const m=load('parityCohort.ts',{'./flowPackageRelease':load('flowPackageRelease.ts')});
test('saved cohort retains 64 identities, deduplicates and refuses malformed path/hash entries',()=>{
 assert.equal(typeof m.readParityCohort,'function');
 const rows=Array.from({length:70},(_,i)=>image(String(i)));
 const parsed=m.readParityCohort(JSON.stringify([null,{...image('bad'),sha256:'oops'},...rows,rows[0]]));
 assert.equal(parsed.length,64);assert.equal(parsed[0].image_uuid,'0');assert.equal(m.readParityCohort('{broken').length,0);
});
test('resolution binds each response by identity, preserves absent/changed/moved/unreadable selections',()=>{
 assert.equal(typeof m.resolveParityCohort,'function');
 const saved=['a','b','c','d','e'].map(id=>image(id));
 const found=id=>({image_uuid:id,sha256:'a'.repeat(64),status:'found',current:{...image(id),file_path:`/current/${id}.png`,valid:1},candidates:[]});
 const result=m.resolveParityCohort(saved,[found('b'),{...found('a'),status:'changed'}, {...found('c'),status:'moved'}, {...found('d'),status:'unreadable'}]);
 assert.deepEqual(result.picks.map(r=>r.image_uuid),['b']);assert.equal(result.picks[0].file_path,'/current/b.png');
 assert.deepEqual(result.unresolved.map(r=>[r.image_uuid,r.status]),[['a','changed'],['c','moved'],['d','unreadable'],['e','missing']]);
 assert.equal(m.resolveParityCohort([image('a')],[{...found('a'),current:{...found('a').current,sha256:'b'.repeat(64)}}]).picks.length,0);
});
test('cohort never auto-replaces duplicate-content moved entries and counts unresolved against its cap',()=>{
 assert.equal(typeof m.pickParityImage,'function');
 const unresolved=[{...image('old'),status:'moved'}];
 const next=m.pickParityImage([],unresolved,{...image('new'),valid:1});
 assert.equal(next.unresolved.length,1,'same digest at another path is not identity');
 assert.equal(next.picks.length,1);
 const full=Array.from({length:63},(_,i)=>image(String(i)));
 assert.equal(m.pickParityImage(full,unresolved,{...image('new'),valid:1}).picks.length,63);
 assert.equal(m.pickParityImage([],unresolved,{...image('bad'),valid:0}).picks.length,0);
 assert.equal(m.pickParityImage([],unresolved,{...image('old'),valid:1}).unresolved.length,0);
});
test('storage namespace separates backend, actor, project, source, task and labelset',()=>{
 assert.equal(typeof m.parityCohortStorageKey,'function');
 const scope={backend:'local',workspace:'w',actor:'a',project:'p',projectDir:'/project',source:'/s',task:'segmentation',labelset:'default'};
 const first=m.parityCohortStorageKey(scope);
 for(const key of Object.keys(scope))assert.notEqual(m.parityCohortStorageKey({...scope,[key]:scope[key]+'2'}),first,key);
});
