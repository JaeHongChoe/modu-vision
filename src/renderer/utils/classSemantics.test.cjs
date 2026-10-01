const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,mocks={}){const name=path.resolve(__dirname,file);assert.ok(fs.existsSync(name),`${file} exists`);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=key=>mocks[key]||(key.endsWith('/classSemantics')||key==='./classSemantics'?load(path.resolve(path.dirname(name),key)+'.ts'):req(key));m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const {cases}=JSON.parse(fs.readFileSync(path.join(__dirname,'classSemanticsCases.json'),'utf8'));
test('renderer resolver matches every shared backend case',()=>{
 const m=load('./classSemantics.ts');
 for(const c of cases){assert.equal(m.classRole(c.name),c.role,`role of ${JSON.stringify(c.name)}`);assert.equal(m.isDefectClass(c.name),c.role!=='normal',`defect flag of ${JSON.stringify(c.name)}`);}
});
test('explicit roles override aliases and unknown names never become normal',()=>{
 const m=load('./classSemantics.ts');
 assert.equal(m.classRole('scratch',{scratch:'normal'}),'normal');assert.equal(m.classRole('OK',{OK:'defect'}),'defect');
 assert.equal(m.isDefectClass('OK',{OK:'defect'}),true);assert.equal(m.classRole('ok_ng'),'defect');assert.deepEqual(m.resolveClassRole('ok_ng'),['defect','conflict']);assert.equal(m.classRole('x',{x:'unknown'}),'unknown');assert.equal(m.isDefectClass('ok_ng'),true);
 assert.equal(m.classRole('scratch'),'defect');assert.equal(m.CLASS_SEMANTICS_VERSION,1);
});
test('evaluation colours use the shared resolver for Korean normal classes and recorded roles',()=>{
 const mocks={'zustand':{create:()=>()=>({})},'../services/api':{api:{},getApiBaseUrl:()=>''},'./useTrainingStore':{useTrainingStore:{getState:()=>({})}}};
 const store=load('../stores/useEvaluationStore.ts',mocks);
 for(const name of ['정상','양품','합격','no_defect','non-defect'])assert.equal(store.isDefectLabel(name),false,name);
 for(const name of ['불량','불합격','scratch','ok_ng'])assert.equal(store.isDefectLabel(name),true,name);
 assert.equal(store.isDefectLabel('alpha',{alpha:'normal'}),false);assert.equal(store.isDefectLabel(0),false);assert.equal(store.isDefectLabel(2),true);assert.equal(store.isDefectLabel(undefined),false);
});
