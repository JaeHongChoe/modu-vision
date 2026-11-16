'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {verifyRuntime,completeFilteredMarkers}=require('../geti-package-runtime.cjs');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function fixture(){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'geti-package-'));
  const entries={'python/bin/python3.14':'python','source/LICENSE':'license','overlay/server.py':'server','python/lib/dependency.so':'dependency'};
  const files=Object.entries(entries).map(([name,body])=>{const file=path.join(root,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,body);return {path:name,size:Buffer.byteLength(body),sha256:sha(body)};});
  const inventory=JSON.stringify({version:1,files,links:[]});fs.writeFileSync(path.join(root,'inventory.json'),inventory);
  fs.writeFileSync(path.join(root,'runtime.json'),JSON.stringify({version:1,upstream_commit:'ddcf394d550bf1b84ca262888260043af361f5e7',platform:process.platform,architecture:process.arch==='x64'?'x86_64':process.arch,python:'python/bin/python3.14',source_root:'source',entrypoint:'overlay/server.py',weights_root:'weights',inventory:'inventory.json',inventory_sha256:sha(inventory)}));
  return root;
}
test('packaged runtime verifies every dependency and remains relocatable',()=>{
  const root=fixture();assert.equal(verifyRuntime(root).files,4);
  const moved=root+'-한국어';fs.renameSync(root,moved);assert.equal(verifyRuntime(moved).files,4);
  fs.writeFileSync(path.join(moved,'python/lib/dependency.so'),'changed');assert.throws(()=>verifyRuntime(moved),/integrity/);
});
test('packaged runtime refuses inventory paths that escape the bundle',()=>{
  const root=fixture(),descriptor=JSON.parse(fs.readFileSync(path.join(root,'runtime.json')));
  const inventory=JSON.stringify({version:1,files:[{path:'../outside',size:1,sha256:sha('x')}],links:[]});
  fs.writeFileSync(path.join(root,'inventory.json'),inventory);descriptor.inventory_sha256=sha(inventory);fs.writeFileSync(path.join(root,'runtime.json'),JSON.stringify(descriptor));
  assert.throws(()=>verifyRuntime(root),/contained|relative|inventory/);
});
test('cross-target portable runtime cannot pass local package acceptance',()=>{
  const root=fixture(),file=path.join(root,'runtime.json'),descriptor=JSON.parse(fs.readFileSync(file));
  descriptor.architecture='another-architecture';fs.writeFileSync(file,JSON.stringify(descriptor));
  assert.throws(()=>verifyRuntime(root),/target OS\/architecture/);
});
test('packaging restores pinned empty source markers without omitting inventory checks',()=>{
  const root=fixture(),descriptorPath=path.join(root,'runtime.json'),descriptor=JSON.parse(fs.readFileSync(descriptorPath));
  const marker='source/application/backend/data/media/.gitkeep';fs.mkdirSync(path.dirname(path.join(root,marker)),{recursive:true});fs.writeFileSync(path.join(root,marker),'');
  const inventory=JSON.parse(fs.readFileSync(path.join(root,'inventory.json')));inventory.files.push({path:marker,size:0,sha256:sha('')});
  const raw=JSON.stringify(inventory);fs.writeFileSync(path.join(root,'inventory.json'),raw);descriptor.inventory_sha256=sha(raw);fs.writeFileSync(descriptorPath,JSON.stringify(descriptor));
  const copied=root+'-packaged';fs.cpSync(root,copied,{recursive:true,filter:source=>path.basename(source)!=='.gitkeep'});
  assert.throws(()=>verifyRuntime(copied),/ENOENT|integrity/);
  assert.equal(completeFilteredMarkers(root,copied),1);
  assert.equal(verifyRuntime(copied).files,5);
});
