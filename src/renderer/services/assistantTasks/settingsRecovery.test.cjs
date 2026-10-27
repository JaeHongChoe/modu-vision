const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'settingsRecovery.ts'),m=new Module(file,module);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);const recover=m.exports.trainingSettingsRecovery;
const values=['exact physical workspace/actor/project','source','folder','key',null,null,'fast',null,null],current=()=>JSON.stringify(values),saved=(device='cpu',batch=8)=>JSON.stringify([...values.slice(0,7),device,batch]);
test('reload recovery names only the original nondefault local inputs',()=>{assert.deepEqual(recover(saved(),current()),{device:'cpu',batch:8,label:'저장 설정 복원 · CPU · 배치 8'});assert.equal(recover(current(),current()),null);});
test('changed physical project source compute preset or manual inputs never restore',()=>{
 for(let i=0;i<7;i++){const changed=[...values];changed[i]='different';assert.equal(recover(saved(),JSON.stringify(changed)),null);}
 assert.equal(recover(saved(),saved('mps',null)),null);assert.equal(recover(saved(),saved(null,16)),null);
});
test('malformed or unsupported stored settings are not a recovery instruction',()=>{for(const value of ['[]','null','invalid',saved('shell',8),saved('cpu',0),saved('cpu',129),saved('cpu',1025)])assert.equal(recover(value,current()),null);});
