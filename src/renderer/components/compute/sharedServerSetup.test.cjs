// Audit F-17 / B4: the shared server set up from a few answers, loopback only, no password in any command.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(){const file=path.join(__dirname,'sharedServerSetup.ts'),m=new Module(file,module);m.filename=file;
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
test('the commands bind to 127.0.0.1, quote paths and never carry a password',()=>{
 const {setupCommands,shellQuote}=load();
 const steps=setupCommands({accountsDir:"/srv/my vision/accounts",projectsDir:'/srv/vision/projects',port:8123,admin:'admin',sshTarget:'kim@server'});
 assert.equal(steps[0].command,"python -m backend.accounts_cli --shared-auth-dir '/srv/my vision/accounts' --username admin");
 assert.match(steps[1].command,/--host 127\.0\.0\.1 --port 8123 /);assert.equal(steps[2].command,'ssh -N -L 8123:127.0.0.1:8123 kim@server');
 assert.equal(steps[3].command,'http://127.0.0.1:8123');
 assert.ok(steps.every(step=>!/password|비밀번호=/.test(step.command)));
 assert.equal(shellQuote("it's"),`'it'\\''s'`);assert.equal(setupCommands({accountsDir:'a',projectsDir:'b',port:0,admin:'x',sshTarget:'y'})[1].command.includes('--port 8000'),true);
});
test('the panel offers a task for shared projects and warns about unencrypted addresses; the guide sits beside it',()=>{
 const panel=fs.readFileSync(path.join(__dirname,'SharedProjectPanel.tsx'),'utf8');
 assert.match(panel,/api\.project\.create\(\{name,task:newTask\}\)/);assert.ok(!panel.includes("task:'classification'"));
 assert.match(panel,/aria-label="공유 프로젝트 작업 종류"/);assert.match(panel,/insecureAddress\(server\)&&<p role="alert"/);
 const start=panel.indexOf('function insecureAddress');const body=panel.slice(start,panel.indexOf('\n',start));
 const m=new Module('insecure.ts',module);m._compile(ts.transpileModule(body+'\nmodule.exports={insecureAddress};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,'insecure.ts');
 assert.equal(m.exports.insecureAddress('http://192.168.0.5:8000'),true);assert.equal(m.exports.insecureAddress('http://127.0.0.1:8000'),false);assert.equal(m.exports.insecureAddress('https://vision.example.com'),false);
 assert.match(fs.readFileSync(path.join(__dirname,'ComputeServerPanel.tsx'),'utf8'),/<SharedProjectPanel \/>\n\s*<SharedServerSetupGuide \/>/);
});
