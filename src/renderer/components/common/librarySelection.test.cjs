const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
let transport='local',context=null;
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref==='../../services/api'?{getApiPersistenceIdentity:()=>transport,getProjectContext:()=>context}:ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const store=new Map();globalThis.window={localStorage:{getItem:key=>store.has(key)?store.get(key):null,setItem:(key,value)=>store.set(key,String(value))}};
const s=load('librarySelection.ts');
const item={relative_path:'ng/검사 1.png',image_uuid:'u-1',sha256:'a'.repeat(64),file_name:'검사 1.png',file_path:'/data/ng/검사 1.png',size:1,width:8,height:8,label:'ng',split:null,valid:true,error_code:null,error_detail:null,annotation_labels:[],annotation_error:null,tags:[],product:null,lot:null,workflow_state:'unworked',usage_state:'active'};
test('a library row becomes a selection that carries its identity, and only such selections are remembered',()=>{
 const chosen=s.selectionFromImage(item);
 assert.deepEqual(s.identityOf(chosen),{image_uuid:'u-1',sha256:'a'.repeat(64),relative_path:'ng/검사 1.png'});
 assert.equal(chosen.thumbnailUrl,'/api/dataset/thumbnail/%EA%B2%80%EC%82%AC%201.png?file_path=%2Fdata%2Fng%2F%EA%B2%80%EC%82%AC%201.png');
 assert.equal(s.identityOf({source:'file',imagePath:'/x.png',fileName:'x.png'}),null,'a local file has no identity to resolve');
 assert.equal(s.identityOf({source:'dataset',imagePath:'/x.png',fileName:'x.png'}),null,'a path-only dataset choice is not resolvable');
 s.rememberSelection('p1',{source:'dataset',imagePath:'/x.png',fileName:'x.png'});assert.equal(s.recallSelection('p1'),null);
 s.rememberSelection('p1',chosen);assert.equal(s.recallSelection('p1').imageUuid,'u-1');assert.equal(s.recallSelection('p2'),null);
 store.set('modu.inspectionImage.p3','{not json');assert.equal(s.recallSelection('p3'),null,'a damaged entry is ignored');});
test('a saved choice that no longer names the same bytes is explained, never silently replaced',()=>{
 const base={image_uuid:'u-1',sha256:'a'.repeat(64),relative_path:'ng/a.png',current:null,candidates:[]};
 assert.equal(s.resolutionNotice({...base,status:'found'}),null);assert.equal(s.resolutionNotice(null),null);
 assert.match(s.resolutionNotice({...base,status:'changed'}),/다른 내용의 파일/);
 assert.match(s.resolutionNotice({...base,status:'missing'}),/찾을 수 없습니다/);
 assert.match(s.resolutionNotice({...base,status:'unreadable'}),/읽지 못했습니다/);
 const moved=s.resolutionNotice({...base,status:'moved',candidates:[{relative_path:'ok/a.png'},{relative_path:'ok/b.png'}]});
 assert.match(moved,/ok\/a\.png 외 1곳/);assert.match(moved,/직접 선택/);});

test('equal project IDs on different servers, workspaces and accounts never restore another choice',()=>{
 store.clear();const chosen=s.selectionFromImage(item);
 try{transport='shared:server-A';context={workspace_id:'w-A',actor_id:'u-A'};s.rememberSelection('same',chosen);
 transport='shared:server-B';assert.equal(s.recallSelection('same'),null);
 transport='shared:server-A';context={workspace_id:'w-B',actor_id:'u-A'};assert.equal(s.recallSelection('same'),null);
 context={workspace_id:'w-A',actor_id:'u-B'};assert.equal(s.recallSelection('same'),null);
 context={workspace_id:'w-A',actor_id:'u-A'};assert.equal(s.recallSelection('same').imageUuid,'u-1');
 const legacy=JSON.stringify(chosen);store.set('modu.inspectionImage.legacy',legacy);assert.equal(s.recallSelection('legacy'),null,'unscoped old entries are preserved but cannot prove which account saved them');assert.equal(store.get('modu.inspectionImage.legacy'),legacy);
 }finally{transport='local';context=null;store.clear();}
});
