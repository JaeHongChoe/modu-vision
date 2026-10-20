// Step 1 "학습 전 점검" (audit A1, F-01/F-02/F-03/F-06/F-10): the import result says what will block training or step 4.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return mod.exports;}
const {importChecks,APPROVAL_TEST_IMAGES_PER_ROLE}=load('importChecklist.ts');
const base={task:'classification',totalImages:48,classes:{OK:24,NG:24},split:{train:34,val:10,test:4},splitState:'saved',
 classSplitCounts:{OK:{train:17,val:5,test:2},NG:{train:17,val:5,test:2}},corrupted:0,excludedUndecodable:null,labelsMissing:false,expectedLayout:'',splitLocked:false};
const byId=(checks,id)=>checks.find(check=>check.id===id);
test('no images explains the expected layout and stops there',()=>{
 const checks=importChecks({...base,totalImages:0,classes:{},expectedLayout:'클래스마다 폴더 하나'});
 assert.equal(checks.length,1);assert.equal(checks[0].level,'block');assert.match(checks[0].text,/클래스마다 폴더 하나/);
});
test('one class blocks, two classes pass',()=>{
 assert.equal(byId(importChecks({...base,classes:{OK:24,NG:0}}),'classes').level,'block');
 assert.match(byId(importChecks({...base,classes:{OK:24,NG:0}}),'classes').text,/빈 클래스: NG/);
 assert.equal(byId(importChecks(base),'classes').level,'ok');
});
test('excluded undecodable images are named; unrecorded ones block',()=>{
 assert.match(byId(importChecks({...base,corrupted:1,excludedUndecodable:{excluded:['OK/corrupt.png'],recorded:true}}),'undecodable').text,/OK\/corrupt\.png/);
 assert.equal(byId(importChecks({...base,corrupted:1,excludedUndecodable:{excluded:[],recorded:false}}),'undecodable').level,'block');
});
test('an automatic or stale classification split blocks with the save action',()=>{
 for(const splitState of ['automatic','stale']){const check=byId(importChecks({...base,splitState}),'split');assert.equal(check.level,'block');assert.equal(check.action,'apply_split');}
 assert.equal(byId(importChecks(base),'split'),undefined);
});
test('test size: zero blocks, small warns with the per-image swing, approval counts per role',()=>{
 assert.equal(byId(importChecks({...base,split:{train:40,val:8,test:0}}),'test').level,'block');
 assert.match(byId(importChecks(base),'test').text,/4장뿐.*25%p/);
 assert.match(byId(importChecks(base),'approval').text,new RegExp(`각각 ${APPROVAL_TEST_IMAGES_PER_ROLE}장.*정상 2 · 불량 2`));
 const enough={...base,split:{train:20,val:10,test:40},classSplitCounts:{OK:{train:10,val:5,test:20},NG:{train:10,val:5,test:20}}};
 assert.equal(byId(importChecks(enough),'test'),undefined);assert.equal(byId(importChecks(enough),'approval'),undefined);
});
test('detection or segmentation without labels blocks with the layout',()=>{
 const check=byId(importChecks({...base,task:'segmentation',classes:{defect_mask:0},labelsMissing:true,expectedLayout:'images/ 와 masks/'}),'labels_missing');
 assert.equal(check.level,'block');assert.match(check.text,/masks\//);
});
