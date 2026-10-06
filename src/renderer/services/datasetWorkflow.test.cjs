const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript');
const file=path.join(__dirname,'datasetWorkflow.ts'),raw=fs.readFileSync(file,'utf8');
const source=ts.createSourceFile(file,raw,ts.ScriptTarget.Latest,true);
const declaration=source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='workflowError');
assert.ok(declaration,'Production error formatter must exist');
const moduleValue=new Module(file,module);moduleValue._compile(ts.transpileModule(declaration.getText(source),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
const {workflowError}=moduleValue.exports;

test('validation errors identify the invalid input without echoing its value',()=>{
 assert.equal(workflowError({detail:[{loc:['body','window_width'],msg:'Input should be greater than 0',input:'private-input',ctx:{secret:'hidden'},url:'https://untrusted.invalid'}]}),'window_width: Input should be greater than 0');
});
test('actual API transport throws a validation array with HTTP status attached',()=>{
 const refusal=Object.assign([{loc:['body','window_width'],msg:'Input should be greater than 0',input:0}],{status:422});
 assert.equal(workflowError(refusal),'window_width: Input should be greater than 0');
 assert.equal(workflowError({message:'Finish the previous edit',status:409}),'Finish the previous edit');
});
test('multiple field validation errors are bounded and retain the nested field',()=>{
 const detail=Array.from({length:8},(_,i)=>({loc:['body','items',i,'width'],msg:'too long '+('x'.repeat(1000))}));
 const rendered=workflowError({detail});assert.equal(rendered.split('\n').length,5);assert.ok(rendered.includes('items.0.width:'));assert.ok(rendered.length<3600);
});
test('malformed validation rows do not stringify private objects',()=>{
 assert.equal(workflowError({detail:[null,{loc:['body'],msg:{secret:'hidden'}},{input:'private'}]}),'요청을 처리하지 못했습니다.');
 assert.equal(workflowError(new Error('Connection lost')),'Connection lost');
 assert.equal(workflowError({detail:'A previous save changed'}),'A previous save changed');
});
