const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');const ts=require('typescript');const test=require('node:test');
function load(file,mock){const name=path.resolve(__dirname,file),mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));const req=mod.require.bind(mod);mod.require=ref=>mock?.(ref)??req(ref);mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return mod.exports;}
const {parseGANCrops,validateGANRegions}=load('ganComposition.ts');
test('native polygon mask text is explicit and invalid drafts block generation',()=>{
  const {parseGANMask}=load('ganComposition.ts');
  assert.deepEqual(parseGANMask('4,4;24,4;4,24'),[[4,4],[24,4],[4,24]]);
  assert.equal(parseGANMask(''),undefined);
  const row={id:'mask',bbox:[4,4,24,24],opacity:1,feather_px:0};
  assert.ok(validateGANRegions([{...row,mask_polygon:parseGANMask('4,4;12,12;24,24')}],[64,48]));
  assert.ok(validateGANRegions([{...row,mask_polygon:parseGANMask('4,4;bad;')}],[64,48]));
});
test('explicit crop rows preserve human labels and never infer truth from filenames',()=>{
  assert.deepEqual(parseGANCrops('some_NG_name.png\t0,0,32,32\ttrain'),[{image:'some_NG_name.png',bbox:[0,0,32,32],split:'train'}]);
  assert.equal(parseGANCrops('image.png\t1,2,33,34\tval\tscratch')[0].label,'scratch');
  assert.throws(()=>parseGANCrops('image.png\t0,0,4,4\ttrain'));
});
test('composition bounds reject invalid native coordinates and retain multiple regions',()=>{
  const rows=[{id:'first',bbox:[8,8,40,40],opacity:1,feather_px:0},{id:'second',bbox:[60,10,80,30],opacity:.5,feather_px:3}];
  assert.equal(validateGANRegions(rows,[100,80]),null);
  assert.ok(validateGANRegions([{...rows[0],opacity:NaN}],[100,80]));
  assert.ok(validateGANRegions([{...rows[0],bbox:[0,0,101,40]}],[100,80]));
  assert.ok(validateGANRegions([rows[0],rows[0]],[100,80]));
});
test('owned preparation and source generation send canonical provenance contracts',async()=>{
  const requests=[];
  const {ganWorkflow}=load('../../services/ganWorkflow.ts',ref=>ref==='./api'?{request:async(url,options)=>{requests.push({url,body:options?JSON.parse(options.body):null});return {};}}:undefined);
  const rows=parseGANCrops('a.png\t0,0,32,32\ttrain\tscratch');
  await ganWorkflow.prepare('/canonical/source',rows);
  assert.equal(requests[0].url,'/api/defect-gan/prepare');assert.equal(requests[0].body.source_dataset_path,'/canonical/source');
  const composition={source_image_path:'/canonical/source/a.png',source_sha256:'a'.repeat(64),regions:[{id:'one',bbox:[0,0,32,32],opacity:1,feather_px:0}]};
  await ganWorkflow.generate('b'.repeat(32),2,41,'cpu',composition);
  assert.deepEqual(requests[1].body,{job_id:'b'.repeat(32),count:2,seed:41,device:'cpu',...composition});
});
