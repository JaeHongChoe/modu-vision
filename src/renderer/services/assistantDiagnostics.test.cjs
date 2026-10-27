const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(name){const file=path.join(__dirname,name+'.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function diagnostics(){assert.ok(fs.existsSync(path.join(__dirname,'assistantDiagnostics.ts')),'safe diagnostic utilities must exist');return load('assistantDiagnostics');}
test('diagnostic text removes private addresses, paths and credential assignments before bounded output',()=>{
 const {diagnosticText}=diagnostics(),raw='failed /Users/kai/private/a.png C:\\private\\b.png \\\\nas\\share\\c.png ~/hidden/d.png https://secret.example/x?token=x worker.internal:22 ssh user@worker.internal password=short api_key: "secret-key" Authorization: Bearer abcdefghijklmnop kai@example.com 114.110.134.49';
 const safe=diagnosticText(raw,1000);for(const secret of ['/Users/kai','C:\\private','nas','~/hidden','secret.example','worker.internal','user@','short','secret-key','abcdefghijklmnop','kai@example.com','114.110.134.49'])assert.ok(!safe.includes(secret),secret);
 assert.ok(safe.includes('a.png'));assert.ok(diagnosticText('가'.repeat(900),80).length<=80);assert.equal(diagnosticText({secret:'do not stringify'}),null);assert.equal(diagnosticText(null),null);
});
test('coded HTTP and catalog failures retain safe reason and status instead of object stringification',()=>{
 const {diagnosticError}=diagnostics();assert.deepEqual(diagnosticError({status:409,error_code:'ERR_CONFLICT',message_ko:'현재 자료가 바뀌었습니다.'}),{code:'ERR_CONFLICT',message:'현재 자료가 바뀌었습니다.',status:409});
 assert.deepEqual(diagnosticError({status:503,detail:{code:'ERR_001',message_kr:'메모리가 부족합니다.'}}),{code:'ERR_001',message:'메모리가 부족합니다.',status:503});
 const value=diagnosticError({details:{headers:{Authorization:'Bearer secret'}}});assert.equal(value.code,null);assert.equal(value.status,null);assert.doesNotMatch(value.message,/object Object|secret|Authorization/);
 assert.equal(diagnosticError(Object.assign(new Error('disk full /Volumes/private/results'),{status:500})).status,500);
});
test('quoted credential keys and IPv6 or short host-port addresses do not escape redaction',()=>{
 const {diagnosticText}=diagnostics(),safe=diagnosticText('"password":"tiny-secret" "api_key": "small-key" host:2222 [2001:db8::1]:22 ssh user@host',1000);
 for(const secret of ['tiny-secret','small-key','host:2222','2001:db8','user@host'])assert.ok(!safe.includes(secret),secret);
});
test('registered dotted diagnostic codes retain their identity without treating the code as a host',()=>{
 const {diagnosticError}=diagnostics();assert.equal(diagnosticError({code:'training.failed',message:'학습 실패'}).code,'training.failed');
 assert.equal(diagnosticError({code:'private.internal',message:'fail'}).code,null);
});
test('Cookie and Digest headers remove the complete value rather than the first attribute',()=>{
 const {diagnosticText}=diagnostics();
 for(const text of ['Cookie: first=synthetic-one; second=synthetic-two','Set-Cookie: first=synthetic-one; Path=/; second=synthetic-two','"Authorization": "Digest username=synthetic-user, nonce=synthetic-nonce"']){
  assert.doesNotMatch(diagnosticText(text,2000),/synthetic-one|synthetic-two|synthetic-user|synthetic-nonce/);
 }
});
test('escaped quotes inside short assigned credentials do not leave a trailing secret fragment',()=>{
 const {diagnosticText}=diagnostics();
 for(const text of ['password: "synthetic \\"phrase\\" tail"',"token: 'synthetic \\'phrase\\' tail'"]){
   assert.doesNotMatch(diagnosticText(text,2000),/synthetic|phrase|tail/);
 }
});
test('catalog cause and recovery keep only sanitized allowlisted evidence and severity',()=>{
 const {diagnosticError}=diagnostics(),value=diagnosticError({code:'ERR_001',severity:'critical',description_kr:'메모리 부족 /Users/private/model.pt',cause_kr:'장치 host:22 password=short 부족',remediation_kr:'배치를 줄이세요. https://private.example/recover token=abc',action:'reduce_batch_size',auto_fixable:true,details:{raw:'never include raw diagnostic details',headers:{Authorization:'secret'}}});
 assert.equal(value.severity,'critical');assert.ok(value.cause.includes('부족'));assert.ok(value.suggested_action.includes('배치를 줄이세요.'));
 assert.doesNotMatch(JSON.stringify(value),/\/Users|private\.example|host:22|password=short|token=abc|never include|Authorization|auto_fixable|details/);
 const invalid=diagnosticError({code:'ERR_BAD',severity:'execute_code',details:'raw-only-details'});assert.equal(invalid.severity,undefined);assert.doesNotMatch(JSON.stringify(invalid),/execute_code|raw-only-details/);
 const nested=diagnosticError({status:500,detail:{error_code:'ERR_001',description_kr:'부족',cause_kr:'작은 메모리',remediation:'배치 감소',severity:'high'}});assert.equal(nested.cause,'작은 메모리');assert.equal(nested.suggested_action,'배치 감소');assert.equal(nested.severity,'high');
});
