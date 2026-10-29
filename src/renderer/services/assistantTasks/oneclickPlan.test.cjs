const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'oneclickPlan.ts');
function plan(input){assert.ok(fs.existsSync(file),'oneclick plan builder exists');const m=new Module(file,module);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports.oneclickPlan(input);}
test('oneclick uses the same bounded task actions and preserves a current completed model',()=>{
 const prepared=plan({splitSaved:true,currentModel:true,trainRatio:.8});
 assert.deepEqual(prepared.steps.map(row=>row.action),['evaluate_model','save_flow','evaluate_flow','inspect_test']);
 const fresh=plan({splitSaved:false,currentModel:false,trainRatio:.8});
 assert.deepEqual(fresh.steps.map(row=>row.action),['save_split','start_training','evaluate_model','save_flow','evaluate_flow','inspect_test']);
 assert.deepEqual(fresh.steps[0].args,{train_percent:80,val_percent:10,test_percent:10});
 assert.ok(!JSON.stringify(fresh).includes('approve'));
});
