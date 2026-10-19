// The data step's two beginner traps: an imbalance warning that fired on balanced data, and a split calibrator that
// showed a preview (80/10/10 → 51/6/7) while the saved split (40/12/12) was what actually applied.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return mod.exports;}
const {severeClassImbalance,calibratorView}=load('splitCalibrator.ts');

test('a balanced 32/32 set is not called imbalanced; 1 vs 40 is; a single class or an empty class is ignored',()=>{
 assert.equal(severeClassImbalance({NG:32,OK:32}),false);
 assert.equal(severeClassImbalance({NG:21,OK:21}),false,'the old floor made any class over 20 images "imbalanced"');
 assert.equal(severeClassImbalance({NG:1,OK:40}),true);
 assert.equal(severeClassImbalance({NG:0,OK:40}),false);
 assert.equal(severeClassImbalance({OK:40}),false);
});

test('before the user touches the calibrator the saved split is shown with its own ratios',()=>{
 const view=calibratorView({touched:false,locked:false,applied:{train:40,val:12,test:12},totalImages:64,ratios:{train:80,val:10,test:10}});
 assert.equal(view.mode,'applied');
 assert.deepEqual(view.counts,{train:40,val:12,test:12});
 assert.deepEqual(view.ratios,{train:63,val:19,test:18});
 assert.equal(view.ratios.train+view.ratios.val+view.ratios.test,100);
});

test('after a preset or slider change the preview counts come from the chosen ratios',()=>{
 const view=calibratorView({touched:true,locked:false,applied:{train:40,val:12,test:12},totalImages:64,ratios:{train:80,val:10,test:10}});
 assert.equal(view.mode,'preview');
 assert.deepEqual(view.counts,{train:51,val:6,test:7});
 assert.deepEqual(view.ratios,{train:80,val:10,test:10});
});

test('a task whose split cannot be changed always shows the saved split; with nothing saved the preview is shown',()=>{
 assert.equal(calibratorView({touched:true,locked:true,applied:{train:5,val:1,test:1},totalImages:7,ratios:{train:80,val:10,test:10}}).mode,'applied');
 assert.equal(calibratorView({touched:false,locked:false,applied:{train:0,val:0,test:0},totalImages:64,ratios:{train:80,val:10,test:10}}).mode,'preview');
});

test('the data step reads these helpers and names the split sets in Korean',()=>{
 const source=fs.readFileSync(path.resolve(__dirname,'DatasetStudio.tsx'),'utf8');
 assert.match(source,/severeClassImbalance\(/);
 assert.match(source,/calibratorView\(/);
 assert.ok(!/Math\.min\(\.\.\.Object\.values\(classes\), 1\)/.test(source),'the inverted floor is still there');
 assert.ok(!/>\s*Train: \{/.test(source),'split labels are still English');
});
