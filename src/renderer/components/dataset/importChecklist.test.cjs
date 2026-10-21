// Step 1 "학습 전 점검" (audit A1, F-01/F-02/F-03/F-06/F-10): the import result says what will block training or step 4.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return mod.exports;}
const {importChecks,classStartBlocker,APPROVAL_TEST_IMAGES_PER_ROLE,MIN_IMAGES_PER_CLASS}=load('importChecklist.ts');
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
test('a class with images below the per-class minimum blocks (F-03)',()=>{
 assert.equal(MIN_IMAGES_PER_CLASS,2);
 const one=byId(importChecks({...base,classes:{OK:24,NG:1}}),'class_minimum');
 assert.equal(one.level,'block');assert.equal(one.action,'reimport');assert.match(one.text,/2장 이상.*NG 1장/);
 // The reason is what the split really does: a two-image class can still miss val (always 2 / 0 / 0 at 80 / 10 / 10),
 // so the text says why one image is too few and promises nothing about two.
 assert.match(one.text,/한 장뿐인 클래스는 학습·검증·시험 중 한 곳에만 들어가/);assert.doesNotMatch(one.text,/한 장씩/);
 assert.match(byId(importChecks({...base,classes:{OK:24,NG:24,scratch:1}}),'class_minimum').text,/scratch 1장/);
 assert.equal(byId(importChecks(base),'class_minimum'),undefined);
 assert.equal(byId(importChecks({...base,classes:{OK:2,NG:2}}),'class_minimum'),undefined,'two images each reach the minimum');
 // One class with images is the two-class rule's block alone.
 assert.equal(byId(importChecks({...base,classes:{OK:24,NG:0}}),'class_minimum'),undefined);
});
test('the saved split: a class without training images blocks, one without validation or test images warns (F-03)',()=>{
 const untrained=importChecks({...base,classes:{OK:24,NG:5},split:{train:17,val:8,test:4},classSplitCounts:{OK:{train:17,val:5,test:2},NG:{train:0,val:3,test:2}}});
 assert.equal(byId(untrained,'class_train').level,'block');assert.match(byId(untrained,'class_train').text,/NG/);
 // 80 / 10 / 10 leaves a five-image class one image for val and test: 4 / 0 / 1 with the app's seed 42 (measured with
 // the step-1 split).
 const thin=importChecks({...base,classes:{OK:40,NG:5},split:{train:36,val:4,test:5},classSplitCounts:{OK:{train:32,val:4,test:4},NG:{train:4,val:0,test:1}}});
 const check=byId(thin,'class_splits');
 assert.equal(check.level,'warn');assert.equal(check.action,'apply_split');assert.match(check.text,/NG \(학습 4 · 검증 0 · 시험 1\)/);
 // The per-preset floors from which the split puts every class in all three splits (measured over 200 seeds).
 assert.match(check.text,/80 \/ 10 \/ 10에서 8장, 70 \/ 20 \/ 10에서 6장, 60 \/ 20 \/ 20에서 4장 이상/);
 assert.equal(byId(thin,'class_train'),undefined);
 assert.equal(byId(importChecks(base),'class_splits'),undefined);assert.equal(byId(importChecks(base),'class_train'),undefined);
 assert.equal(byId(importChecks({...base,classes:{OK:40,NG:5},split:{train:36,val:4,test:5},splitState:'automatic',classSplitCounts:{OK:{train:32,val:4,test:4},NG:{train:4,val:0,test:1}}}),'class_splits'),undefined,'an unsaved split is not judged per class');
 // A split with no test images at all is the test block, not a per-class warning.
 assert.equal(byId(importChecks({...base,split:{train:40,val:8,test:0},classSplitCounts:{OK:{train:20,val:4,test:0},NG:{train:20,val:4,test:0}}}),'class_splits'),undefined);
});
test('step 1, the step-3 start and the footer give the class rules in the same sentence (F-03)',()=>{
 assert.equal(classStartBlocker({OK:24,NG:24},null,base.split),null);
 assert.match(classStartBlocker({OK:24,NG:1},null,base.split),/2장 이상.*NG 1장/);
 assert.equal(classStartBlocker({OK:24,NG:0},null,base.split),null,'the two-class rule speaks first');
 assert.match(classStartBlocker({OK:24,NG:5},{OK:{train:17,val:5,test:2},NG:{train:0,val:3,test:2}},{train:17,val:8,test:4}),/학습 분할에 이미지가 없는 클래스.*NG/);
 // Word for word: the step-1 check and the start notice / footer text.
 assert.equal(byId(importChecks({...base,classes:{OK:24,NG:1}}),'class_minimum').text,classStartBlocker({OK:24,NG:1},base.classSplitCounts,base.split));
 const untrained={...base,classes:{OK:24,NG:5},split:{train:17,val:8,test:4},classSplitCounts:{OK:{train:17,val:5,test:2},NG:{train:0,val:3,test:2}}};
 assert.equal(byId(importChecks(untrained),'class_train').text,classStartBlocker(untrained.classes,untrained.classSplitCounts,untrained.split));
 assert.equal(classStartBlocker({OK:40,NG:5},{OK:{train:32,val:4,test:4},NG:{train:4,val:0,test:1}},{train:36,val:4,test:5}),null,'a class without validation images only warns');
});
test('the step-3 start waits on that rule and shows the per-class split warning beside it (F-03)',()=>{
 const controller=fs.readFileSync(path.join(__dirname,'..','training','TrainingController.tsx'),'utf8');
 assert.match(controller,/classStartBlocker\(datasetClasses/);assert.match(controller,/populatedClasses < 2 \|\| !!classBlocker/);
 assert.match(controller,/aria-label="학습 전 확인"/);
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
